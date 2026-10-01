"""Exercise real server output, cache invalidation, and recovery contracts.

These tests cover the native service boundary absent from the CLI snapshots:
fresh prose with reused citations, changed citation assets/metadata, template
partials/default reloads, global reader context, and concurrent requests.
Build scripts/pandoc-server first or set PMT_PANDOC_SERVER_WORKER_COMMAND.
"""

from __future__ import annotations

import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from pandoc_manuscript.commands import build
from pandoc_manuscript.commands import pandoc_server as client
from pandoc_manuscript.commands import pandoc_server_runtime as runtime
from pandoc_manuscript.commands.pandoc_server import PandocServerInfo, build_with_pandoc_server, _pid_is_running, _stop_pid
from pandoc_manuscript.commands.setup import pandoc_tools_env
from pandoc_manuscript.html.build import prepare_html_metadata
from pandoc_manuscript.html.postprocess import postprocess_html_text
from pandoc_manuscript.runtime import paths
from pandoc_manuscript.runtime.metadata import write_pandoc_metadata
from snapshot_utils import assert_snapshot
from test_build_snapshots import CASES, DOCX_ONLY_CASES, ROOT, SNAPSHOT_ROOT, build_case


@pytest.fixture(scope="module")
def native_command() -> str:
    """Find a real compiled worker; native output assertions never use mocks."""
    configured = os.environ.get("PMT_PANDOC_SERVER_WORKER_COMMAND")
    if configured:
        return configured
    names = ("pmt-pandoc-worker.exe", "pmt-pandoc-worker")
    for name in names:
        candidates = list((ROOT / "scripts/pandoc-server/dist-newstyle/build").glob(f"**/{name}"))
        if candidates:
            return f'"{candidates[0]}"'
        candidate = runtime.PMT_TOOLS_BIN_DIR / name
        if candidate.is_file():
            return f'"{candidate}"'
    pytest.skip("HTML server output tests require a compiled pmt-pandoc-worker")


@pytest.fixture
def server_factory(tmp_path: Path, monkeypatch, native_command: str):
    """Create isolated copies and stop all native workers when the test ends."""
    if shutil.which("pandoc") is None or shutil.which("pandoc-crossref") is None:
        pytest.skip("CLI parity checks require pandoc and pandoc-crossref")
    monkeypatch.setenv("PMT_PANDOC_SERVER_WORKER_COMMAND", native_command)
    monkeypatch.setattr(paths, "PAPPER_HOME_DIR", tmp_path / "state")
    workers = []

    def make(case_name: str = "references", *, custom_defaults: bool = False):
        """Prepare the same HTML settings as the CLI in a writable fixture copy."""
        original, filename = CASES[case_name]
        project = tmp_path / case_name
        shutil.copytree(original, project)
        source = project / filename
        monkeypatch.chdir(ROOT)
        monkeypatch.setattr(build, "SETTINGS", build.BuildSettings(manuscript_file=source.as_posix(), project_name=source.stem))
        effective = build.load_build_metadata()
        prepare_html_metadata(effective, source.stem)
        generated = tmp_path / "initial-metadata.yml"
        write_pandoc_metadata(effective.pandoc_metadata, generated)
        defaults = ROOT / "pandoc/pandoc-html.yml"
        if custom_defaults:
            template_dir = project / "templates"
            shutil.copytree(ROOT / "pandoc/templates", template_dir)
            defaults = project / "defaults.yml"
            defaults.write_text((ROOT / "pandoc/pandoc-html.yml").read_text(encoding="utf-8")
                                .replace("${.}/templates/default.html", (template_dir / "default.html").as_posix())
                                .replace("${.}", (ROOT / "pandoc").as_posix()), encoding="utf-8")
        config = {
            "project_dir": str(project), "resource_paths": [str(project), str(ROOT)],
            "pandoc_args": ["--defaults", str(defaults), "--metadata-file", str(generated),
                            "--resource-path", os.pathsep.join((str(project), str(ROOT)))],
            "pandoc_metadata": effective.pandoc_metadata,
            "metadata_sources": {"style_file": "style.yml", "project_name": source.stem},
            "worker_config": str(tmp_path / "worker.json"),
        }
        worker = runtime.PandocWorker(config, tmp_path / "worker.log")
        workers.append(worker)
        return worker, source

    yield make
    for worker in workers:
        worker.close()


def assert_cli_parity(source: Path, html: str, tmp_path: Path) -> None:
    """Compare the full processed document against a fresh public CLI build."""
    output = tmp_path / "cli.html"
    build_case(source.parent, source.name, "html", output)
    assert html.rstrip() == output.read_text(encoding="utf-8").rstrip()


@pytest.mark.skipif(os.name != "nt", reason="Windows console allocation contract")
def test_detached_server_worker_has_no_console(tmp_path: Path) -> None:
    """Keep a real worker console-free under a detached server without losing I/O."""
    probe = tmp_path / "console_probe.py"
    probe.write_text('''"""Probe worker console allocation and pipes under a detached server.

The outer process uses Papper's worker launcher; the --config child reports
its actual Windows console handle and echoes stdin, with diagnostics on stderr.
"""
import ctypes
import json
import sys
from pathlib import Path

from pandoc_manuscript.commands.pandoc_server_runtime import PandocWorker

if "--config" in sys.argv:
    kernel = ctypes.WinDLL("kernel32")
    kernel.GetConsoleWindow.restype = ctypes.c_void_p
    print(json.dumps({"console": kernel.GetConsoleWindow() or 0,
                      "input": sys.stdin.read()}), flush=True)
    print("Worker diagnostic", file=sys.stderr)
else:
    root = Path(sys.argv[1])
    process = PandocWorker._start_process(
        {"project_dir": str(root), "worker_config": str(root / "worker.json")},
        root / "worker.log",
    )
    try:
        output, _ = process.communicate("Protocol input\\n", timeout=15)
        if process.returncode:
            raise RuntimeError(f"Probe worker exited with {process.returncode}")
        print(output, end="")
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
''', encoding="utf-8")
    environment = {**os.environ, "PMT_PANDOC_SERVER_WORKER_COMMAND": f'"{sys.executable}" "{probe}"'}
    # Reproduce the console-less HTTP parent's real Windows creation mode:
    # a console worker launched without its own flags allocates a new window.
    result = subprocess.run(
        [sys.executable, str(probe), str(tmp_path)], env=environment,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        capture_output=True, text=True, check=True, timeout=25,
    )
    assert json.loads(result.stdout) == {"console": 0, "input": "Protocol input\n"}
    assert (tmp_path / "worker.log").read_text(encoding="utf-8").strip() == "Worker diagnostic"


def test_raw_editor_text_preserves_source_and_invalidates_cache(server_factory) -> None:
    """Render unsaved/empty buffers through real HTTP without touching the manuscript."""
    worker, source = server_factory("references")
    saved = source.read_bytes()
    server = ThreadingHTTPServer(("127.0.0.1", 0), runtime.PmtHtmlRequestHandler)
    server.daemon_threads = True
    server.worker = worker
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=30)

    def convert(text: str | None) -> tuple[int, str, str | None]:
        """Send one actual editor snapshot and consume the complete response."""
        payload = {"path": str(source)}
        if text is not None:
            payload["text"] = text
        connection.request("POST", "/convert/raw", body=json.dumps(payload),
                           headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        return response.status, response.read().decode("utf-8"), response.getheader("X-PMT-Cache")

    try:
        first = saved.decode("utf-8-sig") + "\n\nUnsaved editor paragraph alpha.\n"
        status, html, cache = convert(first)
        assert status == 200 and "Unsaved editor paragraph alpha." in html
        assert cache == "miss"
        assert convert(first) == (200, html, "hit")
        second = first.replace("paragraph alpha", "paragraph beta")
        status, changed, cache = convert(second)
        assert status == 200 and "Unsaved editor paragraph beta." in changed
        assert "Unsaved editor paragraph alpha." not in changed and cache == "miss"
        status, empty, _ = convert("")
        assert status == 200 and "Unsaved editor paragraph" not in empty
        assert "<p>" not in empty
        status, disk, _ = convert(None)
        assert status == 200 and "Unsaved editor paragraph" not in disk
        assert source.read_bytes() == saved
        status, error, _ = convert(123)
        assert status == 400 and "Source text must be a string" in error
        assert convert(second)[1] == changed
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_removed_preview_routes_return_not_found() -> None:
    """Reject removed routes and keep their consumed bodies off the next request."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), runtime.PmtHtmlRequestHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        for route in ("/preview", "/preview/raw"):
            connection.request("POST", route, body=json.dumps({"path": "manuscript.md"}),
                               headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            assert response.status == 404
            assert json.loads(response.read()) == {"error": "Not found"}
        connection.request("GET", "/metrics")
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read()) == {}
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.mark.parametrize("failure, expected", [
    ("exit", "exit code 7"),
    pytest.param("signal", "exit code -11 (SIGSEGV)",
                 marks=pytest.mark.skipif(os.name == "nt", reason="POSIX signal exit codes")),
])
def test_worker_failure_reports_cause_and_preserves_output(tmp_path: Path, monkeypatch,
                                                         failure: str, expected: str) -> None:
    """Expose real native-process failures to HTTP clients without losing old HTML."""
    probe = tmp_path / "failed_worker.py"
    probe.write_text(
        '"""Consume a conversion request and reproduce an external worker crash."""\n'
        'import os\nimport signal\nimport sys\n'
        'sys.stdin.readline()\n'
        'print("Native worker diagnostic", file=sys.stderr, flush=True)\n'
        + ('os.kill(os.getpid(), signal.SIGSEGV)\n' if failure == "signal" else 'sys.exit(7)\n'),
        encoding="utf-8",
    )
    monkeypatch.setenv("PMT_PANDOC_SERVER_WORKER_COMMAND", f'"{sys.executable}" "{probe}"')
    monkeypatch.setattr(paths, "PAPPER_HOME_DIR", tmp_path / "state")
    source = tmp_path / "manuscript.md"
    source.write_text("A manuscript.\n", encoding="utf-8")
    worker = runtime.PandocWorker({
        "project_dir": str(tmp_path), "pandoc_args": [],
        "worker_config": str(tmp_path / "worker.json"),
    }, tmp_path / "worker.log")
    server = ThreadingHTTPServer(("127.0.0.1", 0), runtime.PmtHtmlRequestHandler)
    server.daemon_threads = True
    server.worker = worker
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    output = tmp_path / "result.html"
    saved = b"<html>Existing output</html>"
    output.write_bytes(saved)
    try:
        info = PandocServerInfo("127.0.0.1", server.server_port, worker._process.pid, [], 0)
        with pytest.raises(RuntimeError) as error:
            build_with_pandoc_server(info, source, output)
        assert expected in str(error.value)
        assert "Native worker diagnostic" in str(error.value)
        assert output.read_bytes() == saved
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        worker.close()


def test_local_html_build_without_available_tls_certificates(tmp_path: Path, monkeypatch) -> None:
    """Keep local HTTP builds usable when the system TLS store cannot load."""
    import ssl

    class LocalService(BaseHTTPRequestHandler):
        """Serve the real HTTP boundary without depending on a native worker."""

        def do_GET(self) -> None:
            """Advertise the build protocol used by the production client."""
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"protocol":"pmt-html-v1"}')

        def do_POST(self) -> None:
            """Return document content after consuming the actual request."""
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<html><body>Local build output</body></html>")

        def log_message(self, format: str, *args) -> None:
            """Keep successful test requests out of stderr."""

    def unavailable_certificates(*args, **kwargs):
        """Simulate a failed OS certificate-store initialization."""
        raise OSError("System certificate store unavailable")

    monkeypatch.setattr(ssl, "_create_default_https_context", unavailable_certificates)
    # Start with a fresh client so a previous test cannot conceal TLS loading.
    client._server_opener.cache_clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), LocalService)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        info = PandocServerInfo("127.0.0.1", server.server_port, 0, [], 0)
        assert client._request_version(info.host, info.port)
        output = tmp_path / "result.html"
        assert build_with_pandoc_server(info, tmp_path / "source.md", output)
        assert output.read_text(encoding="utf-8") == "<html><body>Local build output</body></html>"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        client._server_opener.cache_clear()


@pytest.mark.parametrize("case_name", [name for name in CASES if name not in DOCX_ONLY_CASES])
def test_exact_server_matches_existing_html_snapshot(server_factory, case_name: str) -> None:
    """Preserve complete HTML for citations, crossrefs, metadata, styles, and Chinese."""
    worker, source = server_factory(case_name)
    result = worker.convert(source)
    assert_snapshot(result.html.rstrip() + "\n", SNAPSHOT_ROOT / case_name / "html.snap", update=False)
    repeated = worker.convert(source)
    assert repeated.cache_hit and repeated.html == result.html


def test_citation_reuse_keeps_fresh_prose_and_invalidates_changed_inputs(server_factory, tmp_path: Path) -> None:
    """Reuse citations on edits, then refresh changed citations, YAML, and BibTeX."""
    worker, source = server_factory()
    first = worker.convert(source)
    original = source.read_text(encoding="utf-8")
    source.write_text(original + "\nFresh prose after editing.\n", encoding="utf-8")
    edited = worker.convert(source)
    assert not edited.cache_hit and edited.citeproc_cache_hit
    assert "Fresh prose after editing." in edited.html and edited.html != first.html
    assert_cli_parity(source, edited.html, tmp_path)

    source.write_text(original.replace("[@alpha2020]", "[@gamma2022, p. 42]"), encoding="utf-8")
    citations = worker.convert(source)
    assert not citations.cache_hit and not citations.citeproc_cache_hit
    assert_cli_parity(source, citations.html, tmp_path)

    source.write_text(source.read_text(encoding="utf-8").replace("---\nbibliography:",
        "---\ntitle: Updated metadata\nauthors:\n  - name: Changed Author\nbibliography:"), encoding="utf-8")
    header = worker.convert(source)
    assert "Updated metadata" in header.html and "Changed Author" in header.html
    assert_cli_parity(source, header.html, tmp_path)

    bibliography = source.with_name("references.bib")
    bibliography.write_text(bibliography.read_text(encoding="utf-8").replace("A Comparative Evaluation", "Changed Bibliography Title"), encoding="utf-8")
    updated = worker.convert(source)
    assert not updated.cache_hit and not updated.citeproc_cache_hit
    assert "changed bibliography title" in updated.html.lower()
    assert_cli_parity(source, updated.html, tmp_path)

    saved = bibliography.read_bytes()
    bibliography.unlink()
    with pytest.raises(RuntimeError):
        worker.convert(source)
    bibliography.write_bytes(saved)
    recovered = worker.convert(source)
    assert_cli_parity(source, recovered.html, tmp_path)


def test_styles_and_csl_invalidate_prepared_citations(server_factory, tmp_path: Path) -> None:
    """Apply newly created styles and reread CSL files inside a warm service."""
    worker, source = server_factory()
    initial = worker.convert(source)
    csl = source.with_name("custom.csl")
    csl.write_bytes((ROOT / "pandoc/csl/elsevier-vancouver.csl").read_bytes())
    source.with_name("style.yml").write_text("pandocMetadata:\n  csl: custom.csl\n  title: Style override\n", encoding="utf-8")
    styled = worker.convert(source)
    assert not styled.cache_hit and styled.html != initial.html
    assert "Style override" in styled.html
    assert_cli_parity(source, styled.html, tmp_path)
    csl.write_text(csl.read_text(encoding="utf-8").replace('prefix="[" suffix="]"', 'prefix="(" suffix=")"'), encoding="utf-8")
    changed = worker.convert(source)
    assert not changed.cache_hit and not changed.citeproc_cache_hit and changed.html != styled.html
    assert_cli_parity(source, changed.html, tmp_path)


def test_remote_parent_csl_is_cached_and_conditionally_refreshed(server_factory, tmp_path: Path) -> None:
    """Reuse remote parent styles and invalidate HTML after a validated update."""
    state = {"body": (ROOT / "pandoc/csl/elsevier-vancouver.csl").read_bytes(), "etag": '"first"'}

    class ParentStyleHandler(BaseHTTPRequestHandler):
        """Serve a controlled CSL parent with real HTTP conditional responses."""

        def do_GET(self) -> None:  # noqa: N802
            """Return the current style, or preserve its file version with a 304."""
            if self.headers.get("If-None-Match") == state["etag"]:
                self.send_response(304)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/xml")
            self.send_header("ETag", state["etag"])
            self.send_header("Content-Length", str(len(state["body"])))
            self.end_headers()
            self.wfile.write(state["body"])

        def log_message(self, format: str, *args: object) -> None:
            """Suppress routine local fixture requests."""
            return

    parent = ThreadingHTTPServer(("127.0.0.1", 0), ParentStyleHandler)
    thread = threading.Thread(target=parent.serve_forever, daemon=True)
    thread.start()
    try:
        worker, source = server_factory()
        child = source.with_name("dependent.csl")
        child.write_text('<style xmlns="http://purl.org/net/xbiblio/csl" version="1.0"><info>'
                         '<title>Dependent fixture</title><id>https://example.test/dependent</id>'
                         f'<link rel="independent-parent" href="http://127.0.0.1:{parent.server_port}/parent.csl"/>'
                         '</info></style>', encoding="utf-8")
        source.with_name("style.yml").write_text("pandocMetadata:\n  csl: dependent.csl\n", encoding="utf-8")
        original = worker.convert(source)
        source.write_text(source.read_text(encoding="utf-8") + "\nChanged remote-style prose.\n", encoding="utf-8")
        reused = worker.convert(source)
        assert not reused.cache_hit and reused.citeproc_cache_hit
        assert_cli_parity(source, reused.html, tmp_path)
        state["body"] = state["body"].replace(b'prefix="[" suffix="]"', b'prefix="(" suffix=")"')
        state["etag"] = '"second"'
        worker._assets.remote.validation_seconds = 0
        refreshed = worker.convert(source)
        assert not refreshed.cache_hit and not refreshed.citeproc_cache_hit
        assert refreshed.html != reused.html and refreshed.html != original.html
        assert_cli_parity(source, refreshed.html, tmp_path)
        validated = worker.convert(source)
        assert validated.cache_hit and validated.html == refreshed.html
    finally:
        parent.shutdown()
        parent.server_close()
        thread.join(timeout=3)


def test_template_partials_and_defaults_refresh_without_restarting(server_factory) -> None:
    """Observe partial edits and changed defaults rather than serving stale templates."""
    worker, source = server_factory(custom_defaults=True)
    worker.convert(source)
    partial = source.parent / "templates/styles.html"
    partial.write_text(partial.read_text(encoding="utf-8") + "\n/* Changed partial */\n", encoding="utf-8")
    changed = worker.convert(source)
    assert not changed.cache_hit and "Changed partial" in changed.html
    defaults = source.parent / "defaults.yml"
    defaults.write_text(defaults.read_text(encoding="utf-8") + "\nnumber-sections: true\n", encoding="utf-8")
    numbered = worker.convert(source)
    assert not numbered.cache_hit and 'class="header-section-number"' in numbered.html
    custom = source.parent / "normalize_chinese_numbering.lua"
    custom.write_text((ROOT / "pandoc/filters/shared/normalize_chinese_numbering.lua").read_text(encoding="utf-8")
                      + "\n-- Override the document callback for this custom filter\nfunction Pandoc(document)\n"
                        "  document.blocks:insert(pandoc.Para{pandoc.Str('Custom filter output')})\n"
                        "  return document\nend\n", encoding="utf-8")
    defaults.write_text(defaults.read_text(encoding="utf-8").replace(
        (ROOT / "pandoc/filters/shared/normalize_chinese_numbering.lua").as_posix(), custom.as_posix()), encoding="utf-8")
    custom_result = worker.convert(source)
    assert "Custom filter output" in custom_result.html
    custom.write_text(custom.read_text(encoding="utf-8").replace("Custom filter output", "Custom filter updated"), encoding="utf-8")
    updated = worker.convert(source)
    assert not updated.cache_hit and "Custom filter updated" in updated.html


def test_preview_and_note_citation_cache_preserve_global_context(server_factory, tmp_path: Path) -> None:
    """Keep note punctuation fresh and unique identifiers across repeated headings."""
    worker, source = server_factory()
    source.with_name("style.yml").write_text("pandocMetadata:\n  csl: notes.csl\n", encoding="utf-8")
    csl = source.with_name("notes.csl")
    csl.write_text((ROOT / "pandoc/csl/elsevier-vancouver.csl").read_text(encoding="utf-8")
                   .replace('class="in-text"', 'class="note"'), encoding="utf-8")
    original = source.read_text(encoding="utf-8") + "\n# Repeat\n\nCited prose [@alpha2020], more text[^n].\n\n# Repeat\n\nA [global link][target].\n\n[^n]: A note [@beta2021].\n\n[target]: https://example.test\n"
    source.write_text(original, encoding="utf-8")
    first = worker.convert(source)
    assert_cli_parity(source, first.html, tmp_path)
    source.write_text(original.replace("Cited prose", "Changed prose").replace("], more text", "]; more text"), encoding="utf-8")
    changed = worker.convert(source)
    assert changed.citeproc_cache_hit and not changed.cache_hit
    assert_cli_parity(source, changed.html, tmp_path)
    preview = worker.convert(source, mode="preview")
    assert 'id="sec:repeat"' in preview.html and 'id="sec:repeat-1"' in preview.html
    assert 'href="https://example.test"' in preview.html
    effective = build.load_build_metadata()
    prepare_html_metadata(effective, source.stem)
    output = tmp_path / "preview.html"
    fragment_defaults = tmp_path / "fragment.yml"
    # Pandoc's CLI treats an explicit template as implying standalone, so the
    # independent fragment reference must remove it as the server does.
    fragment_defaults.write_text("standalone: false\ntemplate: null\n", encoding="utf-8")
    build.run_pandoc(ROOT / "pandoc/pandoc-html.yml", output, effective, extra_args=["--defaults", str(fragment_defaults)])
    expected = postprocess_html_text(output.read_text(encoding="utf-8"), pandoc_metadata=effective.pandoc_metadata, skip_author_info=True)
    assert preview.html == expected


def test_http_concurrent_requests_and_worker_recovery(server_factory, tmp_path: Path) -> None:
    """Share exact HTML across simultaneous HTTP clients and recover a dead child."""
    worker, source = server_factory()
    server = ThreadingHTTPServer(("127.0.0.1", 0), runtime.PmtHtmlRequestHandler)
    server.daemon_threads = True
    server.worker = worker
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(path: str = source.name):
        """Send a real raw conversion and capture cache headers with its output."""
        payload = urllib.request.Request(url + "/convert/raw", data=json.dumps({"path": path}).encode(),
                                         headers={"Content-Type": "application/json"})
        with opener.open(payload, timeout=15) as response:
            return response.read().decode("utf-8"), response.headers["X-PMT-Cache"]

    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: request(), range(4)))
        assert len({html for html, _ in results}) == 1
        assert sorted(cache for _, cache in results) == ["hit", "hit", "hit", "miss"]
        assert_cli_parity(source, results[0][0], tmp_path)
        # A Windows liveness probe must query the PID, not terminate the child.
        assert _pid_is_running(worker._process.pid)
        assert request()[0] == results[0][0]
        with pytest.raises(urllib.error.HTTPError) as error:
            request("../outside.md")
        assert error.value.code == 400
        info = PandocServerInfo("127.0.0.1", server.server_port, worker._process.pid, [], 0)
        destination = tmp_path / "existing.html"
        destination.write_text("Existing output", encoding="utf-8")
        with pytest.raises(RuntimeError):
            build_with_pandoc_server(info, source.parent.parent / "outside.md", destination)
        assert destination.read_text(encoding="utf-8") == "Existing output"
        assert build_with_pandoc_server(info, source, destination)
        assert destination.read_text(encoding="utf-8") == results[0][0]
        worker._process.kill()
        worker._process.wait()
        source.write_text(source.read_text(encoding="utf-8") + "\nRecovered worker.\n", encoding="utf-8")
        recovered, cache = request()
        assert cache == "miss" and "Recovered worker." in recovered
        assert_cli_parity(source, recovered, tmp_path)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_cli_reuses_service_when_its_output_is_captured(tmp_path: Path, native_command: str) -> None:
    """Let captured CLI calls exit and reuse one service across edits and repeats."""
    if shutil.which("pandoc") is None or shutil.which("pandoc-crossref") is None:
        pytest.skip("CLI parity checks require pandoc and pandoc-crossref")
    project = tmp_path / "cli-project"
    original, filename = CASES["references"]
    shutil.copytree(original, project)
    source = project / filename
    output = project / "result.html"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    environment = {**os.environ, **pandoc_tools_env(), "PMT_PANDOC_SERVER_WORKER_COMMAND": native_command}
    command = [sys.executable, "-m", "pandoc_manuscript.cli", "build", "html", str(source), "-o", str(output)]
    state_file = paths.project_state_dir(project) / "pandoc-server.json"
    owned_pids: set[int] = set()
    try:
        for scenario in ("first", "edit", "unchanged"):
            if scenario == "edit":
                source.write_text(source.read_text(encoding="utf-8") + "\nFresh CLI prose.\n", encoding="utf-8")
            # Before the handle-inheritance fix, Windows waits indefinitely for
            # the detached service to close the captured parent's stdout pipe.
            subprocess.run([*command, "--start-server", "--server-port", str(port)], cwd=project,
                           env=environment, check=True, capture_output=True, text=True, timeout=20)
            state = json.loads(state_file.read_text(encoding="utf-8"))
            owned_pids.add(state["pid"])
            assert len(owned_pids) == 1
        html = output.read_bytes()
        assert b"Fresh CLI prose." in html
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/metrics", timeout=5) as response:
            metrics = json.load(response)
        assert metrics["requests"] == 3 and metrics["html_hits"] == 1 and metrics["citeproc_hits"] == 1
        subprocess.run(command, cwd=project, env=environment, check=True, capture_output=True, text=True, timeout=20)
        assert output.read_bytes() == html
    finally:
        # A timed-out captured caller may already have launched its service.
        if state_file.is_file():
            owned_pids.add(json.loads(state_file.read_text(encoding="utf-8"))["pid"])
        for pid in owned_pids:
            _stop_pid(pid)
