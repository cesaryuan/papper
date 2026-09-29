from __future__ import annotations

import base64
import importlib.util
import json
import sys
from pathlib import Path

import panflute as pf


def load_svg_embed_filter():
    """Load the repository Pandoc filter as a normal Python module."""
    path = Path(__file__).resolve().parents[1] / "pandoc" / "filters" / "docx" / "svg_embed_images.py"
    spec = importlib.util.spec_from_file_location("svg_embed_images_filter_for_tests", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_svg_to_png_filter():
    """Load the following SVG-to-PNG filter for pipeline regression tests."""
    path = Path(__file__).resolve().parents[1] / "pandoc" / "filters" / "docx" / "svg_to_png.py"
    spec = importlib.util.spec_from_file_location("svg_to_png_filter_for_embed_tests", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class DummyDoc:
    """Provide the filter attributes normally attached by panflute prepare()."""

    pmt_svg_embed_images = True
    pmt_svg_embed_base_dirs: list[Path] = []
    pmt_svg_embed_output_root = Path(".papper/cache/svg-embedded")
    pmt_svg_embed_pmt_version = "test"


def image(url: str) -> pf.Image:
    """Create a small panflute image node for filter unit tests."""
    return pf.Image(pf.Str("caption"), url=url)


def write_svg(path: Path, href: str) -> None:
    """Write a minimal SVG with one child image reference."""
    path.write_text(
        f"""<svg xmlns="http://www.w3.org/2000/svg"
     xmlns:xlink="http://www.w3.org/1999/xlink"
     width="100" height="100">
  <image href="{href}" xlink:href="{href}" x="0" y="0" width="100" height="100"/>
</svg>
""",
        encoding="utf-8",
    )


def test_svg_child_image_is_embedded_as_data_uri(tmp_path, monkeypatch) -> None:
    """Rewrite a linked SVG child image to a self-contained data URI cache."""
    svg_filter = load_svg_embed_filter()
    monkeypatch.chdir(tmp_path)
    figures = tmp_path / "figures"
    figures.mkdir()
    child = figures / "panel.png"
    child.write_bytes(b"panel-bytes")
    source_svg = figures / "layout.svg"
    write_svg(source_svg, "panel.png")

    doc = DummyDoc()
    doc.pmt_svg_embed_base_dirs = [tmp_path]
    doc.pmt_svg_embed_output_root = tmp_path / ".papper/cache/svg-embedded"
    elem = image("figures/layout.svg")

    result = svg_filter.action(elem, doc)

    assert result is elem
    target = Path(elem.url)
    output = target.read_text(encoding="utf-8")
    expected = base64.b64encode(b"panel-bytes").decode("ascii")
    assert f"data:image/png;base64,{expected}" in output
    assert "panel.png" not in output


def test_svg_child_image_requests_png_fallback(tmp_path, monkeypatch) -> None:
    """Force PNG fallback because Word cannot render an SVG child data URI."""
    svg_filter = load_svg_embed_filter()
    monkeypatch.chdir(tmp_path)
    figures = tmp_path / "figures"
    figures.mkdir()
    child = figures / "panel.svg"
    child.write_text('<svg xmlns="http://www.w3.org/2000/svg"><circle r="4"/></svg>\n', encoding="utf-8")
    source_svg = figures / "layout.svg"
    write_svg(source_svg, "panel.svg")

    doc = DummyDoc()
    doc.pmt_svg_embed_base_dirs = [tmp_path]
    doc.pmt_svg_embed_output_root = tmp_path / ".papper/cache/svg-embedded"
    elem = image("figures/layout.svg")

    result = svg_filter.action(elem, doc)

    assert result is elem
    assert elem.attributes["to-png"] == "true"
    assert "data:image/svg+xml;base64," in Path(elem.url).read_text(encoding="utf-8")

    png_filter = load_svg_to_png_filter()
    png_doc = type(
        "PngDoc",
        (),
        {
            "pmt_svg_base_dirs": [tmp_path],
            "pmt_svg_output_root": tmp_path / ".papper/cache/svg-png",
            "pmt_svg_dpi": 300,
            "pmt_svg_scale": 1,
            "pmt_svg_width": None,
            "pmt_svg_pmt_version": "test",
            "pmt_svg_convert_all": False,
        },
    )()

    png_result = png_filter.action(elem, png_doc)

    assert png_result is elem
    assert elem.url.endswith(".png")
    assert Path(elem.url).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert "to-png" not in elem.attributes


def test_svg_without_local_child_images_is_left_unchanged(tmp_path) -> None:
    """Leave ordinary SVG files alone when there are no child images to embed."""
    svg_filter = load_svg_embed_filter()
    source_svg = tmp_path / "figure.svg"
    source_svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><circle r="4"/></svg>\n',
        encoding="utf-8",
    )
    elem = image(str(source_svg))

    result = svg_filter.rewrite_image(
        elem,
        [tmp_path],
        tmp_path / ".papper/cache/svg-embedded",
        "test",
    )

    assert result is None
    assert elem.url == str(source_svg)


def test_global_switch_controls_embedding(monkeypatch) -> None:
    """Do not rewrite SVG files when the global embedding switch is disabled."""
    svg_filter = load_svg_embed_filter()

    class DisabledDoc(DummyDoc):
        pmt_svg_embed_images = False

    def fail_rewrite(elem: pf.Image, *args: object) -> pf.Image:
        raise AssertionError("rewrite_image should not be called")

    monkeypatch.setattr(svg_filter, "rewrite_image", fail_rewrite)

    elem = image("figure.svg")
    result = svg_filter.action(elem, DisabledDoc())

    assert result is None
    assert elem.url == "figure.svg"


def test_cache_metadata_tracks_child_image_changes(tmp_path) -> None:
    """Include child image metadata so edited panels invalidate SVG caches."""
    svg_filter = load_svg_embed_filter()
    figures = tmp_path / "figures"
    figures.mkdir()
    child = figures / "panel.png"
    child.write_bytes(b"one")
    source_svg = figures / "layout.svg"
    write_svg(source_svg, "panel.png")
    elem = image("figures/layout.svg")
    output_root = tmp_path / ".papper/cache/svg-embedded"

    svg_filter.rewrite_image(elem, [tmp_path], output_root, "test")
    metadata_path = Path(elem.url).with_suffix(".svg.meta.json")
    first_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    child.write_bytes(b"two-two")
    elem.url = "figures/layout.svg"
    svg_filter.rewrite_image(elem, [tmp_path], output_root, "test")
    second_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    assert first_metadata["resources"][0]["size"] == 3
    assert second_metadata["resources"][0]["size"] == 7
