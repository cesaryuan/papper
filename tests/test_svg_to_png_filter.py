from __future__ import annotations

import json
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import panflute as pf


def load_svg_filter():
    """Load the repository Pandoc filter as a normal Python module."""
    path = Path(__file__).resolve().parents[1] / "pandoc" / "filters" / "docx" / "svg_to_png.py"
    spec = importlib.util.spec_from_file_location("svg_to_png_filter_for_tests", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class DummyDoc:
    """Provide the filter attributes normally attached by panflute prepare()."""

    pmt_svg_base_dirs = [Path.cwd()]
    pmt_svg_output_root = Path(".papper/cache/svg-png")
    pmt_svg_dpi = 300
    pmt_svg_scale = 1
    pmt_svg_width = None
    pmt_svg_pmt_version = "test"
    pmt_svg_convert_all = False


def image(url: str, **attributes: str) -> pf.Image:
    """Create a small panflute image node for filter unit tests."""
    elem = pf.Image(pf.Str("caption"), url=url)
    elem.attributes.update(attributes)
    return elem


def test_to_png_attribute_converts_one_svg(monkeypatch) -> None:
    """Allow one SVG image to opt into PNG conversion without a global switch."""
    svg_filter = load_svg_filter()
    calls: list[str] = []

    def fake_rewrite(elem: pf.Image, *args: object) -> pf.Image:
        calls.append(elem.url)
        elem.url = ".papper/cache/svg-png/figure.png"
        return elem

    monkeypatch.setattr(svg_filter, "rewrite_image", fake_rewrite)

    elem = image("figure.svg", **{"to-png": "true"})
    result = svg_filter.action(elem, DummyDoc())

    assert result is elem
    assert calls == ["figure.svg"]
    assert elem.url == ".papper/cache/svg-png/figure.png"
    assert "to-png" not in elem.attributes


def test_to_png_scale_attribute_overrides_one_svg(monkeypatch) -> None:
    """Allow one rasterized SVG image to override the global PNG scale."""
    svg_filter = load_svg_filter()
    calls: list[tuple[float, str | None]] = []

    def fake_rewrite(elem: pf.Image, *args: object) -> pf.Image:
        calls.append((args[3], args[6]))
        elem.url = ".papper/cache/svg-png/figure.scale-2.png"
        return elem

    monkeypatch.setattr(svg_filter, "rewrite_image", fake_rewrite)

    elem = image("figure.svg", **{"to-png": "true", "to-png-scale": "2"})
    result = svg_filter.action(elem, DummyDoc())

    assert result is elem
    assert calls == [(2.0, "scale-2")]
    assert elem.url == ".papper/cache/svg-png/figure.scale-2.png"
    assert "to-png" not in elem.attributes
    assert "to-png-scale" not in elem.attributes


def test_svg_without_to_png_is_left_unchanged(monkeypatch) -> None:
    """Do not rasterize ordinary SVG images when the global switch is disabled."""
    svg_filter = load_svg_filter()

    def fail_rewrite(elem: pf.Image, *args: object) -> pf.Image:
        raise AssertionError("rewrite_image should not be called")

    monkeypatch.setattr(svg_filter, "rewrite_image", fail_rewrite)

    elem = image("figure.svg")
    result = svg_filter.action(elem, DummyDoc())

    assert result is None
    assert elem.url == "figure.svg"


def test_global_switch_still_converts_svg(monkeypatch) -> None:
    """Keep the existing metadata switch behavior for all SVG images."""
    svg_filter = load_svg_filter()
    calls: list[str] = []

    class ConvertAllDoc(DummyDoc):
        pmt_svg_convert_all = True

    def fake_rewrite(elem: pf.Image, *args: object) -> pf.Image:
        calls.append(elem.url)
        elem.url = ".papper/cache/svg-png/figure.png"
        return elem

    monkeypatch.setattr(svg_filter, "rewrite_image", fake_rewrite)

    elem = image("figure.svg")
    result = svg_filter.action(elem, ConvertAllDoc())

    assert result is elem
    assert calls == ["figure.svg"]


def test_url_encoded_chinese_child_href_is_decoded_before_resvg(tmp_path, monkeypatch) -> None:
    """Decode URL-encoded non-ASCII child image paths before SVG rasterization."""
    svg_filter = load_svg_filter()
    figures = tmp_path / "figures"
    image_dir = figures / "images"
    image_dir.mkdir(parents=True)
    child = image_dir / "纹理.png"
    child.write_bytes(b"png")
    encoded_href = f"images/{quote(child.name)}"
    source = figures / "layout.svg"
    source.write_text(
        f"""<svg xmlns="http://www.w3.org/2000/svg"
     xmlns:xlink="http://www.w3.org/1999/xlink"
     width="100" height="100">
  <image href="{encoded_href}" xlink:href="{encoded_href}" x="0" y="0" width="100" height="100"/>
</svg>
""",
        encoding="utf-8",
    )
    target = tmp_path / ".papper/cache/svg-png/layout.png"
    calls: list[dict[str, object]] = []

    def fake_svg_to_bytes(**kwargs: object) -> bytes:
        calls.append(kwargs)
        return b"png-bytes"

    monkeypatch.setitem(sys.modules, "resvg_py", SimpleNamespace(svg_to_bytes=fake_svg_to_bytes))

    svg_filter.ensure_png(source, target, 300, 1, None, "test")

    assert target.read_bytes() == b"png-bytes"
    assert calls
    svg_string = str(calls[0]["svg_string"])
    assert "images/纹理.png" in svg_string
    assert "%E7" not in svg_string
    metadata = json.loads(target.with_suffix(".png.meta.json").read_text(encoding="utf-8"))
    assert metadata["resources"][0]["path"].endswith("纹理.png")


def test_auto_width_from_percent() -> None:
    """Derive a PNG width of 3000 * num% when width is a percentage."""
    svg_filter = load_svg_filter()
    assert svg_filter.image_auto_width(image("figure.svg", width="90%")) == 2700
    assert svg_filter.image_auto_width(image("figure.svg", width="50%")) == 1500


def test_auto_width_from_pixels() -> None:
    """Double an explicit pixel width."""
    svg_filter = load_svg_filter()
    assert svg_filter.image_auto_width(image("figure.svg", width="800px")) == 1600


def test_auto_width_from_physical_units() -> None:
    """Convert physical units at a fixed 500 dpi."""
    svg_filter = load_svg_filter()
    assert svg_filter.image_auto_width(image("figure.svg", width="1in")) == 500
    assert svg_filter.image_auto_width(image("figure.svg", width="1inch")) == 500
    # 2.54cm == 1in == 500px
    assert svg_filter.image_auto_width(image("figure.svg", width="2.54cm")) == 500
    # 25.4mm == 1in == 500px
    assert svg_filter.image_auto_width(image("figure.svg", width="25.4mm")) == 500


def test_explicit_metadata_width_overrides_auto(monkeypatch) -> None:
    """An explicit docxSvgToPngWidth wins over per-image auto-derivation."""
    svg_filter = load_svg_filter()
    widths: list[int | None] = []

    class WidthDoc(DummyDoc):
        pmt_svg_convert_all = True
        pmt_svg_width = 1600

    def fake_rewrite(elem: pf.Image, *args: object) -> pf.Image:
        widths.append(args[4])
        return elem

    monkeypatch.setattr(svg_filter, "rewrite_image", fake_rewrite)

    svg_filter.action(image("figure.svg", width="50%"), WidthDoc())
    assert widths == [1600]
