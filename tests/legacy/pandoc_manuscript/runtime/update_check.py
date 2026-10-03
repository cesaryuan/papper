"""Cache and refresh PyPI update information without delaying Papper commands.

The CLI reads the local cache after a command completes. When the cache is
stale, it starts this module in a detached worker process, which refreshes the
cache without making the completed command wait for network I/O.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version

from .logging import log_warning


DIST_NAME = "papper"
PYPI_JSON_URL = f"https://pypi.org/pypi/{DIST_NAME}/json"
UPDATE_CACHE_FILENAME = "update.json"
UPDATE_CACHE_TTL_SECONDS = 60 * 60
UPDATE_RETRY_SECONDS = 15 * 60
REQUEST_TIMEOUT_SECONDS = 2
USER_AGENT = f"{DIST_NAME} update check"


@dataclass(frozen=True)
class UpdateCache:
    """Latest known PyPI version and the timestamps used to refresh it."""

    latest_version: str | None
    checked_at: float | None
    attempted_at: float


def latest_pypi_version() -> str | None:
    """Return the latest PyPI version, or None when PyPI cannot be queried."""
    request = urllib.request.Request(PYPI_JSON_URL, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            payload: Any = json.loads(response.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, urllib.error.URLError):
        return None

    info = payload.get("info") if isinstance(payload, dict) else None
    latest_version = info.get("version") if isinstance(info, dict) else None
    if not isinstance(latest_version, str):
        return None
    try:
        Version(latest_version)
    except InvalidVersion:
        return None
    return latest_version


def available_update(installed_version: str, latest_version: str | None = None) -> str | None:
    """Return a newer version than the installed release, or None when unavailable."""
    try:
        installed = Version(installed_version)
    except InvalidVersion:
        return None

    latest_version = latest_version if latest_version is not None else latest_pypi_version()
    if latest_version is None:
        return None
    try:
        latest = Version(latest_version)
    except InvalidVersion:
        return None
    return latest_version if latest > installed else None


def update_cache_path() -> Path:
    """Return the per-user cache path without touching a manuscript project."""
    if sys.platform == "darwin":
        cache_root = Path.home() / "Library" / "Caches"
    elif os.name == "nt":
        cache_root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        cache_root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return cache_root / DIST_NAME / UPDATE_CACHE_FILENAME


def read_update_cache() -> UpdateCache | None:
    """Read a valid update cache, returning None for missing or damaged files."""
    try:
        payload: Any = json.loads(update_cache_path().read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None

    latest_version = payload.get("latest_version")
    checked_at = payload.get("checked_at")
    attempted_at = payload.get("attempted_at")
    if latest_version is not None and not isinstance(latest_version, str):
        return None
    if checked_at is not None and not _is_timestamp(checked_at):
        return None
    if not _is_timestamp(attempted_at):
        return None
    return UpdateCache(latest_version, checked_at, float(attempted_at))


def write_update_cache(cache: UpdateCache) -> None:
    """Atomically replace the update cache so concurrent CLI reads stay valid."""
    cache_path = update_cache_path()
    temporary_path = cache_path.with_suffix(f".{os.getpid()}.tmp")
    payload = {
        "latest_version": cache.latest_version,
        "checked_at": cache.checked_at,
        "attempted_at": cache.attempted_at,
    }
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        temporary_path.replace(cache_path)
    except OSError:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass


def update_cache_needs_refresh(cache: UpdateCache | None, *, now: float | None = None) -> bool:
    """Return whether the cache should launch a refresh worker now."""
    if cache is None:
        return True
    current_time = time.time() if now is None else now
    last_success = cache.checked_at
    if last_success is not None and 0 <= current_time - last_success < UPDATE_CACHE_TTL_SECONDS:
        return False
    return current_time - cache.attempted_at >= UPDATE_RETRY_SECONDS


def cached_available_update(installed_version: str, cache: UpdateCache | None = None) -> str | None:
    """Return a newer version from the local cache without making a network request."""
    cache = read_update_cache() if cache is None else cache
    return available_update(installed_version, cache.latest_version) if cache is not None else None


def start_update_worker() -> None:
    """Start a silent refresh worker and return without waiting for network I/O."""
    process_options: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        process_options["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        process_options["start_new_session"] = True
    try:
        subprocess.Popen(
            [sys.executable, "-m", "pandoc_manuscript.runtime.update_check"],
            **process_options,
        )
    except OSError:
        # Failure to start a non-essential worker must not affect the caller.
        return


def notify_and_schedule_update_check(installed_version: str) -> None:
    """Print a cached upgrade hint, then refresh stale information in the background."""
    cache = read_update_cache()
    latest_version = cached_available_update(installed_version, cache)
    if latest_version is not None:
        log_warning(
            f"[UPDATE] Papper {latest_version} is available, upgrade with "
            f"`uv tool upgrade {DIST_NAME}`"
        )
    if update_cache_needs_refresh(cache):
        start_update_worker()


def run_update_worker() -> None:
    """Fetch PyPI once and persist the result for the next Papper invocation."""
    previous_cache = read_update_cache()
    now = time.time()
    latest_version = latest_pypi_version()
    if latest_version is None:
        cache = UpdateCache(
            previous_cache.latest_version if previous_cache else None,
            previous_cache.checked_at if previous_cache else None,
            now,
        )
    else:
        cache = UpdateCache(latest_version, now, now)
    write_update_cache(cache)


def _is_timestamp(value: object) -> bool:
    """Return whether a JSON value can safely be used as a cache timestamp."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


if __name__ == "__main__":
    run_update_worker()
