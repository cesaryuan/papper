"""Verify optional figure/table/equation reference recovery with real Pandoc and DOCX.

Snapshots cover mixed stable/text references, Unicode numbering, formatting,
ambiguous labels and ID collisions. Public CLI tests protect the default-off
contract and confirm that captions receive usable IDs only when opted in.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from native_support import ROOT, native_pandoc_executable
from snapshot_utils import assert_snapshot


def test_fuzzy_crossrefs_snapshot(tmp_path: Path, snapshot_update: bool) -> None:
    """Protect actual Markdown recovery without damaging captions or ambiguous prose."""
    pandoc = native_pandoc_executable()
    assert pandoc is not None
    fixture = ROOT / "tests/snapshot_cases_convert/fuzzy_crossrefs"
    command = [str(pandoc), str(fixture / "input.md"), "--from=markdown", "--to=markdown", "--wrap=none",
               "--metadata=papper-fuzzy-crossrefs:true", "--standalone"]
    for name in ("detect_figure", "detect_table", "crossrefs", "crossrefs_fuzz"):
        command.extend(["--lua-filter", str(ROOT / "pandoc/filters/convert" / f"{name}.lua")])
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "如[@fig:fuzz-1-14]所示" in result.stdout
    assert "如[@tbl:fuzz-1-3]所示" in result.stdout
    assert "[@fig:_RefMixed]" in result.stdout
    assert "如[@eq:fuzz-4-43]" in result.stdout
    assert "[@eq:_RefEquation]" in result.stdout
    assert "参见[@eq:_RefUnicodeEquation]" in result.stdout
    assert "papper-equation-labels" not in result.stdout
    assert_snapshot(result.stdout, fixture / "snapshots-content/fuzzy_crossrefs.md", update=snapshot_update)


@pytest.mark.parametrize("enabled", [False, True], ids=["default-off", "opt-in"])
def test_convert_fuzzy_crossrefs_option(
    tmp_path: Path, rust_executable: Path, snapshot_update: bool, enabled: bool,
) -> None:
    """Recover figure/table/equation references together, preserving default-off behavior."""
    from docx import Document
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Inches

    document = Document()
    document.add_paragraph("如图1-14所示。The table is shown in Tbl. 1-3.")
    image_path = str(ROOT / "template/examples/images/single-figure-example.png")
    document.add_picture(image_path, width=Inches(1))
    document.add_paragraph("图1‑14 不同失效策略下的脆弱性曲线")
    document.add_paragraph("表1‑3 基于复杂网络理论的鲁棒性度量")
    table = document.add_table(rows=3, cols=3)
    table.cell(0, 0).text = "Metric"
    table.cell(0, 1).text = "Value"
    table.cell(0, 2).text = "Explanation"
    table.cell(1, 0).merge(table.cell(2, 0)).text = "Laplacian"
    table.cell(1, 1).text = "0.0106"
    table.cell(1, 2).text = "Network is easily divided"
    table.cell(2, 1).text = "435282.033"
    table.cell(2, 2).text = "Moderate connectivity"
    document.add_paragraph("A plain text reference to an anchored picture: see Fig. 2.")
    document.add_picture(image_path, width=Inches(1))
    caption = document.add_paragraph()
    start = OxmlElement("w:bookmarkStart")
    start.set(qn("w:id"), "1")
    start.set(qn("w:name"), "_RefMixed")
    caption._p.append(start)
    end = OxmlElement("w:bookmarkEnd")
    end.set(qn("w:id"), "1")
    caption._p.append(end)
    caption.add_run("Fig. 2 Mixed anchored and typed references")
    reference = document.add_paragraph()
    link = OxmlElement("w:hyperlink")
    link.set(qn("w:anchor"), "_RefMixed")
    run = OxmlElement("w:r")
    text = OxmlElement("w:t")
    text.text = "Anchored picture reference"
    run.append(text)
    link.append(run)
    reference._p.append(link)
    document.add_paragraph("As in Eq. (4-43), the variables agree. 如式 4-44 所示。")

    def add_formula(number: str, bookmark: bool = False) -> None:
        """Create a standalone Word inline equation and its typed numeric suffix."""
        paragraph = document.add_paragraph()
        math = OxmlElement("m:oMath")
        run = OxmlElement("m:r")
        text = OxmlElement("m:t")
        text.text = "x=y" if not bookmark else "u=v"
        run.append(text)
        math.append(run)
        paragraph._p.append(math)
        if bookmark:
            start = OxmlElement("w:bookmarkStart")
            start.set(qn("w:id"), "2")
            start.set(qn("w:name"), "_RefEquation")
            paragraph._p.append(start)
            end = OxmlElement("w:bookmarkEnd")
            end.set(qn("w:id"), "2")
            paragraph._p.append(end)
        paragraph.add_run(f" ({number})")

    add_formula("4-43")
    add_formula("4-44", bookmark=True)
    reference = document.add_paragraph()
    link = OxmlElement("w:hyperlink")
    link.set(qn("w:anchor"), "_RefEquation")
    run = OxmlElement("w:r")
    text = OxmlElement("w:t")
    text.text = "Equation reference"
    run.append(text)
    link.append(run)
    reference._p.append(link)
    source = tmp_path / "references.docx"
    document.save(source)
    output = tmp_path / "converted"
    command = [str(rust_executable), "convert", str(source), "-o", str(output)]
    if enabled:
        command.append("--fuzzy-crossrefs")
    result = subprocess.run(
        command, cwd=tmp_path,
        env={**os.environ, "PAPPER_RESOURCE_ROOT": str(ROOT), "PAPPER_HOME": str(tmp_path / "home")},
        capture_output=True, text=True, encoding="utf-8", timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    markdown = (output / "references.md").read_text(encoding="utf-8")
    assert "#fig:_RefMixed" in markdown and "[@fig:_RefMixed]" in markdown
    assert "#eq:_RefEquation" in markdown and "[@eq:_RefEquation]" in markdown
    assert "papper-fuzzy-crossrefs" not in markdown and "papper-equation-labels" not in markdown
    if enabled:
        assert "如[@fig:fuzz-1-14]所示" in markdown
        assert "shown in [@tbl:fuzz-1-3]" in markdown
        assert "see [@fig:_RefMixed]" in markdown
        assert "如[@eq:_RefEquation]" in markdown
        assert "As in [@eq:fuzz-4-43]" in markdown
        # A Markdown snapshot alone can miss a citation whose target ID was not
        # published correctly. Resolve the output using the real downstream filter.
        pandoc = native_pandoc_executable()
        assert pandoc is not None
        resolved = subprocess.run(
            [str(pandoc), str(output / "references.md"), "--from=markdown", "--to=json",
             "--filter=pandoc-crossref", "--metadata=linkReferences:true"],
            cwd=output, capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        assert resolved.returncode == 0, resolved.stdout + resolved.stderr
        assert "Undefined cross-reference" not in resolved.stderr
        assert "Unknown reference" not in resolved.stdout
        links: set[str] = set()

        def collect_links(value: object) -> None:
            """Read resolved reference destinations from the public Pandoc AST."""
            if isinstance(value, dict):
                if value.get("t") == "Link":
                    links.add(value["c"][2][0])
                for child in value.values():
                    collect_links(child)
            elif isinstance(value, list):
                for child in value:
                    collect_links(child)

        collect_links(json.loads(resolved.stdout)["blocks"])
        assert {"#fig:fuzz-1-14", "#tbl:fuzz-1-3", "#fig:_RefMixed",
                "#eq:fuzz-4-43", "#eq:_RefEquation"} <= links
    else:
        assert "如图1-14所示" in markdown
        assert "shown in Tbl. 1-3" in markdown
        assert "see Fig. 2" in markdown
        assert "Eq. (4-43)" in markdown and "如式 4-44" in markdown
        assert "fuzz-" not in markdown
    name = "enabled.md" if enabled else "default.md"
    assert_snapshot(
        markdown, ROOT / "tests/snapshot_cases_convert/fuzzy_crossrefs/snapshots-content" / name,
        update=snapshot_update,
    )
