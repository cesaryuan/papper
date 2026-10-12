"""Verify custom footer output, dynamic page fields, and configuration precedence.

Crafted references cover first/even footers and authored content that the fixed
manuscript snapshots cannot exercise; output assertions protect data preservation.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest
from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml.ns import qn
from lxml import etree

from native_support import ROOT
from test_rust_docx_contract import rust_postprocessor


def process_footer(source: Path, output: Path, executable: Path, config: dict | None) -> None:
    """Run the native editor with an explicit footer setting and report conversion failures."""
    metadata = output.with_suffix(".json")
    metadata.write_text(json.dumps({
        "pmt_settings": {"values": {"docxFooter": config}, "provided": ["docxFooter"],
                         "pandoc_metadata": {}, "reply": None},
        "pandoc_metadata": {}, "has_yaml_header": False,
    }), encoding="utf-8")
    result = subprocess.run([str(executable), str(source), str(output), str(metadata)],
                            capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("alignment", ["left", "center", "right"])
def test_custom_footer_replaces_variants_and_remains_dynamic(
    tmp_path: Path, rust_postprocessor: Path, alignment: str,
) -> None:
    """Replace first/even footers, create the missing primary, and avoid duplicate fields on reuse."""
    document = Document()
    document.styles.add_style("page number", WD_STYLE_TYPE.CHARACTER)
    document.settings.odd_and_even_pages_header_footer = True
    section = document.sections[0]
    section.different_first_page_header_footer = True
    # Access only first/even parts: ordinary pages initially have no primary footer.
    section.first_page_footer.paragraphs[0].text = "Old first page"
    section.even_page_footer.paragraphs[0].text = "Old even page"
    section.header.paragraphs[0].text = "Preserved header"
    document.add_section()
    source = tmp_path / "source.docx"
    document.save(source)
    config = {"content": '审稿 & <draft> - []{t="pageNum"} - / []{ t = \'pageNum\' }',
              "alignment": alignment}
    for index in range(2):
        output = tmp_path / f"output-{index}.docx"
        process_footer(source, output, rust_postprocessor, config)
        document = Document(output)
        for section in document.sections:
            for footer in (section.footer, section.first_page_footer, section.even_page_footer):
                assert len(footer.paragraphs) == 1
                paragraph = footer.paragraphs[0]
                assert paragraph.text == "审稿 & <draft> - 1 - / 1"
                assert paragraph._p.find(qn("w:pPr")).find(qn("w:jc")).get(qn("w:val")) == alignment
                assert [field.text for field in paragraph._p.iter(qn("w:instrText"))] == [" PAGE ", " PAGE "]
                assert [field.get(qn("w:fldCharType")) for field in paragraph._p.iter(qn("w:fldChar"))] == [
                    "begin", "separate", "end", "begin", "separate", "end",
                ]
            assert section.header.paragraphs[0].text == "Preserved header"
            assert section.different_first_page_header_footer
        assert document.settings.odd_and_even_pages_header_footer
        source = output


@pytest.mark.parametrize("content", [None, "", "Confidential & internal\nSecond line\tRight"])
def test_footer_preserve_clear_and_plain_text_without_page_style(
    tmp_path: Path, rust_postprocessor: Path, content: str | None,
) -> None:
    """Preserve authored content with null, clear all content explicitly, and render plain multiline text."""
    document = Document()
    document.sections[0].footer.paragraphs[0].text = "Authored footer"
    document.sections[0].footer.add_paragraph("Second paragraph")
    source = tmp_path / "source.docx"
    document.save(source)
    original = etree.tostring(document.sections[0].footer._element, method="c14n")
    output = tmp_path / "output.docx"
    process_footer(source, output, rust_postprocessor, None if content is None else {"content": content})
    footer = Document(output).sections[0].footer
    if content is None:
        assert etree.tostring(footer._element, method="c14n") == original
    else:
        assert len(footer.paragraphs) == 1
        assert footer.paragraphs[0].text == content
        assert not list(footer._element.iter(qn("w:instrText")))


@pytest.mark.parametrize(("project", "inline", "expected", "alignment"), [
    ('docxFooter: {content: \'- []{t="pageNum"} -\', alignment: left}',
     'docxFooter: {alignment: right}', "- 1 -", "right"),
    ('docxShowPageNumbers: false',
     'docxFooter: {content: \'- []{t="pageNum"} -\'}', "- 1 -", "center"),
    ('docxFooter: {content: \'- []{t="pageNum"} -\'}',
     'docxShowPageNumbers: false', "Authored footer", None),
    ('docxFooter: {content: \'- []{t="pageNum"} -\'}',
     'docxFooter: null', "Authored footer", None),
    ('docxShowPageNumbers: true\ndocxFooter: null',
     '', "Authored footer", None),
])
def test_footer_build_obeys_source_priority(
    tmp_path: Path, rust_executable: Path, project: str, inline: str,
    expected: str, alignment: str | None,
) -> None:
    """Verify actual build output when new, legacy, and null values cross source layers."""
    reference = tmp_path / "reference.docx"
    document = Document(ROOT / "pandoc/manuscript-template/reference-doc.docx")
    document.sections[0].footer.paragraphs[0].text = "Authored footer"
    document.save(reference)
    (tmp_path / "style.yml").write_text(project + "\n", encoding="utf-8")
    source = tmp_path / "paper.md"
    settings = "\n".join("  " + line for line in inline.splitlines())
    source.write_text(f"---\npapperSettings:\n{settings}\n---\nBody\n" if inline else "Body\n", encoding="utf-8")
    output = tmp_path / "output.docx"
    result = subprocess.run([
        str(rust_executable), "build", "docx", str(source), "--reference-doc", str(reference),
        "--no-mathtype", "-o", str(output),
    ], cwd=tmp_path, env={**os.environ, "PAPPER_RESOURCE_ROOT": str(ROOT)},
        capture_output=True, text=True, encoding="utf-8", timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    paragraph = Document(output).sections[0].footer.paragraphs[0]
    assert paragraph.text == expected
    assert len(list(paragraph._p.iter(qn("w:instrText")))) == (1 if expected == "- 1 -" else 0)
    if alignment is not None:
        assert paragraph._p.find(qn("w:pPr")).find(qn("w:jc")).get(qn("w:val")) == alignment
