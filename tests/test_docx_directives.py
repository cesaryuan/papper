"""Protect native insertion output, section inheritance, and failed-build data preservation.

The comprehensive snapshot covers serialized output; these contracts exercise
optional postprocessing/native numbering and invalid source recovery separately.
"""

from __future__ import annotations

from datetime import date
import json
import os
from pathlib import Path
import subprocess
from zipfile import ZipFile

from docx import Document
from docx.oxml.ns import qn
from lxml import html
import pytest

from native_support import ROOT


def test_exported_directive_choices_render_through_the_public_cli(tmp_path: Path, rust_executable: Path) -> None:
    """Prevent editor choices drifting from accepted directives and their real Word fields."""
    exported = subprocess.run(
        [str(rust_executable), "config", "describe", "--format", "json"],
        cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", check=True, timeout=15,
    )
    element = json.loads(exported.stdout)["elementAttributes"]["emptySpan"]
    directives = []
    page_fields = 0
    for kind, variant in element["variants"].items():
        for spelling in [kind, *variant.get("aliases", [])]:
            directives.append(f'[]{{t="{spelling}"}}')
            page_fields += kind == "pageNum"
        for key, field in variant["properties"].items():
            for value in [*field.get("values", []), *field.get("suggestions", [])]:
                attributes = {element["discriminator"]: kind, key: value}
                # Alignment is meaningful only alongside an independent story.
                if kind == "sectionBreak" and key in {"headerAlignment", "footerAlignment"}:
                    attributes[key.removesuffix("Alignment")] = "Story"
                authored = " ".join(f'{name}={json.dumps(value, ensure_ascii=False)}' for name, value in attributes.items())
                directives.append(f"[]{{{authored}}}")
                page_fields += kind == "pageNum"
    source = tmp_path / "choices.md"
    source.write_text("---\npapperSettings:\n  mathtype: false\n---\n\n" + "\n\n".join(directives), encoding="utf-8")
    output = tmp_path / "choices.docx"
    result = build_insertions(rust_executable, source, output)
    assert result.returncode == 0, result.stdout + result.stderr
    document = Document(output)
    instructions = [node.text or "" for node in document.element.iter(qn("w:instrText"))]
    assert sum(" PAGE " in instruction for instruction in instructions) >= page_fields
    assert any(" TOC " in instruction for instruction in instructions)
    assert any(' DATE \\@ "yyyy年M月d日" ' in instruction for instruction in instructions)
    today = date.today()
    assert f"{today.year}年{today.month}月{today.day}日" in "".join(document.element.itertext())


def build_insertions(executable: Path, source: Path, output: Path, *, postprocess: bool = True, target: str = "docx") -> subprocess.CompletedProcess:
    """Build through the public CLI with request-local state and the selected finalization path."""
    return subprocess.run(
        [str(executable), "build", target, str(source), *(["--no-mathtype"] if target == "docx" else []), "-o", str(output)],
        cwd=output.parent,
        env={**os.environ, "PAPPER_RESOURCE_ROOT": str(ROOT), "PMT_ENABLE_DOCX_POSTPROCESS": str(postprocess).lower()},
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )


@pytest.mark.parametrize("postprocess,native", [(True, False), (True, True), (False, False)])
def test_insertions_survive_finalization_and_preserve_section_stories(
    tmp_path: Path, rust_executable: Path, postprocess: bool, native: bool,
) -> None:
    """Verify effective stories and numbering, including an unlinked copy followed by a clear."""
    source = tmp_path / "input.md"
    fixture = ROOT / "tests/snapshot_cases/docx_directives/docx_directives.md"
    text = fixture.read_text(encoding="utf-8").replace(
        "../crossrefs/figure.svg", (ROOT / "tests/snapshot_cases/crossrefs/figure.svg").as_posix(),
    ).replace("  mathtype: false", f"  mathtype: false\n  docxNativeCrossref: {str(native).lower()}")
    # A hyperlink in an independent story exercises OPC relationship relocation,
    # which plain-text output snapshots cannot distinguish from a broken target.
    text = text.replace('header="**Methods header**"',
                        'header="**Methods header** [Site](https://example.org) []{t=\'currentDate\'}"')
    source.write_text(text, encoding="utf-8")
    output = tmp_path / "output.docx"
    result = build_insertions(rust_executable, source, output, postprocess=postprocess)
    assert result.returncode == 0, result.stdout + result.stderr
    document = Document(output)
    first, methods, copied, cleared = document.sections
    assert first.header.paragraphs[0].text == "Global header"
    assert methods.header.paragraphs[0].text == copied.header.paragraphs[0].text
    assert "Methods header" in methods.header.paragraphs[0].text
    assert date.today().isoformat() in methods.header.paragraphs[0].text
    assert not methods.header.is_linked_to_previous
    assert not copied.header.is_linked_to_previous
    assert methods.header.part.partname != copied.header.part.partname
    assert copied.footer.is_linked_to_previous
    assert methods.footer.paragraphs[0].text == copied.footer.paragraphs[0].text == "Section page 1"
    assert cleared.header.paragraphs[0].text == cleared.footer.paragraphs[0].text == ""
    assert methods._sectPr.find(qn("w:pgNumType")).get(qn("w:start")) == "1"
    assert copied._sectPr.find(qn("w:pgNumType")).get(qn("w:start")) is None
    assert [section._sectPr.find(qn("w:type")).get(qn("w:val")) for section in (methods, copied, cleared)] == [
        "nextPage", "continuous", "oddPage",
    ]
    for story in (methods.header, copied.header):
        assert any(relationship.target_ref == "https://example.org" for relationship in story.part.rels.values())
    assert sum(paragraph.style.name == "Papper Figure Caption" for paragraph in document.paragraphs) == 2
    assert sum(paragraph.style.name == "Papper Table Caption" for paragraph in document.paragraphs) == 1
    # Both panel captions stay in layout-table cells and are available only to the expanded list.
    panel_paragraphs = document.element.xpath('.//w:tc/w:p[w:pPr/w:pStyle[@w:val="PapperSubfigureCaption"]]')
    assert len(panel_paragraphs) == 2
    assert document.settings.element.find(qn("w:updateFields")).get(qn("w:val")) == "true"
    assert any(value.get(qn("w:type")) == "page" for value in document.element.iter(qn("w:br")))
    with ZipFile(output) as archive:
        for name in archive.namelist():
            if name.startswith("word/") and name.endswith(".xml"):
                assert b"PMT_DOCX_" not in archive.read(name)
                assert b"PMT_FOOTER_PAGE:" not in archive.read(name)


@pytest.mark.parametrize("markdown,message", [
    ('[]{t="toc" depth="10"}', "depth must be between"),
    ('[]{t="listofFigures" includeSubfigures="yes"}', "must be true or false"),
    ('[]{t="sectionBreak" pageStart="-1"}', "pageStart must be an integer"),
    ('[]{t="sectionBreak" header="Text" headerLink="true"}', "conflicts with headerLink"),
    ('Text []{t="toc"}', "must occupy its own paragraph"),
    ('> []{t="sectionBreak"}', "top-level body paragraph"),
    ('1. []{t="sectionBreak"}', "top-level body paragraph"),
    ('[Text]{t="pageBreak"}', "spans must be empty"),
    ('[]{t="date" format="unsupported"}', "Unsupported DOCX date format"),
    ('[]{t="unknown"}', "Unsupported DOCX directive"),
])
def test_invalid_insertions_fail_without_replacing_existing_output(
    tmp_path: Path, rust_executable: Path, markdown: str, message: str,
) -> None:
    """Catch invalid layout requests before publishing over an existing output file."""
    source = tmp_path / "input.md"
    source.write_text(markdown, encoding="utf-8")
    output = tmp_path / "output.docx"
    output.write_bytes(b"existing output")
    result = build_insertions(rust_executable, source, output)
    assert result.returncode != 0
    assert message in result.stdout + result.stderr
    assert output.read_bytes() == b"existing output"


def test_native_insertions_have_no_effect_on_html(tmp_path: Path, rust_executable: Path) -> None:
    """A DOCX-only directive must not create directories or page-story content in HTML."""
    source = tmp_path / "input.md"
    source.write_text('Before []{t="date"}.\n\n[]{t="toc" title="DOCX contents"}\n\n'
                      '[]{t="sectionBreak" header="DOCX header"}\n\nAfter.\n', encoding="utf-8")
    output = tmp_path / "output.html"
    result = build_insertions(rust_executable, source, output, target="html")
    assert result.returncode == 0, result.stdout + result.stderr
    text = html.fromstring(output.read_text(encoding="utf-8")).text_content()
    assert "Before" in text and "After." in text
    assert "DOCX contents" not in text and "DOCX header" not in text
