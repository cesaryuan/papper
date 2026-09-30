from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pandoc_manuscript.commands import clean as clean_command
from pandoc_manuscript.runtime import paths


def test_distclean_removes_only_current_project_state(tmp_path, monkeypatch) -> None:
    """Preserve another project's reusable cache when deep-cleaning this project."""
    monkeypatch.setattr(paths, "PAPPER_HOME_DIR", tmp_path / "user-state")
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    first_cache = paths.project_cache_dir(first)
    second_cache = paths.project_cache_dir(second)
    first_cache.mkdir(parents=True)
    second_cache.mkdir(parents=True)
    (first_cache / "result.bin").write_bytes(b"first")
    (second_cache / "result.bin").write_bytes(b"second")

    monkeypatch.chdir(first)
    clean_command.distclean()

    assert not paths.project_state_dir(first).exists()
    assert (second_cache / "result.bin").read_bytes() == b"second"
