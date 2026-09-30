from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pandoc_manuscript.commands import build_reply as reply_build
from pandoc_manuscript.mathtype import ole_parts
from pandoc_manuscript.runtime import paths as runtime_paths
from pandoc_manuscript.runtime.paths import (
    process_temp_dir,
    project_cache_dir,
    project_state_dir,
)


def test_generated_work_and_cache_paths_are_isolated(tmp_path, monkeypatch) -> None:
    """Separate reusable project caches from large per-run intermediates."""
    monkeypatch.chdir(tmp_path)
    state = project_state_dir()
    assert state.parent == Path.home() / ".papper" / "projects"
    assert reply_build.line_source_cache_dir().is_relative_to(state / "cache")
    assert ole_parts.mathtype_cache_dir().is_relative_to(state / "cache")
    assert reply_build.line_source_pdf_dir().is_relative_to(process_temp_dir())
    assert reply_build.reply_probe_dir().is_relative_to(process_temp_dir())


def test_cache_resolution_follows_project_changes(tmp_path, monkeypatch) -> None:
    """Switch projects in one process without reusing the first project's cache."""
    monkeypatch.setattr(runtime_paths, "PAPPER_HOME_DIR", tmp_path / "user-state")
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    monkeypatch.chdir(first)
    first_paths = (ole_parts.cache_paths("abcdef"), reply_build.line_source_cache_dir())
    monkeypatch.chdir(second)
    second_paths = (ole_parts.cache_paths("abcdef"), reply_build.line_source_cache_dir())

    assert first_paths != second_paths
    assert second_paths[0][0].is_relative_to(project_cache_dir(second))
    assert second_paths[1].is_relative_to(project_cache_dir(second))
