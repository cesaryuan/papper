"""Verify real SVG fallback pixels and DOCX compatibility without system librsvg.

These checks cover Pandoc's binary pipe contract, physical-unit DPI, original SVG
preservation, and failed conversions that must not overwrite an existing image.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
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

    # A second public build must preserve both the vector and fallback pixels
    # without rewriting the persistent entry, independently of ZIP timestamps.
    entries = list((tmp_path / "state/projects").glob("*/cache/svg-rsvg/**/*.cache"))
    assert len(entries) == 1
    entry = entries[0]
    os.utime(entry, ns=(1_650_000_000_000_000_000,) * 2)
    stamp = entry.stat().st_mtime_ns
    repeated = subprocess.run(
        [str(rust_executable), "build", "docx", str(markdown), "--no-mathtype", "-o", str(output)],
        cwd=tmp_path, env=environment, capture_output=True, timeout=45,
    )
    assert repeated.returncode == 0, (repeated.stdout + repeated.stderr).decode(errors="replace")
    assert entry.stat().st_mtime_ns == stamp
    with ZipFile(output) as archive:
        assert archive.read("word/" + relations[svg_blip.attrib[office_rel + "embed"]]) == svg
        assert archive.read("word/" + relations[blip.attrib[office_rel + "embed"]]) == fallback


def cached_conversion(
    tools: Path, project: Path, svg: bytes, *, dpi: int = 96, controls: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Exercise the real adapter with isolated cache controls and binary output."""
    environment = {**os.environ, "PAPPER_SVG_CACHE_DIR": str(project / "cache"),
                   "PAPPER_SVG_RENDERER_ID": "integration-renderer"}
    for name in ("PAPPER_SVG_RENDERER", "PAPPER_SVG_FONT_ID", "PAPPER_SVG_CACHE"):
        environment.pop(name, None)
    environment.update(controls or {})
    result = subprocess.run(
        [str(converter(tools)), "-f", "png", "-a", "--dpi-x", str(dpi), "--dpi-y", str(dpi)],
        input=svg, cwd=project, env=environment, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    return result


def test_fallback_cache_reuses_pixels_and_invalidates_content_dpi_and_renderer(svg_tools, tmp_path) -> None:
    """Repeat byte streams reuse PNGs; content, DPI and renderer edits invalidate entries."""
    svg = (b'<svg xmlns="http://www.w3.org/2000/svg" width="1in" height="0.5in">'
           b'<rect width="100%" height="100%" fill="red"/></svg>')
    first = cached_conversion(svg_tools, tmp_path, svg).stdout
    entry, = (tmp_path / "cache").rglob("*.cache")
    os.utime(entry, ns=(1_650_000_000_000_000_000,) * 2)
    stamp = entry.stat().st_mtime_ns
    assert cached_conversion(svg_tools, tmp_path, svg).stdout == first
    assert entry.stat().st_mtime_ns == stamp

    changed = cached_conversion(svg_tools, tmp_path, svg.replace(b'red', b'tan')).stdout
    assert pymupdf.Pixmap(changed).pixel(48, 24)[:3] == (210, 180, 140)
    scaled = cached_conversion(svg_tools, tmp_path, svg, dpi=144).stdout
    assert (pymupdf.Pixmap(scaled).width, pymupdf.Pixmap(scaled).height) == (144, 72)
    assert cached_conversion(svg_tools, tmp_path, svg,
                             controls={"PAPPER_SVG_RENDERER_ID": "updated-renderer"}).stdout == first
    assert len(list((tmp_path / "cache").rglob("*.cache"))) == 4


def test_fallback_cache_recovers_damage_and_unavailable_storage(svg_tools, tmp_path) -> None:
    """A corrupt entry or unusable directory cannot replace valid output with cache errors."""
    svg = (b'<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20">'
           b'<rect width="40" height="20" fill="red"/></svg>')
    expected = cached_conversion(svg_tools, tmp_path, svg).stdout
    entry, = (tmp_path / "cache").rglob("*.cache")
    damaged = bytearray(entry.read_bytes())
    damaged[-1] ^= 1
    entry.write_bytes(damaged)
    assert cached_conversion(svg_tools, tmp_path, svg).stdout == expected
    assert cached_conversion(svg_tools, tmp_path, svg).stdout == expected
    blocked = tmp_path / "blocked"
    blocked.write_bytes(b"Keep this authored file")
    result = cached_conversion(svg_tools, tmp_path, svg, controls={"PAPPER_SVG_CACHE_DIR": str(blocked)})
    assert result.stdout == expected and blocked.read_bytes() == b"Keep this authored file"
    os.utime(entry, ns=(1_650_000_000_000_000_000,) * 2)
    stamp = entry.stat().st_mtime_ns
    assert cached_conversion(svg_tools, tmp_path, svg, controls={"PAPPER_SVG_CACHE": "0"}).stdout == expected
    assert entry.stat().st_mtime_ns == stamp


def test_fallback_external_images_refresh_after_timestamp_preserving_edits(svg_tools, tmp_path) -> None:
    """Untracked local dependencies bypass the cache so child-image edits remain visible."""
    child = tmp_path / "child.png"
    red = pymupdf.Pixmap(pymupdf.csRGB, (0, 0, 20, 10), False)
    red.clear_with(0)
    red.set_rect(red.irect, (255, 0, 0))
    child.write_bytes(red.tobytes("png"))
    svg = (b'<svg xmlns="http://www.w3.org/2000/svg" width="20" height="10">'
           b'<image href="child.png" width="20" height="10"/></svg>')
    first = cached_conversion(svg_tools, tmp_path, svg).stdout
    assert pymupdf.Pixmap(first).pixel(10, 5)[:3] == (255, 0, 0)
    before = child.stat()
    red.set_rect(red.irect, (0, 0, 255))
    child.write_bytes(red.tobytes("png"))
    os.utime(child, ns=(before.st_atime_ns, before.st_mtime_ns))
    changed = cached_conversion(svg_tools, tmp_path, svg).stdout
    assert pymupdf.Pixmap(changed).pixel(10, 5)[:3] == (0, 0, 255)
    assert not list((tmp_path / "cache").rglob("*.cache"))


def test_concurrent_fallback_requests_publish_complete_reusable_pngs(svg_tools, tmp_path) -> None:
    """Concurrent cold builds must produce identical complete PNGs and a reusable entry."""
    svg = (b'<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20">'
           b'<rect width="40" height="20" fill="#1266aa"/></svg>')
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(cached_conversion, svg_tools, tmp_path, svg) for _ in range(4)]
        results = [future.result().stdout for future in futures]
    assert all(png == results[0] for png in results)
    assert pymupdf.Pixmap(results[0]).pixel(20, 10)[:3] == (18, 102, 170)
    entry, = (tmp_path / "cache").rglob("*.cache")
    os.utime(entry, ns=(1_650_000_000_000_000_000,) * 2)
    stamp = entry.stat().st_mtime_ns
    assert cached_conversion(svg_tools, tmp_path, svg).stdout == results[0]
    assert entry.stat().st_mtime_ns == stamp


@pytest.mark.skipif(os.name != "nt", reason="Isolate the renderer's Windows system font roots")
def test_text_fallback_cache_tracks_actual_installed_font_bytes(svg_tools, tmp_path) -> None:
    """Installing a font must invalidate text PNGs; standalone text without an identity bypasses."""
    system = tmp_path / "system"
    fonts = system / "Fonts"
    fonts.mkdir(parents=True)
    controls = {"SYSTEMROOT": str(system), "USERPROFILE": str(tmp_path / "user")}
    helper = svg_tools / "papper-svg.exe"
    empty = subprocess.run([str(helper), "font-identity"], env={**os.environ, **controls},
                           capture_output=True, check=True, timeout=30).stdout.decode().strip()
    svg = (b'<svg xmlns="http://www.w3.org/2000/svg" width="150" height="30">'
           b'<text x="0" y="20" font-family="Times New Roman">Papper</text></svg>')
    blank = cached_conversion(svg_tools, tmp_path, svg, controls={**controls, "PAPPER_SVG_FONT_ID": empty}).stdout
    shutil.copy2(Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "Fonts/times.ttf", fonts / "times.ttf")
    installed = subprocess.run([str(helper), "font-identity"], env={**os.environ, **controls},
                               capture_output=True, check=True, timeout=30).stdout.decode().strip()
    text = cached_conversion(svg_tools, tmp_path, svg,
                             controls={**controls, "PAPPER_SVG_FONT_ID": installed}).stdout
    assert text != blank
    assert cached_conversion(svg_tools, tmp_path, svg, controls=controls).stdout == text
    assert len(list((tmp_path / "cache").rglob("*.cache"))) == 2
