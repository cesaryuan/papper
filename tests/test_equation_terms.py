"""Check real equation-term layouts and reject malformed blocks without replacing output.

Content snapshots cover rich text and cross-references. These checks additionally
protect rendered cell alignment, editable Word math, style overrides, and failed
build data preservation, which content snapshots alone cannot establish.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
from zipfile import ZipFile

from lxml import etree, html
import pytest

from native_support import ROOT


def build(executable: Path, source: Path, output: Path) -> subprocess.CompletedProcess:
    """Run the public CLI against current repository resources in an isolated project."""
    return subprocess.run(
        [str(executable), "build", output.suffix[1:], str(source), "-o", str(output),
         *(["--no-mathtype"] if output.suffix == ".docx" else [])],
        cwd=output.parent, env={**os.environ, "PAPPER_RESOURCE_ROOT": str(ROOT)},
        capture_output=True, text=True, encoding="utf-8", timeout=90,
    )


@pytest.mark.parametrize("target", ["html", "docx", "latex"])
def test_equation_terms_output(
    tmp_path: Path, rust_executable: Path, target: str,
) -> None:
    """Catch lost symbols/formatting, unwanted table numbering, and global-autofit leaks."""
    source = ROOT / "tests/snapshot_cases/equation_terms/equation_terms.md"
    (tmp_path / "style.yml").write_text(
        "mathtype: false\ntableAutofit: content\n"
        "docxStyle:\n  式中:\n    fontSize: 11pt\n    lineSpacing: 1.5\n",
        encoding="utf-8",
    )
    output = tmp_path / f"paper.{target}"
    result = build(rust_executable, source, output)
    assert result.returncode == 0, result.stdout + result.stderr
    if target == "latex":
        text = output.read_text(encoding="utf-8")
        assert text.count("式中") == 3  # Two explicit blocks and the ordinary paragraph.
        assert text.count(r"\begin{description}") == 3  # Includes the unmarked list.
        assert "软掩码连续权重矩阵" in text
        assert r"\mathrm{GaussianBlur}" in text
        assert "A single-entry block" in text
        return
    if target == "html":
        document = html.fromstring(output.read_text(encoding="utf-8"))
        tables = document.xpath("//div[contains(@class,'equation-terms')]/table")
        assert [len(table.xpath("./tbody/tr")) for table in tables] == [4, 1]
        for table in tables:
            assert not table.xpath("./caption | ./thead")
            assert table.get("data-autofit") == "window"
            assert ["".join(cell.itertext()).strip() for cell in table.xpath("./tbody/tr/td[1]")].count("式中") == 1
        assert tables[0].xpath(".//strong[normalize-space(.)='Kernel size']")
        assert tables[0].xpath(".//em[text()='emphasis']")
        assert len(tables[0].xpath(".//ul/li")) == 2
        assert document.xpath("//dl/dt[normalize-space(.)='Ordinary term']")
        caption = document.xpath("//table[@id='tbl:ordinary']/caption")[0]
        assert " ".join(caption.itertext()).split()[:2] == ["Table", "1"]
        assert document.xpath("//table[@id='tbl:ordinary']")[0].get("data-autofit") == "content"
        return
    namespace = {
        "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
        "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    }
    with ZipFile(output) as package:
        document = etree.fromstring(package.read("word/document.xml"))
        styles = etree.fromstring(package.read("word/styles.xml"))
    terms_style = styles.xpath("//w:style[w:name/@w:val='式中']", namespaces=namespace)[0]
    style_id = terms_style.get(f"{{{namespace['w']}}}styleId")
    assert terms_style.xpath("./w:rPr/w:sz/@w:val", namespaces=namespace) == ["22"]
    assert terms_style.xpath("./w:pPr/w:spacing/@w:line", namespaces=namespace) == ["360"]
    tables = document.xpath("//w:tbl[w:tblPr/w:tblStyle/@w:val='TableNoBorder']", namespaces=namespace)
    assert [len(table.xpath("./w:tr", namespaces=namespace)) for table in tables] == [4, 1]
    for table in tables:
        assert table.xpath("./w:tblPr/w:tblLayout/@w:type", namespaces=namespace) == ["autofit"]
        for row in table.xpath("./w:tr", namespaces=namespace):
            cells = row.xpath("./w:tc", namespaces=namespace)
            assert len(cells) == 4
            assert "".join(cells[2].xpath(".//w:t/text()", namespaces=namespace)) == "——"
            for cell, alignment in zip(cells, ["left", "right", "center", "left"], strict=True):
                if not cell.xpath(".//w:t | .//m:oMath", namespaces=namespace):
                    continue
                assert cell.xpath("./w:p[1]/w:pPr/w:jc/@w:val", namespaces=namespace) == [alignment]
                assert cell.xpath("./w:p[1]/w:pPr/w:pStyle/@w:val", namespaces=namespace) == [style_id]
    assert tables[0].xpath(".//m:oMath", namespaces=namespace)
    assert tables[0].xpath(".//w:r[w:t='Kernel size']/w:rPr/w:b", namespaces=namespace)
    assert document.xpath("//w:t[text()='Table 1 Ordinary numbered table.']", namespaces=namespace)


@pytest.mark.parametrize("target", ["html", "docx"])
@pytest.mark.parametrize("body", [
    "A paragraph instead of a definition list.",
    "",
    "$x$\n:",
    "$x$\n: First description.\n: Second description.",
])
def test_invalid_equation_terms_preserve_output(
    tmp_path: Path, rust_executable: Path, target: str, body: str,
) -> None:
    """Invalid explicit syntax must fail visibly and preserve an existing user artifact."""
    source = tmp_path / "invalid.md"
    source.write_text(f"::: {{.equation-terms}}\n{body}\n:::\n", encoding="utf-8")
    output = tmp_path / f"existing.{target}"
    output.write_bytes(b"Existing user document")
    result = build(rust_executable, source, output)
    assert result.returncode != 0
    assert "equation-terms:" in result.stdout + result.stderr
    assert output.read_bytes() == b"Existing user document"
