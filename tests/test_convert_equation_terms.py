"""Exercise thesis term-table import through the public CLI and real DOCX reader.

Word vertical merges and native equations cannot be represented by pipe-table
fixtures. These imports verify row pairing, rich descriptions, ordinary table
retention, and recovery of Papper's own exported layout.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

from docx import Document
from docx.oxml import OxmlElement
from lxml import html
import pytest

from native_support import ROOT
from snapshot_utils import assert_snapshot


def convert(executable: Path, source: Path, output: Path) -> str:
    """Import a DOCX with repository resources and read the published Markdown."""
    result = subprocess.run(
        [str(executable), "convert", str(source), "-o", str(output), "--offline"],
        cwd=output.parent,
        env={**os.environ, "PAPPER_RESOURCE_ROOT": str(ROOT), "PAPPER_HOME": str(output.parent / "home")},
        capture_output=True, text=True, encoding="utf-8", timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return (output / f"{source.stem}.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("layout", [
    "four-columns", "merged-label", "repeated-label",
    "combined-description", "external-label", "external-combined",
])
def test_convert_word_term_layouts(
    layout: str, tmp_path: Path, rust_executable: Path, snapshot_update: bool,
) -> None:
    """Recover Word-specific layouts without losing symbols or rich description blocks."""
    document = Document()
    document.add_paragraph("Before the terms.")
    external = layout.startswith("external")
    combined = layout in {"combined-description", "external-combined"}
    if external:
        document.add_paragraph("式中：")
    columns = 2 + (not external) + (not combined)
    table = document.add_table(rows=2, cols=columns)
    for index, symbol in enumerate(["x", "σ"]):
        offset = 0 if external else 1
        if not external:
            table.cell(index, 0).text = "式中" if index == 0 or layout == "repeated-label" else ""
        # An actual Word OMML formula verifies that math remains editable inline
        # syntax rather than becoming plain text or a separate display equation.
        math = OxmlElement("m:oMath")
        run, text = OxmlElement("m:r"), OxmlElement("m:t")
        text.text = symbol
        run.append(text)
        math.append(run)
        display = OxmlElement("m:oMathPara")
        display.append(math)
        table.cell(index, offset).paragraphs[0]._p.append(display)
        if not combined:
            table.cell(index, offset + 1).text = "——"
        description = table.cell(index, columns - 1)
        paragraph = description.paragraphs[0]
        if combined:
            paragraph.add_run("——").bold = True
            paragraph.add_run(" ")
        paragraph.add_run("Position" if index == 0 else "Standard deviation").bold = True
        paragraph.add_run("；" if index == 0 else "。")
        if index == 0:
            description.add_paragraph("A second paragraph with ").add_run("emphasis").italic = True
            description.add_paragraph("A list item.", style="List Bullet")
            paragraph.add_run().add_picture(str(ROOT / "template/examples/images/single-figure-example.png"))
    if layout == "merged-label":
        table.cell(0, 0).merge(table.cell(1, 0))
    document.add_paragraph("After the terms.")
    source = tmp_path / "terms.docx"
    document.save(source)
    markdown = convert(rust_executable, source, tmp_path / "converted")
    assert markdown.count("::: equation-terms") == 1
    assert "$x$" in markdown and r"$\sigma$" in markdown
    assert "**Position**" in markdown and "**Standard deviation**" in markdown
    assert "*emphasis*" in markdown and "- A list item." in markdown
    assert "![](media/" in markdown
    assert "——" not in markdown and "式中" not in markdown
    assert markdown.index("Before the terms.") < markdown.index("$x$") < markdown.index("After the terms.")
    assert list((tmp_path / "converted/media").glob("*.png"))
    # Each Word layout must restore the same author-facing syntax and row pairing.
    assert_snapshot(
        markdown,
        ROOT / "tests/snapshot_cases_convert/equation_terms/snapshots-content/word_terms.md",
        update=snapshot_update,
    )
    if layout == "four-columns":
        # Imported Markdown must rebuild as the special layout, including its
        # nested description blocks, rather than only looking right as text.
        output = tmp_path / "rebuilt.html"
        result = subprocess.run(
            [str(rust_executable), "build", "html", str(tmp_path / "converted/terms.md"), "-o", str(output)],
            cwd=tmp_path, env={**os.environ, "PAPPER_RESOURCE_ROOT": str(ROOT)},
            capture_output=True, text=True, encoding="utf-8", timeout=90,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        tables = html.fromstring(output.read_text(encoding="utf-8")).xpath(
            "//div[contains(@class, 'equation-terms')]/table",
        )
        assert len(tables) == 1 and len(tables[0].xpath("./tbody/tr")) == 2
        assert tables[0].xpath(".//ul/li") and tables[0].xpath(".//img")
        assert tables[0].xpath(".//em[normalize-space(.)='emphasis']")


@pytest.mark.parametrize("mismatch", [
    "missing-separator", "merged-symbol", "merged-description", "empty-symbol",
    "empty-description", "caption", "numbered-caption",
])
def test_convert_keeps_nonmatching_word_tables(
    mismatch: str, tmp_path: Path, rust_executable: Path,
) -> None:
    """Do not partially consume a table, a label, or a caption when recognition fails."""
    document = Document()
    if mismatch in {"caption", "numbered-caption"}:
        document.add_paragraph("表1 Symbol data", style="Caption" if mismatch == "caption" else None)
    table = document.add_table(rows=2, cols=4)
    for index in range(2):
        for cell, text in zip(table.rows[index].cells, [
            "式中" if index == 0 else "", "x" if index == 0 else "y", "——", f"Description {index + 1}",
        ], strict=True):
            cell.text = text
    if mismatch == "missing-separator":
        table.cell(1, 2).text = "Data"
    elif mismatch == "merged-symbol":
        table.cell(0, 1).merge(table.cell(1, 1))
    elif mismatch == "merged-description":
        table.cell(0, 3).merge(table.cell(1, 3))
    elif mismatch == "empty-symbol":
        table.cell(1, 1).text = ""
    elif mismatch == "empty-description":
        table.cell(1, 3).text = ""
    source = tmp_path / "ordinary.docx"
    document.save(source)
    markdown = convert(rust_executable, source, tmp_path / "converted")
    assert "equation-terms" not in markdown
    assert "式中" in markdown and "Description 1" in markdown
    if mismatch != "empty-description":
        assert "Description 2" in markdown
    if mismatch in {"caption", "numbered-caption"}:
        assert "Symbol data" in markdown


def test_convert_recovers_exported_equation_terms(tmp_path: Path, rust_executable: Path) -> None:
    """Make Papper's own DOCX layout round-trip back to editable definition-list entries."""
    source = tmp_path / "terms.md"
    source.write_text(
        "::: {.equation-terms}\n"
        "$M_{soft}$\n: **Mask**；\n\n"
        "$\\sigma$\n: Standard deviation。\n:::\n\n"
        "式中，$k$ 为核大小。\n",
        encoding="utf-8",
    )
    docx = tmp_path / "terms.docx"
    result = subprocess.run(
        [str(rust_executable), "build", "docx", str(source), "-o", str(docx), "--no-mathtype"],
        cwd=tmp_path, env={**os.environ, "PAPPER_RESOURCE_ROOT": str(ROOT)},
        capture_output=True, text=True, encoding="utf-8", timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    markdown = convert(rust_executable, docx, tmp_path / "converted")
    assert markdown.count("::: equation-terms") == 1
    assert "**Mask**" in markdown and "Standard deviation。" in markdown
    assert r"$\sigma$" in markdown and "$M_{soft}$" in markdown
    assert "式中，$k$ 为核大小。" in markdown
