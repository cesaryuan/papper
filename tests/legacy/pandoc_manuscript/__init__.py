"""Command-line helpers for Papper's Markdown-first manuscript workflow."""

from __future__ import annotations

import importlib.metadata as importlib_metadata
import json

_DIST_NAME = "papper"
_UNKNOWN_VERSION = "0+unknown"


def _installed_version() -> str:
    """Read the installed package version so CLI output follows release metadata."""
    try:
        return importlib_metadata.version(_DIST_NAME)
    except importlib_metadata.PackageNotFoundError:
        return _UNKNOWN_VERSION


__version__ = _installed_version()


def _direct_url_commit_id() -> str | None:
    """Return the installed Git commit id recorded by PEP 610 metadata."""
    try:
        direct_url_text = importlib_metadata.distribution(_DIST_NAME).read_text("direct_url.json")
    except importlib_metadata.PackageNotFoundError:
        return None
    if not direct_url_text:
        return None
    try:
        direct_url = json.loads(direct_url_text)
    except json.JSONDecodeError:
        return None
    commit_id = direct_url.get("vcs_info", {}).get("commit_id")
    return commit_id if isinstance(commit_id, str) and commit_id else None


def runtime_cache_version() -> str:
    """Return a cache key version that changes for Git-installed revisions."""
    commit_id = _direct_url_commit_id()
    if commit_id:
        return f"{__version__}+git.{commit_id}"
    return __version__

__all__ = [
    "__version__",
    "runtime_cache_version",
]
