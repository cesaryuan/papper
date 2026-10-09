"""Build DOCX fixtures, export through Word and compare every rendered PDF page.

Run ``uv run --no-sync pytest tests/test_docx_visual.py --visual`` on Windows with
Microsoft Word. Baselines are explicit (--visual-update) and separate from ZIP/XML
content snapshots. Each export opens a unique read-only copy, closes only that copy,
and verifies existing user documents stay open; it never quits the Word application.
PyMuPDF rasterizes print-quality PDFs at 144 DPI, so pagination, fonts, formulas,
tables, revision text and image layout are tested independently of XML serialization.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import pymupdf
from docx import Document
from docx.shared import RGBColor

from docx_visual_support import VISUAL_PLATFORM, VISUAL_ROOT, WordVisualSession
from native_support import papper_command
from test_build_snapshots import CASE_ROOT, CASES, build_case, copy_case


@pytest.fixture(scope="module")
def word_visual_session(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> WordVisualSession:
    """Probe the real Word renderer once without altering global application settings."""
    return WordVisualSession(tmp_path_factory.mktemp("word-visual"), request.config.getoption("--visual-update"))


@pytest.mark.visual
@pytest.mark.parametrize("case_name", [*CASES, "reply"])
def test_docx_render_matches_visual_snapshot(
    case_name: str, tmp_path: Path, word_visual_session: WordVisualSession,
) -> None:
    """Protect actual Word page layout for manuscripts and reviewer replies."""
    project = tmp_path / case_name
    if case_name == "reply":
        copy_case(CASE_ROOT / "reply", project)
        output = project / "rendered.docx"
        result = subprocess.run(
            [*papper_command(), "build", "docx", str(project / "reply.md"),
             "--manuscript-line-source", str(project / "manuscript.pdf"), "-o", str(output)],
            cwd=project, capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        case_dir, markdown = CASES[case_name]
        copy_case(case_dir, project)
        if case_name == "bilingual_captions":
            # Relative sibling image references must resolve before Word pagination.
            copy_case(CASES["crossrefs"][0], tmp_path / "crossrefs")
        output = project / "rendered.docx"
        style_file = project / "style.yml" if case_name in {"native_crossrefs", "equation_attributes_no_mathtype"} else None
        build_case(project, markdown, "docx", output, style_file=style_file)
    baseline = CASE_ROOT / case_name / "snapshots-visual" / VISUAL_PLATFORM / "docx-word"
    results = VISUAL_ROOT / "results/docx" / case_name
    word_visual_session.check(output, baseline, results)


@pytest.mark.visual
def test_docx_visual_reports_color_and_pagination_changes(
    tmp_path: Path, word_visual_session: WordVisualSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify real Word rendering rejects changed text color and added pages with useful artifacts."""
    source = tmp_path / "layout.docx"
    document = Document()
    run = document.add_paragraph().add_run("DOCX visual regression contract")
    document.save(source)
    images, geometry = word_visual_session.capture(source, tmp_path / "original")
    baseline = tmp_path / "baseline"
    baseline.mkdir()
    for index, image in enumerate(images, 1):
        (baseline / f"page-{index:03d}.png").write_bytes(image)
    (baseline / "pages.json").write_text(json.dumps({"pages": geometry}), encoding="utf-8")
    # Even baseline-generation runs must independently prove visible changes fail.
    monkeypatch.setattr(word_visual_session, "update", False)
    run.font.color.rgb = RGBColor(255, 0, 0)
    document.save(source)
    color_results = tmp_path / "color-difference"
    with pytest.raises(AssertionError, match="Visual mismatch"):
        word_visual_session.check(source, baseline, color_results)
    difference = json.loads((color_results / "page-001/difference.json").read_text(encoding="utf-8"))
    assert difference["changed_pixels"] > 0, "Changed text color produced no visible difference artifact"

    document.add_page_break()
    document.add_paragraph("Additional DOCX page")
    document.save(source)
    pagination_results = tmp_path / "pagination-difference"
    with pytest.raises(AssertionError, match="DOCX page count/geometry changed"):
        word_visual_session.check(source, baseline, pagination_results)
    with pymupdf.open(pagination_results / "actual.pdf") as actual:
        assert actual.page_count > len(geometry), "Failure PDF lost the additional page"
