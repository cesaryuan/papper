"""Validate version reporting from the current native executable."""

import importlib.metadata
import subprocess

from native_support import papper_command


def test_cli_version_matches_distribution_metadata() -> None:
    """Keep the shipped native command and installed distribution version synchronized."""
    result = subprocess.run([*papper_command(), "--version"], capture_output=True,
                            text=True, encoding="utf-8", timeout=15)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"papper {importlib.metadata.version('papper')}"
