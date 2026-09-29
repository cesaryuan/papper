from pathlib import Path
import io
import shutil
import sys
import zipfile
import tarfile
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from contextlib import nullcontext

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pandoc_manuscript.commands.setup import pandoc_tools as tools
from pandoc_manuscript.runtime.paths import PMT_TOOLS_BIN_DIR


def test_pandoc_asset_selection_uses_platform_preferences(monkeypatch) -> None:
    """Select the platform-specific Pandoc archive from GitHub release metadata."""
    monkeypatch.setattr(tools, "current_platform_key", lambda: ("windows", "x64"))
    release = {
        "assets": [
            {"name": "pandoc-3.10-linux-amd64.tar.gz", "browser_download_url": "linux"},
            {"name": "pandoc-3.10-windows-x86_64.zip", "browser_download_url": "windows"},
        ]
    }

    assert tools.select_release_asset("pandoc", release) == {
        "name": "pandoc-3.10-windows-x86_64.zip",
        "url": "windows",
    }


def test_crossref_asset_selection_uses_platform_preferences(monkeypatch) -> None:
    """Select the platform-specific pandoc-crossref archive."""
    monkeypatch.setattr(tools, "current_platform_key", lambda: ("linux", "arm64"))
    release = {
        "assets": [
            {"name": "pandoc-crossref-Linux-X64.tar.xz", "browser_download_url": "x64"},
            {"name": "pandoc-crossref-Linux-ARM64.tar.xz", "browser_download_url": "arm64"},
        ]
    }

    assert tools.select_release_asset("pandoc-crossref", release) == {
        "name": "pandoc-crossref-Linux-ARM64.tar.xz",
        "url": "arm64",
    }


def test_pandoc_tools_env_prepends_managed_bin(monkeypatch, tmp_path) -> None:
    """Expose managed tools and the active Windows Python to Pandoc."""
    monkeypatch.chdir(tmp_path)

    env = tools.pandoc_tools_env({"PATH": "base"})

    path_entries = env["PATH"].split(tools.os.pathsep)
    if tools.os.name == "nt":
        assert path_entries[0] == str(Path(tools.sys.executable).parent)
        assert path_entries[1] == str(PMT_TOOLS_BIN_DIR.resolve())
    else:
        assert path_entries[0] == str(PMT_TOOLS_BIN_DIR.resolve())
    assert env["PATH"].endswith("base")


def test_resolve_tool_prefers_system_path(monkeypatch, tmp_path) -> None:
    """Use an existing system command instead of downloading a managed copy."""
    tools.TOOL_CACHE.clear()
    fake_executable = tmp_path / ("pandoc.exe" if tools.os.name == "nt" else "pandoc")
    fake_executable.write_text("fake", encoding="utf-8")

    monkeypatch.setattr(shutil, "which", lambda command: str(fake_executable) if command == "pandoc" else None)
    monkeypatch.setattr(tools, "subprocess_run_version", lambda executable: "pandoc 3.11")
    monkeypatch.setattr(
        tools,
        "install_release_tool",
        lambda command: (_ for _ in ()).throw(AssertionError("download should not run")),
    )

    resolved = tools.resolve_tool("pandoc")

    assert resolved.executable == fake_executable
    assert resolved.source == "PATH"
    tools.TOOL_CACHE.clear()


def test_crossref_pandoc_version_parses_reported_build_version(monkeypatch, tmp_path) -> None:
    """Read the Pandoc ABI version reported by pandoc-crossref --version."""
    crossref = tmp_path / "pandoc-crossref.exe"
    crossref.write_text("fake", encoding="utf-8")
    monkeypatch.setattr(
        tools,
        "subprocess_run_version",
        lambda executable: (
            "pandoc-crossref v0.3.24 built with Pandoc v3.9.0.2, "
            "pandoc-types v1.23.1.1 and GHC 9.8.4"
        ),
    )

    assert tools.crossref_pandoc_version(crossref) == "3.9.0.2"


def test_download_proxy_prefers_https_proxy_env(monkeypatch) -> None:
    """Use HTTPS_PROXY before consulting the operating system proxy."""
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
    monkeypatch.setattr(
        tools,
        "system_https_proxy",
        lambda: (_ for _ in ()).throw(AssertionError("system proxy should not be read")),
    )

    proxy = tools.download_proxy_config()

    assert proxy.proxy == "http://127.0.0.1:7890"
    assert proxy.source == "HTTPS_PROXY"


def test_download_proxy_uses_system_proxy_when_env_missing(monkeypatch) -> None:
    """Fall back to the OS proxy when HTTPS_PROXY is not set."""
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.setattr(tools, "system_https_proxy", lambda: "http://127.0.0.1:7891")

    proxy = tools.download_proxy_config()

    assert proxy.proxy == "http://127.0.0.1:7891"
    assert proxy.source == "system proxy"


def test_download_proxy_direct_when_no_proxy(monkeypatch) -> None:
    """Use direct downloads when neither environment nor system proxy exists."""
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.setattr(tools, "system_https_proxy", lambda: None)

    proxy = tools.download_proxy_config()

    assert proxy.proxy is None
    assert proxy.source == "direct"


@pytest.mark.parametrize("url", [
    "https://api.github.com/repos/jgm/pandoc/releases/latest",
    "https://github.com/jgm/pandoc/releases/download/3.11/pandoc.zip",
])
def test_download_uses_detected_windows_system_proxy(monkeypatch, url) -> None:
    """Route real HTTPS CONNECT requests through a detected proxy without internet access."""
    destinations = []

    class ProxyHandler(BaseHTTPRequestHandler):
        """Record proxy requests and stop before connecting to the public internet."""

        def do_CONNECT(self):
            """Confirm the selected proxy received the HTTPS tunnel request."""
            destinations.append(self.path)
            self.send_error(502, "Test proxy stops here")

        def log_message(self, format, *args):
            """Suppress expected local proxy error logs."""
            pass

    for key in ("HTTPS_PROXY", "https_proxy", "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(tools.sys, "platform", "win32")
    with ThreadingHTTPServer(("127.0.0.1", 0), ProxyHandler) as proxy:
        address = f"http://127.0.0.1:{proxy.server_port}"
        monkeypatch.setattr(tools.urllib.request, "getproxies_registry", lambda: {"https": address}, raising=False)
        worker = threading.Thread(target=proxy.serve_forever, daemon=True)
        worker.start()
        try:
            with pytest.raises(urllib.error.URLError, match="502"):
                tools.open_download_url(tools.urllib.request.Request(url), timeout=5)
        finally:
            proxy.shutdown()
            worker.join(timeout=5)
    assert destinations == [tools.urllib.parse.urlsplit(url).hostname + ":443"]


def test_progress_line_shows_percentage_for_known_size() -> None:
    """Render a determinate progress line when Content-Length is known."""
    line = tools.progress_line(512, 1024)

    assert "50.0%" in line
    assert "512 B/1.0 KiB" in line
    assert "\r" not in line


def test_write_progress_clears_previous_longer_line(monkeypatch) -> None:
    """Pad shorter progress updates so stale terminal characters disappear."""
    output = io.StringIO()
    long_line = tools.progress_line(10 * 1024 * 1024, 10 * 1024 * 1024)
    short_line = tools.progress_line(1, None)
    padding = " " * (len(long_line) - len(short_line))

    monkeypatch.setattr(tools.sys, "stdout", output)
    monkeypatch.setattr(tools, "should_log", lambda level: True)
    monkeypatch.setattr(tools, "PROGRESS_LINE_LENGTH", 0)

    tools.write_progress(10 * 1024 * 1024, 10 * 1024 * 1024)
    tools.write_progress(1, None, final=True)

    assert "\r" + short_line + padding + "\n" in output.getvalue()
    assert tools.PROGRESS_LINE_LENGTH == 0


class FakeDownloadResponse:
    """Small response double for streamed download progress tests."""

    def __init__(self, chunks: list[bytes], content_length: int | None = None) -> None:
        self.chunks = chunks
        self.headers = {}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)

    def read(self, size: int) -> bytes:
        """Return the next fake network chunk."""
        if not self.chunks:
            return b""
        return self.chunks.pop(0)


def test_copy_response_with_progress_streams_body_and_finishes(monkeypatch) -> None:
    """Copy downloaded bytes in chunks and emit a final progress update."""
    response = FakeDownloadResponse([b"abc", b"def"], content_length=6)
    output = io.BytesIO()
    updates = []

    def fake_write_progress(downloaded, total, *, final=False):
        """Record progress updates without writing to the test terminal."""
        updates.append((downloaded, total, final))

    monkeypatch.setattr(tools, "write_progress", fake_write_progress)

    tools.copy_response_with_progress(response, output)

    assert output.getvalue() == b"abcdef"
    assert updates == [(3, 6, False), (6, 6, False), (6, 6, True)]


@pytest.mark.parametrize("version,accepted", [("2.19", False), ("3.10.1", False), ("3.11", True), ("3.11.1", True)])
def test_resolve_pandoc_enforces_minimum_version(monkeypatch, tmp_path, version, accepted) -> None:
    """Use supported PATH versions and replace older ones with a managed install."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tools, "TOOL_CACHE", {})
    system = tmp_path / "system-pandoc.exe"
    monkeypatch.setattr(tools.shutil, "which", lambda name: str(system))
    monkeypatch.setattr(tools, "subprocess_run_version", lambda path: f"pandoc {version}")
    managed = tools.ResolvedTool("pandoc", tmp_path / "managed-pandoc.exe", "managed")
    monkeypatch.setattr(tools, "install_release_tool", lambda *args, **kwargs: managed)
    resolved = tools.resolve_pandoc()
    assert resolved.executable == (system if accepted else managed.executable)


def test_old_crossref_does_not_pin_unsupported_pandoc(monkeypatch) -> None:
    """An older crossref ABI must not cause installation of Pandoc below 3.11."""
    monkeypatch.setattr(tools, "release_by_tag", lambda *args: pytest.fail("unsupported release requested"))
    assert tools.pandoc_release_for_crossref("3.7.0.2") is None


def test_old_path_pandoc_starts_managed_setup(monkeypatch, tmp_path) -> None:
    """Automatically run the setup flow when PATH Pandoc is below 3.11."""
    tools.TOOL_CACHE.clear()
    executable = tmp_path / "pandoc.exe"
    executable.write_text("fake", encoding="utf-8")
    monkeypatch.setattr(tools.shutil, "which", lambda command: str(executable))
    monkeypatch.setattr(tools, "subprocess_run_version", lambda path: "pandoc 3.10.2")
    expected = (
        tools.ResolvedTool("pandoc", tmp_path / "managed-pandoc.exe", "~/.papper/tools"),
        tools.ResolvedTool("pandoc-crossref", tmp_path / "managed-crossref.exe", "~/.papper/tools"),
    )
    monkeypatch.setattr(tools, "setup_pandoc_tools", lambda: expected)

    assert tools.ensure_pandoc_tools() == expected


@pytest.mark.parametrize("failure", [KeyboardInterrupt, OSError])
def test_interrupted_download_can_be_retried(monkeypatch, tmp_path, failure) -> None:
    """Never reuse partial bytes after interruption or a network read failure."""
    target = tmp_path / "release.zip"

    class InterruptedResponse(FakeDownloadResponse):
        """Fail after writing one chunk to emulate an interrupted transfer."""

        def read(self, size):
            """Return the initial bytes, then interrupt the download."""
            if not self.chunks:
                raise failure()
            return super().read(size)

    monkeypatch.setattr(tools, "open_download_url", lambda *args, **kwargs: nullcontext(InterruptedResponse([b"partial"])))
    with pytest.raises(failure):
        tools.download_asset("https://example.test/release.zip", target)
    assert not target.exists()
    assert not list(tmp_path.glob("*.part"))
    monkeypatch.setattr(tools, "open_download_url", lambda *args, **kwargs: nullcontext(FakeDownloadResponse([b"complete"], 8)))
    tools.download_asset("https://example.test/release.zip", target)
    assert target.read_bytes() == b"complete"


def test_short_download_preserves_existing_archive(monkeypatch, tmp_path) -> None:
    """A forced refresh must retain the old archive until all new bytes arrive."""
    target = tmp_path / "release.zip"
    target.write_bytes(b"previous")
    monkeypatch.setattr(tools, "open_download_url", lambda *args, **kwargs: nullcontext(FakeDownloadResponse([b"short"], 100)))
    with pytest.raises(RuntimeError, match="Incomplete download"):
        tools.download_asset("https://example.test/release.zip", target, force=True)
    assert target.read_bytes() == b"previous"


@pytest.mark.parametrize("suffix", [".zip", ".tar.gz", ".tar.xz", ".7z"])
def test_install_repairs_legacy_partial_archive(monkeypatch, tmp_path, suffix) -> None:
    """Recover a cached truncated archive by redownloading and extracting afresh."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tools, "PMT_TOOLS_DOWNLOAD_DIR", tmp_path / "downloads")
    monkeypatch.setattr(tools, "PMT_TOOLS_BIN_DIR", tmp_path / "bin")
    archive_name = "release" + suffix
    good = tmp_path / archive_name
    executable = tmp_path / tools.executable_name("pandoc")
    executable.write_bytes(b"complete executable")
    if suffix == ".zip":
        with zipfile.ZipFile(good, "w") as archive:
            archive.write(executable, executable.name)
    elif suffix == ".7z":
        import py7zr
        with py7zr.SevenZipFile(good, "w") as archive:
            archive.write(executable, executable.name)
    else:
        with tarfile.open(good, "w:gz" if suffix == ".tar.gz" else "w:xz") as archive:
            archive.add(executable, executable.name)
    payload = good.read_bytes()
    cached = tools.PMT_TOOLS_DOWNLOAD_DIR / archive_name
    cached.parent.mkdir(parents=True)
    cached.write_bytes(payload[:10])
    monkeypatch.setattr(tools, "select_release_asset", lambda *args: {"name": archive_name, "url": "https://example.test/release"})
    monkeypatch.setattr(tools, "subprocess_run_version", lambda path: "pandoc 3.11")
    downloads = []

    def response(*args, **kwargs):
        """Supply one complete replacement archive and record network requests."""
        downloads.append(1)
        return nullcontext(FakeDownloadResponse([payload], len(payload)))

    monkeypatch.setattr(tools, "open_download_url", response)
    result = tools.install_release_tool("pandoc", {"tag_name": "3.11"})
    assert result.executable.read_bytes() == executable.read_bytes()
    assert downloads == [1]


def test_corrupt_managed_executable_is_reinstalled(monkeypatch, tmp_path) -> None:
    """A nonempty but broken executable from an old interrupted install is replaced."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tools, "PMT_TOOLS_BIN_DIR", tmp_path / "bin")
    monkeypatch.setattr(tools, "TOOL_CACHE", {})
    monkeypatch.setattr(tools.shutil, "which", lambda name: None)
    managed = tools.managed_executable("pandoc-crossref")
    managed.parent.mkdir(parents=True)
    managed.write_bytes(b"partial executable")

    def broken_version(path):
        """Emulate Windows rejecting a truncated executable image."""
        raise OSError("not a valid executable")

    def install(tool, **kwargs):
        """Replace the broken image and return the repaired tool."""
        managed.write_bytes(b"repaired")
        return tools.ResolvedTool(tool, managed, "managed")

    monkeypatch.setattr(tools, "subprocess_run_version", broken_version)
    monkeypatch.setattr(tools, "install_release_tool", install)
    assert tools.resolve_tool("pandoc-crossref").executable.read_bytes() == b"repaired"


def test_interrupted_install_preserves_existing_executable(monkeypatch, tmp_path) -> None:
    """Interrupt the executable copy, then successfully rerun from the cached archive."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tools, "PMT_TOOLS_DOWNLOAD_DIR", tmp_path / "downloads")
    monkeypatch.setattr(tools, "PMT_TOOLS_BIN_DIR", tmp_path / "bin")
    cached = tools.PMT_TOOLS_DOWNLOAD_DIR / "release.zip"
    cached.parent.mkdir(parents=True)
    with zipfile.ZipFile(cached, "w") as archive:
        archive.writestr(tools.executable_name("pandoc"), b"new executable")
    installed = tools.managed_executable("pandoc")
    installed.parent.mkdir(parents=True)
    installed.write_bytes(b"old executable")
    monkeypatch.setattr(tools, "select_release_asset", lambda *args: {"name": "release.zip", "url": "https://example.test/release"})
    monkeypatch.setattr(tools, "subprocess_run_version", lambda path: "pandoc 3.11")
    original_copy = tools.shutil.copy2

    def interrupted_copy(source, target):
        """Emulate Ctrl+C after only part of the executable has been copied."""
        target.write_bytes(b"partial executable")
        raise KeyboardInterrupt()

    monkeypatch.setattr(tools.shutil, "copy2", interrupted_copy)
    with pytest.raises(KeyboardInterrupt):
        tools.install_release_tool("pandoc", {"tag_name": "3.11"})
    assert installed.read_bytes() == b"old executable"
    assert not list(installed.parent.glob("*.part"))
    monkeypatch.setattr(tools.shutil, "copy2", original_copy)
    tools.install_release_tool("pandoc", {"tag_name": "3.11"})
    assert installed.read_bytes() == b"new executable"


def test_invalid_redownload_fails_without_poisoning_next_run(monkeypatch, tmp_path) -> None:
    """Bound archive retries and remove unusable completed downloads before returning."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tools, "PMT_TOOLS_DOWNLOAD_DIR", tmp_path / "downloads")
    monkeypatch.setattr(tools, "PMT_TOOLS_BIN_DIR", tmp_path / "bin")
    monkeypatch.setattr(tools, "select_release_asset", lambda *args: {"name": "release.zip", "url": "https://example.test/release"})
    downloads = []

    def response(*args, **kwargs):
        """Return an HTML/error-like body instead of the requested archive."""
        downloads.append(1)
        return nullcontext(FakeDownloadResponse([b"not an archive"]))

    monkeypatch.setattr(tools, "open_download_url", response)
    with pytest.raises(RuntimeError, match="Could not unpack a usable pandoc"):
        tools.install_release_tool("pandoc", {"tag_name": "3.11"})
    assert downloads == [1, 1]
    assert not (tools.PMT_TOOLS_DOWNLOAD_DIR / "release.zip").exists()
    assert not tools.managed_executable("pandoc").exists()


def test_setup_pandoc_tools_installs_managed_tools_even_when_path_exists(monkeypatch, tmp_path) -> None:
    """Prepare ~/.papper/tools explicitly instead of reusing system PATH tools."""
    tools.TOOL_CACHE.clear()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tools, "PMT_TOOLS_BIN_DIR", tmp_path / "bin")
    monkeypatch.setattr(shutil, "which", lambda command: f"C:/system/{command}.exe")
    monkeypatch.setattr(tools, "crossref_pandoc_version", lambda path: "3.9.0.2")
    monkeypatch.setattr(tools, "pandoc_release_for_crossref", lambda version: {"tag_name": version, "assets": []})
    calls = []

    def fake_install_release_tool(tool, release=None, *, force_download=False):
        """Create a fake managed executable without network access."""
        calls.append((tool, release, force_download))
        executable = tools.managed_executable(tool)
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.write_text("fake", encoding="utf-8")
        return tools.ResolvedTool(tool, executable, "~/.papper/tools (fake)")

    monkeypatch.setattr(tools, "install_release_tool", fake_install_release_tool)

    pandoc, crossref = tools.setup_pandoc_tools()

    assert pandoc.executable == tools.managed_executable("pandoc")
    assert crossref.executable == tools.managed_executable("pandoc-crossref")
    assert calls == [
        ("pandoc-crossref", None, False),
        ("pandoc", {"tag_name": "3.9.0.2", "assets": []}, False),
    ]
    tools.TOOL_CACHE.clear()
