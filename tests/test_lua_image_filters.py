"""Exercise Lua illustration caches through the real retained Pandoc and SVG renderer.

These contracts protect manuscript source bytes, composed SVG panels, cache
invalidation after timestamp-preserving edits, and recovery from failed rendering.
"""

from __future__ import annotations

import base64
import gzip
import json
import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote

import pytest

from pandoc_manuscript.runtime.resources import native_pandoc_executable


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def image_filter_tools() -> tuple[Path, Path]:
    """Use existing native build outputs without rebuilding Cargo during filter checks."""
    pandoc = native_pandoc_executable() or shutil.which("pandoc")
    assert pandoc is not None, "Lua image contracts require the retained Pandoc engine"
    filename = "papper-svg.exe" if os.name == "nt" else "papper-svg"
    candidates = [ROOT / "target" / profile / filename for profile in ("debug", "release")]
    if configured := os.environ.get("PAPPER_SVG_RENDERER"):
        candidates.insert(0, Path(configured))
    renderer = next((candidate for candidate in candidates if candidate.is_file()), None)
    assert renderer is not None, "Build papper-svg before running Lua image integration tests"
    return Path(pandoc), renderer


def run_image_filters(
    tools: tuple[Path, Path], project: Path, source: Path, *, embed: bool,
) -> tuple[subprocess.CompletedProcess[bytes], dict | None]:
    """Convert one real image AST with isolated caches and capture conversion failures."""
    pandoc, renderer = tools
    document = {"pandoc-api-version": [1, 23, 1], "meta": {}, "blocks": [
        {"t": "Para", "c": [{"t": "Image", "c": [
            ["figure", ["illustration"], [["data-purpose", "retain"], ["to-png", "true"]]],
            [{"t": "Str", "c": "Panel caption"}], [source.name, "Panel title"],
        ]}]}]}
    environment = {**os.environ, "PMT_SVG_EMBED_IMAGES": str(embed).lower(),
                   "PMT_SVG_EMBED_BASE_DIRS": str(project),
                   "PMT_SVG_TO_PNG_BASE_DIRS": json.dumps([str(project)]),
                   "PMT_SVG_EMBED_DIR": str(project / "embedded"),
                   "PMT_SVG_TO_PNG_DIR": str(project / "png"),
                   "PMT_SVG_EMBED_PMT_VERSION": "image-contract",
                   "PMT_SVG_TO_PNG_PMT_VERSION": "image-contract",
                   "PMT_SVG_TO_PNG_CONVERT_ALL": "false", "PMT_SVG_TO_PNG_DPI": "300",
                   "PMT_SVG_TO_PNG_SCALE": "1", "PAPPER_SVG_RENDERER": str(renderer)}
    # Caller-wide image controls must not override this fixture's intrinsic viewport.
    environment.pop("PMT_SVG_TO_PNG_WIDTH", None)
    command = [str(pandoc), "--from=json", "--to=json"]
    for name in ("svg_embed_images", "svg_to_png"):
        command.extend(["--lua-filter", str(ROOT / "pandoc/filters/docx" / f"{name}.lua")])
    result = subprocess.run(command, input=json.dumps(document).encode("utf-8"),
                            cwd=project, env=environment, capture_output=True, timeout=30)
    if result.returncode != 0:
        return result, None
    image = json.loads(result.stdout)["blocks"][0]["c"][0]
    assert image["c"][0] == ["figure", ["illustration"], [["data-purpose", "retain"]]]
    assert image["c"][1] == [{"t": "Str", "c": "Panel caption"}]
    assert image["c"][2][1] == "Panel title"
    return result, image


def render_reference(renderer: Path, source: Path, svg: bytes) -> bytes:
    """Render the independent panel directly to verify actual composed image pixels."""
    result = subprocess.run([str(renderer), "render", "--source", str(source)], input=svg,
                            capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    return result.stdout


def embedded_child_bytes(target: Path) -> bytes:
    """Decode the generated SVG image reference without depending on sidecar internals."""
    image = next(element for element in ET.fromstring(target.read_bytes()).iter()
                 if element.tag.rsplit("}", 1)[-1] == "image")
    return base64.b64decode(image.attrib["href"].split(",", 1)[1])


def test_nested_svg_cache_preserves_sources_and_invalidates_restored_timestamp_edits(
    tmp_path: Path, image_filter_tools: tuple[Path, Path],
) -> None:
    """Reuse composed panels but refresh both caches when child bytes change invisibly to stat."""
    project = tmp_path / "中文 项目"
    project.mkdir()
    panel = project / "子图 面板.svg"
    panel_bytes = (b'<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20">'
                   b'<rect width="40" height="20" fill="red"/></svg>')
    panel.write_bytes(panel_bytes)
    source = project / "合成.svg"
    source_bytes = (f'<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20">'
                    f'<image href="{quote(panel.name)}" width="40" height="20"/></svg>').encode("utf-8")
    source.write_bytes(source_bytes)
    result, image = run_image_filters(image_filter_tools, project, source, embed=True)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    png = Path(image["c"][2][0])
    pixels = png.read_bytes()
    embedded, = (project / "embedded").rglob("*.svg")
    assert embedded_child_bytes(embedded) == panel_bytes
    assert pixels == render_reference(image_filter_tools[1], panel, panel_bytes)
    assert source.read_bytes() == source_bytes and panel.read_bytes() == panel_bytes

    # Age generated files so even a fast rewrite cannot pass the reuse assertion
    # by falling within the filesystem's timestamp resolution.
    for target in (embedded, png):
        os.utime(target, ns=(1_650_000_000_000_000_000, 1_650_000_000_000_000_000))
    timestamps = {target: target.stat().st_mtime_ns for target in (embedded, png)}
    result, repeated = run_image_filters(image_filter_tools, project, source, embed=True)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    assert repeated == image and png.read_bytes() == pixels
    assert all(target.stat().st_mtime_ns == stamp for target, stamp in timestamps.items())

    before = panel.stat()
    changed = panel_bytes.replace(b'red', b'tan')
    panel.write_bytes(changed)
    os.utime(panel, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert panel.stat().st_size == before.st_size and panel.stat().st_mtime_ns == before.st_mtime_ns
    result, refreshed = run_image_filters(image_filter_tools, project, source, embed=True)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    assert refreshed == image and embedded_child_bytes(embedded) == changed
    assert png.read_bytes() != pixels
    assert png.read_bytes() == render_reference(image_filter_tools[1], panel, changed)
    assert source.read_bytes() == source_bytes and panel.read_bytes() == changed


def test_svgz_render_failure_preserves_previous_png_and_recovers(
    tmp_path: Path, image_filter_tools: tuple[Path, Path],
) -> None:
    """Leave complete cached output intact after damaged SVGZ and render the repaired source."""
    source = tmp_path / "压缩 图.svgz"
    original = (b'<svg xmlns="http://www.w3.org/2000/svg" width="30" height="10">'
                b'<rect width="30" height="10" fill="red"/></svg>')
    compressed = gzip.compress(original, mtime=0)
    source.write_bytes(compressed)
    result, image = run_image_filters(image_filter_tools, tmp_path, source, embed=False)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    png = Path(image["c"][2][0])
    pixels, timestamp = png.read_bytes(), png.stat().st_mtime_ns
    assert pixels == render_reference(image_filter_tools[1], source, original)
    assert source.read_bytes() == compressed

    damaged = compressed[:8]
    source.write_bytes(damaged)
    failed, output = run_image_filters(image_filter_tools, tmp_path, source, embed=False)
    assert failed.returncode != 0 and failed.stderr and output is None
    assert png.read_bytes() == pixels and png.stat().st_mtime_ns == timestamp
    assert source.read_bytes() == damaged

    repaired = original.replace(b'red', b'tan')
    repaired_compressed = gzip.compress(repaired, mtime=0)
    source.write_bytes(repaired_compressed)
    result, recovered = run_image_filters(image_filter_tools, tmp_path, source, embed=False)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    assert recovered == image and png.read_bytes() != pixels
    assert png.read_bytes() == render_reference(image_filter_tools[1], source, repaired)
    assert source.read_bytes() == repaired_compressed
