"""Snapshot the shared filter that imports handwritten square-bracket lists."""

from __future__ import annotations

import shutil
import subprocess
import os
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET

import pytest

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
