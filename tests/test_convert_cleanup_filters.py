"""Verify Convert caption pairing and TOC cleanup with real Pandoc output.

Markdown fixtures snapshot each filter independently, including nested content
and near misses. A generated Word document exercises the public Convert chain
to protect caption text, bookmark references, extracted images and dimensions.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from native_support import ROOT, native_pandoc_executable
from snapshot_utils import assert_snapshot


@pytest.mark.parametrize(("case", "filter_name"), [
    ("figure_captions", "figure_captions"),
    ("toc_cleanup", "remove_toc_anchors"),
])
def test_convert_cleanup_filter_snapshot(
    case: str, filter_name: str, tmp_path: Path, snapshot_update: bool,
) -> None:
    """Protect actual imported syntax and content preservation for each new filter."""
    pandoc = native_pandoc_executable()
    assert pandoc is not None, "Convert filters require the retained Pandoc engine"
    fixture = ROOT / "tests/snapshot_cases_convert" / case
    result = subprocess.run(
        [str(pandoc), str(fixture / "input.md"), "--from=markdown", "--to=markdown", "--wrap=none",
         "--lua-filter", str(ROOT / "pandoc/filters/convert" / f"{filter_name}.lua")],
        cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert_snapshot(result.stdout, fixture / "snapshots-content" / f"{case}.md", update=snapshot_update)


def test_paired_image_crossrefs_snapshot(tmp_path: Path, snapshot_update: bool) -> None:
    """Catch missed figure references when caption pairing leaves Para/Image AST nodes."""
    pandoc = native_pandoc_executable()
    assert pandoc is not None, "Convert filters require the retained Pandoc engine"
    fixture = ROOT / "tests/snapshot_cases_convert/paired_crossrefs"
    result = subprocess.run(
        [str(pandoc), str(fixture / "input.md"), "--from=markdown", "--to=markdown", "--wrap=none",
         "--lua-filter", str(ROOT / "pandoc/filters/convert/figure_captions.lua"),
         "--lua-filter", str(ROOT / "pandoc/filters/convert/crossrefs.lua")],
        cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "@fig:_Ref202795969" in result.stdout
    assert '[First bookmark](#_RefFirst)' in result.stdout
    assert '[Inline picture](#_RefInline)' in result.stdout
    assert_snapshot(
        result.stdout, fixture / "snapshots-content/paired_crossrefs.md", update=snapshot_update,
    )


def test_convert_pairs_word_captions_and_removes_nested_toc_links(
    tmp_path: Path, rust_executable: Path, snapshot_update: bool,
) -> None:
    """Pair real Word captions, clean TOC links and resolve inbound figure references."""
    from docx import Document
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Inches

    document = Document()
    document.add_picture(str(ROOT / "template/examples/images/single-figure-example.png"), width=Inches(1))
    caption = document.add_paragraph()
    # Nested Word bookmarks reproduce the user's nested Span pattern in Pandoc.
    for bookmark_id, name in [(1, "_Ref181174213"), (2, "_Toc241697898")]:
        start = OxmlElement("w:bookmarkStart")
        start.set(qn("w:id"), str(bookmark_id))
        start.set(qn("w:name"), name)
        caption._p.append(start)
    for bookmark_id in (2, 1):
        end = OxmlElement("w:bookmarkEnd")
        end.set(qn("w:id"), str(bookmark_id))
        caption._p.append(end)
    caption.add_run("图1‑11 区域桥隧网络脆弱节点识别结果")
    toc = document.add_paragraph()
    # Markdown readers reject nested links; real nested Word links are needed
    # here to verify that removing both wrappers preserves the page number.
    outer = OxmlElement("w:hyperlink")
    outer.set(qn("w:anchor"), "_Toc241697943")
    text_run = document.add_paragraph("图2‑35 各时段核密度估计 ")._p[0]
    document._body._body.remove(text_run.getparent())
    outer.append(text_run)
    inner = OxmlElement("w:hyperlink")
    inner.set(qn("w:anchor"), "_Toc241697943")
    page_run = document.add_paragraph("78")._p[0]
    document._body._body.remove(page_run.getparent())
    inner.append(page_run)
    outer.append(inner)
    toc._p.append(outer)
    reference = document.add_paragraph()
    link = OxmlElement("w:hyperlink")
    link.set(qn("w:anchor"), "_Ref181174213")
    run = document.add_paragraph("Figure reference")._p[0]
    document._body._body.remove(run.getparent())
    link.append(run)
    reference._p.append(link)
    source = tmp_path / "captions.docx"
    document.save(source)
    output = tmp_path / "converted"
    result = subprocess.run(
        [str(rust_executable), "convert", str(source), "-o", str(output)], cwd=tmp_path,
        env={**os.environ, "PAPPER_RESOURCE_ROOT": str(ROOT), "PAPPER_HOME": str(tmp_path / "home")},
        capture_output=True, text=True, encoding="utf-8", timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    markdown = (output / "captions.md").read_text(encoding="utf-8")
    assert "_Toc" not in markdown
    assert "![图1‑11 区域桥隧网络脆弱节点识别结果](media/" in markdown
    assert "#fig:_Ref181174213" in markdown
    assert 'width="1.0in"' in markdown
    assert "图2‑35 各时段核密度估计 78" in markdown
    assert "@fig:_Ref181174213" in markdown
    assert len(list((output / "media").glob("*.png"))) == 1
    assert_snapshot(
        markdown, ROOT / "tests/snapshot_cases_convert/word_cleanup/snapshots-content/word_cleanup.md",
        update=snapshot_update,
    )
