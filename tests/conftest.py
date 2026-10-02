"""Pytest configuration for Papper's repository tests."""

from __future__ import annotations

import pytest
import sys
from pathlib import Path

# The former Python product is a frozen differential oracle. It is deliberately
# absent from release wheels; native contracts invoke the real Rust executables.
sys.path.insert(0, str(Path(__file__).resolve().parent / "legacy"))


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
