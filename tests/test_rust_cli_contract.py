"""Verify the native migration against real documents and the retained engine.

These tests invoke the Rust executable and its HTTP service directly, compare existing
snapshots without rewriting them, and exercise observable cache/recovery rules.
"""

from __future__ import annotations

import http.client
import ctypes
import json
import os
import shutil
import signal
import socket
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from snapshot_utils import assert_snapshot, canonical_html, semantic_html
from test_build_snapshots import CASES, DOCX_ONLY_CASES, ROOT, SNAPSHOT_ROOT


def _available_port() -> int:
    """Reserve an unused local port long enough to discover its number."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _stop_owned_pid(pid: int) -> None:
    """Stop only a process explicitly recorded as belonging to a test service."""
    try:
        os.kill(pid, signal.SIGTERM)
        if os.name == "nt":
            # TerminateProcess returns before exit completes; wait so the next
            # request tests a dead worker rather than racing its closing pipe.
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.WaitForSingleObject.restype = wintypes.DWORD
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle.restype = wintypes.BOOL
            handle = kernel.OpenProcess(0x00100000, False, pid)
            if handle:
                try:
                    assert kernel.WaitForSingleObject(handle, 5000) == 0, "Owned worker did not exit"
                finally:
                    kernel.CloseHandle(handle)
    except ProcessLookupError:
        pass
    except OSError as error:
        # Windows reports an already-reaped PID as ERROR_INVALID_PARAMETER.
        if os.name != "nt" or error.winerror != 87:
            raise


def _child_pids(parent_pid: int) -> list[int]:
    """Inspect the OS process boundary to identify an owned conversion child."""
    if os.name == "nt":
        from ctypes import wintypes

        class ProcessEntry(ctypes.Structure):
            """Describe the documented Windows Toolhelp32 process record."""

            _fields_ = [
                ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260),
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
        kernel.Process32FirstW.restype = wintypes.BOOL
        kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
        kernel.Process32NextW.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateToolhelp32Snapshot(2, 0)
        assert handle != ctypes.c_void_p(-1).value, ctypes.get_last_error()
        try:
            entry = ProcessEntry()
            entry.dwSize = ctypes.sizeof(entry)
            found = kernel.Process32FirstW(handle, ctypes.byref(entry))
            children = []
            while found:
                if entry.th32ParentProcessID == parent_pid:
                    children.append(entry.th32ProcessID)
                found = kernel.Process32NextW(handle, ctypes.byref(entry))
            return children
        finally:
            kernel.CloseHandle(handle)
    proc = Path(f"/proc/{parent_pid}/task/{parent_pid}/children")
    if proc.exists():
        return [int(value) for value in proc.read_text(encoding="ascii").split()]
    result = subprocess.run(["ps", "-eo", "pid=,ppid="], check=True, capture_output=True, text=True, timeout=5)
    return [int(pid) for line in result.stdout.splitlines() if len(values := line.split()) == 2
            for pid, parent in [values] if int(parent) == parent_pid]


@dataclass
class NativeProject:
    """Hold one writable manuscript copy, isolated native state, and executable."""

    directory: Path
    source: Path
    executable: Path
    environment: dict[str, str]

    def build(self, output: Path, *, server_port: int | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
        """Run the full public command with captured handles and real file output."""
        command = [str(self.executable), "build", "html", "-m", str(self.source), "-o", str(output)]
        if server_port is not None:
            command.extend(["--start-server", "--server-port", str(server_port)])
        process = subprocess.Popen(command, cwd=self.directory, env=self.environment, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, encoding="utf-8")
        try:
            stdout, stderr = process.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            # A regression can let the background server inherit captured
            # pipes. Stop owned daemons before draining them: subprocess.run's
            # automatic timeout cleanup otherwise waits forever on those pipes.
            for state in Path(self.environment["PAPPER_HOME"]).glob("projects/*/work/rust-v1/server-state.json"):
                _stop_owned_pid(json.loads(state.read_text(encoding="utf-8"))["pid"])
            process.communicate(timeout=5)
            raise
        result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        if check:
            assert result.returncode == 0, result.stdout + result.stderr
        return result

    def assert_cli_parity(self, html: str) -> None:
        """Compare a service result to fresh conversion through the public CLI."""
        output = self.directory / "cli-parity.html"
        self.build(output)
        assert semantic_html(html) == canonical_html(output)


class NativeService:
    """Own one real Rust HTTP process and its project-bound conversion pipeline."""

    def __init__(self, project: NativeProject, *, custom_defaults: bool = False):
        """Prepare an isolated service config and wait for its real health response."""
        self.project = project
        self.port = _available_port()
        self.defaults = ROOT / "pandoc/pandoc-html.yml"
        self.templates: Path | None = None
        if custom_defaults:
            self.templates = project.directory / "templates"
            shutil.copytree(ROOT / "pandoc/templates", self.templates)
            self.defaults = project.directory / "defaults.yml"
            text = (ROOT / "pandoc/pandoc-html.yml").read_text(encoding="utf-8")
            text = text.replace("${.}/templates/default.html", (self.templates / "default.html").as_posix())
            text = text.replace("${.}", (ROOT / "pandoc").as_posix())
            self.defaults.write_text(text, encoding="utf-8")
        config = {
            "runtime_version": "0.9.2-rust-v1", "source_text_protocol": 1,
            "project_dir": str(project.directory), "resource_root": str(ROOT),
            "resource_paths": [str(project.directory), str(ROOT)],
            "pandoc_args": ["--defaults", str(self.defaults), "--resource-path",
                            os.pathsep.join((str(project.directory), str(ROOT)))],
            "metadata_sources": {"style_file": "style.yml", "project_name": project.source.stem},
        }
        self.config_path = project.directory / "rust-server-config.json"
        self.config_path.write_text(json.dumps(config), encoding="utf-8")
        self.log_path = project.directory / "rust-server.log"
        self.log = self.log_path.open("wb")
        self.process = subprocess.Popen(
            [str(project.executable), "__server", "--config", str(self.config_path), "--host", "127.0.0.1", "--port", str(self.port)],
            cwd=project.directory, env=project.environment, stdin=subprocess.DEVNULL, stdout=self.log, stderr=self.log,
        )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                self.log.close()
                pytest.fail(self.log_path.read_text(encoding="utf-8", errors="replace"))
            try:
                status, body, _ = self.request("GET", "/version")
                if status == 200 and json.loads(body)["runtime"] == "rust":
                    return
            except (OSError, http.client.HTTPException):
                pass
            time.sleep(0.02)
        self.close()
        pytest.fail("Native HTTP service did not become ready\n" + self.log_path.read_text(encoding="utf-8", errors="replace"))

    def request(self, method: str, endpoint: str, payload: Any = None) -> tuple[int, bytes, dict[str, str]]:
        """Send one independent HTTP request without proxies or Python TLS startup."""
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            body = None if payload is None else json.dumps(payload).encode("utf-8")
            connection.request(method, endpoint, body=body, headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            connection.close()

    def convert(self, *, text: str | None = None, path: str | None = None, mode: str = "exact") -> dict[str, Any]:
        """Convert disk or unsaved text and retain the real cache/timing response."""
        payload: dict[str, Any] = {"path": path or self.project.source.name, "mode": mode}
        if text is not None:
            payload["text"] = text
        status, body, _ = self.request("POST", "/convert", payload)
        assert status == 200, body.decode("utf-8", errors="replace")
        return json.loads(body)

    def close(self) -> None:
        """Stop the owned process tree even when a conversion assertion fails."""
        if self.process.poll() is None:
            _stop_owned_pid(self.process.pid)
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.log.close()


@pytest.fixture
def native_project_factory(tmp_path: Path, rust_executable: Path):
    """Copy fixtures so builds and deliberately changed dependencies never touch sources."""
    def make(case_name: str = "references") -> NativeProject:
        """Create a writable fixture with a private Papper managed-state directory."""
        original, filename = CASES[case_name]
        directory = tmp_path / case_name
        shutil.copytree(original, directory)
        environment = {**os.environ, "PAPPER_HOME": str(tmp_path / "state"), "PAPPER_RESOURCE_ROOT": str(ROOT)}
        return NativeProject(directory, directory / filename, rust_executable, environment)

    return make


@pytest.fixture
def native_service_factory(native_project_factory):
    """Create real native services and close every owned worker at test teardown."""
    services: list[NativeService] = []

    def make(case_name: str = "references", *, custom_defaults: bool = False) -> NativeService:
        """Start one isolated service using the selected manuscript fixture."""
        service = NativeService(native_project_factory(case_name), custom_defaults=custom_defaults)
        services.append(service)
        return service

    yield make
    for service in reversed(services):
        service.close()


@pytest.mark.parametrize("case_name", [name for name in CASES if name not in DOCX_ONLY_CASES])
def test_native_html_cli_and_service_match_existing_snapshots(native_service_factory, case_name: str) -> None:
    """Preserve all existing HTML documents through native CLI, server and cache reuse."""
    service = native_service_factory(case_name)
    output = service.project.directory / "native.html"
    service.project.build(output)
    expected = SNAPSHOT_ROOT / case_name / "html.snap"
    assert_snapshot(canonical_html(output), expected, update=False)
    first = service.convert()
    assert_snapshot(semantic_html(first["output"]), expected, update=False)
    second = service.convert()
    assert second["cache_hit"] and second["output"] == first["output"]


def test_native_citation_cache_reuses_prose_and_invalidates_citations_and_bibliography(native_service_factory) -> None:
    """Keep new prose while rejecting cached citation output after cited inputs change."""
    service = native_service_factory()
    source = service.project.source
    original = source.read_text(encoding="utf-8")
    first = service.convert()
    source.write_text(original + "\nFresh native prose.\n", encoding="utf-8")
    edited = service.convert()
    assert not edited["cache_hit"] and edited["citeproc_cache_hit"]
    assert "Fresh native prose." in edited["output"] and edited["output"] != first["output"]
    service.project.assert_cli_parity(edited["output"])
    source.write_text(original.replace("[@alpha2020]", "[@gamma2022, p. 42]"), encoding="utf-8")
    citations = service.convert()
    assert not citations["cache_hit"] and not citations["citeproc_cache_hit"]
    service.project.assert_cli_parity(citations["output"])
    bibliography = source.with_name("references.bib")
    bibliography.write_text(bibliography.read_text(encoding="utf-8").replace("A Comparative Evaluation", "Updated Bibliography Title"), encoding="utf-8")
    updated = service.convert()
    assert not updated["cache_hit"] and not updated["citeproc_cache_hit"]
    assert "updated bibliography title" in updated["output"].lower()
    service.project.assert_cli_parity(updated["output"])


def test_native_table_autofit_setting_changes_inside_warm_service(native_service_factory) -> None:
    """Refresh top-level table settings without leaking a previous request's Lua environment."""
    from lxml import html

    service = native_service_factory()
    service.project.source.write_text("| Authored | Value |\n|---|---|\n| Sample | 1 |\n", encoding="utf-8")
    style = service.project.directory / "style.yml"
    for mode in ["window", "content", "none", "window"]:
        style.write_text(f"tableAutofit: {mode}\n", encoding="utf-8")
        result = service.convert()
        document = html.fromstring(result["output"])
        table = document.xpath("//table[.//th='Authored']")[0]
        assert table.get("data-autofit") == (None if mode == "none" else mode)
        repeated = service.convert()
        assert repeated["cache_hit"] and repeated["output"] == result["output"]
        service.project.assert_cli_parity(result["output"])


def test_native_style_and_csl_dependencies_refresh_inside_warm_service(native_service_factory) -> None:
    """Discover later-created styles and invalidate the native CSL cache after edits."""
    service = native_service_factory()
    original = service.convert()
    csl = service.project.directory / "custom.csl"
    csl.write_bytes((ROOT / "pandoc/csl/elsevier-vancouver.csl").read_bytes())
    (service.project.directory / "style.yml").write_text("pandocMetadata:\n  csl: custom.csl\n  title: Native style override\n", encoding="utf-8")
    styled = service.convert()
    assert not styled["cache_hit"] and styled["output"] != original["output"]
    assert "Native style override" in styled["output"]
    service.project.assert_cli_parity(styled["output"])
    csl.write_text(csl.read_text(encoding="utf-8").replace('prefix="[" suffix="]"', 'prefix="(" suffix=")"'), encoding="utf-8")
    changed = service.convert()
    assert not changed["cache_hit"] and not changed["citeproc_cache_hit"]
    assert changed["output"] != styled["output"]
    service.project.assert_cli_parity(changed["output"])


def test_native_template_partials_and_defaults_invalidate_cached_output(native_service_factory) -> None:
    """Reload template partials and defaults without restarting the public HTTP process."""
    service = native_service_factory(custom_defaults=True)
    service.convert()
    assert service.templates is not None
    partial = service.templates / "styles.html"
    partial.write_text(partial.read_text(encoding="utf-8") + "\n/* Native partial update */\n", encoding="utf-8")
    updated = service.convert()
    assert not updated["cache_hit"] and "Native partial update" in updated["output"]
    service.defaults.write_text(service.defaults.read_text(encoding="utf-8") + "\nnumber-sections: true\n", encoding="utf-8")
    numbered = service.convert()
    assert not numbered["cache_hit"] and 'class="header-section-number"' in numbered["output"]


def test_native_unsaved_text_preserves_disk_and_accepts_external_sources(native_service_factory) -> None:
    """Render disk and editor snapshots outside the project without modifying originals."""
    service = native_service_factory()
    original = service.project.source.read_bytes()
    source_text = original.decode("utf-8") + "\nUnsaved native editor text.\n"
    result = service.convert(text=source_text)
    assert "Unsaved native editor text." in result["output"]
    assert service.project.source.read_bytes() == original
    repeated = service.convert(text=source_text)
    assert repeated["cache_hit"] and repeated["output"] == result["output"]
    disk = service.convert()
    assert "Unsaved native editor text." not in disk["output"]
    outside = service.project.directory.parent / "outside.md"
    outside.write_text("External saved manuscript", encoding="utf-8")
    assert "External saved manuscript" in service.convert(path="../outside.md")["output"]
    external = service.convert(path=str(outside), text="External unsaved manuscript")
    assert "External unsaved manuscript" in external["output"]
    assert "External saved manuscript" not in external["output"]
    assert outside.read_text(encoding="utf-8") == "External saved manuscript"
    repeated = service.convert(path=str(outside), text="External unsaved manuscript")
    assert repeated["cache_hit"] and repeated["output"] == external["output"]
    missing = outside.with_name("not-saved-yet.md")
    assert "External new buffer" in service.convert(path=str(missing), text="External new buffer")["output"]
    assert not missing.exists()


def test_native_failed_build_preserves_existing_output_and_recovers(native_service_factory) -> None:
    """Protect old output on conversion failure and recover after a missing bibliography returns."""
    service = native_service_factory()
    service.convert()
    destination = service.project.directory / "existing.html"
    destination.write_bytes(b"Previous successful output\n")
    bibliography = service.project.directory / "references.bib"
    saved = bibliography.read_bytes()
    bibliography.unlink()
    failed = service.project.build(destination, server_port=service.port, check=False)
    assert failed.returncode != 0
    assert destination.read_bytes() == b"Previous successful output\n"
    bibliography.write_bytes(saved)
    service.project.build(destination, server_port=service.port)
    service.project.assert_cli_parity(destination.read_text(encoding="utf-8"))


def test_native_concurrent_requests_keep_complete_documents(native_service_factory) -> None:
    """Serialize shared worker conversions and return identical complete HTML to clients."""
    service = native_service_factory()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: service.convert(), range(4)))
    assert len({result["output"] for result in results}) == 1
    assert sum(result["cache_hit"] for result in results) == 3
    service.project.assert_cli_parity(results[0]["output"])


def test_native_worker_crash_recovers_on_next_conversion(native_service_factory) -> None:
    """Restart the real killed conversion process without losing fresh manuscript edits."""
    service = native_service_factory()
    service.convert()
    children = _child_pids(service.process.pid)
    assert children, "The ready native service must own a conversion worker"
    for pid in children:
        _stop_owned_pid(pid)
    source = service.project.source
    source.write_text(source.read_text(encoding="utf-8") + "\nRecovered native worker.\n", encoding="utf-8")
    recovered = service.convert()
    assert not recovered["cache_hit"] and "Recovered native worker." in recovered["output"]
    service.project.assert_cli_parity(recovered["output"])


def test_native_cli_rejects_another_project_service_and_preserves_output(native_service_factory, tmp_path: Path) -> None:
    """Reject a service bound to another manuscript root before publishing any output."""
    service = native_service_factory()
    other = tmp_path / "another-project"
    other.mkdir()
    source = other / "manuscript.md"
    source.write_text("# Another manuscript\n\nDifferent project.\n", encoding="utf-8")
    project = NativeProject(other, source, service.project.executable, service.project.environment)
    output = other / "existing.html"
    output.write_bytes(b"Existing other-project output")
    failed = project.build(output, server_port=service.port, check=False)
    assert failed.returncode != 0
    assert "project" in failed.stderr.lower()
    assert output.read_bytes() == b"Existing other-project output"


def test_native_captured_cli_reuses_one_background_service(native_project_factory) -> None:
    """Let captured invocations finish and reuse the owned service after text edits."""
    project = native_project_factory()
    output = project.directory / "native.html"
    port = _available_port()
    pids: set[int] = set()
    try:
        for scenario in ("first", "edit", "unchanged"):
            if scenario == "edit":
                project.source.write_text(project.source.read_text(encoding="utf-8") + "\nCaptured native CLI prose.\n", encoding="utf-8")
            project.build(output, server_port=port)
            states = list(Path(project.environment["PAPPER_HOME"]).glob("projects/*/work/rust-v1/server-state.json"))
            assert states, "A detached service must leave owned process state for cleanup"
            pids.add(json.loads(states[0].read_text(encoding="utf-8"))["pid"])
            assert len(pids) == 1
        assert b"Captured native CLI prose." in output.read_bytes()
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            connection.request("GET", "/metrics")
            metrics = json.loads(connection.getresponse().read())
        finally:
            connection.close()
        assert metrics["requests"] == 3 and metrics["html_hits"] == 1 and metrics["citeproc_hits"] == 1
        project.assert_cli_parity(output.read_text(encoding="utf-8"))
    finally:
        for state in Path(project.environment["PAPPER_HOME"]).glob("projects/*/work/rust-v1/server-state.json"):
            pids.add(json.loads(state.read_text(encoding="utf-8"))["pid"])
        for pid in pids:
            _stop_owned_pid(pid)
