"""Snapshot the shared filter that imports handwritten square-bracket lists."""

from __future__ import annotations

import os
import json
import re
import shutil
import subprocess
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET

import fitz
import pytest
from lxml import etree
from playwright.sync_api import sync_playwright

from native_support import ROOT, native_pandoc_executable
from snapshot_utils import assert_snapshot


@pytest.fixture(scope="module")
def pandoc_executable() -> str:
    """Use the retained engine when available, with standalone Pandoc as fallback."""
    executable = native_pandoc_executable() or shutil.which("pandoc")
    if executable is None:
        pytest.skip("Square-bracket list conversion requires Pandoc")
    return str(executable)


def test_bracketed_list_filter_snapshot(
    pandoc_executable: str, tmp_path: Path, snapshot_update: bool,
) -> None:
    """Protect list recognition, inline formatting, start numbers, and conservative misses."""
    fixture = ROOT / "tests/snapshot_cases/bracketed_lists"
    result = subprocess.run(
        [pandoc_executable, "--from=markdown", "--to=native", "--lua-filter",
         str(ROOT / "pandoc/filters/shared/bracketed_lists.lua"), str(fixture / "input.md")],
        cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert_snapshot(
        result.stdout, fixture / "snapshots-content/bracketed_lists.native",
        update=snapshot_update,
    )


def test_html_bracketed_list_labels(pandoc_executable: str) -> None:
    """Verify browser-rendered labels, including restarts, nesting, and ordinary lists."""
    source = """[1] First item
[2] Second item

[4] Fourth item
[5] Fifth item

- Parent

  [7] Nested item
  [8] Nested next item

Ordinary:

1. Ordinary item
2. Ordinary next item
"""
    result = subprocess.run(
        [pandoc_executable, "--defaults", str(ROOT / "pandoc/pandoc-html.yml"),
         "--from=markdown", "--to=html5"],
        input=source, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()

            def block_network(route) -> None:
                """Keep rendering deterministic by blocking unrelated external assets."""
                route.abort()

            page.route("**/*", block_network)
            page.set_content(result.stdout)
            pdf = page.pdf()
        finally:
            browser.close()
    with fitz.open(stream=pdf, filetype="pdf") as document:
        # Positioned labels may follow body text in PDF content-stream order.
        text = " ".join(page.get_text(sort=True) for page in document)
    text = re.sub(r"\s+", " ", text)
    for label in ["[1] First item", "[2] Second item", "[4] Fourth item", "[5] Fifth item",
                  "[7] Nested item", "[8] Nested next item", "1. Ordinary item", "2. Ordinary next item"]:
        assert label in text, text


@pytest.mark.parametrize("hanging_chars", [200, 300])
def test_html_bracketed_list_uses_reference_style(
    tmp_path: Path, rust_executable: Path, hanging_chars: int,
) -> None:
    """Apply a reference paragraph style through the public HTML build and real browser."""
    resources = tmp_path / "resources"
    shutil.copytree(ROOT / "pandoc", resources / "pandoc")
    shutil.copytree(ROOT / "defaults", resources / "defaults")
    engine = native_pandoc_executable()
    if engine is not None:
        record = resources / ".pmt/pandoc-worker/current.json"
        record.parent.mkdir(parents=True)
        record.write_text(json.dumps({"executable": str(engine)}), encoding="utf-8")
    styles_path = resources / "pandoc/manuscript-template/reference-doc/word/styles.xml"
    styles = etree.parse(str(styles_path))
    namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    style = styles.find("w:style[@w:styleId='BracketedList']", {"w": namespace})
    style.find(f"{{{namespace}}}pPr/{{{namespace}}}ind").set(
        f"{{{namespace}}}hangingChars", str(hanging_chars),
    )
    run = style.find(f"{{{namespace}}}rPr")
    if run is None:
        run = etree.SubElement(style, f"{{{namespace}}}rPr")
    etree.SubElement(run, f"{{{namespace}}}color", {f"{{{namespace}}}val": "C02040"})
    etree.SubElement(run, f"{{{namespace}}}sz", {f"{{{namespace}}}val": "32"})
    styles.write(str(styles_path), encoding="UTF-8", xml_declaration=True)
    source = tmp_path / "paper.md"
    source.write_text(
        "[10] Styled first with enough additional words to wrap across multiple lines\n"
        "[11] Styled second\n\nOrdinary paragraph.\n", encoding="utf-8",
    )
    output = tmp_path / "paper.html"
    result = subprocess.run(
        [str(rust_executable), "build", "html", "-m", str(source), "-o", str(output)],
        cwd=tmp_path, env={**os.environ, "PAPPER_RESOURCE_ROOT": str(resources)},
        capture_output=True, text=True, encoding="utf-8", timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()

            def block_network(route) -> None:
                """Ignore unrelated external assets while checking local generated CSS."""
                route.abort()

            page.route("**/*", block_network)
            page.set_content(output.read_text(encoding="utf-8"))
            page.locator('.pmt-bracketed-list').evaluate("el => el.style.width = '240px'")
            paragraphs = page.locator('.pmt-bracketed-list li > p[data-custom-style="Bracketed List"]')
            assert paragraphs.count() == 2
            for paragraph in paragraphs.all():
                assert paragraph.evaluate("el => getComputedStyle(el).color") == "rgb(192, 32, 64)"
                assert float(paragraph.evaluate("el => parseFloat(getComputedStyle(el).fontSize)")) == pytest.approx(64 / 3, abs=0.001)
            geometry = paragraphs.first.evaluate("""el => {
                const style = getComputedStyle(el);
                const marker = getComputedStyle(el, '::before');
                const node = el.firstChild;
                const range = document.createRange();
                const lines = [];
                for (let i = 0; i < node.length; i++) {
                    range.setStart(node, i);
                    range.setEnd(node, i + 1);
                    const rect = range.getBoundingClientRect();
                    if (rect.width > 0 && !lines.some(line => Math.abs(line.y - rect.y) < 1)) {
                        lines.push({x: rect.x, y: rect.y});
                    }
                }
                return {lines, left: el.getBoundingClientRect().left,
                        fontSize: parseFloat(style.fontSize), padding: parseFloat(style.paddingInlineStart),
                        indent: parseFloat(style.textIndent), markerPosition: marker.position};
            }""")
            assert len(geometry["lines"]) >= 2
            assert geometry["indent"] == 0
            assert geometry["markerPosition"] == "absolute"
            assert geometry["padding"] == pytest.approx(hanging_chars / 100 * geometry["fontSize"], abs=0.01)
            for line in geometry["lines"]:
                assert line["x"] - geometry["left"] == pytest.approx(geometry["padding"], abs=0.1)
            pdf = page.pdf()
            assert page.locator('p', has_text="Ordinary paragraph.").evaluate(
                "el => getComputedStyle(el).color"
            ) != "rgb(192, 32, 64)"
        finally:
            browser.close()
    with fitz.open(stream=pdf, filetype="pdf") as document:
        words = [word for sheet in document for word in sheet.get_text("words")]
    marker = next(word for word in words if word[4] == "[10]")
    first = next(word for word in words if word[4] == "Styled")
    assert marker[2] < first[0], (marker, first)


@pytest.mark.parametrize("native_crossrefs", [True, False])
def test_docx_bracketed_numbering(
    tmp_path: Path, rust_executable: Path, native_crossrefs: bool,
) -> None:
    """Verify actual Word numbering, restart values, notes, and adjacent ordinary lists."""
    source = tmp_path / "paper.md"
    source.write_text("""[1] First bracket item
[2] **Second bracket item**

Restart:

[4] Fourth bracket item
[5] Fifth bracket item

Ordinary:

1. First ordinary item
2. Second ordinary item

Nested:

- Parent

  [7] Nested bracket item
  [8] Next nested item

Note reference[^note].

[^note]:
    [3] Note bracket item
    [4] Next note item
""", encoding="utf-8")
    (tmp_path / "style.yml").write_text(
        f"mathtype: false\ndocxNativeCrossref: {str(native_crossrefs).lower()}\n",
        encoding="utf-8",
    )
    output = tmp_path / "paper.docx"
    result = subprocess.run(
        [str(rust_executable), "build", "docx", "-m", str(source), "-o", str(output)],
        cwd=tmp_path, env={**os.environ, "PAPPER_RESOURCE_ROOT": str(ROOT)},
        capture_output=True, text=True, encoding="utf-8", timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    value = "{" + namespace["w"] + "}val"
    with ZipFile(output) as package:
        numbering = ET.fromstring(package.read("word/numbering.xml"))
        paragraphs = [paragraph for part in ["word/document.xml", "word/footnotes.xml"]
                      for paragraph in ET.fromstring(package.read(part)).findall(".//w:p", namespace)]

    instances = {item.get("{" + namespace["w"] + "}numId"): item
                 for item in numbering.findall("w:num", namespace)}
    definitions = {item.get("{" + namespace["w"] + "}abstractNumId"): item
                   for item in numbering.findall("w:abstractNum", namespace)}
    rendered, counters = {}, {}
    for paragraph in paragraphs:
        properties = paragraph.find("w:pPr/w:numPr", namespace)
        if properties is None:
            continue
        instance_id = properties.find("w:numId", namespace).get(value)
        level = properties.find("w:ilvl", namespace).get(value)
        instance = instances[instance_id]
        abstract = definitions[instance.find("w:abstractNumId", namespace).get(value)]
        override = instance.find(f"w:lvlOverride[@w:ilvl='{level}']", namespace)
        definition = override.find("w:lvl", namespace) if override is not None else None
        if definition is None:
            definition = abstract.find(f"w:lvl[@w:ilvl='{level}']", namespace)
        if definition.find("w:numFmt", namespace).get(value) != "decimal":
            continue
        start = override.find("w:startOverride", namespace) if override is not None else None
        if start is None:
            start = definition.find("w:start", namespace)
        key = (instance_id, level)
        number = counters.get(key, int(start.get(value)) - 1) + 1
        counters[key] = number
        label = definition.find("w:lvlText", namespace).get(value).replace(f"%{int(level) + 1}", str(number))
        text = "".join(paragraph.itertext())
        rendered[text] = label

    assert rendered["First bracket item"] == "[1]"
    assert rendered["Second bracket item"] == "[2]"
    assert rendered["Fourth bracket item"] == "[4]"
    assert rendered["Fifth bracket item"] == "[5]"
    assert rendered["Nested bracket item"] == "[7]"
    assert rendered["Next nested item"] == "[8]"
    assert rendered["Note bracket item"] == "[3]"
    assert rendered["Next note item"] == "[4]"
    assert rendered["First ordinary item"] == "1."
    assert rendered["Second ordinary item"] == "2."
