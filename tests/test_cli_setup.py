from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pandoc_manuscript import cli
from pandoc_manuscript.commands import setup as setup_command
from pandoc_manuscript.commands.setup import command as setup_command_impl
from pandoc_manuscript.commands.setup import ResolvedTool


def test_setup_command_runs_in_project_directory(tmp_path, monkeypatch) -> None:
    """Run papper setup from the selected project directory."""
    calls = []

    def fake_setup_pandoc_tools(*, force=False):
        """Record the setup working directory without touching the network."""
        calls.append((Path.cwd(), force))
        return (
            ResolvedTool("pandoc", Path(".papper/tools/bin/pandoc"), ".papper/tools"),
            ResolvedTool("pandoc-crossref", Path(".papper/tools/bin/pandoc-crossref"), ".papper/tools"),
        )

    monkeypatch.setattr(setup_command_impl, "setup_pandoc_tools", fake_setup_pandoc_tools)

    monkeypatch.chdir(tmp_path)
    result = cli.SetupSettings(force=True).run()

    assert result == 0
    assert calls == [(tmp_path.resolve(), True)]


def test_setup_package_reexports_tool_api() -> None:
    """Expose setup command settings and managed tool helpers from the setup package."""
    assert setup_command.SetupSettings is cli.SetupSettings
    assert setup_command.ResolvedTool is ResolvedTool
    assert callable(setup_command.setup_pandoc_tools)
    assert callable(setup_command.ensure_pandoc_tools)
    assert callable(setup_command.resolve_tool)
    assert callable(setup_command.pandoc_command)
    assert callable(setup_command.pandoc_tools_env)
