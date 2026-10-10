from pathlib import Path
import shutil
import subprocess
import zipfile

import pytest
from docx import Document
from docx.oxml.ns import qn

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
