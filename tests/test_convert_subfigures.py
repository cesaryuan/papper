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

import pytest
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
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
    assert metadata.get("showPanelLabels", True)
    assert not metadata["autoRenumber"] and bottom["label"] == ""
    assert not svg.find(f"s:g[@data-element-id='{bottom['id']}']", ns).findall("s:text", ns)
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


@pytest.mark.parametrize(("chinese", "english", "panel_letter", "expected"), [
    ("图 3‑9 对比图", "Fig. 3‑9 Comparison", "g", "图 3‑9 对比图"),
    ("图 3‑9 对比图", "Fig. ‑. Comparison", "g", "图 3‑9 对比图"),
    ("图 ‑9 对比图", "Fig. 3‑9 Comparison", "g", "图 3-9 对比图"),
    ("图 ‑8 对比图", "Fig. 3‑9 Comparison", "g", None),
    ("图 3‑9 对比图", "Fig. 3‑9 Comparison", "d", None),
])
def test_convert_shared_row_titles_and_single_figure(
    tmp_path: Path, rust_executable: Path, chinese: str, english: str, panel_letter: str,
    expected: str | None,
) -> None:
    """Keep merged bilingual titles across each picture pair and recover the standalone figure."""
    document = Document()
    source_image = tmp_path / "sample.png"
    Image.new("RGB", (40, 30), "red").save(source_image)
    single = document.add_table(rows=3, cols=1)
    single.cell(0, 0).paragraphs[0].add_run().add_picture(str(source_image), width=Inches(2))
    caption = single.cell(1, 0).paragraphs[0]
    start, end = OxmlElement("w:bookmarkStart"), OxmlElement("w:bookmarkEnd")
    start.set(qn("w:name"), "_Ref123456")
    for anchor in (start, end):
        anchor.set(qn("w:id"), "1")
    caption._p.append(start)
    caption.add_run("图 2‑1 方法流程图")
    caption._p.append(end)
    single.cell(2, 0).text = "Fig. 2‑1 Workflow of the proposed method"
    reference = document.add_paragraph("See ")
    link = OxmlElement("w:hyperlink")
    link.set(qn("w:anchor"), "_Ref123456")
    link.append(reference.add_run("图 2‑1")._r)
    reference._p.append(link)
    pairs = document.add_table(rows=8, cols=2)
    for index, (primary, translation) in enumerate((("甲桥", "Bridge A"), ("乙桥", "Bridge B"))):
        for cell in pairs.rows[index * 3].cells:
            cell.paragraphs[0].add_run().add_picture(str(source_image), width=Inches(2))
        pairs.cell(index * 3 + 1, 0).merge(pairs.cell(index * 3 + 1, 1)).text = f"(g) {primary}"
        label = panel_letter if index == 0 else "g"
        pairs.cell(index * 3 + 2, 0).merge(pairs.cell(index * 3 + 2, 1)).text = f"({label}) {translation}"
    pairs.cell(6, 0).merge(pairs.cell(6, 1)).text = chinese
    pairs.cell(7, 0).merge(pairs.cell(7, 1)).text = english
    source = tmp_path / "thesis-layout.docx"
    document.save(source)
    output = tmp_path / "converted"
    result = subprocess.run(
        [str(rust_executable), "convert", str(source), "-o", str(output), "--svg", "--offline"],
        cwd=tmp_path, env={**os.environ, "PAPPER_RESOURCE_ROOT": str(ROOT)},
        capture_output=True, text=True, encoding="utf-8", timeout=90,
    )
    assert result.returncode == 0, result.stderr
    manuscript = (output / "thesis-layout.md").read_text(encoding="utf-8")
    assert "![图 2‑1 方法流程图](media/" in manuscript and "#fig:_Ref123456" in manuscript
    assert "See [@fig:_Ref123456]" in manuscript
    assert 'caption-en="Workflow of the proposed method"' in manuscript
    if expected is None:
        assert not list((output / "media").glob("subfigure-*.svg"))
        assert chinese in manuscript and english in manuscript
        assert f"\\({panel_letter}\\) Bridge A" in manuscript
        return
    assert expected in manuscript and 'caption-en="Comparison"' in manuscript
    svg_path, = (output / "media").glob("subfigure-*.svg")
    ns = {"s": "http://www.w3.org/2000/svg"}
    svg = ET.parse(svg_path).getroot()
    figure = json.loads(svg.find("s:metadata", ns).text)
    assert len(svg.findall(".//s:image", ns)) == 4
    rows = [figure["elements"][:3], figure["elements"][3:]]
    for (left, right, title), expected in zip(rows, ("(g) 甲桥\n(g) Bridge A", "(g) 乙桥\n(g) Bridge B"), strict=True):
        assert title["kind"] == "text" and "".join(run["text"] for run in title["runs"]) == expected
        assert title["x"] == pytest.approx(left["x"], abs=0.01)
        assert title["x"] + title["width"] == pytest.approx(right["x"] + right["width"], abs=0.01)
        assert title["y"] > max(panel["y"] + panel["height"] for panel in (left, right))
        assert all(panel["labelPosition"] == "none" for panel in (left, right))
    assert rows[0][2]["y"] + 25 < rows[1][0]["y"]


def test_convert_tiff_panels_with_a_mismatched_english_letter(
    tmp_path: Path, rust_executable: Path,
) -> None:
    """Recover the aligned bilingual TIFF grid without changing either authored letter."""
    document = Document()
    source_image = tmp_path / "source.tiff"
    indexed = Image.new("P", (40, 30), 1)
    indexed.putpalette([0, 240, 40, 220, 20, 170] + [0] * 762)
    indexed.paste(0, (0, 0, 8, 9))
    indexed.save(source_image, compression="tiff_lzw")
    table = document.add_table(rows=5, cols=3)
    for index, (label, other, primary, translation) in enumerate((
        ("d", "c", "突变", "Mutation"),
        ("e", "e", "次小值", "Minor"),
        ("f", "f", "趋势", "Trend"),
    )):
        table.cell(0, index).paragraphs[0].add_run().add_picture(str(source_image), width=Inches(2))
        table.cell(1, index).text = f"({label}) {primary}"
        table.cell(2, index).text = f"({other}) {translation}"
    table.cell(3, 0).merge(table.cell(3, 2)).text = "图 2‑8 各类工况对比"
    table.cell(4, 0).merge(table.cell(4, 2)).text = "Fig. 2‑8 Comparison of different cases"
    document.add_picture(str(source_image), width=Inches(1))
    document.add_paragraph("图 2‑9 单独图片")
    source = tmp_path / "tiff-panels.docx"
    document.save(source)
    output = tmp_path / "converted"
    result = subprocess.run(
        [str(rust_executable), "convert", str(source), "-o", str(output), "--svg", "--offline"],
        cwd=tmp_path, env={**os.environ, "PAPPER_RESOURCE_ROOT": str(ROOT)},
        capture_output=True, text=True, encoding="utf-8", timeout=90,
    )
    assert result.returncode == 0, result.stderr
    markdown = (output / "tiff-panels.md").read_text(encoding="utf-8")
    svg_path, = (output / "media").glob("subfigure-*.svg")
    assert f"![图 2‑8 各类工况对比](media/{svg_path.name})" in markdown
    assert 'caption-en="Comparison of different cases"' in markdown
    original, = (output / "media").glob("*.tiff")
    assert f"](media/{original.name})" in markdown and original.read_bytes() == source_image.read_bytes()
    ns = {"s": "http://www.w3.org/2000/svg"}
    svg = ET.parse(svg_path).getroot()
    figure = json.loads(svg.find("s:metadata", ns).text)
    assert [panel["label"] for panel in figure["elements"]] == ["d", "e", "f"]
    assert "".join(run["text"] for run in figure["elements"][0]["caption"]) == "突变\n(c) Mutation"
    assert "".join(run["text"] for run in figure["elements"][1]["caption"]) == "次小值\nMinor"
    assert [
        ["".join(line.itertext()) for line in group.findall("s:text", ns)]
        for group in svg.findall("s:g", ns)
    ] == [["(d) 突变", "(c) Mutation"], ["(e) 次小值", "(e) Minor"], ["(f) 趋势", "(f) Trend"]]
    hrefs = [image.attrib["href"] for image in svg.findall(".//s:image", ns)]
    assert len(hrefs) == 3 and len(set(hrefs)) == 1
    with Image.open(svg_path.parent / hrefs[0]) as png, Image.open(source_image) as tiff:
        assert png.format == "PNG" and png.size == tiff.size
        assert png.convert("RGB").tobytes() == tiff.convert("RGB").tobytes()


@pytest.mark.parametrize("layout", ["image-only-rows", "caption-rows", "line-break", "image-cell"])
def test_convert_unnumbered_subfigure_layouts(tmp_path: Path, rust_executable: Path, layout: str) -> None:
    """Preserve unnumbered layouts, with or without authored bilingual subcaptions."""
    document = Document()
    source = tmp_path / "panel.png"
    Image.new("RGB", (40, 30), "green").save(source)
    image_only = layout == "image-only-rows"
    rows = {"image-only-rows": 7, "caption-rows": 4, "line-break": 3, "image-cell": 2}[layout]
    table = document.add_table(rows=rows, cols=1 if image_only else 2)
    for index, (primary, translation) in enumerate((("正常", "Normal"), ("突变", "Mutation"))):
        cell = table.cell(index, 0) if image_only else table.cell(0, index)
        cell.paragraphs[0].add_run().add_picture(str(source), width=Inches(2))
        if layout == "caption-rows":
            table.cell(1, index).text = primary
            table.cell(2, index).paragraphs[0].add_run(translation).bold = True
        elif layout == "line-break":
            caption = table.cell(1, index).paragraphs[0]
            caption.add_run(primary).add_break()
            caption.add_run(translation).bold = True
        elif layout == "image-cell":
            cell.add_paragraph(primary)
            cell.add_paragraph().add_run(translation).bold = True
    if image_only:
        for row in table.rows[2:5]:
            row.cells[0].paragraphs[0].add_run().add_picture(str(source), width=Inches(2))
        table.cell(5, 0).text = "图 5‑9 实桥GPS位移监测数据在不同缺失工况下的重构结果"
        table.cell(6, 0).text = "Fig. 5‑9 Reconstruction results of real bridge GPS displacement monitoring data"
    else:
        table.cell(rows - 1, 0).merge(table.cell(rows - 1, 1)).text = "图 2‑8 各类工况对比"
    input_docx = tmp_path / "caption-layout.docx"
    document.save(input_docx)
    result = subprocess.run(
        [str(rust_executable), "convert", str(input_docx), "-o", "out", "--svg", "--offline"],
        cwd=tmp_path, env={**os.environ, "PAPPER_RESOURCE_ROOT": str(ROOT)},
        capture_output=True, text=True, encoding="utf-8", timeout=90,
    )
    assert result.returncode == 0, result.stderr
    svg_path, = (tmp_path / "out/media").glob("subfigure-*.svg")
    ns = {"s": "http://www.w3.org/2000/svg"}
    svg = ET.parse(svg_path).getroot()
    figure = json.loads(svg.find("s:metadata", ns).text)
    assert figure["showPanelLabels"] is False and all(not panel["label"] for panel in figure["elements"])
    if image_only:
        assert figure["layout"]["rows"] == [1, 1, 1, 1, 1]
        assert len(svg.findall(".//s:image", ns)) == 5 and not svg.findall(".//s:text", ns)
        markdown = (tmp_path / "out/caption-layout.md").read_text(encoding="utf-8")
        assert f"![图 5‑9 实桥GPS位移监测数据在不同缺失工况下的重构结果](media/{svg_path.name})" in markdown
        assert 'caption-en="Reconstruction results of real bridge GPS displacement monitoring data"' in markdown
        return
    for group, primary, translation in zip(
        svg.findall("s:g", ns), ("正常", "突变"), ("Normal", "Mutation"), strict=True,
    ):
        lines = group.findall("s:text", ns)
        assert ["".join(line.itertext()) for line in lines] == [primary, translation]
        assert lines[1].find("s:tspan[@font-weight='bold']", ns) is not None
        assert float(lines[0].attrib["y"]) < float(lines[1].attrib["y"]) < float(svg.attrib["viewBox"].split()[3])


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
        "See [@fig:a], and [panel B](#fig:b). Literal: `@fig:a`. See [@fig:b].\n\n"
        "::: {#fig:pair}\n![left](a.png){#fig:a width=\"40%\" label=\"a\"} ![right](b.png){#fig:b width=\"60%\"}\n\nPair.\n:::\n\n"
        "::: {#fig:math}\n![$x^2$](c.png){#fig:c} ![d](d.png)\n\nMath.\n:::\n\nSee [@fig:c].\n", encoding="utf-8")
    result = run_pandoc(source, "-t", "markdown", "--wrap=none", "-L", str(ROOT / "pandoc/filters/convert/subfigure_svg.lua"))
    assert "[@fig:pair] (a)" in result.stdout and "[panel B](#fig:pair)" in result.stdout and "`@fig:a`" in result.stdout
    assert "See [@fig:pair]." in result.stdout
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
