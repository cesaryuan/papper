"""Start and reuse a local Pandoc HTTP server for editor integrations."""

from __future__ import annotations

import json
import ctypes
import os
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

from .. import __version__
from ..runtime.logging import log_info, log_warning
from ..runtime.paths import project_state_dir



def server_state_file() -> Path:
    """Return the current project's persisted server connection details."""
    return project_state_dir() / "pandoc-server.json"


def server_log_file() -> Path:
    """Return the current project's server log path."""
    return project_state_dir() / "pandoc-server.log"


def server_config_file() -> Path:
    """Return the current project's server configuration path."""
    return project_state_dir() / "pandoc-server-config.json"
DEFAULT_SERVER_HOST = "127.0.0.1"
DEFAULT_SERVER_PORT = 3030


@dataclass(frozen=True)
class PandocServerInfo:
    """Persisted details needed to reuse a local Pandoc server."""

    host: str
    port: int
    pid: int
    command: list[str]
    started_at: float
    config_path: str | None = None
    config_mtime_ns: int | None = None

    @property
    def base_url(self) -> str:
        """Return the local HTTP base URL used by editor clients."""
        return f"http://{self.host}:{self.port}"


def _read_state() -> PandocServerInfo | None:
    """Read a valid server state file, discarding malformed stale state."""
    try:
        raw = json.loads(server_state_file().read_text(encoding="utf-8"))
        return PandocServerInfo(
            host=str(raw["host"]),
            port=int(raw["port"]),
            pid=int(raw["pid"]),
            command=[str(item) for item in raw["command"]],
            started_at=float(raw["started_at"]),
            config_path=str(raw["config_path"]) if raw.get("config_path") else None,
            config_mtime_ns=int(raw["config_mtime_ns"]) if raw.get("config_mtime_ns") else None,
        )
    except (FileNotFoundError, OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _write_state(info: PandocServerInfo) -> None:
    """Write server state atomically enough for a single local project."""
    target = server_state_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(asdict(info), indent=2), encoding="utf-8")
    temporary.replace(target)


def _pid_is_running(pid: int) -> bool:
    """Return whether a local process identifier still exists."""
    if pid <= 0:
        return False
    if os.name == "nt":
        # Windows os.kill(pid, 0) calls TerminateProcess rather than probing;
        # querying the exit code avoids killing a live service during cleanup.
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # Access denied still indicates a live PID
        try:
            exit_code = wintypes.DWORD()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code))) and exit_code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def _stop_pid(pid: int) -> None:
    """Stop a previously managed server before replacing its configuration."""
    if not _pid_is_running(pid):
        return
    if os.name == "nt":
        # A console-less caller would otherwise flash a taskkill window when
        # replacing the background service after a configuration change.
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    else:
        os.kill(pid, 15)


class _LazyHTTPSHandler(urllib.request.HTTPSHandler):
    """Keep standard HTTPS redirects without loading certificates for HTTP."""

    def __init__(self) -> None:
        """Register an HTTP-compatible handler without creating a TLS context."""
        urllib.request.AbstractHTTPHandler.__init__(self)
        self._delegate: urllib.request.HTTPSHandler | None = None

    def https_open(self, request: urllib.request.Request):
        """Initialize the normal certificate-verifying handler on first HTTPS."""
        if self._delegate is None:
            self._delegate = urllib.request.HTTPSHandler()
            self._delegate.add_parent(self.parent)
        return self._delegate.https_open(request)


@lru_cache(maxsize=1)
def _server_opener() -> urllib.request.OpenerDirector:
    """Reuse a proxy-free client with TLS initialized only for actual HTTPS."""
    # Python 3.13 eagerly loads system certificates in HTTPSHandler.__init__.
    # Supplying its lazy subclass avoids that cost for local HTTP probes/builds
    # while retaining urllib's redirect, status-code and HTTPS verification rules.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _LazyHTTPSHandler())


def _request_version(host: str, port: int, timeout: float = 0.35) -> bool:
    """Probe the official `/version` endpoint without requiring a client library."""
    request = urllib.request.Request(f"http://{host}:{port}/version", method="GET")
    try:
        with _server_opener().open(request, timeout=timeout) as response:
            return 200 <= response.status < 300
    except (OSError, urllib.error.URLError):
        return False


def write_pmt_server_config(
    *,
    project_dir: Path,
    pandoc_args: list[str],
    pandoc_metadata: dict[str, object],
    resource_paths: list[Path] | None = None,
    metadata_sources: dict[str, object] | None = None,
) -> Path:
    """Write the project-bound conversion settings consumed by the PMT server."""
    target = server_config_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    serialized = json.dumps(
        {
            # A wheel upgrade must replace the old Python/native service even
            # when project settings and installed package paths are unchanged.
            "runtime_version": __version__,
            # Changing the buffer protocol must invalidate a running pre-upgrade
            # service, which would otherwise silently ignore editor text.
            "source_text_protocol": 1,
            "project_dir": str(project_dir.resolve()),
            "pandoc_args": pandoc_args,
            "pandoc_metadata": pandoc_metadata,
            "resource_paths": [str(path.resolve()) for path in (resource_paths or [])],
            "metadata_sources": metadata_sources,
        },
        indent=2, ensure_ascii=False, default=str,
    )
    # Identical configuration must preserve mtime; otherwise every CLI build
    # kills the warm worker and discards its citation caches.
    if target.is_file() and target.read_text(encoding="utf-8") == serialized:
        return target
    temporary.write_text(serialized, encoding="utf-8")
    temporary.replace(target)
    return target


def build_with_pandoc_server(info: PandocServerInfo, source: Path, output: Path) -> bool:
    """Build through a PMT service; generic servers retain the normal CLI path."""
    opener = _server_opener()
    with opener.open(f"{info.base_url}/version", timeout=2) as response:
        version = json.load(response)
    if not isinstance(version, dict) or version.get("protocol") != "pmt-html-v1":
        return False
    request = urllib.request.Request(
        f"{info.base_url}/convert/raw",
        data=json.dumps({"path": str(source.resolve())}).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with opener.open(request, timeout=120) as response:
            html = response.read().decode("utf-8")
            cache = response.headers.get("X-PMT-Cache", "unknown")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Pandoc server conversion failed: {exc.read().decode('utf-8', errors='replace')}") from exc
    # Decode before opening the destination: a failed request must preserve an
    # existing output file, and use the same newline policy as the CLI writer.
    output.write_text(html, encoding="utf-8")
    log_info(f"[Pandoc server] HTML built at {info.base_url} (cache {cache})")
    return True


def split_server_command(command: str) -> list[str]:
    """Split overrides while removing Windows quotes retained by shlex."""
    parts = shlex.split(command, posix=os.name != "nt")
    if os.name == "nt":
        # Popen already quotes each list argument. Retained wrapping quotes
        # otherwise become part of the executable name, especially with spaces.
        parts = [part[1:-1] if len(part) >= 2 and part[0] == part[-1] and part[0] in {'"', "'"} else part for part in parts]
    if not parts:
        raise ValueError("Pandoc server command is empty")
    return parts


def _resolve_command(command: str | None, config_path: Path | None) -> tuple[list[str], bool]:
    """Resolve an explicit server command or the bundled PMT runtime."""
    configured = command or os.environ.get("PMT_PANDOC_SERVER_COMMAND")
    if configured:
        return split_server_command(configured), False

    if config_path is not None:
        return [
            sys.executable,
            "-m",
            "pandoc_manuscript.commands.pandoc_server_runtime",
            "--config",
            str(config_path.resolve()),
        ], True

    names = ("pandoc-server.exe", "pandoc-server") if os.name == "nt" else ("pandoc-server",)
    for name in names:
        resolved = shutil.which(name)
        if resolved:
            return [resolved], False
    raise FileNotFoundError(
        "Pandoc server executable not found. Set PMT_PANDOC_SERVER_COMMAND or provide PMT server configuration."
    )

def ensure_pandoc_server(
    *,
    host: str = DEFAULT_SERVER_HOST,
    port: int = DEFAULT_SERVER_PORT,
    command: str | None = None,
    config_path: Path | None = None,
    environment: dict[str, str] | None = None,
    wait_seconds: float = 8.0,
) -> PandocServerInfo:
    """Reuse or start a local Pandoc HTTP server and return its connection info."""
    existing = _read_state()
    config_mtime_ns = config_path.stat().st_mtime_ns if config_path and config_path.exists() else None
    config_matches = (
        config_path is None
        or (existing is not None and existing.config_path == str(config_path.resolve())
            and existing.config_mtime_ns == config_mtime_ns)
    )
    if existing and existing.host == host and existing.port == port and config_matches:
        if _request_version(host, port):
            log_info(f"[Pandoc server] Reusing {existing.base_url} (pid {existing.pid})")
            return existing
        if not _pid_is_running(existing.pid):
            server_state_file().unlink(missing_ok=True)
        else:
            log_warning(
                f"[WARN] Pandoc server state exists at {existing.base_url}, but /version did not respond."
            )
    elif existing and existing.host == host and existing.port == port and not config_matches:
        _stop_pid(existing.pid)
        server_state_file().unlink(missing_ok=True)

    command_parts, is_pmt_runtime = _resolve_command(command, config_path)
    full_command = [*command_parts, "--port", str(port)]
    if is_pmt_runtime:
        full_command.extend(["--host", host])
    server_log_file().parent.mkdir(parents=True, exist_ok=True)
    log_handle = server_log_file().open("a", encoding="utf-8")
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    try:
        process = subprocess.Popen(
            full_command,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            env={**os.environ, **(environment or {})},
            creationflags=creationflags,
            # A detached Windows service must not inherit a captured CLI stdout
            # handle: its caller would wait for pipe EOF until the service exits.
            close_fds=True,
        )
    except BaseException:
        log_handle.close()
        raise
    log_handle.close()

    info = PandocServerInfo(
        host=host,
        port=port,
        pid=process.pid,
        command=full_command,
        started_at=time.time(),
        config_path=str(config_path.resolve()) if config_path else None,
        config_mtime_ns=config_mtime_ns,
    )
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if _request_version(host, port):
            _write_state(info)
            log_info(f"[Pandoc server] Started {info.base_url} (pid {info.pid})")
            return info
        if process.poll() is not None:
            break
        time.sleep(0.1)

    if process.poll() is None:
        process.terminate()
    raise RuntimeError(
        f"Pandoc server did not become ready at http://{host}:{port}. "
        f"See {server_log_file()} for its output."
    )


__all__ = [
    "DEFAULT_SERVER_HOST",
    "DEFAULT_SERVER_PORT",
    "PandocServerInfo",
    "ensure_pandoc_server",
    "build_with_pandoc_server",
    "write_pmt_server_config",
]
