"""Compare actual native DOCX builds to the existing complete OPC snapshots.

The fixtures cover user-visible document text, numbering, layout, author
footnotes, revision attributes, style metadata, and image resources. The Rust
command runs directly; Python only canonicalizes artifacts for verification.
"""

from __future__ import annotations

import os
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from snapshot_utils import assert_snapshot, canonical_docx
from test_build_snapshots import CASES, ROOT, SNAPSHOT_ROOT
from test_rust_cli_contract import rust_executable


@pytest.fixture(scope="module")
def rust_postprocessor() -> Path:
    """Build the native standalone package editor for crafted-document contracts."""
    result = subprocess.run(["cargo", "build", "--offline", "-p", "papper-document", "--example", "postprocess_docx"],
                            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    return ROOT / "target/debug/examples" / ("postprocess_docx.exe" if os.name == "nt" else "postprocess_docx")


@pytest.fixture(scope="module")
def rust_mathtype_converter() -> Path:
    """Build the package converter for native OLE, layout, and cache behavior checks."""
    result = subprocess.run(["cargo", "build", "--offline", "-p", "papper-document", "--example", "convert_mathtype_docx"],
                            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    return ROOT / "target/debug/examples" / ("convert_mathtype_docx.exe" if os.name == "nt" else "convert_mathtype_docx")


@pytest.mark.parametrize("case_name", CASES)
def test_native_docx_matches_existing_snapshot(case_name: str, tmp_path: Path, rust_executable: Path) -> None:
    """Require full package parity for native Word equations and retained Lua filters."""
    case_dir, markdown = CASES[case_name]
    if case_name == "chinese_crossrefs":
        copied = tmp_path / case_name
        shutil.copytree(case_dir, copied)
        case_dir = copied
    output = tmp_path / f"{case_name}.docx"
    command = [str(rust_executable), "build", "docx", "-m", str(case_dir / markdown),
               "-o", str(output), "--no-mathtype"]
    if case_name == "native_crossrefs":
        command.extend(["--style-file", str(case_dir / "style.yml")])
    result = subprocess.run(command, cwd=tmp_path, env={**os.environ, "PAPPER_RESOURCE_ROOT": str(ROOT)},
                            capture_output=True, text=True, encoding="utf-8", timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    actual = canonical_docx(output, repository_root=ROOT, project_dir=tmp_path,
                            normalize_native_crossrefs=case_name == "native_crossrefs")
    assert_snapshot(actual, SNAPSHOT_ROOT / case_name / "docx.snap", update=False)


def test_native_package_editor_preserves_unknown_parts_and_cancels_inherited_equation_tabs(
    tmp_path: Path, rust_postprocessor: Path,
) -> None:
    """Preserve embedded data and style precedence in a document with custom equation tabs."""
    from docx import Document
    from docx.enum.text import WD_TAB_ALIGNMENT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Inches, Pt
    from lxml import etree
    from zipfile import ZipFile, ZIP_DEFLATED

    document = Document()
    document.sections[0].page_width = Inches(8.5)
    document.sections[0].left_margin = Inches(1)
    document.sections[0].right_margin = Inches(1.5)
    document.styles["Body Text"].paragraph_format.tab_stops.add_tab_stop(Inches(1))
    equation = document.add_paragraph()
    equation.add_run("\t")
    math = OxmlElement("m:oMath")
    math_run = OxmlElement("m:r")
    math_text = OxmlElement("m:t")
    math_text.text = "x=1"
    math_run.append(math_text)
    math.append(math_run)
    equation._p.append(math)
    equation.add_run("\t(1)")
    equation.paragraph_format.tab_stops.add_tab_stop(Inches(2), WD_TAB_ALIGNMENT.CENTER)
    equation.paragraph_format.space_before = Pt(3)
    equation.paragraph_format.space_after = Pt(18)
    equation.paragraph_format.line_spacing = 2
    source = tmp_path / "source.docx"
    document.save(source)
    payload = bytes(range(256)) * 3
    with ZipFile(source, "a", ZIP_DEFLATED) as archive:
        archive.writestr("customXml/preserved-user-data.bin", payload)
    metadata = tmp_path / "metadata.json"
    metadata.write_text(json.dumps({"pmt_settings": {"values": {}, "provided": [], "pandoc_metadata": {}, "reply": None},
                                   "pandoc_metadata": {}, "has_yaml_header": False}), encoding="utf-8")
    target = tmp_path / "target.docx"
    result = subprocess.run([str(rust_postprocessor), str(source), str(target), str(metadata)],
                            capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    with ZipFile(target) as archive:
        assert archive.read("customXml/preserved-user-data.bin") == payload
    reopened = Document(target)
    paragraph = reopened.paragraphs[0]
    assert paragraph.style.name == "Para Equation"
    assert paragraph.style.base_style.name == "Body Text"
    assert paragraph.paragraph_format.space_before.pt == 3
    assert paragraph._p.pPr.find(qn("w:tabs")) is None
    assert dict(paragraph._p.pPr.find(qn("w:spacing")).attrib) == {qn("w:before"): "60"}
    assert [(tab.position.inches, tab.alignment) for tab in paragraph.style.paragraph_format.tab_stops] == [
        (1, WD_TAB_ALIGNMENT.CLEAR), (3, WD_TAB_ALIGNMENT.CENTER), (6, WD_TAB_ALIGNMENT.RIGHT),
    ]
    assert reopened.styles["Body Text"].paragraph_format.tab_stops[0].position.inches == 1
    # Canonical XML compares the formula itself without depending on inherited
    # namespace declaration order chosen by the package serializer.
    assert etree.tostring(paragraph._p.find(qn("m:oMath")), method="c14n", exclusive=True) == etree.tostring(
        math, method="c14n", exclusive=True,
    )


def test_native_mathtype_preserves_failed_formulas_layout_and_invalidates_corrupt_cache(
    tmp_path: Path, rust_mathtype_converter: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep unsupported OMML and attachments while matching real OLE/WMF and recovering bad caches."""
    from docx import Document
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt
    from lxml import etree
    from zipfile import ZipFile, ZIP_DEFLATED
    from pandoc_manuscript.mathtype import marked_docx, ole_parts

    document = Document()
    formulas = [("inline", "x_1"), ("display", r"\frac{"), ("display", "y+2"), ("inline", "x_1")]
    for style, latex in formulas:
        paragraph = document.add_paragraph()
        marker = paragraph.add_run(f"MTLATEX:{style}:{latex}")
        marker.font.hidden = True
        marker.font.size = Pt(10.5)
        math = OxmlElement("m:oMath")
        math_run = OxmlElement("m:r")
        text = OxmlElement("m:t")
        text.text = latex
        math_run.append(text)
        math.append(math_run)
        paragraph._p.append(math)
    source = tmp_path / "marked.docx"
    document.save(source)
    payload = b"Unrelated custom data must survive native equation conversion\x00\xff"
    with ZipFile(source, "a", ZIP_DEFLATED) as archive:
        archive.writestr("customXml/attachment.bin", payload)
    metadata = tmp_path / "metadata.json"
    metadata.write_text(json.dumps({"pmt_settings": {"values": {"mathtypeConversionMethod": "rust", "mathtypeSvgBackend": "typst", "mathtypeTypstMathFont": "XITS Math"},
                                                   "provided": [], "pandoc_metadata": {}, "reply": None},
                                    "pandoc_metadata": {}, "has_yaml_header": False}), encoding="utf-8")
    target = tmp_path / "native.docx"
    environment = {**os.environ, "PAPPER_RESOURCE_ROOT": str(ROOT), "PAPPER_HOME": str(tmp_path / "home")}

    def convert() -> dict:
        """Build through the native runner and return its artifact-level conversion report."""
        result = subprocess.run([str(rust_mathtype_converter), str(source), str(target), str(metadata), str(tmp_path)],
                                env=environment, capture_output=True, text=True, encoding="utf-8", timeout=90)
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout.strip())

    first = convert()
    assert first == {"total": 4, "converted": 3, "failures": 1, "cache_hits": 1}
    monkeypatch.setattr(ole_parts, "mathtype_cache_dir", lambda: tmp_path / "python-cache")
    # Use the real existing DLL at the external native boundary. The Python
    # source-loader otherwise recompiles it mid-test and correctly invalidates
    # Rust's cache fingerprint, obscuring the cache-reuse contract under test.
    converter = ole_parts.native.NativeConverter("mathtype-rust", ole_parts.native.library_path("mathtype-rust"))
    monkeypatch.setattr(ole_parts.native, "get_converter", lambda project: converter)
    requests = marked_docx.extract_marked_equation_requests(source)
    expected = ole_parts.generate_equation_parts(requests, tmp_path / "python", conversion_method="rust", svg_backend="typst")
    assert expected[1] is None
    with ZipFile(target) as archive:
        assert archive.read("customXml/attachment.bin") == payload
        xml = etree.fromstring(archive.read("word/document.xml"))
        assert b"MTLATEX:" not in archive.read("word/document.xml")
        ns = marked_docx.NS
        assert xml.xpath("count(.//o:OLEObject)", namespaces=ns) == 3
        assert xml.xpath(".//m:oMath//m:t/text()", namespaces=ns) == [r"\frac{"]
        for index in (0, 2, 3):
            equation = expected[index]
            assert equation is not None
            assert archive.read(f"word/embeddings/mathtype_formula_{index + 1}.bin") == equation.ole_path.read_bytes()
            assert archive.read(f"word/media/mathtype_formula_{index + 1}.wmf") == equation.wmf_path.read_bytes()
        # A display formula must not inherit inline baseline positioning.
        display = xml.xpath(".//w:r[o:OLEObject or w:object/o:OLEObject[@ShapeID='_x0000_i3003']]", namespaces=ns)
        assert display and not display[0].xpath("w:rPr/w:position", namespaces=ns)
        inline = xml.xpath(".//w:r[w:object/o:OLEObject[@ShapeID='_x0000_i3001']]/w:rPr/w:position/@w:val", namespaces=ns)
        assert inline == [str(-round(expected[0].baseline_from_bottom_pt * 2))]
    assert convert()["cache_hits"] == 3
    caches = list((tmp_path / "home").rglob("equation.ole.bin"))
    assert len(caches) == 2
    for cached in caches:
        cached.write_bytes(b"broken cache must never enter a document")
    recovered = convert()
    assert recovered == first
    with ZipFile(target) as archive:
        assert archive.read("word/embeddings/mathtype_formula_1.bin") == expected[0].ole_path.read_bytes()


def test_native_docx_rejects_unrendered_reference_syntax_without_replacing_existing_output(
    tmp_path: Path, rust_postprocessor: Path,
) -> None:
    """Reject broken visible references in body and footer while retaining an existing result."""
    from docx import Document

    metadata = tmp_path / "metadata.json"
    metadata.write_text(json.dumps({"pmt_settings": {"values": {}, "provided": [], "pandoc_metadata": {}, "reply": None},
                                   "pandoc_metadata": {}, "has_yaml_header": False}), encoding="utf-8")
    output = tmp_path / "previous.docx"
    original = b"previous successful output"
    for text in ["Equation (Equation 6)", "Table 3 Table 3", "[@citation]", "::: {.note}", "Figure 2.1 Figure 2.1"]:
        document = Document()
        if text.startswith("Figure"):
            document.add_paragraph("A valid body")
            document.sections[0].footer.paragraphs[0].text = text
        else:
            document.add_paragraph(text)
        source = tmp_path / "broken.docx"
        document.save(source)
        output.write_bytes(original)
        result = subprocess.run([str(rust_postprocessor), str(source), str(output), str(metadata)],
                                capture_output=True, text=True, encoding="utf-8", timeout=30)
        assert result.returncode != 0, text
        assert "unrendered Pandoc syntax" in result.stderr
        assert output.read_bytes() == original
    # Formula TeX is an intermediate hidden marker, not final visible text; it
    # can legally contain strings that resemble broken prose references.
    document = Document()
    paragraph = document.add_paragraph("A valid formula follows")
    hidden = paragraph.add_run(r"MTLATEX:inline:\text{Figure Figure}")
    hidden.font.hidden = True
    source = tmp_path / "marker.docx"
    document.save(source)
    result = subprocess.run([str(rust_postprocessor), str(source), str(output), str(metadata)],
                            capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert Document(output).paragraphs[0].text.startswith("A valid formula follows")
