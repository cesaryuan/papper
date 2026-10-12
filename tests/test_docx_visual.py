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
from zipfile import ZipFile

import pytest
import pymupdf
from PIL import Image, ImageChops
from docx import Document
from docx.shared import RGBColor
from lxml import etree

from docx_visual_support import VISUAL_PLATFORM, WordVisualSession
from native_support import papper_command
from test_build_snapshots import CASE_ROOT, CASES, build_case, copy_case


def freeze_visual_dates(source: Path, destination: Path) -> None:
    """Lock DATE caches only in the visual copy so field updates cannot depend on today's date."""
    namespace = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
    dates = 0
    with ZipFile(source) as original, ZipFile(destination, 'w') as frozen:
        for member in original.infolist():
            data = original.read(member.filename)
            if member.filename.startswith('word/') and member.filename.endswith('.xml'):
                root = etree.fromstring(data)
                for paragraph in root.iter(namespace + 'p'):
                    begin, in_date, result = None, False, False
                    for run in paragraph.iter(namespace + 'r'):
                        field = run.find(namespace + 'fldChar')
                        if field is not None:
                            kind = field.get(namespace + 'fldCharType')
                            if kind == 'begin':
                                begin = field
                            elif kind == 'separate':
                                result = True
                            elif kind == 'end':
                                begin, in_date, result = None, False, False
                        instruction = run.find(namespace + 'instrText')
                        if instruction is not None and (instruction.text or '').strip().startswith('DATE '):
                            assert begin is not None, 'DATE field is missing its beginning'
                            begin.set(namespace + 'fldLock', 'true')
                            begin.set(namespace + 'dirty', 'false')
                            in_date = True
                            dates += 1
                        if in_date and result:
                            for value in run.iter(namespace + 't'):
                                value.text = '2000-01-02'
                data = etree.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)
            frozen.writestr(member, data)
    assert dates > 0, 'Native insertion visual fixture lost its DATE field'


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
        if case_name in {"bilingual_captions", "docx_directives"}:
            # Relative sibling image references must resolve before Word pagination.
            copy_case(CASES["crossrefs"][0], tmp_path / "crossrefs")
        output = project / "rendered.docx"
        style_file = project / "style.yml" if case_name in {"native_crossrefs", "equation_attributes_no_mathtype"} else None
        build_case(project, markdown, "docx", output, style_file=style_file)
    update_fields = case_name == "docx_directives"
    if update_fields:
        visual_copy = project / "visual-fixed-date.docx"
        freeze_visual_dates(output, visual_copy)
        output = visual_copy
    baseline = CASE_ROOT / case_name / "snapshots-visual" / VISUAL_PLATFORM / "docx-word"
    word_visual_session.check(output, baseline, update_fields=update_fields)
    if update_fields:
        # Check real Word output as well as pixels: a stable placeholder image
        # must not become the baseline when directory updating silently fails.
        with pymupdf.open(output.parent / "word-render/export-2/rendered.pdf") as rendered:
            text = '\n'.join(page.get_text() for page in rendered)
            assert 'Update field in Word.' not in text
            assert '2000-01-02' in text
            for caption in ('Figure 1 Primary figure', 'Figure 2 Combined panels',
                            '(a) First panel', '(b) Second panel', 'Table 1 Measurements'):
                assert text.count(caption) >= 2, f'Directory lost caption: {caption}'


@pytest.mark.visual
def test_docx_visual_reports_color_and_pagination_changes(
    tmp_path: Path, word_visual_session: WordVisualSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify Word rejects visible color/page changes, keeping only a diff PNG beside snapshots."""
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
    with pytest.raises(AssertionError, match="Visual mismatch"):
        word_visual_session.check(source, baseline)
    with Image.open(baseline / "page-001.png") as original, Image.open(baseline / "page-001-diff.png") as difference:
        assert ImageChops.difference(original, difference).getbbox(), "Changed text color produced no visible diff"

    document.add_page_break()
    document.add_paragraph("Additional DOCX page")
    document.save(source)
    with pytest.raises(AssertionError, match="DOCX page count/geometry changed"):
        word_visual_session.check(source, baseline)
    with pymupdf.open(tmp_path / "word-render/export-2/rendered.pdf") as actual:
        assert actual.page_count > len(geometry), "Failure PDF lost the additional page"
