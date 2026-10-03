"""Pytest configuration for Papper's repository tests."""

from __future__ import annotations

import pytest
import os
import shutil
import subprocess
from pathlib import Path
from collections.abc import Iterator

from native_support import ROOT


@pytest.fixture(scope="session", autouse=True)
def rust_executable(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """Compile current Rust sources once and run owned copies so Windows permits later builds."""
    completed = subprocess.run(
        ["cargo", "build", "--locked", "--offline", "-p", "papper-cli"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=240,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    name = "papper.exe" if os.name == "nt" else "papper"
    target = Path(os.environ.get("CARGO_TARGET_DIR", ROOT / "target"))
    if not target.is_absolute():
        target = ROOT / target
    executable = tmp_path_factory.mktemp("native-cli-binary") / name
    shutil.copy2(target / "debug" / name, executable)
    previous = os.environ.get("PAPPER_TEST_EXECUTABLE")
    os.environ["PAPPER_TEST_EXECUTABLE"] = str(executable)
    try:
        yield executable
    finally:
        if previous is None:
            os.environ.pop("PAPPER_TEST_EXECUTABLE", None)
        else:
            os.environ["PAPPER_TEST_EXECUTABLE"] = previous


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register the opt-in flag used to create or refresh output snapshots."""
    parser.addoption(
        "--snapshot-update",
        action="store_true",
        default=False,
        help="Create or refresh Papper output snapshots.",
    )


@pytest.fixture
def snapshot_update(request: pytest.FixtureRequest) -> bool:
    """Return whether the current run is allowed to rewrite snapshots."""
    return bool(request.config.getoption("--snapshot-update"))
