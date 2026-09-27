"""Pytest configuration for Papper's repository tests."""

from __future__ import annotations

import pytest


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
