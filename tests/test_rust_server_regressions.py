"""Guard warm-service output, dependency precedence, and owned-process cleanup.

These contracts run the native CLI and retained Haskell worker on isolated real
projects. They cover regressions that ordinary single-source snapshot builds
cannot expose: returning to a cached header, changing another source's image,
and two clients racing to create their first shared background service.
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import http.client
import json
import os
import re
import shutil
import struct
import subprocess
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from lxml import html as html_parser
import pytest
import yaml

from test_build_snapshots import ROOT
from test_rust_cli_contract import (
    NativeProject,
    _available_port,
    _stop_owned_pid,
    native_project_factory,
    native_service_factory,
)


@pytest.fixture(autouse=True)
def disable_update_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep command startup independent of unrelated automatic update activity."""
    monkeypatch.setenv("PAPPER_DISABLE_UPDATE_CHECK", "1")


def _metadata_source(label: str, body: str) -> str:
    """Give each header distinct visible and document metadata for cache checks."""
    return (
        f"---\ntitle: Header {label}\nsubtitle: Subtitle {label}\n"
        f"authors:\n  - name: Author {label}\n    email: {label.lower()}@example.test\n"
        f"author-meta: Author {label}\n"
        f"keywords: [keyword-{label.lower()}]\nabstract: Abstract {label}.\n"
        f"description-meta: Description {label}.\n---\n\n{body}\n"
    )


def test_native_cli_bootstraps_from_external_empty_markdown_and_reuses_service(
    native_project_factory, tmp_path: Path,
) -> None:
    """External editor bootstrap must start a real worker that accepts later source snapshots."""
    project = native_project_factory()
    original = project.source.read_bytes()
    external = tmp_path / "external-source"
    external.mkdir()
    source = external / "bootstrap.md"
    source.write_text("", encoding="utf-8")
    bootstrap = NativeProject(project.directory, source, project.executable, project.environment)
    port = _available_port()
    owned: set[int] = set()
    try:
        bootstrap.build(external / "startup.html", server_port=port)
        status, raw = _http_request(port, "GET", "/version")
        assert status == 200
        version = json.loads(raw)
        assert version["project_dir"] == str(project.directory.resolve())
        owned.update([version["pid"], version["worker_pid"]])
        source.unlink()  # The extension removes its bootstrap file before sending editor text.
        status, raw = _http_request(port, "POST", "/convert/raw", {
            "path": str(project.source), "text": "Current unsaved editor buffer",
        })
        assert status == 200 and b"Current unsaved editor buffer" in raw
        assert project.source.read_bytes() == original
        source.write_text("---\ntitle: External manuscript\n---\n\nExternal saved body.\n", encoding="utf-8")
        output = external / "rendered.html"
        bootstrap.build(output, server_port=port)
        status, raw = _http_request(port, "GET", "/version")
        updated = json.loads(raw)
        assert status == 200 and updated["pid"] == version["pid"]
        assert updated["worker_pid"] == version["worker_pid"]
        bootstrap.assert_cli_parity(output.read_text(encoding="utf-8"))
        assert "External saved body." in output.read_text(encoding="utf-8")
    finally:
        try:
            status, raw = _http_request(port, "GET", "/version")
            version = json.loads(raw)
            if status == 200 and version.get("project_dir") == str(project.directory.resolve()):
                owned.update([version["pid"], version["worker_pid"]])
                _http_request(port, "POST", "/shutdown", {"project_dir": version["project_dir"]})
        except (OSError, http.client.HTTPException):
            pass
        if owned and not _wait_for_exit(owned, timeout=3):
            for pid in owned:
                if _process_alive(pid):
                    _stop_owned_pid(pid)


def _assert_header(html: str, label: str) -> None:
    """Inspect actual HTML metadata and title-page elements rather than cached settings."""
    document = html_parser.fromstring(html)
    assert document.xpath("string(//title)") == f"Header {label}"
    assert document.xpath("string(//h1[@class='title'])") == f"Header {label}"
    assert document.xpath("string(//p[@class='subtitle'])") == f"Subtitle {label}"
    assert f"Author {label}" in document.xpath("string(//p[@class='author'])")
    assert document.xpath("//meta[@name='author']/@content") == [f"Author {label}"]
    assert document.xpath("//meta[@name='keywords']/@content") == [f"keyword-{label.lower()}"]
    assert document.xpath("//meta[@name='description']/@content") == [f"Description {label}."]
    assert f"Abstract {label}." in document.xpath("string(//div[@class='abstract'])")


def test_native_service_restores_cached_header_after_another_header_build(native_service_factory) -> None:
    """An A→B→A edit must not read B's overwritten metadata during a fresh body build."""
    service = native_service_factory()
    project = service.project
    for label, body in [("Alpha", "First body."), ("Beta", "Second body."),
                        ("Alpha", "Fresh body after returning to Alpha.")]:
        project.source.write_text(_metadata_source(label, body), encoding="utf-8")
        result = service.convert()
        assert not result["cache_hit"]
        _assert_header(result["output"], label)
        assert body in result["output"]
    assert "Header Beta" not in result["output"]
    assert "Author Beta" not in result["output"]
    status, raw, _ = service.request("POST", "/convert/raw", {"path": project.source.name})
    assert status == 200
    output = project.directory / "cold-header.html"
    project.build(output)
    assert raw == output.read_bytes()


def test_native_service_refreshes_manuscript_style_overrides(native_service_factory) -> None:
    """Apply buffer-local styles across edits and cache reuse, then restore file styles on removal."""
    service = native_service_factory()
    (service.project.directory / "style.yml").write_text(
        "tableAutofit: window\ndocxStyle:\n  Table Text:\n"
        "    paragraphSpacing: {before: 7pt, after: 8pt}\n",
        encoding="utf-8",
    )
    body = "| Item | Value |\n|---|---|\n| Sample | 1 |\n\n: Results\n"
    for mode, after in [("content", 0), ("fixed", 3), ("content", 0)]:
        header = yaml.safe_dump({
            "papper-style": {
                "tableAutofit": mode,
                "docxStyle": {"Table Text": {"paragraphSpacing": {"after": f"{after}pt"}}},
            },
        })
        result = service.convert(text=f"---\n{header}---\n\n{body}")
        document = html_parser.fromstring(result["output"])
        assert document.xpath("//table/@data-autofit") == [mode]
        assert "--pmt-table-text-before: 7pt;" in result["output"]
        assert f"--pmt-table-text-after: {after}pt;" in result["output"]
    assert result["cache_hit"]
    restored = service.convert(text=body)
    document = html_parser.fromstring(restored["output"])
    assert document.xpath("//table/@data-autofit") == ["window"]
    assert "--pmt-table-text-after: 8pt;" in restored["output"]


def test_native_service_detects_reply_buffers_and_refreshes_manuscript_numbers(native_service_factory) -> None:
    """Unsaved reply headers and changes to their manuscript must affect warm HTML output."""
    service = native_service_factory()
    project = service.project
    manuscript = project.directory / "original.md"
    manuscript.write_text("# First\n\n# Selected {#sec:chosen}\n", encoding="utf-8")
    reply = "---\nreply: original.md\n---\n\nWe revised @sec:chosen.\n"
    first = service.convert(text=reply)
    assert "We revised Section 2." in first["output"]
    manuscript.write_text("# Leading\n\n" + manuscript.read_text(encoding="utf-8"), encoding="utf-8")
    updated = service.convert(text=reply)
    assert "We revised Section 3." in updated["output"]
    ordinary = service.convert(text="# Reply\n\nreply: missing.md\n\n# Local {#sec:chosen}\n\nSee @sec:chosen.\n")
    visible = " ".join(html_parser.fromstring(ordinary["output"]).text_content().split())
    assert "See Section 2." in visible
    assert "reply: missing.md" in ordinary["output"]


@pytest.mark.parametrize("reply", [False, True])
def test_html_reply_caption_table_and_where_colors_match_word(native_service_factory, reply: bool) -> None:
    """Inspect browser styles in cold, warm and preview HTML; prose/math and ordinary builds keep their colors."""
    from playwright.sync_api import sync_playwright

    service = native_service_factory()
    project = service.project
    (project.directory / "original.md").write_text("# Manuscript\n", encoding="utf-8")
    (project.directory / "style.yml").write_text(
        "docxStyle:\n  Table Text:\n    fontColor: '#AA0000'\n"
        "  Table Caption:\n    fontColor: '#00AA00'\n"
        "  Image Caption:\n    fontColor: '#00AA00'\n"
        "  Para Where:\n    fontColor: '#AA0000'\n", encoding="utf-8",
    )
    markdown = ('---\nreply: original.md\n---\n\n' if reply else '') + (
        '<p id="comment">Reviewer comment.</p>\n\n'
        '![Figure caption](data:image/svg+xml,%3Csvg%20xmlns=%22http://www.w3.org/2000/svg%22/%3E)\n\n'
        '| **Header** | Value |\n|---|---|\n| **Strong cell** | [Linked cell](https://example.test) |\n\n'
        ': Table caption {revision_rows="2"}\n\n'
        '$$ x=1 $$\n\nwhere $x$ is the variable.\n'
    )
    project.source.write_text(markdown, encoding="utf-8")
    output = project.directory / "colors.html"
    project.build(output)
    sources = [output.read_text(encoding="utf-8"), service.convert(text=markdown)["output"],
               service.convert(text=markdown, mode="preview")["output"]]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            def block_external_assets(route) -> None:
                """Keep color checks independent of external fonts, math scripts and hyperlinks."""
                route.abort()

            page.route("**/*", block_external_assets)
            for source in sources:
                document = html_parser.fromstring(source)
                for script in document.xpath("//script"):
                    script.drop_tree()  # Computed text styles do not need external math scripts.
                page.set_content(html_parser.tostring(document, encoding="unicode"))
                caption_color = "rgb(0, 0, 255)" if reply else "rgb(0, 170, 0)"
                for selector in ["figcaption", "caption", "th", "th strong", "td", "td strong", "td a"]:
                    style = page.locator(selector).first.evaluate(
                        "element => ({color: getComputedStyle(element).color, italic: getComputedStyle(element).fontStyle})"
                    )
                    if reply:
                        assert style == {"color": caption_color, "italic": "italic"}, (selector, style)
                    elif selector in {"caption", "figcaption"}:
                        assert style["color"] == caption_color
                where = page.locator('div[data-custom-style="Para Where"] > p').evaluate(
                    "element => getComputedStyle(element).color"
                )
                assert where == ("rgb(0, 0, 255)" if reply else "rgb(170, 0, 0)")
                assert page.locator("#comment").evaluate("element => getComputedStyle(element).color") != "rgb(0, 0, 255)"
                assert page.locator(".math.display").evaluate("element => getComputedStyle(element).color") != "rgb(0, 0, 255)"
        finally:
            browser.close()


def test_native_service_updates_used_custom_styles_and_preserves_table_text_overrides(
    native_service_factory,
) -> None:
    """Refresh custom styles and configured inheritance without losing explicit child overrides."""
    service = native_service_factory()
    project = service.project
    style_path = project.directory / "style.yml"
    configured = (
        "docxStyle:\n  Table Text:\n    paragraphSpacing:\n      before: 7pt\n      after: 8pt\n"
        "  正文文本:\n    firstLineIndentChars: 2\n"
        "    paragraphSpacing:\n      before: 0pt\n      after: 0pt\n"
    )
    style_path.write_text(configured, encoding="utf-8")
    markdown = (
        '::: {custom-style="Reply Header"}\n\n'
        'A response with [highlighted words]{custom-style="Reply Char"}.\n\n:::\n\n'
        '| Value |\n|---|\n| 1 |\n\n'
        ': Results {custom-style="TableNoBorder" cell_margin_left="9pt"}\n\n'
        '::: {custom-style="Para After Table"}\n\nAn indented continuation.\n\n:::\n\n'
        '::: {custom-style="Para Where"}\n\nAn explicitly unindented explanation.\n\n:::\n'
    )
    first = service.convert(text=markdown)
    document = html_parser.fromstring(first["output"])
    css = document.xpath('string(//style[@id="pmt-custom-styles"])')
    assert 'div[data-custom-style="Reply Header"] > p' in css
    assert '[data-custom-style="Reply Char"]' in css
    assert 'color: #0000FF;' in css
    assert 'Revision Char' not in css
    assert 'text-indent: 2em;' in css
    assert 'text-indent: 0em;' in css
    assert '--pmt-table-text-before: 7pt;' in first["output"]
    assert '--pmt-table-text-after: 8pt;' in first["output"]
    assert '--pmt-table-text-before:' not in css
    assert '--pmt-table-cell-margin-left: 9pt;' in document.xpath('string(//table/@style)')
    assert service.convert(text=markdown)["cache_hit"]

    edited = markdown.replace('Reply Header', 'Revision Para').replace('Reply Char', 'Revision Char')
    second = service.convert(text=edited, mode="preview")
    assert not second["cache_hit"]
    document = html_parser.fromstring(second["output"])
    css = document.xpath('string(//style[@id="pmt-custom-styles"])')
    assert 'div[data-custom-style="Revision Para"] > p' in css
    assert '[data-custom-style="Revision Char"]' in css
    assert 'color: #EE0000;' in css
    assert '--pmt-table-text-before: 7pt;' in second["output"]
    assert '--pmt-table-text-after: 8pt;' in second["output"]
    assert 'Reply Header' not in css and 'Reply Char' not in css

    # Changing a configured parent must invalidate warm-service output and
    # update descendants; the child's explicit zero indentation must survive.
    style_path.write_text(configured.replace("firstLineIndentChars: 2", "firstLineIndentChars: 3"),
                          encoding="utf-8")
    changed = service.convert(text=markdown, mode="preview")
    assert not changed["cache_hit"]
    css = html_parser.fromstring(changed["output"]).xpath('string(//style[@id="pmt-custom-styles"])')
    assert 'text-indent: 3em;' in css and 'text-indent: 2em;' not in css
    assert 'text-indent: 0em;' in css
    assert '--pmt-table-text-before: 7pt;' in changed["output"]
    assert '--pmt-table-text-after: 8pt;' in changed["output"]
    plain = service.convert(text="A plain document with no custom styles.\n")
    assert not html_parser.fromstring(plain["output"]).xpath('//style[@id="pmt-custom-styles"]')


def _png_bytes(color: tuple[int, int, int]) -> bytes:
    """Encode a real single-pixel PNG without adding an imaging dependency."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        """Write one CRC-protected PNG chunk used by the small image fixture."""
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(b"\x00" + bytes(color))) + chunk(b"IEND", b""))


def _embedded_image(html: str) -> bytes:
    """Decode the rendered figure so assertions cover Pandoc's actual selected file."""
    document = html_parser.fromstring(html)
    sources = document.xpath("//img[@alt='Local figure']/@src")
    assert len(sources) == 1
    prefix, separator, encoded = sources[0].partition(",")
    assert separator and prefix == "data:image/png;base64"
    return base64.b64decode(encoded, validate=True)


@pytest.mark.parametrize("external", [False, True], ids=["project-source", "external-source"])
def test_native_service_tracks_secondary_source_images_and_later_priority_creation(
    native_service_factory, tmp_path: Path, external: bool,
) -> None:
    """Invalidate real embedded images by bytes and by newly available source-local overrides."""
    service = native_service_factory(custom_defaults=True)
    project = service.project
    defaults = yaml.safe_load(service.defaults.read_text(encoding="utf-8"))
    defaults["embed-resources"] = True
    # The local-image contract must not fetch a remote KaTeX distribution when
    # Pandoc makes standalone output self-contained. Its default math is local.
    defaults.pop("math-method", None)
    service.defaults.write_text(yaml.safe_dump(defaults), encoding="utf-8")
    project.source.write_text("# Initial source\n\nInitial document.\n", encoding="utf-8")
    service.convert()
    directory = tmp_path / "external-images" if external else project.directory / "subdir"
    directory.mkdir()
    source = directory / "other.md"
    source_path = str(source) if external else "subdir/other.md"
    source.write_text("# Other source\n\n![Local figure](figure.png)\n", encoding="utf-8")
    fallback = project.directory / "figure.png"
    fallback_bytes = _png_bytes((255, 0, 0))
    fallback.write_bytes(fallback_bytes)
    first = service.convert(path=source_path)
    assert not first["cache_hit"]
    assert _embedded_image(first["output"]) == fallback_bytes
    assert service.convert(path=source_path)["cache_hit"]

    # Missing candidates must participate in the fingerprint: creating this
    # higher-priority source-local file changes which image Pandoc selects.
    preferred = directory / "figure.png"
    preferred_bytes = _png_bytes((0, 255, 0))
    preferred.write_bytes(preferred_bytes)
    created = service.convert(path=source_path)
    assert not created["cache_hit"]
    assert _embedded_image(created["output"]) == preferred_bytes
    assert created["output"] != first["output"]
    assert service.convert(path=source_path)["cache_hit"]

    previous = preferred.stat()
    updated_bytes = _png_bytes((0, 0, 255))
    assert len(updated_bytes) == len(preferred_bytes)
    preferred.write_bytes(updated_bytes)
    os.utime(preferred, ns=(previous.st_atime_ns, previous.st_mtime_ns))
    assert preferred.stat().st_mtime_ns == previous.st_mtime_ns
    assert preferred.stat().st_size == previous.st_size
    edited = service.convert(path=source_path)
    assert not edited["cache_hit"]
    assert _embedded_image(edited["output"]) == updated_bytes
    assert edited["output"] != created["output"]


def _decoded_embedded_resources(html: str) -> set[bytes]:
    """Expand nested data URIs so CSS imports and their images can be inspected."""
    resources = {html.encode("utf-8")}
    pending = list(resources)
    pattern = re.compile(rb"data:[^,\s\"'()<>]+;base64,([A-Za-z0-9+/]+={0,2})")
    while pending:
        content = pending.pop()
        for match in pattern.finditer(content):
            decoded = base64.b64decode(match[1], validate=True)
            if decoded not in resources:
                resources.add(decoded)
                pending.append(decoded)
    return resources


def test_native_service_invalidates_embedded_css_imports_and_local_images(native_service_factory) -> None:
    """Refresh parent CSS, imported CSS, and same-mtime image bytes inside self-contained HTML."""
    service = native_service_factory(custom_defaults=True)
    project = service.project
    project.source.write_text("# Stylesheet probe\n\nAn unchanged document.\n", encoding="utf-8")
    directory = project.directory / "stylesheets"
    directory.mkdir()
    stylesheet = directory / "custom.css"
    stylesheet.write_text('@import "theme.css";\n.css-probe { color: red; }\n', encoding="utf-8")
    imported = directory / "theme.css"
    imported.write_text('.theme-probe { color: orange; background-image: url("figure.png"); }\n', encoding="utf-8")
    image = directory / "figure.png"
    original_image = _png_bytes((255, 0, 0))
    image.write_bytes(original_image)
    defaults = yaml.safe_load(service.defaults.read_text(encoding="utf-8"))
    defaults["embed-resources"] = True
    defaults["css"] = [str(stylesheet)]
    defaults.pop("math-method", None)
    service.defaults.write_text(yaml.safe_dump(defaults), encoding="utf-8")
    first = service.convert()
    assert not first["cache_hit"]
    assert original_image in _decoded_embedded_resources(first["output"])
    assert service.convert()["cache_hit"]

    stylesheet.write_text('@import "theme.css";\n.css-probe { color: blue; }\n', encoding="utf-8")
    parent_edit = service.convert()
    assert not parent_edit["cache_hit"]
    assert ".css-probe { color: blue; }" in parent_edit["output"]
    assert parent_edit["output"] != first["output"]
    assert service.convert()["cache_hit"]

    # Switching import syntax exercises both CSS string imports and url()
    # imports while keeping the same referenced file and unchanged manuscript.
    stylesheet.write_text('@import url("theme.css");\n.css-probe { color: blue; }\n', encoding="utf-8")
    service.convert()
    assert service.convert()["cache_hit"]
    imported.write_text('.theme-probe { color: purple; background-image: url("figure.png"); }\n', encoding="utf-8")
    child_edit = service.convert()
    assert not child_edit["cache_hit"]
    resources = _decoded_embedded_resources(child_edit["output"])
    assert any(b"color: purple" in data for data in resources)
    assert child_edit["output"] != parent_edit["output"]
    assert service.convert()["cache_hit"]

    previous = image.stat()
    edited_image = _png_bytes((0, 0, 255))
    assert len(edited_image) == previous.st_size
    image.write_bytes(edited_image)
    os.utime(image, ns=(previous.st_atime_ns, previous.st_mtime_ns))
    assert image.stat().st_mtime_ns == previous.st_mtime_ns
    image_edit = service.convert()
    assert not image_edit["cache_hit"]
    resources = _decoded_embedded_resources(image_edit["output"])
    assert edited_image in resources and original_image not in resources
    assert image_edit["output"] != child_edit["output"]


@pytest.fixture
def mutable_remote_image():
    """Serve a changing real PNG at one local HTTP URL without external network access."""
    state = {"image": _png_bytes((255, 0, 0))}

    class ImageHandler(BaseHTTPRequestHandler):
        """Return the current image to Pandoc's actual HTTP resource fetcher."""

        def do_GET(self) -> None:
            """Publish the selected PNG bytes with a complete content-length response."""
            image = state["image"]
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(image)))
            self.end_headers()
            self.wfile.write(image)

        def log_message(self, format: str, *arguments: Any) -> None:
            """Keep local fixture access diagnostics out of normal test output."""
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), ImageHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/figure.png", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_native_service_refreshes_remote_embedded_images_but_caches_plain_urls(
    native_service_factory, mutable_remote_image,
) -> None:
    """Embed current remote bytes while retaining unchanged ordinary-URL HTML cache hits."""
    service = native_service_factory(custom_defaults=True)
    project = service.project
    url, state = mutable_remote_image
    project.source.write_text(f"# Remote resource probe\n\n![Local figure]({url})\n", encoding="utf-8")
    defaults = yaml.safe_load(service.defaults.read_text(encoding="utf-8"))
    defaults["embed-resources"] = False
    defaults.pop("math-method", None)
    service.defaults.write_text(yaml.safe_dump(defaults), encoding="utf-8")
    plain = service.convert()
    assert html_parser.fromstring(plain["output"]).xpath("//img[@alt='Local figure']/@src") == [url]
    state["image"] = _png_bytes((0, 0, 255))
    unchanged_url = service.convert()
    assert unchanged_url["cache_hit"] and unchanged_url["output"] == plain["output"]

    defaults["embed-resources"] = True
    service.defaults.write_text(yaml.safe_dump(defaults), encoding="utf-8")
    embedded = service.convert()
    assert not embedded["cache_hit"] and _embedded_image(embedded["output"]) == state["image"]
    state["image"] = _png_bytes((255, 0, 0))
    refreshed = service.convert()
    assert not refreshed["cache_hit"]
    assert _embedded_image(refreshed["output"]) == state["image"]
    assert refreshed["output"] != embedded["output"]

    # Direct Pandoc flags must obey the same contract as defaults. Reconfigure
    # the actual service rather than mocking worker arguments or cache wiring.
    defaults["embed-resources"] = False
    service.defaults.write_text(yaml.safe_dump(defaults), encoding="utf-8")
    original_config = json.loads(service.config_path.read_text(encoding="utf-8"))
    for flag in ("--embed-resources", "--self-contained"):
        config = {**original_config, "pandoc_args": [*original_config["pandoc_args"], flag]}
        status, body, _ = service.request("POST", "/config", config)
        assert status == 200, body.decode("utf-8", errors="replace")
        current = service.convert()
        assert not current["cache_hit"] and _embedded_image(current["output"]) == state["image"]
        state["image"] = _png_bytes((0, 255, 0)) if flag == "--embed-resources" else _png_bytes((0, 0, 255))
        changed = service.convert()
        assert not changed["cache_hit"]
        assert _embedded_image(changed["output"]) == state["image"]
        assert changed["output"] != current["output"]


def _http_request(port: int, method: str, route: str, payload: Any = None) -> tuple[int, bytes]:
    """Contact the public local API without proxy settings or external HTTP tools."""
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        connection.request(method, route, body=body, headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def _process_alive(pid: int) -> bool:
    """Observe owned process exit, including Windows handles and Unix zombies."""
    if os.name == "nt":
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            assert ctypes.get_last_error() == 87, ctypes.get_last_error()
            return False
        try:
            code = wintypes.DWORD()
            assert kernel.GetExitCodeProcess(handle, ctypes.byref(code)), ctypes.get_last_error()
            return code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    status = Path(f"/proc/{pid}/stat")
    return not status.is_file() or status.read_text(encoding="ascii").rpartition(")")[2].split()[0] != "Z"


def _wait_for_exit(pids: set[int], timeout: float = 10) -> bool:
    """Allow graceful worker shutdown while retaining a bounded failure deadline."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if all(not _process_alive(pid) for pid in pids):
            return True
        time.sleep(0.05)
    return all(not _process_alive(pid) for pid in pids)


def test_native_first_concurrent_cli_start_records_serving_pid_and_clean_stops_worker(
    tmp_path: Path, rust_executable: Path,
) -> None:
    """Two first clients must share an owner that clean stops before removing project caches."""
    for round_number in range(3):
        directory = tmp_path / f"project-{round_number}"
        directory.mkdir()
        source = directory / "paper.md"
        original = f"---\ntitle: Concurrent project {round_number}\n---\n\nA complete document.\n"
        source.write_text(original, encoding="utf-8")
        home = tmp_path / f"home-{round_number}"
        project = NativeProject(directory, source, rust_executable,
                                {**os.environ, "PAPPER_HOME": str(home), "PAPPER_RESOURCE_ROOT": str(ROOT)})
        port = _available_port()
        barrier = threading.Barrier(2)
        owned: set[int] = set()

        def first_build(index: int) -> subprocess.CompletedProcess[str]:
            """Release independent CLI processes together before either service exists."""
            barrier.wait(timeout=5)
            return project.build(directory / f"output/client-{index}.html", server_port=port, check=False)

        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(first_build, range(2)))
            status, body = _http_request(port, "GET", "/version")
            assert status == 200
            version = json.loads(body)
            assert version["project_dir"] == str(directory.resolve())
            owned.update([version["pid"], version["worker_pid"]])
            for result in results:
                assert result.returncode == 0, result.stdout + result.stderr
            first = directory / "output/client-0.html"
            assert first.read_bytes() == (directory / "output/client-1.html").read_bytes()
            assert f"Concurrent project {round_number}" in first.read_text(encoding="utf-8")
            states = list(home.glob("projects/*/work/rust-v1/server-state.json"))
            assert len(states) == 1
            saved = json.loads(states[0].read_text(encoding="utf-8"))
            assert saved["pid"] == version["pid"]
            assert all(_process_alive(pid) for pid in owned)
            state = states[0].parents[2]
            cache = state / "cache/result.bin"
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(b"Reusable project data")
            cleaned = subprocess.run([str(rust_executable), "clean"], cwd=directory, env=project.environment,
                                     capture_output=True, text=True, encoding="utf-8", timeout=30)
            assert cleaned.returncode == 0, cleaned.stdout + cleaned.stderr
            assert _wait_for_exit(owned), f"Owned service or worker survived clean: {owned}"
            assert not state.exists()
            assert not (directory / "output").exists()
            assert source.read_text(encoding="utf-8") == original
        finally:
            # Record the actual responding owner even when a failed first CLI
            # saved its losing child's PID. Teardown must not leak that service.
            try:
                status, body = _http_request(port, "GET", "/version")
                version = json.loads(body)
                if status == 200 and version.get("project_dir") == str(directory.resolve()):
                    owned.update([version["pid"], version["worker_pid"]])
                    _http_request(port, "POST", "/shutdown", {"project_dir": version["project_dir"]})
            except (OSError, http.client.HTTPException):
                pass
            if owned and not _wait_for_exit(owned, timeout=3):
                for pid in owned:
                    if _process_alive(pid):
                        _stop_owned_pid(pid)


def _close_project_service(project: NativeProject, port: int, owned: set[int]) -> None:
    """Stop only test-owned project processes, including a replacement after failed assertions."""
    try:
        status, raw = _http_request(port, "GET", "/version")
        version = json.loads(raw)
        if status == 200 and version.get("project_dir") == str(project.directory.resolve()):
            owned.update([version["pid"], version["worker_pid"]])
            _http_request(port, "POST", "/shutdown", {"project_dir": version["project_dir"]})
    except (OSError, http.client.HTTPException):
        pass
    if not _wait_for_exit(owned, timeout=3):
        for pid in owned:
            if _process_alive(pid):
                _stop_owned_pid(pid)


def test_native_upgrade_replaces_unlocked_entrypoint_and_restarts_one_shared_service(
    native_project_factory,
) -> None:
    """An in-place Windows upgrade must succeed and concurrent clients must retire the old worker."""
    project = native_project_factory()
    source = project.source.read_bytes()
    launcher = project.directory / project.executable.name
    shutil.copy2(project.executable, launcher)
    installed = NativeProject(project.directory, project.source, launcher, project.environment)
    port = _available_port()
    owned: set[int] = set()
    try:
        previous_output = project.directory / "previous.html"
        installed.build(previous_output, server_port=port)
        previous_html = previous_output.read_bytes()
        _, raw = _http_request(port, "GET", "/version")
        previous = json.loads(raw)
        old_pids = {previous["pid"], previous["worker_pid"]}
        owned.update(old_pids)
        state_file = next(Path(project.environment["PAPPER_HOME"]).glob(
            "projects/*/work/rust-v1/server-state.json"
        ))
        cache = state_file.parents[2] / "cache/upgrade-preserved.bin"
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(b"Preserved project cache")

        # Config reload must never rewrite the executing program's identity.
        config = json.loads(state_file.with_name("server-config.json").read_text(encoding="utf-8"))
        config["runtime_version"] = "unrelated-config-version"
        status, raw = _http_request(port, "POST", "/config", config)
        assert status == 200, raw
        _, raw = _http_request(port, "GET", "/version")
        assert json.loads(raw)["runtime_version"] == previous["runtime_version"]

        # PE/ELF loaders permit an overlay. Change binary identity without a
        # second compilation; copying over a locked Windows shim would fail here.
        shutil.copyfile(project.executable, launcher)
        with launcher.open("ab") as executable:
            executable.write(b"Papper upgrade regression image\n")
        expected_identity = hashlib.sha256(launcher.read_bytes()).hexdigest()
        assert expected_identity != previous["runtime_id"]
        _, raw = _http_request(port, "GET", "/version")
        assert json.loads(raw)["pid"] == previous["pid"]

        # A replacement that cannot be staged must leave the live server and
        # existing output intact, so retrying after fixing storage is safe.
        blocked_runtime = Path(project.environment["PAPPER_HOME"]) / "server-runtimes" / expected_identity
        blocked_runtime.write_bytes(b"Deliberately unavailable runtime directory")
        failed = installed.build(previous_output, server_port=port, check=False)
        assert failed.returncode != 0
        _, raw = _http_request(port, "GET", "/version")
        assert json.loads(raw)["pid"] == previous["pid"]
        assert previous_output.read_bytes() == previous_html
        blocked_runtime.unlink()

        barrier = threading.Barrier(2)

        def upgraded_build(index: int) -> subprocess.CompletedProcess[str]:
            """Race two new CLI invocations while the old project service owns the port."""
            barrier.wait(timeout=5)
            return installed.build(project.directory / f"upgrade-{index}.html", server_port=port, check=False)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(upgraded_build, range(2)))
        _, raw = _http_request(port, "GET", "/version")
        current = json.loads(raw)
        owned.update([current["pid"], current["worker_pid"]])
        for result in results:
            assert result.returncode == 0, result.stdout + result.stderr
        assert current["runtime_id"] == expected_identity
        assert current["pid"] != previous["pid"]
        assert current["worker_pid"] != previous["worker_pid"]
        assert _wait_for_exit(old_pids), "Upgrade left the old server or worker alive"
        assert (project.directory / "upgrade-0.html").read_bytes() == previous_html
        assert (project.directory / "upgrade-1.html").read_bytes() == previous_html
        assert previous_output.read_bytes() == previous_html
        assert cache.read_bytes() == b"Preserved project cache"
        assert project.source.read_bytes() == source
        installed.build(project.directory / "reused.html", server_port=port)
        _, raw = _http_request(port, "GET", "/version")
        assert json.loads(raw)["pid"] == current["pid"]
    finally:
        _close_project_service(project, port, owned)


def test_native_server_watches_installation_and_restarts_with_updated_runtime(
    native_project_factory,
) -> None:
    """A changed installed image must replace the server and its owned Pandoc worker."""
    project = native_project_factory()
    launcher = project.directory / project.executable.name
    shutil.copy2(project.executable, launcher)
    environment = {
        **project.environment,
        "PAPPER_SERVER_UPDATE_SOURCE": str(launcher),
    }
    installed = NativeProject(project.directory, project.source, launcher, environment)
    port = _available_port()
    owned: set[int] = set()
    try:
        installed.build(project.directory / "before-upgrade.html", server_port=port)
        _, raw = _http_request(port, "GET", "/version")
        previous = json.loads(raw)
        old_pids = {previous["pid"], previous["worker_pid"]}
        owned.update(old_pids)

        with launcher.open("ab") as executable:
            executable.write(b"Papper watched upgrade image\n")
        expected_identity = hashlib.sha256(launcher.read_bytes()).hexdigest()

        deadline = time.monotonic() + 15
        current = previous
        while time.monotonic() < deadline:
            try:
                _, raw = _http_request(port, "GET", "/version")
                current = json.loads(raw)
                if current.get("runtime_id") == expected_identity:
                    break
            except (OSError, http.client.HTTPException):
                pass
            time.sleep(0.05)
        owned.update([current["pid"], current.get("worker_pid")])
        assert current["runtime_id"] == expected_identity
        assert current["pid"] != previous["pid"]
        assert current["worker_pid"] != previous["worker_pid"]
        assert _wait_for_exit(old_pids), "Self-upgrade left the old server or worker alive"
        assert (project.directory / "before-upgrade.html").is_file()
    finally:
        _close_project_service(installed, port, owned)


@pytest.mark.parametrize("old_identity", ["older-version", "missing-runtime-id", "shutdown-refused"])
def test_native_upgrade_retires_older_http_runtime_even_with_matching_config(
    native_project_factory, old_identity: str,
) -> None:
    """A matching config must not keep an old runtime or one predating binary identity checks."""
    project = native_project_factory()
    port = _available_port()
    owned: set[int] = set()

    class PreviousRuntime(BaseHTTPRequestHandler):
        """Model the previous release at the HTTP boundary; replacement uses the real native engine."""

        def do_GET(self) -> None:
            """Report an old process whose config already matches the new client's request."""
            config_file = next(Path(project.environment["PAPPER_HOME"]).glob(
                "projects/*/work/rust-v1/server-config.json"
            ))
            config = json.loads(config_file.read_text(encoding="utf-8"))
            version = {
                "pid": os.getpid(), "runtime": "rust", "protocol": "pmt-html-v1",
                "project_dir": str(project.directory.resolve()),
                "runtime_version": "older-runtime" if old_identity == "older-version" else config["runtime_version"],
                "config_digest": hashlib.sha256(json.dumps(
                    config, ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8")).hexdigest(),
            }
            if old_identity == "older-version":
                version["runtime_id"] = "older-executable"
            body = json.dumps(version).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            """Release the old listener only for a correctly identified shutdown request."""
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            valid = (self.path == "/shutdown" and old_identity != "shutdown-refused"
                     and payload["project_dir"] == str(project.directory.resolve()))
            body = json.dumps({"ok": valid}).encode("utf-8")
            self.send_response(200 if valid else 400)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            if valid:
                self.server.shutdown()
                self.server.server_close()

    previous = ThreadingHTTPServer(("127.0.0.1", port), PreviousRuntime)
    previous.daemon_threads = True
    thread = threading.Thread(target=previous.serve_forever, daemon=True)
    thread.start()
    try:
        output = project.directory / "upgraded.html"
        if old_identity == "shutdown-refused":
            output.write_bytes(b"Existing output")
            failed = project.build(output, server_port=port, check=False)
            assert failed.returncode != 0
            assert output.read_bytes() == b"Existing output"
            _, raw = _http_request(port, "GET", "/version")
            assert json.loads(raw)["pid"] == os.getpid()
            return
        project.build(output, server_port=port)
        _, raw = _http_request(port, "GET", "/version")
        current = json.loads(raw)
        owned.update([current["pid"], current["worker_pid"]])
        assert current["pid"] != os.getpid()
        assert current["runtime_id"] == hashlib.sha256(project.executable.read_bytes()).hexdigest()
        project.assert_cli_parity(output.read_text(encoding="utf-8"))
    finally:
        previous.shutdown()
        previous.server_close()
        thread.join(timeout=5)
        _close_project_service(project, port, owned)


def test_native_upgrade_preserves_another_projects_service_and_rejects_stale_shutdown(
    native_service_factory, tmp_path: Path,
) -> None:
    """A reused port or stale PID must never stop another project's live converter."""
    service = native_service_factory()
    status, raw, _ = service.request("GET", "/version")
    version = json.loads(raw)
    assert status == 200
    status, _, _ = service.request("POST", "/shutdown", {
        "project_dir": version["project_dir"], "pid": version["pid"] + 1,
    })
    assert status == 400
    directory = tmp_path / "another-project"
    directory.mkdir()
    source = directory / "paper.md"
    source.write_text("Another project's manuscript.\n", encoding="utf-8")
    output = directory / "preserved.html"
    output.write_bytes(b"Existing output")
    other = NativeProject(directory, source, service.project.executable, service.project.environment)
    result = other.build(output, server_port=service.port, check=False)
    assert result.returncode != 0 and "another Papper project" in result.stderr
    assert output.read_bytes() == b"Existing output"
    assert "Original service remains available" in service.convert(text="Original service remains available")["output"]
    _, raw, _ = service.request("GET", "/version")
    assert json.loads(raw)["pid"] == version["pid"]
