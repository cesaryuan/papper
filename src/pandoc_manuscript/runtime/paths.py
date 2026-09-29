"""Resolve per-project Papper state and per-process temporary work paths."""

from __future__ import annotations

import atexit
import hashlib
import os
import shutil
import tempfile
from pathlib import Path


PAPPER_HOME_DIR = Path.home() / ".papper"
PMT_TOOLS_DIR = PAPPER_HOME_DIR / "tools"
PMT_TOOLS_BIN_DIR = PMT_TOOLS_DIR / "bin"
PMT_TOOLS_DOWNLOAD_DIR = PAPPER_HOME_DIR / "downloads"
_TEMP_ROOT: Path | None = None


def project_key(project_dir: Path | None = None) -> str:
    """Identify a project by its canonical path, independent of its folder name."""
    root = (project_dir or Path.cwd()).resolve()
    return hashlib.sha256(os.path.normcase(str(root)).encode("utf-8")).hexdigest()[:20]


def project_state_dir(project_dir: Path | None = None) -> Path:
    """Return user-level state isolated to one manuscript project."""
    return PAPPER_HOME_DIR / "projects" / project_key(project_dir)


def project_work_dir(project_dir: Path | None = None) -> Path:
    """Return persistent project work such as metadata and server state."""
    return project_state_dir(project_dir) / "work"


def project_cache_dir(project_dir: Path | None = None) -> Path:
    """Return reusable project caches without mixing results between projects."""
    return project_state_dir(project_dir) / "cache"


def _cleanup_temp_root() -> None:
    """Remove only temporary files created by this process."""
    if _TEMP_ROOT is not None:
        shutil.rmtree(_TEMP_ROOT, ignore_errors=True)


def process_temp_dir() -> Path:
    """Allocate system-temp space for this process's build intermediates."""
    global _TEMP_ROOT
    if _TEMP_ROOT is None:
        _TEMP_ROOT = Path(tempfile.mkdtemp(prefix="papper-"))
        atexit.register(_cleanup_temp_root)
    return _TEMP_ROOT / project_key()
