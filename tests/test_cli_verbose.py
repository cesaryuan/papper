import os
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pandoc_manuscript import cli
from pandoc_manuscript.commands import build as build_command
from pandoc_manuscript.commands.build_reply import resolve as reply_resolve
from pandoc_manuscript.runtime.logging import LOG_LEVEL_ENV, log_debug


@pytest.fixture(autouse=True)
def disable_update_check(monkeypatch) -> None:
    """Keep CLI behavior tests independent of the external PyPI endpoint."""
    monkeypatch.setattr(cli, "notify_and_schedule_update_check", lambda version: None)


def test_verbose_temporarily_enables_debug_logging(monkeypatch, capsys) -> None:
    """Enable DEBUG during a verbose command without leaking it to later commands."""
    monkeypatch.delenv(LOG_LEVEL_ENV, raising=False)

    def fake_run(self) -> int:
        """Emit one debug line through the shared runtime logging gate."""
        log_debug("verbose command output")
        return 0

    monkeypatch.setattr(cli.DoctorSettings, "run", fake_run)

    assert cli.main(["doctor", "--verbose"]) == 0
    assert "verbose command output" in capsys.readouterr().out
    assert LOG_LEVEL_ENV not in os.environ

    assert cli.main(["doctor"]) == 0
    assert "verbose command output" not in capsys.readouterr().out


@pytest.mark.parametrize("runner", [build_command.run_command, reply_resolve.run_command])
def test_external_command_echo_is_debug_only(runner, monkeypatch, capsys) -> None:
    """Hide complete external commands by default while retaining verbose diagnostics."""
    monkeypatch.delenv(LOG_LEVEL_ENV, raising=False)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout="", stderr=""),
    )

    runner(["pandoc", "--version"])

    assert "[Run] pandoc --version" not in capsys.readouterr().out

    monkeypatch.setenv(LOG_LEVEL_ENV, "DEBUG")
    runner(["pandoc", "--version"])

    assert "[Run] pandoc --version" in capsys.readouterr().out
