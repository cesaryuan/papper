from pathlib import Path
import sys
import tempfile

import pytest
from docx import Document

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pandoc_manuscript.commands import build
from pandoc_manuscript.commands.build_reply import output as reply_output
from pandoc_manuscript.docx import build as docx_build
from pandoc_manuscript.docx.page_margins import (
    apply_page_margin_settings,
)
from pandoc_manuscript.runtime.metadata import PmtSettings


def test_apply_page_margin_settings_updates_all_docx_sections() -> None:
    """Apply docxPageMargins values to every generated DOCX section."""
    doc = Document()
    doc.add_section()

    result = apply_page_margin_settings(
        doc,
        PmtSettings.model_validate({
            "docxPageMargins": {
                "top": "2.54cm",
                "bottom": "2.54cm",
                "left": "3.17cm",
                "right": "3.17cm",
            }
        }),
    )

    assert result == {
        "margins": {
            "top": "2.54cm",
            "bottom": "2.54cm",
            "left": "3.17cm",
            "right": "3.17cm",
        },
        "sections": 2,
    }
    for section in doc.sections:
        assert section.top_margin.cm == pytest.approx(2.54, abs=0.001)
        assert section.bottom_margin.cm == pytest.approx(2.54, abs=0.001)
        assert section.left_margin.cm == pytest.approx(3.17, abs=0.001)
        assert section.right_margin.cm == pytest.approx(3.17, abs=0.001)


def test_build_reference_doc_args_uses_margin_adjusted_reference(tmp_path: Path, monkeypatch) -> None:
    """Pass Pandoc a generated reference DOCX when docxPageMargins is configured."""
    source = tmp_path / "reference.docx"
    Document().save(source)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(build.SETTINGS, "reference_doc", str(source))
    monkeypatch.setattr(build.SETTINGS, "project_name", "paper")

    args = docx_build.docx_reference_doc_args(
        build.SETTINGS,
        PmtSettings.model_validate(
            {"docxPageMargins": {"left": "3.17cm", "right": "3.17cm"}}
        ),
        build.resource_path,
        build.to_pandoc_path,
    )

    assert args[0] == "--reference-doc"
    assert Path(args[1]).is_relative_to(Path(tempfile.gettempdir()))
    assert Path(args[1]).parent.name == "reference-doc"
    section = Document(args[1]).sections[0]
    assert section.left_margin.cm == pytest.approx(3.17, abs=0.001)
    assert section.right_margin.cm == pytest.approx(3.17, abs=0.001)


def test_reply_reference_doc_for_pandoc_uses_margin_adjusted_reference(tmp_path: Path, monkeypatch) -> None:
    """Prepare a generated reply reference DOCX when docxPageMargins is configured."""
    source = tmp_path / "reference.docx"
    output = tmp_path / "reply.docx"
    Document().save(source)
    monkeypatch.chdir(tmp_path)

    reference = reply_output.reply_reference_doc_for_pandoc(
        source,
        output,
        PmtSettings.model_validate(
            {"docxPageMargins": {"left": "3.17cm", "right": "3.17cm"}}
        ),
    )

    assert reference.is_relative_to(Path(tempfile.gettempdir()))
    assert reference.parent.name == "reference-doc"
    section = Document(str(reference)).sections[0]
    assert section.left_margin.cm == pytest.approx(3.17, abs=0.001)
    assert section.right_margin.cm == pytest.approx(3.17, abs=0.001)
