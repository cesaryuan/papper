"""Exercise diagnostic logging through actual Rust builds."""

import json
import os
import subprocess
from pathlib import Path

from native_support import ROOT, papper_command


def test_verbose_logging_does_not_leak_to_later_commands(tmp_path: Path) -> None:
    """Show native build diagnostics only for the invocation requesting verbose output."""
    source = tmp_path / "paper.md"
    source.write_text("# Introduction\n\nA paragraph.\n", encoding="utf-8")
    output = tmp_path / "paper.json"
    environment = {**os.environ, "PAPPER_HOME": str(tmp_path / "state"),
                   "PAPPER_RESOURCE_ROOT": str(ROOT)}
    for verbose in (True, False):
        command = [*papper_command(), "build", "json", str(source), "-o", str(output)]
        if verbose:
            command.append("--verbose")
        result = subprocess.run(command, cwd=tmp_path, env=environment,
                                capture_output=True, text=True, encoding="utf-8", timeout=45)
        assert result.returncode == 0, result.stdout + result.stderr
        assert ("[DEBUG] Native input:" in result.stdout + result.stderr) is verbose
        assert json.loads(output.read_text(encoding="utf-8"))["blocks"]
