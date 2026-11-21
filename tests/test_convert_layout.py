"""Exercise Word layout import through public Convert and DOCX rebuilds.

Existing Convert snapshots contain no page stories or explicit pagination. These
cases use a checked-in layout DOCX and targeted generated inputs to protect
layout boundaries, dynamic fields, story resources, and inheritance
by inspecting actual Markdown and reopened DOCX output.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml
from docx import Document
from docx.enum.section import WD_SECTION_START
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from lxml import etree

from native_support import ROOT, native_pandoc_executable
from snapshot_utils import assert_snapshot


def add_field(paragraph, instruction: str, result: str, *, simple: bool = False) -> None:
    """Author real Word fields, including instructions split across separate runs."""
    if simple:
        field = OxmlElement("w:fldSimple")
        field.set(qn("w:instr"), instruction)
        run = OxmlElement("w:r")
        text = OxmlElement("w:t")
        text.text = result
        run.append(text)
        field.append(run)
        paragraph._p.append(field)
        return
    for kind in ("begin", "instruction", "separate", "result", "end"):
        run = paragraph.add_run()
        run.bold = True
        if kind == "instruction":
            for chunk in (instruction[:3], instruction[3:]):
                text = OxmlElement("w:instrText")
                text.set(qn("xml:space"), "preserve")
                text.text = chunk
                paragraph.add_run()._r.append(text)
        elif kind == "result":
            run.text = result
        else:
            field = OxmlElement("w:fldChar")
            field.set(qn("w:fldCharType"), kind)
            run._r.append(field)


def run_papper(executable: Path, directory: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Run the real CLI with isolated tool state and surface conversion/build failures."""
    if arguments and arguments[0] == "convert":
        arguments = (*arguments, "--offline", "--zotero-data-dir", str(directory / "zotero"))
    result = subprocess.run(
        [str(executable), *arguments], cwd=directory,
        env={**os.environ, "PAPPER_RESOURCE_ROOT": str(ROOT), "PAPPER_HOME": str(directory / "home")},
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def add_outline_numbering(document, chapter_format: str = "%1") -> int:
    """Create a three-level decimal outline for headings in the Word fixture."""
    numbering = document.part.numbering_part.element
    definition = OxmlElement("w:abstractNum")
    definition.set(qn("w:abstractNumId"), "42")
    for index in range(3):
        level = OxmlElement("w:lvl")
        level.set(qn("w:ilvl"), str(index))
        outline_label = chapter_format if index == 0 else ".".join(f"%{i + 1}" for i in range(index + 1))
        for name, value in [("start", "1"), ("numFmt", "decimal"), ("lvlText", outline_label)]:
            prop = OxmlElement(f"w:{name}")
            prop.set(qn("w:val"), value)
            level.append(prop)
        definition.append(level)
    numbering.insert(0, definition)
    return numbering.add_num(42).numId


def test_convert_page_settings_and_chapter_header(
    tmp_path: Path, rust_executable: Path, snapshot_update: bool,
) -> None:
    """Rebuild section layout, real chapter numbering, and a chapter-linked page header."""
    document = Document()
    document.settings.odd_and_even_pages_header_footer = True
    first = document.sections[0]
    first.top_margin, first.left_margin = 2098 * 635, 1701 * 635
    first.header_distance = 1531 * 635
    chapter = add_outline_numbering(document, "第 %1 章")
    add_numbered_heading(document, "绪论", 1, chapter)
    add_numbered_heading(document, "背景", 2, chapter)
    document.add_paragraph("First section")
    second = document.add_section(WD_SECTION_START.NEW_PAGE)
    second.top_margin = 2155 * 635
    lines = OxmlElement("w:lnNumType")
    lines.set(qn("w:countBy"), "1")
    lines.set(qn("w:restart"), "newSection")
    second._sectPr.append(lines)
    second.even_page_header.is_linked_to_previous = False
    header = second.even_page_header.paragraphs[0]
    add_field(header, r'STYLEREF "Heading 1" \n \* MERGEFORMAT', "第 2 章")
    header.add_run(" ")
    add_field(header, r'STYLEREF "自定义样式" \* MERGEFORMAT', "自定义标题")
    add_numbered_heading(document, "方法", 1, chapter)
    third = document.add_section(WD_SECTION_START.NEW_PAGE)
    third.top_margin = 2098 * 635
    third._sectPr.remove(third._sectPr.find(qn("w:lnNumType")))
    document.add_paragraph("Third section")

    markdown = convert_heading_fixture(document, tmp_path, rust_executable)
    metadata = yaml.safe_load(markdown[4:].split("\n---\n", 1)[0])
    assert metadata["papperSettings"]["docxShowLineNumbers"] is False
    assert metadata["papperSettings"]["docxPageMargins"]["top"] == "104.9pt"
    section_directives = [line for line in markdown.splitlines() if line.startswith('[]{t="sectionBreak"')]
    assert_snapshot(
        "\n".join(section_directives) + "\n",
        ROOT / "tests/snapshot_cases_convert/page_layout/snapshots-content/chapter-sections.md",
        update=snapshot_update,
    )
    output = tmp_path / "converted"
    rebuilt = output / "rebuilt.docx"
    run_papper(rust_executable, output, "build", "docx", "headings.md", "--no-mathtype", "-o", str(rebuilt))
    reopened = Document(rebuilt)
    assert [section.top_margin.twips for section in reopened.sections] == [2098, 2155, 2098]
    assert all(section.left_margin.twips == 1701 and section.header_distance.twips == 1531
               for section in reopened.sections)
    assert [None if (lines := s._sectPr.find(qn("w:lnNumType"))) is None else lines.get(qn("w:restart"))
            for s in reopened.sections] == [None, "newSection", None]
    header = reopened.sections[1].even_page_header
    assert header.paragraphs[0].text == "第 2 章 自定义标题"
    assert [field.text for field in header._element.iter(qn("w:instrText"))] == [r"STYLEREF 1 \n \* MERGEFORMAT"]
    numbering = reopened.part.numbering_part.element
    number_id = reopened.paragraphs[0]._p.pPr.numPr.numId.val
    instance = next(num for num in numbering.findall(qn("w:num")) if num.get(qn("w:numId")) == str(number_id))
    abstract_id = instance.find(qn("w:abstractNumId")).get(qn("w:val"))
    outline = next(num for num in numbering.findall(qn("w:abstractNum")) if num.get(qn("w:abstractNumId")) == abstract_id)
    assert [level.find(qn("w:lvlText")).get(qn("w:val")) for level in outline.findall(qn("w:lvl"))[:2]] == [
        "第 %1 章", "%1.%2",
    ]


def add_numbered_heading(document, title: str, level: int, number_id: int):
    """Attach an outline level to an actual Word heading paragraph."""
    heading = document.add_heading(title, level)
    properties = heading._p.get_or_add_pPr().get_or_add_numPr()
    properties.get_or_add_numId().val = number_id
    properties.get_or_add_ilvl().val = level - 1
    return heading


def add_bookmark(paragraph, name: str, bookmark_id: int) -> None:
    """Bookmark the text run so REF fields can point to this Word paragraph."""
    start, end = OxmlElement("w:bookmarkStart"), OxmlElement("w:bookmarkEnd")
    start.set(qn("w:name"), name)
    start.set(qn("w:id"), str(bookmark_id))
    end.set(qn("w:id"), str(bookmark_id))
    run = paragraph.runs[0]._r
    run.addprevious(start)
    run.addnext(end)


def convert_heading_fixture(document, tmp_path: Path, executable: Path, *options: str) -> str:
    """Import a generated Word document through the public Convert command."""
    source = tmp_path / "headings.docx"
    document.save(source)
    output = tmp_path / "converted"
    run_papper(executable, tmp_path, "convert", str(source), "-o", str(output), *options)
    return (output / "headings.md").read_text(encoding="utf-8")


def render_heading_references(markdown: str):
    """Resolve the converted Markdown and expose the rendered link text and targets."""
    pandoc = native_pandoc_executable()
    assert pandoc is not None
    resolved = subprocess.run(
        [str(pandoc), "--from=markdown", "--to=html", "--filter=pandoc-crossref",
         "-M", "numberSections=true", "-M", "linkReferences=true"],
        input=markdown, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert resolved.returncode == 0, resolved.stderr
    return etree.HTML(resolved.stdout)


def test_convert_numbered_heading_references(tmp_path: Path, rust_executable: Path) -> None:
    """Recover forward REF fields and keep their destinations and displayed wrappers after renumbering."""
    document = Document()
    reference = document.add_paragraph("参见")
    add_field(reference, r"REF _RefMethods \r \h", "5.4.2")
    reference.add_run("节。")
    chapter_ref = document.add_paragraph("另见 ")
    add_field(chapter_ref, r'REF "_RefChapter" \n \h \* MERGEFORMAT', "第 5 章", simple=True)
    heading_text = document.add_paragraph("标题 ")
    add_field(heading_text, r"REF _RefMethods \h", "实桥算例")
    local_number = document.add_paragraph("末级编号 ")
    add_field(local_number, r"REF _RefMethods \n \h", "2")
    page = document.add_paragraph("页码 ")
    add_field(page, r"PAGEREF _RefMethods \h", "104")
    document.add_paragraph("旧稿第5章的原文。")
    number_id = add_outline_numbering(document)
    add_bookmark(add_numbered_heading(document, "案例", 1, number_id), "_RefChapter", 1)
    add_numbered_heading(document, "实验", 2, number_id)
    add_bookmark(add_numbered_heading(document, "实桥算例", 3, number_id), "_RefMethods", 2)
    markdown = convert_heading_fixture(document, tmp_path, rust_executable)
    assert "[-@sec:实桥算例]" in markdown
    assert "第 [-@sec:案例] 章" in markdown
    assert "[**实桥算例**](#sec:实桥算例)" in markdown
    assert "[**2**](#sec:实桥算例)" in markdown
    assert "[**104**](#sec:实桥算例)" in markdown
    assert "旧稿第5章的原文。" in markdown

    renumbered = markdown.replace("# 案例", "# 新增章节\n\n# 案例", 1)
    html = render_heading_references(renumbered)
    assert ["".join(a.itertext()) for a in html.xpath('//a[@href="#sec:实桥算例"]')] == ["2.1.1", "实桥算例", "2", "104"]
    assert ["".join(a.itertext()) for a in html.xpath('//a[@href="#sec:案例"]')] == ["2"]


def test_convert_refs_distinguish_duplicate_heading_titles(tmp_path: Path, rust_executable: Path) -> None:
    """Resolve same-named headings by bookmark and group aliases under one destination."""
    document = Document()
    number_id = add_outline_numbering(document)
    forward = document.add_paragraph("Forward ")
    add_field(forward, r"REF _RefSecond \w \h \t", "7.1.", simple=True)
    add_numbered_heading(document, "First chapter", 1, number_id)
    first = add_numbered_heading(document, "Methods", 2, number_id)
    add_bookmark(first, "_RefFirst", 1)
    add_bookmark(first, "_RefFirstAlias", 2)
    add_numbered_heading(document, "Second chapter", 1, number_id)
    second = add_numbered_heading(document, "Methods", 2, number_id)
    add_bookmark(second, "_RefSecond", 3)
    backward = document.add_paragraph("Backward ")
    add_field(backward, r"REF _RefFirst \r \h", "6.1")
    backward.add_run(" and ")
    add_field(backward, r"REF _RefFirstAlias \w \h", "6.1")
    backward.add_run(" or ")
    add_field(backward, r"REF _RefFirstAlias \h", "Methods")

    html = render_heading_references(convert_heading_fixture(document, tmp_path, rust_executable))
    first_id, second_id = html.xpath('//h2[contains(., "Methods")]/@id')
    assert first_id != second_id
    forward_paragraph = html.xpath('//p[starts-with(., "Forward")]')[0]
    assert "".join(forward_paragraph.itertext()) == "Forward 2.1."
    assert forward_paragraph.xpath('./a/@href') == [f"#{second_id}"]
    backward_links = html.xpath('//p[starts-with(., "Backward")]//a')
    assert [(a.get("href"), "".join(a.itertext())) for a in backward_links] == [
        (f"#{first_id}", "1.1"), (f"#{first_id}", "1.1"), (f"#{first_id}", "Methods"),
    ]


def test_convert_heading_refs_keep_unsupported_word_results(tmp_path: Path, rust_executable: Path) -> None:
    """Keep cached text for unsupported fields even alongside a recovered heading reference."""
    document = Document()
    number_id = add_outline_numbering(document)
    add_bookmark(add_numbered_heading(document, "Methods", 1, number_id), "_RefMethods", 1)
    add_bookmark(document.add_heading("Preface", 1), "_RefPreface", 2)
    add_bookmark(document.add_paragraph("Ordinary paragraph"), "_RefOrdinary", 3)
    cases = [
        ("Numbered", r"REF _RefMethods \w \h", "9", "1"),
        ("Relative", r"REF _RefMethods \r \p \h", "9 above", "9 above"),
        ("Unnumbered", r"REF _RefPreface \r \h", "8", "8"),
        ("Nonheading", r"REF _RefOrdinary \r \h", "7", "7"),
        ("Roman", r"REF _RefMethods \n \h", "IX", "IX"),
    ]
    for label, instruction, cached, _ in cases:
        add_field(document.add_paragraph(label + " "), instruction, cached)

    markdown = convert_heading_fixture(document, tmp_path, rust_executable, "--fuzzy-crossrefs")
    html = render_heading_references(markdown)
    for label, _, _, expected in cases:
        paragraph = html.xpath('//p[starts-with(., $label)]', label=label + " ")[0]
        assert "".join(paragraph.itertext()) == f"{label} {expected}"
    methods_id = html.xpath('//h1[contains(., "Methods")]/@id')[0]
    preface_id = html.xpath('//h1[.="Preface"]/@id')[0]
    ordinary_id = html.xpath('//p[.="Ordinary paragraph"]/span/@id')[0]
    for label, target_id in [
        ("Numbered", methods_id), ("Relative", methods_id), ("Unnumbered", preface_id),
        ("Nonheading", ordinary_id), ("Roman", methods_id),
    ]:
        assert html.xpath('//p[starts-with(., $label)]//a/@href', label=label + " ") == [f"#{target_id}"]


def test_convert_paragraph_bottom_borders(tmp_path: Path, rust_executable: Path) -> None:
    """Keep Word borders on body and even-header paragraphs through a Convert/Build round trip."""
    document = Document()
    document.settings.odd_and_even_pages_header_footer = True
    body = document.add_paragraph("Bordered body paragraph")
    header = document.sections[0].even_page_header.paragraphs[0]
    header.add_run("Even-page heading: ")
    add_field(header, " STYLEREF 1 ", "Sample section", simple=True)
    cases = [
        (body, "dashed", "6", "305F80", "4"),
        (header, "thinThickSmallGap", "24", "auto", "1"),
    ]
    for paragraph, style, size, color, space in cases:
        group, bottom = OxmlElement("w:pBdr"), OxmlElement("w:bottom")
        for name, value in [("val", style), ("sz", size), ("color", color), ("space", space)]:
            bottom.set(qn(f"w:{name}"), value)
        group.append(bottom)
        paragraph._p.get_or_add_pPr().append(group)
    document.add_paragraph("Unbordered body paragraph")
    source = tmp_path / "borders.docx"
    document.save(source)
    output = tmp_path / "converted"
    run_papper(rust_executable, tmp_path, "convert", str(source), "-o", str(output))
    markdown = (output / "borders.md").read_text(encoding="utf-8")
    assert markdown.count("borders.bottom=") == 2
    assert "------------------------------------------------------------------------" not in markdown

    rebuilt = output / "rebuilt.docx"
    run_papper(rust_executable, output, "build", "docx", "borders.md", "--no-mathtype", "-o", str(rebuilt))
    reopened = Document(rebuilt)
    even_header = reopened.sections[0].even_page_header
    for paragraphs, (original, style, size, color, space) in zip(
        [reopened.paragraphs, even_header.paragraphs], cases, strict=True,
    ):
        paragraph = next(p for p in paragraphs if p.text.startswith(original.runs[0].text))
        bottom = paragraph._p.find(f".//{qn('w:bottom')}")
        assert bottom is not None
        assert tuple(bottom.get(qn(f"w:{key}")) for key in ("val", "sz", "color", "space")) == (style, size, color, space)
    assert [field.text for field in even_header._element.iter(qn("w:instrText"))] == ["STYLEREF 1"]
    control = next(p for p in reopened.paragraphs if p.text == "Unbordered body paragraph")
    assert control._p.find(f".//{qn('w:bottom')}") is None


def test_convert_layout_snapshot_and_rebuild(
    tmp_path: Path, rust_executable: Path, snapshot_update: bool,
) -> None:
    """Protect section order, first/even inheritance, dynamic fields, and story media on rebuild."""
    source = ROOT / "tests/snapshot_cases_convert/page_layout/layout.docx"
    original = source.read_bytes()
    with ZipFile(source) as archive:
        source_logo = archive.read("word/media/image1.png")
    output = tmp_path / "converted"
    (output / "media").mkdir(parents=True)
    existing = output / "media/image1.png"
    existing.write_bytes(b"previous imported image")
    run_papper(rust_executable, tmp_path, "convert", str(source), "-o", str(output))
    markdown = (output / "layout.md").read_text(encoding="utf-8")
    assert source.read_bytes() == original
    assert existing.read_bytes() == b"previous imported image"
    assert "PAPPERLAYOUT" not in markdown and "papper-convert-layout" not in markdown
    assert markdown.count("Layout specimen") == 1
    assert_snapshot(
        markdown, ROOT / "tests/snapshot_cases_convert/page_layout/snapshots-content/layout.md",
        update=snapshot_update,
    )
    rebuilt = output / "rebuilt.docx"
    run_papper(rust_executable, output, "build", "docx", "layout.md", "--no-mathtype", "-o", str(rebuilt))
    reopened = Document(rebuilt)
    assert len(reopened.sections) == 5, "Initial settings must not create an extra empty section"
    assert [section.start_type for section in reopened.sections[1:]] == [
        WD_SECTION_START.CONTINUOUS, WD_SECTION_START.ODD_PAGE,
        WD_SECTION_START.EVEN_PAGE, WD_SECTION_START.NEW_PAGE,
    ]
    assert [section.different_first_page_header_footer for section in reopened.sections] == [True, False, True, False, False]
    assert reopened.settings.odd_and_even_pages_header_footer
    first, second, third, fourth, fifth = reopened.sections
    assert first.header.paragraphs[0].text == 'Header "quoted" & italic Project'
    assert first.header.paragraphs[0].alignment == WD_ALIGN_PARAGRAPH.RIGHT
    assert first.header.tables[0].cell(1, 1).text == "Draft"
    assert any(paragraph.text == "Chapter: Chapter 4" for paragraph in first.header.paragraphs)
    assert [node.text for node in first.header._element.iter(qn("w:instrText"))] == ["STYLEREF 1"]
    assert list(first.header._element.iter(qn("m:oMath")))
    assert first.footer.paragraphs[0].text == "Page I of 12"
    assert first.first_page_header.paragraphs[0].text == third.first_page_header.paragraphs[0].text == "Opening header"
    assert first.first_page_footer.paragraphs[0].text == ""
    assert second.footer.paragraphs[0].text == third.footer.paragraphs[0].text == ""
    assert second.even_page_footer.paragraphs[0].text == "Even footer"
    assert third.header.paragraphs[0].text == "New normal header"
    assert fourth.header.paragraphs[0].text == fourth.even_page_header.paragraphs[0].text == ""
    assert fifth.footer.paragraphs[0].text == "Final - a"
    assert second._sectPr.find(qn("w:pgNumType")).get(qn("w:start")) == "3"
    assert third._sectPr.find(qn("w:pgNumType")).get(qn("w:start")) is None
    assert third.header_distance.pt == 40 and third.footer_distance.pt == 24
    assert sum(node.get(qn("w:type")) == "page" for node in reopened.element.iter(qn("w:br"))) == 2
    assert [field.text for field in first.footer._element.iter(qn("w:instrText"))] == [
        r" PAGE \* ROMAN ", "NUMPAGES",
    ]
    assert [field.text for field in reopened.element.iter(qn("w:instrText"))] == [
        " PAGE ", r" PAGE \* roman ",
    ]
    with ZipFile(rebuilt) as archive:
        assert any(archive.read(name) == source_logo
                   for name in archive.namelist() if name.startswith("word/media/"))
        assert "Opening header" not in reopened.element.xml
        assert any(b"https://example.org/paper?draft=1&amp;view=full" in archive.read(name)
                   for name in archive.namelist() if name.startswith("word/_rels/header"))


@pytest.mark.parametrize("format_name", ["upperRoman", "decimal"])
def test_convert_inherited_page_field_format(
    tmp_path: Path, rust_executable: Path, format_name: str,
) -> None:
    """Keep inherited PAGE fields dynamic when the following section changes its numbering format."""
    document = Document()
    first = document.sections[0]
    first.footer.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
    add_field(first.footer.paragraphs[0], " PAGE ", "1", simple=True)
    # Inactive first/even stories must not enable layout modes during import.
    first.first_page_header.paragraphs[0].text = "Dormant first"
    first.even_page_header.paragraphs[0].text = "Dormant even"
    document.add_paragraph("Initial")
    second = document.add_section(WD_SECTION_START.NEW_PAGE)
    number = OxmlElement("w:pgNumType")
    number.set(qn("w:fmt"), format_name)
    second._sectPr.append(number)
    document.add_paragraph("Following")
    source = tmp_path / "inherited.docx"
    document.save(source)
    output = tmp_path / "converted"
    run_papper(rust_executable, tmp_path, "convert", str(source), "-o", str(output))
    markdown = (output / "inherited.md").read_text(encoding="utf-8")
    assert "Dormant" not in markdown
    rebuilt = output / "rebuilt.docx"
    run_papper(rust_executable, output, "build", "docx", "inherited.md", "--no-mathtype", "-o", str(rebuilt))
    reopened = Document(rebuilt)
    assert len(reopened.sections) == 2
    assert not reopened.settings.odd_and_even_pages_header_footer
    assert not any(section.different_first_page_header_footer for section in reopened.sections)
    assert reopened.sections[0].footer.paragraphs[0].text == "1"
    assert reopened.sections[1].footer.paragraphs[0].text == ("I" if format_name == "upperRoman" else "1")
    assert all(section.footer.paragraphs[0].alignment == WD_ALIGN_PARAGRAPH.CENTER
               for section in reopened.sections)


@pytest.mark.parametrize("note_kind", ["footnote", "endnote"])
def test_convert_layout_in_tables_and_notes_with_pretty_xml(
    tmp_path: Path, rust_executable: Path, note_kind: str,
) -> None:
    """Retain nested fields/breaks and authored marker-like text in pretty-printed OOXML."""
    document = Document()
    document.add_paragraph("Literal PAPPERLAYOUT1END")
    if note_kind == "endnote":
        number = OxmlElement("w:pgNumType")
        number.set(qn("w:fmt"), "lowerRoman")
        document.sections[0]._sectPr.append(number)
    paragraph = document.add_table(rows=1, cols=1).cell(0, 0).paragraphs[0]
    paragraph.add_run("Cell before")
    paragraph.add_run().add_break(WD_BREAK.PAGE)
    paragraph.add_run("Cell after ")
    add_field(paragraph, " PAGE ", "9", simple=True)
    note_reference = OxmlElement(f"w:{note_kind}Reference")
    note_reference.set(qn("w:id"), "1")
    document.add_paragraph("Note").add_run()._r.append(note_reference)
    source = tmp_path / "nested.docx"
    document.save(source)
    with ZipFile(source) as archive:
        parts = {entry.filename: archive.read(entry) for entry in archive.infolist()}
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    footnotes = etree.fromstring(f'<w:{note_kind}s xmlns:w="{w}"><w:{note_kind} w:id="1"><w:p/></w:{note_kind}></w:{note_kind}s>')
    note_paragraph = footnotes[0][0]
    for field_type in ("begin", "separate", "end"):
        run = etree.SubElement(note_paragraph, qn("w:r"))
        etree.SubElement(run, qn("w:fldChar"), {qn("w:fldCharType"): field_type})
        if field_type == "begin":
            etree.SubElement(run, qn("w:instrText")).text = " PAGE "
        if field_type == "separate":
            etree.SubElement(run, qn("w:t")).text = "9"
    parts[f"word/{note_kind}s.xml"] = etree.tostring(footnotes)
    rels = etree.fromstring(parts["word/_rels/document.xml.rels"])
    etree.SubElement(rels, f"{{{rels.nsmap[None]}}}Relationship", {
        "Id": "rIdLayoutNote", "Target": f"{note_kind}s.xml",
        "Type": f"http://schemas.openxmlformats.org/officeDocument/2006/relationships/{note_kind}s",
    })
    parts["word/_rels/document.xml.rels"] = etree.tostring(rels)
    types = etree.fromstring(parts["[Content_Types].xml"])
    etree.SubElement(types, f"{{{types.nsmap[None]}}}Override", {
        "PartName": f"/word/{note_kind}s.xml",
        "ContentType": f"application/vnd.openxmlformats-officedocument.wordprocessingml.{note_kind}s+xml",
    })
    parts["[Content_Types].xml"] = etree.tostring(types)
    parts["word/document.xml"] = etree.tostring(etree.fromstring(parts["word/document.xml"]), pretty_print=True)
    with ZipFile(source, "w") as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    original = source.read_bytes()
    output = tmp_path / "converted"
    run_papper(rust_executable, tmp_path, "convert", str(source), "-o", str(output))
    markdown = (output / "nested.md").read_text(encoding="utf-8")
    assert "Literal PAPPERLAYOUT1END" in markdown
    assert markdown.count('t="pageNum"') == 2
    assert markdown.count('t="pageBreak"') == 1
    assert "Cell before" in markdown and "Cell after" in markdown and "[^1]:" in markdown
    assert source.read_bytes() == original
    rebuilt = output / "rebuilt.docx"
    run_papper(rust_executable, output, "build", "docx", "nested.md", "--no-mathtype", "-o", str(rebuilt))
    reopened = Document(rebuilt)
    assert len(reopened.sections) == 1
    assert reopened.sections[0].footer.paragraphs[0].text == ""
    expected_instruction = r" PAGE \* roman " if note_kind == "endnote" else " PAGE "
    assert [node.text for node in reopened.element.iter(qn("w:instrText"))] == [expected_instruction]
    with ZipFile(rebuilt) as archive:
        assert [node.text for node in etree.fromstring(archive.read("word/footnotes.xml")).iter(qn("w:instrText"))] == [expected_instruction]


def test_convert_layout_retains_comprehensive_import_contract(
    tmp_path: Path, rust_executable: Path,
) -> None:
    """Protect equation recovery, captions, crossrefs, notes, and media when layout forces a reread."""
    case = ROOT / "tests/snapshot_cases_convert/comprehensive"
    shutil.copytree(case / "styles", tmp_path / "zotero/styles")
    with ZipFile(case / "comprehensive.docx") as archive:
        parts = {entry.filename: archive.read(entry) for entry in archive.infolist()}
    root = etree.fromstring(parts["word/document.xml"])
    body = root.find(qn("w:body"))
    paragraph = OxmlElement("w:p")
    run = OxmlElement("w:r")
    page_break = OxmlElement("w:br")
    page_break.set(qn("w:type"), "page")
    run.append(page_break)
    paragraph.append(run)
    body.insert(len(body) - 1, paragraph)
    parts["word/document.xml"] = etree.tostring(root)
    source = tmp_path / "comprehensive.docx"
    with ZipFile(source, "w") as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    output = tmp_path / "converted"
    run_papper(rust_executable, tmp_path, "convert", str(source), "-o", str(output))
    markdown = (output / "comprehensive.md").read_text(encoding="utf-8")
    expected = (case / "snapshots-content/comprehensive.md").read_text(encoding="utf-8")
    header, content = markdown[4:].split("\n---\n", 1)
    original_metadata, original_content = {}, expected
    if expected.startswith("---\n"):
        original_header, original_content = expected[4:].split("\n---\n", 1)
        original_metadata = yaml.safe_load(original_header)
    metadata = yaml.safe_load(header)
    imported_settings = metadata.pop("papperSettings")
    assert imported_settings.pop("docxFooter")["content"] == ""
    imported_settings.pop("docxHeader")
    assert imported_settings == original_metadata.pop("papperSettings")
    assert metadata == original_metadata
    assert (output / metadata["csl"]).read_bytes() == (
        case / "snapshots-content" / metadata["csl"]
    ).read_bytes()
    # Empty page stories serialize the YAML header early; the bibliography link
    # and its reference data must survive that path together.
    assert (output / metadata["bibliography"]).read_bytes() == (
        case / "snapshots-content" / metadata["bibliography"]
    ).read_bytes()
    assert content.count('[]{t="pageBreak"}') == 1
    assert content.replace('\n\n[]{t="pageBreak"}\n\n', "\n\n").lstrip("\n") == original_content.lstrip("\n")
    expected_media = {path.relative_to(case / "snapshots-content").as_posix()
                      for path in (case / "snapshots-content/media").rglob("*") if path.is_file()}
    assert {path.relative_to(output).as_posix() for path in (output / "media").rglob("*")
            if path.is_file()} == expected_media
