"""Guard warm-service output, dependency precedence, and owned-process cleanup.

These contracts run the native CLI and retained Haskell worker on isolated real
projects. They cover regressions that ordinary single-source snapshot builds
cannot expose: returning to a cached header, changing another source's image,
and two clients racing to create their first shared background service.
"""

from __future__ import annotations

import base64
import ctypes
import http.client
import json
import os
import re
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
    rust_executable,
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


def test_native_service_tracks_secondary_source_images_and_later_priority_creation(native_service_factory) -> None:
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
    directory = project.directory / "subdir"
    directory.mkdir()
    source = directory / "other.md"
    source.write_text("# Other source\n\n![Local figure](figure.png)\n", encoding="utf-8")
    fallback = project.directory / "figure.png"
    fallback_bytes = _png_bytes((255, 0, 0))
    fallback.write_bytes(fallback_bytes)
    first = service.convert(path="subdir/other.md")
    assert not first["cache_hit"]
    assert _embedded_image(first["output"]) == fallback_bytes
    assert service.convert(path="subdir/other.md")["cache_hit"]

    # Missing candidates must participate in the fingerprint: creating this
    # higher-priority source-local file changes which image Pandoc selects.
    preferred = directory / "figure.png"
    preferred_bytes = _png_bytes((0, 255, 0))
    preferred.write_bytes(preferred_bytes)
    created = service.convert(path="subdir/other.md")
    assert not created["cache_hit"]
    assert _embedded_image(created["output"]) == preferred_bytes
    assert created["output"] != first["output"]
    assert service.convert(path="subdir/other.md")["cache_hit"]

    previous = preferred.stat()
    updated_bytes = _png_bytes((0, 0, 255))
    assert len(updated_bytes) == len(preferred_bytes)
    preferred.write_bytes(updated_bytes)
    os.utime(preferred, ns=(previous.st_atime_ns, previous.st_mtime_ns))
    assert preferred.stat().st_mtime_ns == previous.st_mtime_ns
    assert preferred.stat().st_size == previous.st_size
    edited = service.convert(path="subdir/other.md")
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
