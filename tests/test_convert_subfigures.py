"""Protect recovered subfigures and downstream cross-reference image retention.

The Markdown fixture covers public syntax, formatting, malformed table exports,
bookmarks and near misses. The real pandoc-crossref engine must
retain every image and resolve parent/child IDs.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from xml.etree import ElementTree as ET

from docx import Document
from docx.oxml import OxmlElement
from docx.shared import Inches
from PIL import Image

from native_support import ROOT, native_pandoc_executable
from snapshot_utils import assert_snapshot


def test_convert_editable_svg_from_docx(rust_executable: Path, tmp_path: Path) -> None:
    """Convert a real Word layout and verify the visible artwork matches its editable v2 panels."""
    doc = Document()
    section = doc.sections[0]
    section.page_width, section.left_margin, section.right_margin = Inches(8), Inches(1), Inches(1)
    table = doc.add_table(rows=5, cols=2)
    table.columns[0].width, table.columns[1].width = Inches(2), Inches(4)
    for index, color in enumerate(("red", "blue")):
        source = tmp_path / f"{color}.png"
        Image.new("RGB", (80 + index * 40, 40), color).save(source)
        picture = table.cell(0, index).paragraphs[0].add_run().add_picture(str(source), width=Inches(2))
        if index == 0:
            crop = OxmlElement("a:srcRect")
            crop.set("r", "15000")
            picture._inline.graphic.graphicData.pic.blipFill.insert(1, crop)
        label = "ab"[index]
        table.cell(1, index).text = f"（{label}）测试"
        translation = table.cell(2, index).paragraphs[0]
        if index == 0:
            translation.add_run(f"({label}) ")
        else:
            marker = OxmlElement("m:oMath")
            math_run = OxmlElement("m:r")
            math_text = OxmlElement("m:t")
            math_text.text = f"({label})"
            math_run.append(math_text)
            marker.append(math_run)
            translation._p.append(marker)
            translation.add_run(" ")
        translation.add_run(f"Field {color}").bold = True
    table.cell(3, 0).merge(table.cell(3, 1)).paragraphs[0].add_run().add_picture(str(source), width=Inches(2))
    table.cell(4, 0).merge(table.cell(4, 1)).text = "图 1 比较"
    doc.add_paragraph("Figure 1. Comparison")
    doc.save(tmp_path / "paper.docx")
    result = subprocess.run([str(rust_executable), "convert", "paper.docx", "-o", "out", "--svg", "--offline"],
                            cwd=tmp_path, env={**os.environ, "PAPPER_RESOURCE_ROOT": str(ROOT)},
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    out = tmp_path / "out"
    markdown = (out / "paper.md").read_text(encoding="utf-8")
    svg_path, = out.rglob("subfigure-*.svg")
    assert svg_path == out / "media" / "subfigure-subfig-1.svg"
    assert f"](media/{svg_path.name})" in markdown and "#fig:subfig-1" in markdown
    assert "图 1 比较" in markdown and 'caption-en="Comparison"' in markdown
    ns = {"s": "http://www.w3.org/2000/svg"}
    svg = ET.parse(svg_path).getroot()
    metadata = json.loads(svg.find("s:metadata", ns).text)
    assert metadata["version"] == 2 and metadata["width"] == 432 and metadata["layout"]["rows"] == [2, 1]
    left, right, bottom = metadata["elements"]
    assert abs(right["width"] / left["width"] - 2) < 0.001 and bottom["width"] > right["width"]
    assert left["crop"]["right"] == 15
    for index, panel in enumerate((left, right)):
        group = svg.find(f"s:g[@data-element-id='{panel['id']}']", ns)
        image = group.find("s:svg/s:image", ns)
        assert panel["label"] == "ab"[index] and panel["ratio"] == index + 2
        asset = svg_path.parent / image.attrib["href"]
        assert asset.parent == svg_path.parent and asset.read_bytes() == (tmp_path / f"{('red', 'blue')[index]}.png").read_bytes()
        translation = f"Field {('red', 'blue')[index]}"
        assert "".join(run["text"] for run in panel["caption"]) == f"测试\n{translation}"
        assert any(run["text"] == translation and run["bold"] for run in panel["caption"])
        assert translation in "".join(group.itertext())
    # The original Crossref export also retains both titles in its native caption syntax.
    crossref = subprocess.run([str(rust_executable), "convert", "paper.docx", "-o", "crossref", "--offline"],
                              cwd=tmp_path, env={**os.environ, "PAPPER_RESOURCE_ROOT": str(ROOT)},
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
    assert crossref.returncode == 0, crossref.stdout + crossref.stderr
    plain = (tmp_path / "crossref" / "paper.md").read_text(encoding="utf-8")
    assert 'caption-en="Comparison"' in plain and 'caption-en="**Field blue**"' in plain
    # A later import must not destroy edits made to the SVG in VS Code.
    svg_path.write_text("<svg><!-- manual edit --></svg>", encoding="utf-8")
    retry = subprocess.run(result.args, cwd=tmp_path, env={**os.environ, "PAPPER_RESOURCE_ROOT": str(ROOT)},
                           capture_output=True, timeout=90)
    assert retry.returncode != 0 and svg_path.read_text(encoding="utf-8") == "<svg><!-- manual edit --></svg>"
    assert (out / "paper.md").read_text(encoding="utf-8") == markdown


def ast_elements(value: object, kind: str) -> list[dict]:
    """Collect public AST elements for image retention and reference assertions."""
    result = []
    if isinstance(value, dict):
        if value.get("t") == kind:
            result.append(value)
        for child in value.values():
            result.extend(ast_elements(child, kind))
    elif isinstance(value, list):
        for child in value:
            result.extend(ast_elements(child, kind))
    return result


def run_pandoc(source: Path, *options: str) -> subprocess.CompletedProcess:
    """Run the retained engine with the real convert filters and crossref support."""
    pandoc = native_pandoc_executable()
    assert pandoc is not None, "Subfigure conversion requires the retained Pandoc engine"
    result = subprocess.run(
        [str(pandoc), str(source), *options], cwd=source.parent,
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def test_svg_rewrites_child_references_and_preserves_unsupported_groups(tmp_path: Path) -> None:
    """A converted child has no Markdown anchor; edits must not affect an unconverted sibling group."""
    source = tmp_path / "paper.md"
    source.write_text(
        "See [@fig:a], and [panel B](#fig:b). Literal: `@fig:a`.\n\n"
        "::: {#fig:pair}\n![left](a.png){#fig:a width=\"40%\"} ![right](b.png){#fig:b width=\"60%\"}\n\nPair.\n:::\n\n"
        "::: {#fig:math}\n![$x^2$](c.png){#fig:c} ![d](d.png)\n\nMath.\n:::\n\nSee [@fig:c].\n", encoding="utf-8")
    result = run_pandoc(source, "-t", "markdown", "--wrap=none", "-L", str(ROOT / "pandoc/filters/convert/subfigure_svg.lua"))
    assert "[@fig:pair] (a)" in result.stdout and "[panel B](#fig:pair)" in result.stdout and "`@fig:a`" in result.stdout
    assert "subfigure-pair.svg" in result.stdout and "subfigure-math.svg" not in result.stdout
    assert "[@fig:c]" in result.stdout and "#fig:math" in result.stdout


def test_subfigure_markdown_snapshot(tmp_path: Path, snapshot_update: bool) -> None:
    """Preserve group references and round source sizes after detection rewrites layout sizes."""
    fixture = ROOT / "tests/snapshot_cases_convert/subfigures"
    options = ["-f", "markdown", "-t", "markdown-simple_tables-multiline_tables", "--standalone", "--wrap=none"]
    for name in ["detect_subfigures", "extract_inline_images", "detect_figure", "crossrefs",
                 "round_image_dimensions", "crossrefs_fuzz"]:
        options.extend(["-L", str(ROOT / f"pandoc/filters/convert/{name}.lua")])
    converted = run_pandoc(fixture / "input.md", *options).stdout
    assert_snapshot(converted, fixture / "snapshots-content/subfigures.md", update=snapshot_update)
    output = tmp_path / "groups.md"
    output.write_text(converted, encoding="utf-8")
    resolved = run_pandoc(output, "-f", "markdown", "-t", "json", "--filter=pandoc-crossref",
                          "-M", "linkReferences:true")
    assert "Undefined cross-reference" not in resolved.stderr
    ast = json.loads(resolved.stdout)["blocks"]
    before = json.loads(run_pandoc(fixture / "input.md", "-t", "json").stdout)["blocks"]
    assert [image["c"][2][0] for image in ast_elements(ast, "Image")] == [
        image["c"][2][0] for image in ast_elements(before, "Image")]
    links = {link["c"][2][0] for link in ast_elements(ast, "Link")}
    assert {"#fig:_RefGrid", "#fig:_RefPanel", "#fig:existing"} <= links
    assert "This paragraph must survive." in converted
    # Repeated conversion must not nest groups or allocate new panel IDs.
    repeated = run_pandoc(output, "-t", "markdown-simple_tables-multiline_tables", "--standalone", "--wrap=none", "-L",
                          str(ROOT / "pandoc/filters/convert/detect_subfigures.lua"))
    assert repeated.stdout == converted
