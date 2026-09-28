from pathlib import Path

import pytest
import yaml

from pandoc_manuscript.runtime import metadata as metadata_module
from pandoc_manuscript.runtime.metadata import (
    PmtSettings,
    load_effective_metadata,
    write_pandoc_metadata,
)


def test_new_style_structure_separates_pmt_and_pandoc_metadata(tmp_path: Path) -> None:
    """Keep Papper-owned settings out of the mapping supplied to Pandoc."""
    style = tmp_path / "style.yml"
    style.write_text(
        """mathtype: true
docxStyle:
  Body Text: {firstLineIndentChars: 2}
pandocMetadata:
  figureTitle: Figure
  custom-list: [一, two]
""",
        encoding="utf-8",
    )

    settings = PmtSettings.load(style)

    assert settings.mathtype is True
    assert settings.docx_style == {"Body Text": {"firstLineIndentChars": 2}}
    assert settings.pandoc_metadata["figureTitle"] == "Figure"
    assert settings.pandoc_metadata["custom-list"] == ["一", "two"]
    assert "linkReferences" not in settings.pandoc_metadata
    assert "mathtype" not in settings.pandoc_metadata
    assert "docxStyle" not in settings.pandoc_metadata


def test_manuscript_header_only_overrides_pandoc_metadata(tmp_path: Path) -> None:
    """Do not let manuscript YAML implicitly change Papper-owned settings."""
    style = tmp_path / "style.yml"
    style.write_text(
        """mathtype: false
pandocMetadata:
  figureTitle: Figure
  nested: {left: style, right: keep}
""",
        encoding="utf-8",
    )
    manuscript = tmp_path / "paper.md"
    manuscript.write_text(
        """---
mathtype: true
figureTitle: Fig.
nested: {left: manuscript}
---
Body
""",
        encoding="utf-8",
    )

    effective = load_effective_metadata(manuscript, style)

    assert effective.pmt_settings.mathtype is False
    assert effective.pandoc_metadata["mathtype"] is True
    assert effective.pandoc_metadata["figureTitle"] == "Fig."
    assert effective.pandoc_metadata["nested"] == {"left": "manuscript", "right": "keep"}


def test_builtin_pandoc_metadata_defaults_apply_without_style_file(tmp_path: Path) -> None:
    """Keep cross-reference defaults when style metadata is omitted."""
    manuscript = tmp_path / "paper.md"
    manuscript.write_text("---\ntitle: Example\n---\nBody\n", encoding="utf-8")
    effective = load_effective_metadata(manuscript, None)

    assert effective.pmt_settings.pandoc_metadata == {}
    assert effective.pandoc_metadata["title"] == "Example"
    assert effective.pandoc_metadata["figureTitle"] == "Figure "
    assert effective.pandoc_metadata["linkReferences"] is True


def test_english_docx_style_defaults_apply_to_effective_settings(tmp_path: Path) -> None:
    """Apply English DOCX style defaults when no Chinese language is selected."""
    manuscript = tmp_path / "paper.md"
    manuscript.write_text("---\ntitle: Example\n---\nBody\n", encoding="utf-8")

    effective = load_effective_metadata(manuscript, style_path=None)

    assert effective.pmt_settings.docx_style == {
        "Heading 1": {"fontSize": "小四"},
        "Heading 2": {"fontSize": "小四"},
        "Body Text": {
            "firstLineIndentChars": 2,
            "paragraphSpacing": {"before": "0pt", "after": "0pt"},
        },
    }


def test_empty_style_metadata_keeps_defaults_and_allows_overrides(tmp_path: Path) -> None:
    """Apply explicit style values after the built-in Pandoc defaults."""
    style = tmp_path / "style.yml"
    style.write_text(
        "pandocMetadata:\n  figureTitle: 'Fig. '\n  sectionsDepth: 2\n",
        encoding="utf-8",
    )
    manuscript = tmp_path / "paper.md"
    manuscript.write_text("---\ntitle: Example\n---\nBody\n", encoding="utf-8")

    settings = PmtSettings.load(style)
    effective = load_effective_metadata(manuscript, style)

    assert settings.pandoc_metadata["figureTitle"] == "Fig. "
    assert settings.pandoc_metadata["sectionsDepth"] == 2
    assert "tableTitle" not in settings.pandoc_metadata
    assert effective.pandoc_metadata["figureTitle"] == "Fig. "
    assert effective.pandoc_metadata["tableTitle"] == "Table "


def test_reply_overrides_both_domains_before_reply_header(tmp_path: Path) -> None:
    """Apply reply Papper and Pandoc overrides while keeping the domains separate."""
    style = tmp_path / "style.yml"
    style.write_text(
        """docxStyle:
  Body Text: {firstLineIndentChars: 2, alignment: left}
pandocMetadata:
  reference-section-title: References
  nested: {base: true}
reply:
  docxStyle:
    Body Text: {firstLineIndentChars: 0}
  pandocMetadata:
    reference-section-title: Reply References
    nested: {reply: true}
""",
        encoding="utf-8",
    )
    reply = tmp_path / "reply.md"
    reply.write_text(
        """---
reference-section-title: Final References
---
Reply
""",
        encoding="utf-8",
    )

    effective = load_effective_metadata(reply, style, reply=True)

    assert effective.pmt_settings.docx_style == {
        "Heading 1": {"fontSize": "小四"},
        "Heading 2": {"fontSize": "小四"},
        "Body Text": {
            "firstLineIndentChars": 0,
            "paragraphSpacing": {"before": "0pt", "after": "0pt"},
            "alignment": "left",
        },
    }
    assert effective.pandoc_metadata["reference-section-title"] == "Final References"
    assert effective.pandoc_metadata["nested"] == {"base": True, "reply": True}


def test_empty_reply_metadata_does_not_reset_project_overrides(tmp_path: Path) -> None:
    """Treat reply metadata as an override layer rather than a fresh default set."""
    style = tmp_path / "style.yml"
    style.write_text(
        "pandocMetadata:\n  figureTitle: 'Fig. '\nreply:\n  pandocMetadata: {}\n",
        encoding="utf-8",
    )
    reply = tmp_path / "reply.md"
    reply.write_text("---\ntitle: Reply\n---\nBody\n", encoding="utf-8")

    effective = load_effective_metadata(reply, style, reply=True)

    assert effective.pandoc_metadata["figureTitle"] == "Fig. "
    assert effective.pandoc_metadata["tableTitle"] == "Table "


def test_legacy_flat_pandoc_metadata_warns_and_nested_value_wins(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Migrate legacy flat Pandoc keys in memory without rewriting the source."""
    style = tmp_path / "style.yml"
    original = "figureTitle: Legacy\ncustom: old\npandocMetadata:\n  figureTitle: New\n"
    style.write_text(original, encoding="utf-8")
    warnings: list[str] = []
    monkeypatch.setattr(metadata_module, "log_warning", warnings.append)

    settings = PmtSettings.load(style)

    assert settings.pandoc_metadata["figureTitle"] == "New"
    assert settings.pandoc_metadata["custom"] == "old"
    assert "linkReferences" not in settings.pandoc_metadata
    assert len(warnings) == 1
    assert "custom" in warnings[0]
    assert "figureTitle" in warnings[0]
    assert "Conflicts use pandocMetadata values: figureTitle" in warnings[0]
    assert style.read_text(encoding="utf-8") == original


def test_invalid_nested_sections_report_the_source_path(tmp_path: Path) -> None:
    """Reject non-mapping configuration sections at the centralized boundary."""
    style = tmp_path / "style.yml"
    style.write_text("pandocMetadata: invalid\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"style\.yml.*pandocMetadata.*mapping"):
        PmtSettings.load(style)


def test_invalid_known_field_reports_the_source_path(tmp_path: Path) -> None:
    """Include the style path when a typed Papper field fails validation."""
    style = tmp_path / "style.yml"
    style.write_text("mathtype: definitely\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"(?s)Invalid style settings.*style\.yml.*mathtype"):
        PmtSettings.load(style)


def test_non_mapping_style_root_reports_the_source_path(tmp_path: Path) -> None:
    """Reject sequence-valued style files before build consumers see them."""
    style = tmp_path / "style.yml"
    style.write_text("- mathtype\n- pandocMetadata\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"Invalid style settings.*style\.yml"):
        PmtSettings.load(style)


def test_generated_pandoc_yaml_preserves_unicode_and_excludes_pmt(tmp_path: Path) -> None:
    """Serialize only the explicitly supplied Pandoc mapping."""
    output = write_pandoc_metadata(
        {"标题": "示例", "items": ["一", "二"]},
        tmp_path / "work" / "pandoc.generated.yml",
    )

    written = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert written == {"标题": "示例", "items": ["一", "二"]}


def test_environment_variables_do_not_override_style_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep document builds deterministic when similarly named environment variables exist."""
    manuscript = tmp_path / "paper.md"
    manuscript.write_text("---\ntitle: Example\n---\nBody\n", encoding="utf-8")
    monkeypatch.setenv("MATHTYPE", "true")

    effective = load_effective_metadata(manuscript, style_path=None)

    assert effective.pmt_settings.mathtype is False


def test_pmt_settings_validate_nested_mappings_and_svg_controls() -> None:
    """Reject invalid Papper structures before any DOCX helper receives them."""
    with pytest.raises(ValueError, match="docxStyle"):
        PmtSettings.model_validate({"docxStyle": {"Body Text": "invalid"}})

    with pytest.raises(ValueError, match="docxPageMargins"):
        PmtSettings.model_validate({"docxPageMargins": ["2.54cm"]})

    with pytest.raises(ValueError, match="Only one of docxSvgToPngWidth"):
        PmtSettings.model_validate(
            {"docxSvgToPngWidth": 1600, "docxSvgToPngScale": 2}
        )


def test_legacy_body_text_is_migrated_at_the_settings_boundary(tmp_path: Path) -> None:
    """Convert legacy bodyText once instead of keeping alias logic in DOCX consumers."""
    style = tmp_path / "style.yml"
    style.write_text(
        "bodyText:\n  firstLineIndentChars: 2\n",
        encoding="utf-8",
    )

    settings = PmtSettings.load(style)

    assert settings.docx_style == {
        "正文文本": {"firstLineIndentChars": 2}
    }
    assert "bodyText" not in settings.pandoc_metadata


def test_docx_line_numbers_default_to_continuous_and_serialize_with_new_key() -> None:
    """Enable continuous DOCX line numbers by default under the renamed setting."""
    settings = PmtSettings.model_validate({})
    legacy = PmtSettings.model_validate({"show-line-numbers": "每页重编"})

    assert settings.docx_show_line_numbers == "continuous"
    assert settings.to_mapping()["docxShowLineNumbers"] == "continuous"
    assert legacy.docx_show_line_numbers == "每页重编"
    assert legacy.to_mapping()["docxShowLineNumbers"] == "每页重编"


def test_docx_page_numbers_are_optional_and_serialize_when_configured() -> None:
    """Keep reference-DOCX footers unchanged unless page-number metadata is set."""
    default = PmtSettings.model_validate({})
    enabled = PmtSettings.model_validate({"show-page-numbers": True})

    assert default.docx_show_page_numbers is None
    assert "docxShowPageNumbers" not in default.to_mapping()
    assert enabled.docx_show_page_numbers is True
    assert enabled.to_mapping()["docxShowPageNumbers"] is True


def test_legacy_pandoc_citation_delimiter_moves_to_pmt_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Remove the old custom-filter option from generated Pandoc metadata."""
    style = tmp_path / "style.yml"
    style.write_text(
        "pandocMetadata:\n  citation-number-range-delimiter: '-'\n  figureTitle: Figure\n",
        encoding="utf-8",
    )
    warnings: list[str] = []
    monkeypatch.setattr(metadata_module, "log_warning", warnings.append)

    settings = PmtSettings.load(style)

    assert settings.citation_number_range_delimiter == "-"
    assert settings.pandoc_metadata["figureTitle"] == "Figure"
    assert "citation-number-range-delimiter" not in settings.pandoc_metadata
    assert len(warnings) == 1
    assert "citationNumberRangeDelimiter" in warnings[0]


def test_manuscript_citation_delimiter_no_longer_enters_pandoc_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the retired manuscript override out of generated Pandoc metadata."""
    manuscript = tmp_path / "paper.md"
    manuscript.write_text(
        "---\ncitation-number-range-delimiter: '-'\ntitle: Example\n---\nBody\n",
        encoding="utf-8",
    )
    warnings: list[str] = []
    monkeypatch.setattr(metadata_module, "log_warning", warnings.append)

    effective = load_effective_metadata(manuscript, style_path=None)

    assert effective.pandoc_metadata["title"] == "Example"
    assert "citation-number-range-delimiter" not in effective.pandoc_metadata
    assert len(warnings) == 1
    assert "citationNumberRangeDelimiter" in warnings[0]
