"""Locate native test executables and normalize artifacts without importing product Python code."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def papper_command() -> list[str]:
    """Use the owned Rust CLI copy prepared once by the session fixture."""
    return [os.environ["PAPPER_TEST_EXECUTABLE"]]


def native_pandoc_executable() -> Path | None:
    """Find a real retained worker from source-build records or installed native resources."""
    name = "pmt-pandoc-worker.exe" if os.name == "nt" else "pmt-pandoc-worker"
    for root in (ROOT, Path(os.environ.get("PAPPER_RESOURCE_ROOT", ROOT))):
        for candidate in (root / "bin" / name, root / "src/pandoc_manuscript/bin" / name):
            if candidate.is_file():
                return candidate
        record = root / ".pmt/pandoc-worker/current.json"
        if record.is_file():
            candidate = record.parent / json.loads(record.read_text(encoding="utf-8"))["executable"]
            if candidate.is_file():
                return candidate
    builds = list((ROOT / "scripts/pandoc-server/dist-newstyle/build").glob(
        f"*/ghc-*/pmt-pandoc-server-*/x/pmt-pandoc-worker/build/pmt-pandoc-worker/{name}"
    ))
    if builds:
        return max(builds, key=lambda path: path.stat().st_mtime_ns)
    homes = [Path(os.environ.get("PAPPER_HOME", Path.home() / ".papper")), Path.home() / ".papper"]
    return next((home / "tools/bin" / name for home in homes if (home / "tools/bin" / name).is_file()), None)


def project_cache_dir(project: Path) -> Path:
    """Locate managed cache paths solely to remove temporary project identities from snapshots."""
    identity = os.path.normcase(str(project.resolve()))
    key = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    home = Path(os.environ.get("PAPPER_HOME", Path.home() / ".papper"))
    return home / "projects" / key / "cache"
