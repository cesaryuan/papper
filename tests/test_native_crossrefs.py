"""Verify native Word references through the public manuscript build command."""

from __future__ import annotations

import os
from collections import Counter
from pathlib import Path
import shutil
import subprocess
from native_support import native_pandoc_executable, papper_command
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
pytestmark = pytest.mark.skipif(
    native_pandoc_executable() is None and
    (shutil.which("pandoc") is None or shutil.which("pandoc-crossref") is None),
    reason="cross-reference builds require the native engine or standalone Pandoc and crossref",
)


def prepare_project(project: Path, manuscript: str, native_crossref: bool, **metadata) -> None:
    """Create a self-contained manuscript project with explicit style choices."""
    (project / "paper.md").write_text(manuscript, encoding="utf-8")
    shutil.copyfile(ROOT / "tests/snapshot_cases/crossrefs/figure.svg", project / "figure.svg")
    (project / "style.yml").write_text(
        yaml.safe_dump({"mathtype": False, "docxNativeCrossref": native_crossref, "pandocMetadata": metadata}),
        encoding="utf-8",
    )


def build_document(project: Path, target: str = "docx") -> tuple[Path, str]:
    """Run the current Rust CLI to cover style parsing and the actual filter chain."""
    output = project / f"result.{target}"
    environment = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run(
        [*papper_command(), "build", target, "paper.md", "-o", str(output)],
        cwd=project,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return output, result.stdout + result.stderr


def read_native_content(path: Path) -> tuple[list[dict[str, str]], dict[str, str], ET.Element]:
    """Read complete fields and their visible bookmark text in document order."""
    with ZipFile(path) as package:
        root = ET.fromstring(package.read("word/document.xml"))
    fields, stack, bookmarks, active = [], [], {}, {}
    for element in root.iter():
        if element.tag == W + "bookmarkStart":
            identifier, name = element.get(W + "id"), element.get(W + "name")
            assert identifier not in active and name not in bookmarks
            active[identifier] = name
            bookmarks[name] = ""
        elif element.tag == W + "bookmarkEnd":
            assert element.get(W + "id") in active
            del active[element.get(W + "id")]
        elif element.tag == W + "fldChar":
            kind = element.get(W + "fldCharType")
            if kind == "begin":
                stack.append({"code": "", "result": "", "separated": False})
            elif kind == "separate":
                assert stack and not stack[-1]["separated"]
                stack[-1]["separated"] = True
            elif kind == "end":
                assert stack and stack[-1].pop("separated")
                record = stack.pop()
                record["code"] = record["code"].strip()
                fields.append(record)
        elif element.tag == W + "instrText" and stack:
            stack[-1]["code"] += element.text or ""
        elif element.tag == W + "t":
            for name in active.values():
                bookmarks[name] += element.text or ""
            for record in stack:
                if record["separated"]:
                    record["result"] += element.text or ""
    assert not stack and not active
    return fields, bookmarks, root


def assert_reference_targets(fields: list[dict[str, str]], bookmarks: dict[str, str]) -> list[str]:
    """Assert each REF cache matches its destination's exact displayed text."""
    values = []
    for field in fields:
        if field["code"].startswith("REF "):
            name = field["code"].split()[1]
            assert name in bookmarks
            if "\\r" not in field["code"]:
                assert bookmarks[name] == field["result"]
            values.append(field["result"])
    return values


@pytest.mark.parametrize("native_crossref", [False, True])
@pytest.mark.parametrize("name_in_link", [False, True])
def test_docx_style_selects_legacy_or_native_crossrefs(
    tmp_path: Path, native_crossref: bool, name_in_link: bool,
) -> None:
    """Preserve old hyperlinks when disabled and native forward references when enabled."""
    manuscript = """---
docxNativeCrossref: HEADER_SETTING
---

# Overview {#sec:overview}

See @fig:first, @fig:last, @tbl:values, @eq:model, and @sec:overview.

[External source](https://example.com/).

![First caption includes year 2024.](figure.svg){#fig:first}

![An unreferenced middle figure.](figure.svg){#fig:middle}

![The final figure.](figure.svg){#fig:last}

| Value | Meaning |
| --- | --- |
| 1 | baseline |

: Table caption. {#tbl:values}

$$
x^2 = 1
$$ {#eq:model}
""".replace("HEADER_SETTING", str(not native_crossref).lower())
    prepare_project(tmp_path, manuscript, native_crossref, nameInLink=name_in_link)
    output, log = build_document(tmp_path)
    fields, bookmarks, root = read_native_content(output)
    assert assert_reference_targets(fields, bookmarks) == (["1", "3", "1", "1", "1"] if native_crossref else [])
    sequences = [field for field in fields if field["code"].startswith("SEQ ")]
    assert len(sequences) == (5 if native_crossref else 0)
    if native_crossref:
        assert [field["result"] for field in sequences] == ["1", "2", "3", "1", "1"]
    anchored = [link for link in root.iter(W + "hyperlink") if link.get(W + "anchor")]
    assert len(anchored) == (0 if native_crossref else 5)
    assert len(list(root.iter(W + "hyperlink"))) == (1 if native_crossref else 6)
    heading = next(paragraph for paragraph in root.iter(W + "p") if "Overview" in "".join(node.text or "" for node in paragraph.iter(W + "t")))
    heading_text = "".join(node.text or "" for node in heading.iter(W + "t"))
    assert heading_text == ("Overview" if native_crossref else "1 Overview")
    assert (heading.find(W + "pPr/" + W + "numPr") is not None) is native_crossref
    section_refs = [item for item in fields if "\\r \\h" in item["code"]]
    assert len(section_refs) == (1 if native_crossref else 0)
    assert len(list(root.iter(W + "drawing"))) == 3
    assert "No marked number" not in log


@pytest.mark.parametrize("native_crossref", [False, True])
def test_chinese_refs_preserve_chapter_numbers_and_normalized_sections(tmp_path: Path, native_crossref: bool) -> None:
    """Keep chapter prefixes and reset SEQ item numbers at the next chapter."""
    manuscript = """---
lang: zh-CN
---

# 第一章

# 第二章

# 第三章

## 第一节 {#sec:first}

参见 @fig:first、@tbl:first、@eq:first 和 @sec:first。

![图一](figure.svg){#fig:first}

| 值 |
| --- |
| 1 |

: 表一 {#tbl:first}

$$
x=1
$$ {#eq:first}

# 第四章

参见 @fig:second、@tbl:second 和 @eq:second。

![图二](figure.svg){#fig:second}

| 值 |
| --- |
| 2 |

: 表二 {#tbl:second}

$$
x=2
$$ {#eq:second}
"""
    prepare_project(tmp_path, manuscript, native_crossref)
    output, _ = build_document(tmp_path)
    fields, bookmarks, _ = read_native_content(output)
    assert assert_reference_targets(fields, bookmarks) == (["3-1", "3-1", "3-1", "3.1", "4-1", "4-1", "4-1"] if native_crossref else [])
    sequences = [field for field in fields if field["code"].startswith("SEQ ")]
    assert len(sequences) == (6 if native_crossref else 0)
    if native_crossref:
        assert all("\\s 1" in field["code"] for field in sequences)
        assert len([field for field in fields if field["code"].startswith("STYLEREF ")]) == 6


@pytest.mark.parametrize("native_crossref", [False, True])
@pytest.mark.parametrize("link_references", [False, True])
def test_bilingual_caption_numbers_share_one_sequence_and_bookmark(
    tmp_path: Path, native_crossref: bool, link_references: bool,
) -> None:
    """Keep bilingual captions on one sequence with valid targets in both link modes."""
    manuscript = r'''---
lang: zh-CN
---

# 第一章

# 第二章

# 第三章

参见 @fig:first 和 @tbl:first。

![中文图一](figure.svg){#fig:first caption-en="Architecture of the *network* with $\\alpha$"}

![中文图二](figure.svg){#fig:second}

| 模型 | 得分 |
| --- | --- |
| A | 1 |

: 中文表一 {#tbl:first caption-en="Comparison of **models**" revision_rows="*"}

| 模型 | 得分 |
| --- | --- |
| B | 2 |

: 中文表二 {#tbl:second}

# 第四章

![中文图三](figure.svg){#fig:third caption-en="Next chapter"}
'''
    prepare_project(tmp_path, manuscript, native_crossref, linkReferences=link_references)
    output, log = build_document(tmp_path)
    assert "No marked number" not in log
    fields, bookmarks, _ = read_native_content(output)
    sequences = [item for item in fields if item["code"].startswith("SEQ ")]
    assert [item["result"] for item in sequences] == (["1", "2", "1", "2", "1"] if native_crossref else [])
    values = assert_reference_targets(fields, bookmarks)
    expected = ["3-1", "3-1", "4-1"]
    if link_references:
        expected = ["3-1", "3-1", *expected]
    assert values == (expected if native_crossref else [])
    assert "keeping its Link" not in log


def test_explicit_unlinked_references_keep_plain_text_with_native_crossref(tmp_path: Path) -> None:
    """Respect linkReferences:false while still producing native caption numbering."""
    prepare_project(tmp_path, "See @fig:one.\n\n![Caption](figure.svg){#fig:one}\n", True, linkReferences=False)
    output, _ = build_document(tmp_path)
    fields, bookmarks, root = read_native_content(output)
    assert not assert_reference_targets(fields, bookmarks)
    assert len(fields) == 1 and fields[0]["code"].startswith("SEQ ")
    assert "Figure" in "".join(element.text or "" for element in root.iter(W + "t"))


def test_native_crossref_style_does_not_rewrite_json_crossrefs(tmp_path: Path) -> None:
    """Keep the JSON build's normal Link nodes for reply probes and AST inspection."""
    prepare_project(tmp_path, "See @fig:one.\n\n![Caption](figure.svg){#fig:one}\n", True)
    output, _ = build_document(tmp_path, "json")
    text = output.read_text(encoding="utf-8")
    assert '"Link"' in text and "#fig:one" in text
    assert "PapperRef" not in text and "pmt-native-" not in text and "SEQ Figure" not in text


def test_ambiguous_custom_caption_keeps_a_working_link(tmp_path: Path) -> None:
    """Keep custom captions readable instead of emitting a REF to a wrong range."""
    prepare_project(
        tmp_path,
        "See @fig:one.\n\n![Custom caption](figure.svg){#fig:one}\n",
        True,
        figureTemplate="$$figureTitle$$ $$i$$/$$i$$ $$t$$",
    )
    output, log = build_document(tmp_path)
    fields, bookmarks, root = read_native_content(output)
    assert not fields
    anchors = [link.get(W + "anchor") for link in root.iter(W + "hyperlink")]
    assert len(anchors) == 1 and "Figure 1/1 Custom caption" in bookmarks[anchors[0]]
    assert "Figure 1/1 Custom caption" in "".join(element.text or "" for element in root.iter(W + "t"))
    assert "Ambiguous number template" in log


def test_native_crossrefs_respect_disabled_section_numbering(tmp_path: Path) -> None:
    """Keep unnumbered headings untouched while still enabling native figure references."""
    prepare_project(tmp_path, "# Overview\n\nSee @fig:one.\n\n![Caption](figure.svg){#fig:one}\n", True, numberSections=False)
    output, _ = build_document(tmp_path)
    fields, bookmarks, root = read_native_content(output)
    assert assert_reference_targets(fields, bookmarks) == ["1"]
    heading = next(paragraph for paragraph in root.iter(W + "p") if "Overview" in "".join(node.text or "" for node in paragraph.iter(W + "t")))
    assert heading.find(W + "pPr/" + W + "numPr") is None


def test_ambiguous_heading_template_preserves_legacy_reference(tmp_path: Path) -> None:
    """Keep repeated custom heading numbers intact rather than binding the wrong range."""
    prepare_project(tmp_path, "# Overview {#sec:one}\n\nSee @sec:one.\n", True, secHeaderTemplate="$$i$$/$$i$$ $$t$$")
    output, log = build_document(tmp_path)
    fields, bookmarks, root = read_native_content(output)
    assert not fields
    anchors = [link.get(W + "anchor") for link in root.iter(W + "hyperlink")]
    assert len(anchors) == 1 and bookmarks[anchors[0]].startswith("1/1 Overview")
    assert "1/1 Overview" in "".join(node.text or "" for node in root.iter(W + "t"))
    assert "Custom heading numbering" in log


@pytest.mark.parametrize(
    ("figure_title", "table_title", "figure_sequence", "table_sequence"),
    [
        ("  Supplementary Figure\t", " Table A ", '"Supplementary Figure"', '"Table A"'),
        ("\u00a0图\u3000", "\u3000表\u00a0", "图", "表"),
        ("", "   ", "Figure", "Table"),
    ],
)
def test_native_sequences_follow_trimmed_caption_titles(
    tmp_path: Path, figure_title: str, table_title: str, figure_sequence: str, table_sequence: str,
) -> None:
    """Keep custom and localized caption sequences usable, including empty titles."""
    prepare_project(
        tmp_path,
        """See @fig:first, @fig:second, @tbl:first, @tbl:second, and @eq:one.

![First figure](figure.svg){#fig:first}

![Second figure](figure.svg){#fig:second}

| Value |
| --- |
| 1 |

: First table {#tbl:first}

| Value |
| --- |
| 2 |

: Second table {#tbl:second}

$$
x=1
$$ {#eq:one}
""",
        True,
        figureTitle=figure_title,
        tableTitle=table_title,
        numberSections=False,
    )
    output, _ = build_document(tmp_path)
    fields, bookmarks, _ = read_native_content(output)
    assert assert_reference_targets(fields, bookmarks) == ["1", "2", "1", "2", "1"]
    sequences = [field for field in fields if field["code"].startswith("SEQ ")]
    assert [field["code"] for field in sequences] == [
        f"SEQ {name} \\* ARABIC"
        for name in [figure_sequence, figure_sequence, table_sequence, table_sequence, "Equation"]
    ]


@pytest.mark.parametrize("number_sections", [False, True])
def test_independent_native_builds_have_distinct_paired_bookmarks(tmp_path: Path, number_sections: bool) -> None:
    """Keep references independent when combining builds with identical source labels."""
    manuscript = """# Overview {#sec:one}

See @fig:one and @tbl:one.

[Custom target]{#custom-target}. [Jump](#custom-target).

Footnote[^one].

[^one]: [Footnote target]{#note-target}. See @fig:one.

![Caption](figure.svg){#fig:one}

| Value |
| --- |
| 1 |

: Table caption {#tbl:one}
"""
    documents = []
    for name in ["first", "second"]:
        project = tmp_path / name
        project.mkdir()
        prepare_project(project, manuscript, True, numberSections=number_sections)
        output, _ = build_document(project)
        fields, bookmarks, root = read_native_content(output)
        assert assert_reference_targets(fields, bookmarks) == ["1", "1"]
        native_names = {name for name in bookmarks if name.lstrip("_").startswith("PapperRef_")}
        assert native_names
        # Word requires legal names; Pandoc releases may hide them with a prefix.
        assert all(len(name) <= 40 and all(char.isalnum() or char == "_" for char in name) for name in native_names)
        anchors = [link.get(W + "anchor") for link in root.iter(W + "hyperlink")]
        assert len(anchors) == 1 and bookmarks[anchors[0]] == "Custom target"
        identifiers = set()
        with ZipFile(output) as package:
            for part_name in package.namelist():
                if not part_name.startswith("word/") or not part_name.endswith(".xml"):
                    continue
                part_root = ET.fromstring(package.read(part_name))
                starts = Counter(node.get(W + "id") for node in part_root.iter(W + "bookmarkStart"))
                ends = Counter(node.get(W + "id") for node in part_root.iter(W + "bookmarkEnd"))
                assert starts == ends and all(count == 1 for count in starts.values())
                assert identifiers.isdisjoint(starts)
                assert all(0 < int(identifier) < 2**31 for identifier in starts)
                identifiers.update(starts)
            footnotes = ET.fromstring(package.read("word/footnotes.xml"))
            footnote_refs = [node.text.strip().split()[1] for node in footnotes.iter(W + "instrText") if node.text.strip().startswith("REF ")]
            assert len(footnote_refs) == 1 and footnote_refs[0] in native_names
        documents.append((fields, bookmarks, native_names, identifiers))
    first, second = documents
    assert first[2].isdisjoint(second[2]) and first[3].isdisjoint(second[3])
    assert assert_reference_targets(first[0] + second[0], first[1] | second[1]) == ["1", "1", "1", "1"]


def prepare_citation_project(project: Path, native_crossref: bool, csl: Path, **metadata) -> None:
    """Create numeric citations covering repeats, narrative citations, and a collapsed range."""
    prepare_project(
        project,
        """---
bibliography: references.bib
---

Single [@alpha2020].

Range [@alpha2020; @beta2021; @gamma2022].

Repeated [@beta2021].

Narrative @alpha2020.
""",
        native_crossref,
        csl=csl.as_posix(),
        **{"link-citations": True, "numberSections": False, **metadata},
    )
    shutil.copyfile(ROOT / "tests/snapshot_cases/references/references.bib", project / "references.bib")


def document_paragraphs(root: ET.Element) -> list[str]:
    """Read visible paragraph text for comparisons of citation and bibliography formatting."""
    return [
        "".join(node.text or "" for node in paragraph.iter(W + "t"))
        for paragraph in root.iter(W + "p")
    ]


@pytest.mark.parametrize("native_crossref", [False, True])
@pytest.mark.parametrize(
    ("style_name", "superscript"),
    [
        ("elsevier-vancouver.csl", False),
        ("GB-T-7714—2015（顺序编码，双语，姓名不大写，无URL、DOI）.csl", True),
        ("sage-vancouver.csl", True),
        ("sage-vancouver-brackets.csl", False),
    ],
)
def test_numeric_citations_keep_csl_display_and_reference_bibliography_numbers(
    tmp_path: Path, native_crossref: bool, style_name: str, superscript: bool,
) -> None:
    """Keep CSL punctuation and superscripts while switching hyperlinks to real Word fields."""
    prepare_citation_project(tmp_path, native_crossref, ROOT / "pandoc/csl" / style_name)
    output, _ = build_document(tmp_path)
    fields, bookmarks, root = read_native_content(output)
    assert assert_reference_targets(fields, bookmarks) == (["1", "1", "3", "2", "1"] if native_crossref else [])
    assert [field["result"] for field in fields if field["code"].startswith("SEQ ")] == (["1", "2", "3"] if native_crossref else [])
    links = [link.get(W + "anchor") for link in root.iter(W + "hyperlink") if link.get(W + "anchor")]
    # Verify citation destinations rather than Pandoc's version-dependent bookmark names.
    assert [next(year for year in ["2020", "2021", "2022"] if year in bookmarks[anchor]) for anchor in links] == (
        [] if native_crossref else ["2020", "2020", "2022", "2021", "2020"]
    )
    text = "\n".join(document_paragraphs(root))
    assert "1–3" in text and "2020" in text and "2021" in text and "2022" in text
    if style_name != "sage-vancouver.csl":
        assert "[1–3]" in text and "[1]" in text
    for paragraph in list(root.iter(W + "p"))[:4]:
        for run in paragraph.iter(W + "r"):
            visible = "".join(node.text or "" for node in run.iter(W + "t"))
            if visible.isdigit():
                alignment = run.find(W + "rPr/" + W + "vertAlign")
                assert (alignment is not None and alignment.get(W + "val") == "superscript") == superscript


@pytest.mark.parametrize("customization", ["inline-labels", "locators", "ambiguous-labels"])
def test_custom_numeric_csl_preserves_locators_and_rejects_ambiguous_labels(
    tmp_path: Path, customization: str,
) -> None:
    """Preserve citation affixes and page numbers without treating them as reference numbers."""
    style = (ROOT / "pandoc/csl/elsevier-vancouver.csl").read_text(encoding="utf-8")
    if customization == "inline-labels":
        style = style.replace(' second-field-align="flush"', "")
    elif customization == "locators":
        style = style.replace(
            '<text variable="citation-number"/>',
            '<text variable="citation-number" prefix="(" suffix=")"/><text variable="locator" prefix=", p. "/>',
            1,
        )
    else:
        style = style.replace('prefix="[" suffix="]"/>', 'prefix="[" suffix="/1]"/>')
    csl = tmp_path / "custom.csl"
    csl.write_text(style, encoding="utf-8")
    prepare_citation_project(tmp_path, True, csl)
    if customization == "locators":
        (tmp_path / "paper.md").write_text(
            "---\nbibliography: references.bib\n---\n\n"
            "[@alpha2020, p. 42]; [see 7 in @beta2021, p. 2021].\n",
            encoding="utf-8",
        )
    output, log = build_document(tmp_path)
    fields, bookmarks, root = read_native_content(output)
    if customization == "ambiguous-labels":
        assert not fields and "Unsupported bibliography number labels" in log
        assert any("2020" in bookmarks.get(link.get(W + "anchor"), "") for link in root.iter(W + "hyperlink"))
    else:
        expected = ["1", "2"] if customization == "locators" else ["1", "1", "3", "2", "1"]
        assert assert_reference_targets(fields, bookmarks) == expected
        assert not any(link.get(W + "anchor") for link in root.iter(W + "hyperlink"))
        if customization == "locators":
            assert "[(1), p. 42]; [see 7 in (2), p. 2021]." in "\n".join(document_paragraphs(root))


def test_author_date_citations_keep_their_original_citeproc_output(tmp_path: Path) -> None:
    """Keep author names and years intact rather than misinterpreting years as citation numbers."""
    default_style = subprocess.run(
        ["pandoc", "--print-default-data-file", "default.csl"],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout
    csl = tmp_path / "author-date.csl"
    csl.write_text(default_style, encoding="utf-8")
    documents = []
    for native in [False, True]:
        project = tmp_path / str(native)
        project.mkdir()
        prepare_citation_project(project, native, csl)
        output, _ = build_document(project)
        fields, bookmarks, root = read_native_content(output)
        assert not fields
        assert any("2020" in bookmarks.get(link.get(W + "anchor"), "") for link in root.iter(W + "hyperlink"))
        documents.append(document_paragraphs(root))
    assert documents[0] == documents[1]
    assert "Alpha 2020" in "\n".join(documents[1])


def test_explicit_unlinked_citations_keep_plain_text(tmp_path: Path) -> None:
    """Respect link-citations:false while keeping bibliography numbering editable in Word."""
    prepare_citation_project(tmp_path, True, ROOT / "pandoc/csl/elsevier-vancouver.csl", **{"link-citations": False})
    output, _ = build_document(tmp_path)
    fields, _, root = read_native_content(output)
    assert [field["result"] for field in fields] == ["1", "2", "3"]
    assert all(field["code"].startswith("SEQ ") for field in fields)
    assert "Range [1–3]." in "\n".join(document_paragraphs(root))


def test_native_citations_preserve_the_configured_range_delimiter(tmp_path: Path) -> None:
    """Keep Papper's citation range delimiter around independently updatable REF endpoints."""
    prepare_citation_project(tmp_path, True, ROOT / "pandoc/csl/elsevier-vancouver.csl")
    style_file = tmp_path / "style.yml"
    settings = yaml.safe_load(style_file.read_text(encoding="utf-8"))
    settings["citationNumberRangeDelimiter"] = "~"
    style_file.write_text(yaml.safe_dump(settings), encoding="utf-8")
    output, _ = build_document(tmp_path)
    fields, bookmarks, root = read_native_content(output)
    assert assert_reference_targets(fields, bookmarks) == ["1", "1", "3", "2", "1"]
    assert "Range [1~3]." in "\n".join(document_paragraphs(root))


@pytest.mark.parametrize("postprocess", [False, True])
def test_native_citations_cover_footnotes_uncited_entries_and_existing_crossrefs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, postprocess: bool,
) -> None:
    """Keep bibliography REF targets valid across stories and alongside figure/heading fields."""
    monkeypatch.setenv("PMT_ENABLE_DOCX_POSTPROCESS", str(postprocess).lower())
    prepare_citation_project(tmp_path, True, ROOT / "pandoc/csl/elsevier-vancouver.csl", numberSections=True)
    (tmp_path / "paper.md").write_text(
        """---
bibliography: references.bib
nocite: '@*'
---

# Overview {#sec:one}

See @sec:one, @fig:one, and [@alpha2020]. Footnote[^one].

[^one]: Cited again [@alpha2020; @beta2021].

![Caption](figure.svg){#fig:one}
""",
        encoding="utf-8",
    )
    output, _ = build_document(tmp_path)
    fields, bookmarks, _ = read_native_content(output)
    assert assert_reference_targets(fields, bookmarks) == ["1", "1", "1"]
    assert len([field for field in fields if field["code"].startswith("SEQ ")]) == 4
    with ZipFile(output) as package:
        footnotes = ET.fromstring(package.read("word/footnotes.xml"))
    references = [node.text.strip().split()[1] for node in footnotes.iter(W + "instrText") if node.text.strip().startswith("REF ")]
    assert len(references) == 2
    assert [bookmarks[name] for name in references] == ["1", "2"]
    assert any(bookmarks[name] == "3" for name in bookmarks if name.lstrip("_").startswith("PapperRef_"))


def test_native_citations_leave_json_output_unchanged(tmp_path: Path) -> None:
    """Keep bibliography and citation fields exclusive to the manuscript DOCX target."""
    prepare_citation_project(tmp_path, True, ROOT / "pandoc/csl/elsevier-vancouver.csl")
    output, _ = build_document(tmp_path, "json")
    text = output.read_text(encoding="utf-8")
    assert '"Cite"' in text and "alpha2020" in text
    assert "PapperRef" not in text and "PapperBibliography" not in text
