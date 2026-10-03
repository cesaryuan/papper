"""Verify explicit style selection through the public build command."""

import json
import shutil
import os
import subprocess
from pathlib import Path

import pytest

from native_support import ROOT, native_pandoc_executable, papper_command


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
    monkeypatch.setenv("PAPPER_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("PAPPER_RESOURCE_ROOT", str(ROOT))
    return tmp_path


def run_build(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    """Run the current Rust CLI and capture user-visible selection errors."""
    return subprocess.run([*papper_command(), *arguments], env=os.environ,
                          capture_output=True, text=True, encoding="utf-8", timeout=45)


@pytest.mark.skipif(
    native_pandoc_executable() is None and
    (shutil.which("pandoc") is None or shutil.which("pandoc-crossref") is None),
    reason="JSON builds require the native engine or standalone Pandoc and crossref",
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

    result = run_build([*arguments, "--style-file", style_arg, "-o", str(output)])
    assert result.returncode == 0, result.stdout + result.stderr
    metadata = json.loads(output.read_text(encoding="utf-8"))["meta"]
    assert metadata["title"]["c"] == [{"t": "Str", "c": "Selected"}]
    assert metadata["subtitle"]["c"] == [{"t": "Str", "c": "Manuscript"}]
    assert "localOnly" not in metadata
    assert "workingOnly" not in metadata

    result = run_build([*arguments, "-o", str(output)])
    assert result.returncode == 0, result.stdout + result.stderr
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
    style_project: Path, style_arg: str, message: str
) -> None:
    """Report invalid explicit paths instead of silently falling back to default styles."""
    output = style_project / "result.json"

    result = run_build(
        ["build", "json", "chapters/paper.md", "--style-file", style_arg, "-o", str(output)]
    )
    assert result.returncode != 0
    assert message in result.stderr
    assert not output.exists()
