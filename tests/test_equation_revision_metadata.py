from pathlib import Path
import shutil
import subprocess
import zipfile
import json

import pytest
from docx import Document
from docx.oxml.ns import qn

from test_rust_docx_contract import rust_postprocessor


def test_equation_revision_attr_filter_wraps_display_equation_and_keeps_label() -> None:
    """Preserve the equation label while extracting revision=true before crossref."""
    pandoc = shutil.which("pandoc")
    if pandoc is None:
        pytest.skip("pandoc is not installed")

    filter_path = Path(__file__).resolve().parents[1] / "pandoc" / "filters" / "docx" / "equation_revision_attr.lua"
    markdown = """\
$$
a+b
$$ {#eq:sum revision=true}
"""

    result = subprocess.run(
        [pandoc, "--lua-filter", str(filter_path), "-f", "markdown", "-t", "native"],
        input=markdown,
        text=True,
        capture_output=True,
        check=True,
    )

    assert '( "revision" , "true" )' in result.stdout
    assert "{#eq:sum}" in result.stdout


def test_docx_metadata_filter_emits_equation_revision_marker(tmp_path) -> None:
    """Export revised display equations through the shared DOCX metadata filter."""
    pandoc = shutil.which("pandoc")
    if pandoc is None:
        pytest.skip("pandoc is not installed")

    filter_path = Path(__file__).resolve().parents[1] / "pandoc" / "filters" / "docx" / "docx_metadata.lua"
    output_path = tmp_path / "equation-metadata.docx"
    markdown = """\
::: {revision=true}
$$
a+b
$$
:::
"""

    subprocess.run(
        [pandoc, "--lua-filter", str(filter_path), "-f", "markdown", "-o", str(output_path)],
        input=markdown,
        text=True,
        capture_output=True,
        check=True,
    )

    with zipfile.ZipFile(output_path) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")

    assert "PMT_EQUATION_METADATA:" in document_xml


def test_mathtype_marker_filter_preserves_inline_and_display_context(tmp_path, monkeypatch) -> None:
    """Mark abstract and body math without interpreting crossref metadata templates."""
    pandoc = shutil.which("pandoc")
    if pandoc is None:
        pytest.skip("pandoc is not installed")

    filter_path = Path(__file__).resolve().parents[1] / "pandoc" / "filters" / "docx" / "mathtype_markers.lua"
    output_path = tmp_path / "mathtype-markers.docx"
    markdown = """\
---
abstract: |
  Abstract equation $E=mc^2$.
eqnBlockTemplate: "$$t$$"
---

Inline $x_i$.

$$
\\frac{1}{2}
$$
"""
    monkeypatch.setenv("PMT_ENABLE_MATHTYPE_MARKERS", "true")

    subprocess.run(
        [pandoc, "--lua-filter", str(filter_path), "-f", "markdown", "-o", str(output_path)],
        input=markdown,
        text=True,
        capture_output=True,
        check=True,
    )

    with zipfile.ZipFile(output_path) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")

    assert "MTLATEX:inline:E=mc^2" in document_xml
    assert "MTLATEX:inline:x_i" in document_xml
    # Pandoc versions retain different surrounding whitespace for display math;
    # the marked TeX and its display context are the behavior being verified.
    markers = [node.text or "" for node in Document(output_path).element.iter(qn("w:t"))]
    assert any(marker.startswith("MTLATEX:display:")
               and marker.removeprefix("MTLATEX:display:").strip() == r"\frac{1}{2}" for marker in markers)
    assert "MTLATEX:display:t" not in document_xml


def test_native_equation_metadata_colors_word_display_equations(tmp_path, rust_postprocessor) -> None:
    """Color native Word display-equation runs red when the hidden marker is present."""
    pandoc = shutil.which("pandoc")
    if pandoc is None:
        pytest.skip("pandoc is not installed")

    markdown_path = tmp_path / "equation.md"
    markdown_path.write_text("$$\na+b\n$$\n", encoding="utf-8")
    docx_path = tmp_path / "equation.docx"

    subprocess.run(
        [pandoc, str(markdown_path), "-o", str(docx_path)],
        text=True,
        capture_output=True,
        check=True,
    )

    doc = Document(str(docx_path))
    equation_paragraph = next(
        paragraph for paragraph in doc.paragraphs if paragraph._p.findall(f".//{qn('m:oMathPara')}")
    )
    equation_paragraph.insert_paragraph_before('PMT_EQUATION_METADATA:{"revision":"true"}')

    doc.save(docx_path)
    metadata = tmp_path / "metadata.json"
    metadata.write_text(json.dumps({"pmt_settings": {"values": {}, "provided": [],
                         "pandoc_metadata": {}, "reply": None}, "pandoc_metadata": {},
                         "has_yaml_header": False}), encoding="utf-8")
    output = tmp_path / "processed.docx"
    result = subprocess.run([str(rust_postprocessor), str(docx_path), str(output), str(metadata)],
                            capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    doc = Document(output)
    equation_paragraph = next(p for p in doc.paragraphs if p._p.findall(f".//{qn('m:oMathPara')}"))
    assert all("PMT_EQUATION_METADATA:" not in paragraph.text for paragraph in doc.paragraphs)

    math_runs = equation_paragraph._p.findall(f".//{qn('m:r')}")
    assert math_runs
    for math_run in math_runs:
        # OMML's Word formatting is a direct child of m:r; nesting it in
        # m:rPr creates an unsupported property that Word can discard.
        word_rpr = math_run.find(qn("w:rPr"))
        assert word_rpr is not None
        color = word_rpr.find(qn("w:color"))
        assert color is not None
        assert color.get(qn("w:val")) == "FF0000"
