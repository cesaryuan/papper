"""Start and reuse a local Pandoc HTTP server for editor integrations."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

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
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        os.kill(pid, 15)


def _request_version(host: str, port: int, timeout: float = 0.35) -> bool:
    """Probe the official `/version` endpoint without requiring a client library."""
    request = urllib.request.Request(f"http://{host}:{port}/version", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return 200 <= response.status < 300
    except (OSError, urllib.error.URLError):
        return False


def write_pmt_server_config(
    *,
    project_dir: Path,
    pandoc_args: list[str],
    pandoc_metadata: dict[str, object],
    resource_paths: list[Path] | None = None,
) -> Path:
    """Write the project-bound conversion settings consumed by the PMT server."""
    target = server_config_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {
                "project_dir": str(project_dir.resolve()),
                "pandoc_args": pandoc_args,
                "pandoc_metadata": pandoc_metadata,
                "resource_paths": [str(path.resolve()) for path in (resource_paths or [])],
            },
            indent=2,
            ensure_ascii=False,
            default=str,
        ),
        encoding="utf-8",
    )
    temporary.replace(target)
    return target


def _resolve_command(command: str | None, config_path: Path | None) -> tuple[list[str], bool]:
    """Resolve an explicit server command or the bundled PMT runtime."""
    configured = command or os.environ.get("PMT_PANDOC_SERVER_COMMAND")
    if configured:
        parts = shlex.split(configured, posix=os.name != "nt")
        if not parts:
            raise ValueError("PMT_PANDOC_SERVER_COMMAND is empty")
        return parts, False

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
            close_fds=os.name != "nt",
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
    "write_pmt_server_config",
]
