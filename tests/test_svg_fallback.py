"""Verify real SVG fallback pixels and DOCX compatibility without system librsvg.

These checks cover Pandoc's binary pipe contract, physical-unit DPI, original SVG
preservation, and failed conversions that must not overwrite an existing image.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile

import pymupdf
import pytest

from native_support import ROOT, native_pandoc_executable


@pytest.fixture(scope="module")
def svg_tools(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build and copy both helpers together to exercise installed sibling discovery."""
    result = subprocess.run(
        ["cargo", "build", "--locked", "--offline", "-p", "papper-svg"], cwd=ROOT,
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    target = Path(os.environ.get("CARGO_TARGET_DIR", ROOT / "target"))
    if not target.is_absolute():
        target = ROOT / target
    directory = tmp_path_factory.mktemp("SVG helpers 中文")
    for name in ("papper-svg", "rsvg-convert"):
        filename = name + (".exe" if os.name == "nt" else "")
        shutil.copy2(target / "debug" / filename, directory / filename)
    return directory


def converter(directory: Path) -> Path:
    """Return the platform launcher filename for real subprocess calls."""
    return directory / ("rsvg-convert.exe" if os.name == "nt" else "rsvg-convert")


@pytest.mark.parametrize("file_input", [False, True])
def test_adapter_renders_physical_dimensions_and_unicode_paths(svg_tools, tmp_path, file_input) -> None:
    """Preserve binary PNG pixels and requested DPI for both stdin and named files."""
    source = tmp_path / "物理尺寸 图.svg"
    svg = (b'<svg xmlns="http://www.w3.org/2000/svg" width="1in" height="0.5in">'
           b'<rect width="100%" height="100%" fill="#1266aa"/></svg>')
    source.write_bytes(svg)
    output = tmp_path / "输出 图.png"
    arguments = [str(converter(svg_tools)), "-f", "png", "-a", "--dpi-x", "144", "--dpi-y", "144"]
    if file_input:
        arguments.extend(["-o", str(output), str(source)])
    environment = {**os.environ, "PATH": ""}
    environment.pop("PAPPER_SVG_RENDERER", None)
    result = subprocess.run(arguments, input=None if file_input else svg,
                            env=environment, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    image = pymupdf.Pixmap(output.read_bytes() if file_input else result.stdout)
    assert (image.width, image.height) == (144, 72)
    assert image.pixel(72, 36)[:3] == (18, 102, 170)
    assert source.read_bytes() == svg


@pytest.mark.parametrize("svg, arguments", [
    (b"not an SVG", []),
    (b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>', ["-f", "pdf"]),
    (b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>', ["--dpi-x", "0"]),
])
def test_failed_adapter_conversion_preserves_existing_output(svg_tools, tmp_path, svg, arguments) -> None:
    """Invalid SVG or unsupported requests must fail before replacing an authored output."""
    output = tmp_path / "previous.png"
    previous = b"Previous output must survive failed conversion"
    output.write_bytes(previous)
    environment = {**os.environ, "PATH": ""}
    environment.pop("PAPPER_SVG_RENDERER", None)
    result = subprocess.run([str(converter(svg_tools)), *arguments, "-o", str(output)],
                            input=svg, env=environment, capture_output=True, timeout=30)
    assert result.returncode != 0
    assert result.stdout == b""
    assert output.read_bytes() == previous


def test_docx_keeps_svg_and_embeds_png_without_system_converter(svg_tools, rust_executable, tmp_path) -> None:
    """A public DOCX build must retain the vector image and link real fallback pixels."""
    engine = native_pandoc_executable()
    if engine is None:
        pytest.skip("Build the retained Pandoc worker for an isolated DOCX fallback check")
    resources = tmp_path / "runtime"
    shutil.copytree(ROOT / "pandoc", resources / "pandoc")
    shutil.copytree(ROOT / "template", resources / "template")
    shutil.copytree(svg_tools, resources / "bin")
    worker_name = "pmt-pandoc-worker" + (".exe" if os.name == "nt" else "")
    shutil.copy2(engine, resources / "bin" / worker_name)
    source = tmp_path / "插图.svg"
    svg = (b'<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20">'
           b'<rect width="40" height="20" fill="#1266aa"/></svg>')
    source.write_bytes(svg)
    markdown = tmp_path / "paper.md"
    markdown.write_text("![Illustration](插图.svg)\n", encoding="utf-8")
    output = tmp_path / "paper.docx"
    environment = {**os.environ, "PATH": "", "PAPPER_HOME": str(tmp_path / "state"),
                   "PAPPER_RESOURCE_ROOT": str(resources)}
    environment.pop("PAPPER_SVG_RENDERER", None)
    result = subprocess.run(
        [str(rust_executable), "build", "docx", str(markdown), "--no-mathtype", "-o", str(output)],
        cwd=tmp_path, env=environment, capture_output=True, timeout=45,
    )
    assert result.returncode == 0, (result.stdout + result.stderr).decode(errors="replace")
    assert b"Could not convert image" not in result.stderr
    with ZipFile(output) as archive:
        document = ET.fromstring(archive.read("word/document.xml"))
        relations = {rel.attrib["Id"]: rel.attrib["Target"]
                     for rel in ET.fromstring(archive.read("word/_rels/document.xml.rels"))}
        drawing = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
        office_rel = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
        svg_ns = "{http://schemas.microsoft.com/office/drawing/2016/SVG/main}"
        blip = document.find(f".//{drawing}blip")
        svg_blip = blip.find(f".//{svg_ns}svgBlip")
        assert archive.read("word/" + relations[svg_blip.attrib[office_rel + "embed"]]) == svg
        fallback = archive.read("word/" + relations[blip.attrib[office_rel + "embed"]])
        image = pymupdf.Pixmap(fallback)
        assert (image.width, image.height) == (40, 20)
        assert image.pixel(20, 10)[:3] == (18, 102, 170)
    assert source.read_bytes() == svg
    assert markdown.read_text(encoding="utf-8") == "![Illustration](插图.svg)\n"
