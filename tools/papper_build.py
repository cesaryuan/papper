"""PEP 517/660 bridge to the Rust wheel builder, used only at package-build time.

uv invokes this small standard build interface; Cargo owns compilation, runtime
staging and wheel contents. Installed executables never import this module or
start Python. Editable builds install the native development executables.
"""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _build(directory: str, *, editable: bool) -> str:
    """Delegate packaging to Rust and return the frontend's exact wheel filename."""
    output = Path(directory).resolve()
    before = {path: path.stat().st_mtime_ns for path in output.glob("papper-*.whl")}
    command = ["cargo", "run", "--locked", "-p", "papper-dev", "--"]
    command.extend(["editable" if editable else "wheel", "--output", str(output)])
    subprocess.run(command, cwd=ROOT, check=True)
    wheels = [path for path in output.glob("papper-*.whl") if path not in before or path.stat().st_mtime_ns != before[path]]
    if len(wheels) != 1:
        raise RuntimeError(f"Expected one newly built native wheel, found {len(wheels)}")
    return wheels[0].name


def build_wheel(wheel_directory: str, config_settings=None, metadata_directory=None) -> str:
    """Build the distributable native wheel with retained platform components."""
    return _build(wheel_directory, editable=False)


def build_editable(wheel_directory: str, config_settings=None, metadata_directory=None) -> str:
    """Build direct development executables for uv run and native integration tests."""
    return _build(wheel_directory, editable=True)


def _metadata(directory: str) -> str:
    """Provide dependency-free product metadata without compiling native engines."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    name = f"papper-{project['version']}.dist-info"
    destination = Path(directory) / name
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "METADATA").write_text(
        f"Metadata-Version: 2.4\nName: papper\nVersion: {project['version']}\nRequires-Python: {project['requires-python']}\nSummary: {project['description']}\n",
        encoding="utf-8",
    )
    return name


def prepare_metadata_for_build_wheel(metadata_directory: str, config_settings=None) -> str:
    """Let uv resolve an installation before performing expensive native builds."""
    return _metadata(metadata_directory)


def prepare_metadata_for_build_editable(metadata_directory: str, config_settings=None) -> str:
    """Resolve the native editable installation using the same product metadata."""
    return _metadata(metadata_directory)
