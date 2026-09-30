from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pandoc_manuscript import cli
from pandoc_manuscript.cli import InitSettings
from pandoc_manuscript.runtime.metadata import load_effective_metadata


def test_init_without_directory_uses_current_directory(tmp_path, monkeypatch) -> None:
    """Use the current directory when the init target is omitted."""
    monkeypatch.chdir(tmp_path)

    assert cli.main(["init"]) == 0

    assert (tmp_path / "manuscript.md").is_file()
    assert (tmp_path / "reply_to_reviewers.md").is_file()
    assert "# Introduction" in (tmp_path / "manuscript.md").read_text(encoding="utf-8")


def test_init_zh_cn_uses_translated_manuscript_and_reply_templates(tmp_path) -> None:
    """Select the Chinese source templates while keeping standard destination names."""
    target = tmp_path / "paper"

    InitSettings(directory=str(target), lang="zh-cn").run()

    manuscript = (target / "manuscript.md").read_text(encoding="utf-8")
    reply = (target / "reply_to_reviewers.md").read_text(encoding="utf-8")
    assert "lang: zh-CN" in manuscript
    assert "# 引言" in manuscript
    assert "# 对审稿意见的回复" in reply
    assert "# Introduction" not in manuscript
    assert "# Reply to comments of reviewers" not in reply


def test_init_project_style_does_not_override_language_defaults(tmp_path) -> None:
    """Keep an initialized project's style file from pinning English defaults."""
    target = tmp_path / "paper"
    InitSettings(directory=str(target)).run()
    manuscript = target / "manuscript.md"
    manuscript.write_text("---\nlang: zh-CN\n---\n正文\n", encoding="utf-8")

    effective = load_effective_metadata(manuscript, target / "style.yml")

    assert effective.pandoc_metadata["figureTitle"] == "图"
    assert effective.pmt_settings.docx_style["标题 2"]["fontSize"] == "小四"


def test_init_rejects_unsupported_language(tmp_path) -> None:
    """Keep the init language contract explicit until more localized templates exist."""
    with pytest.raises(ValueError, match="Only `--lang zh-cn`"):
        InitSettings(directory=str(tmp_path / "paper"), lang="en-US").run()


def test_init_merge_agents_directory_keeps_existing_files(tmp_path) -> None:
    """Merge .agents by filling missing files without overwriting local edits."""
    target = tmp_path / "paper"
    existing_skill = target / ".agents" / "word-manuscript-fix" / "SKILL.md"
    existing_skill.parent.mkdir(parents=True)
    existing_skill.write_text("local skill notes\n", encoding="utf-8")

    InitSettings(directory=str(target), merge=True).run()

    assert existing_skill.read_text(encoding="utf-8") == "local skill notes\n"
    assert (target / ".agents" / "manuscript-review" / "SKILL.md").is_file()
    assert (target / ".agents" / "word-manuscript-fix" / "scripts" / "unescape_latex.py").is_file()
