"""Verify explicit style selection through the public build command."""

import json
import shutil
from pathlib import Path

import pytest

from pandoc_manuscript import cli
from pandoc_manuscript.commands import build
from pandoc_manuscript.runtime import paths


@pytest.fixture
def style_project(tmp_path: Path, monkeypatch) -> Path:
    """Create conflicting default styles to expose accidental discovery or merging."""
    source = tmp_path / "chapters"
    source.mkdir()
    (source / "paper.md").write_text(
        "---\nsubtitle: Manuscript\n---\nBody\n", encoding="utf-8"
    )
    (source / "style.yml").write_text(
        "pandocMetadata:\n  title: Local\n  localOnly: true\n", encoding="utf-8"
    )
    (tmp_path / "style.yml").write_text(
        "pandocMetadata:\n  title: Working\n  workingOnly: true\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(build, "SETTINGS", build.BuildSettings(style_file="style.yml"))
    monkeypatch.setattr(paths, "PAPPER_HOME_DIR", tmp_path / "state")
    monkeypatch.setattr(cli, "notify_and_schedule_update_check", lambda version: None)
    return tmp_path


@pytest.mark.skipif(
    shutil.which("pandoc") is None or shutil.which("pandoc-crossref") is None,
    reason="JSON builds require pandoc and pandoc-crossref",
)
@pytest.mark.parametrize("path_kind", ["relative", "absolute", "explicit_default"])
def test_build_uses_only_explicit_style_for_one_invocation(
    style_project: Path, path_kind: str
) -> None:
    """Select the requested style in output, preserve YAML priority, and restore discovery."""
    selected = style_project / "styles with spaces" / "journal.yml"
    selected.parent.mkdir()
    selected.write_text(
        "pandocMetadata:\n  title: Selected\n  subtitle: Style\n", encoding="utf-8"
    )
    if path_kind == "explicit_default":
        selected = style_project / "style.yml"
        selected.write_text(
            "pandocMetadata:\n  title: Selected\n  subtitle: Style\n", encoding="utf-8"
        )
    style_arg = str(selected if path_kind == "absolute" else selected.relative_to(style_project))
    output = style_project / "selected.json"
    arguments = ["build", "json", "chapters/paper.md"]

    assert cli.main([*arguments, "--style-file", style_arg, "-o", str(output)]) == 0
    metadata = json.loads(output.read_text(encoding="utf-8"))["meta"]
    assert metadata["title"]["c"] == [{"t": "Str", "c": "Selected"}]
    assert metadata["subtitle"]["c"] == [{"t": "Str", "c": "Manuscript"}]
    assert "localOnly" not in metadata
    assert "workingOnly" not in metadata

    assert cli.main([*arguments, "-o", str(output)]) == 0
    metadata = json.loads(output.read_text(encoding="utf-8"))["meta"]
    assert metadata["title"]["c"] == [{"t": "Str", "c": "Local"}]
    assert metadata["localOnly"]["c"] is True
    if path_kind != "explicit_default":
        assert metadata["workingOnly"]["c"] is True


@pytest.mark.parametrize(
    ("style_arg", "message"),
    [("missing.yml", "Style file not found"), ("chapters", "Style path is not a file")],
)
def test_build_rejects_invalid_explicit_style(
    style_project: Path, style_arg: str, message: str, capsys
) -> None:
    """Report invalid explicit paths instead of silently falling back to default styles."""
    output = style_project / "result.json"

    assert cli.main(
        ["build", "json", "chapters/paper.md", "--style-file", style_arg, "-o", str(output)]
    ) == 1
    assert message in capsys.readouterr().err
    assert not output.exists()
