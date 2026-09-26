"""Generate HTML typography from the current reference DOCX style sheet."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

from ..docx.postprocess.docx_style import normalize_docx_style_settings
from ..runtime.metadata import PmtSettings

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


@dataclass
class _StyleSpec:
    """Effective subset of one Word paragraph or table style."""

    style_id: str = ""
    based_on: str | None = None
    font_size_pt: float | None = None
    western_font: str | None = None
    chinese_font: str | None = None
    bold: bool | None = None
    italic: bool | None = None
    font_color_rgb: tuple[int, int, int] | None = None
    line_height: str | None = None
    space_before_pt: float | None = None
    space_after_pt: float | None = None
    alignment: str | None = None
    first_line_indent: str | None = None
    left_indent_pt: float | None = None
    right_indent_pt: float | None = None


def _q(name: str) -> str:
    """Return a qualified WordprocessingML name."""
    return f"{{{W_NS}}}{name}"


def _value(element: ElementTree.Element | None, name: str) -> str | None:
    """Read one WordprocessingML attribute from an optional element."""
    return None if element is None else element.get(_q(name))


def _points(twips: str | None) -> float | None:
    """Convert Word twentieths of a point to CSS points."""
    return None if twips is None else float(twips) / 20.0


def _font_size(half_points: str | None) -> float | None:
    """Convert Word half-points to CSS points."""
    return None if half_points is None else float(half_points) / 2.0


def _bool_property(element: ElementTree.Element | None) -> bool | None:
    """Read a boolean Word property, including explicit false values."""
    if element is None:
        return None
    value = _value(element, "val")
    return True if value is None else value.casefold() not in {"0", "false", "off", "no"}


def _parse_line_height(spacing: ElementTree.Element | None) -> str | None:
    """Convert Word automatic or exact line spacing into a CSS value."""
    line = _value(spacing, "line")
    if line is None:
        return None
    rule = (_value(spacing, "lineRule") or "auto").casefold()
    if rule in {"auto", "atleast"}:
        return f"{float(line) / 240.0:g}"
    return f"{float(line) / 20.0:g}pt"


def _read_direct_style(style: ElementTree.Element) -> _StyleSpec:
    """Read only properties explicitly declared by one XML style element."""
    p_pr = style.find(_q("pPr"))
    r_pr = style.find(_q("rPr"))
    spacing = None if p_pr is None else p_pr.find(_q("spacing"))
    indentation = None if p_pr is None else p_pr.find(_q("ind"))
    fonts = None if r_pr is None else r_pr.find(_q("rFonts"))
    size = None if r_pr is None else r_pr.find(_q("sz"))
    color = None if r_pr is None else r_pr.find(_q("color"))
    first_line = None
    for name, sign, divisor, unit in (
        ("firstLineChars", "", 100.0, "em"),
        ("hangingChars", "-", 100.0, "em"),
        ("firstLine", "", 20.0, "pt"),
        ("hanging", "-", 20.0, "pt"),
    ):
        raw = _value(indentation, name)
        if raw is not None:
            first_line = f"{sign}{float(raw) / divisor:g}{unit}"
            break
    color_value = _value(color, "val")
    color_rgb = None
    if color_value and len(color_value) == 6 and color_value.casefold() != "auto":
        try:
            color_rgb = tuple(int(color_value[index : index + 2], 16) for index in (0, 2, 4))
        except ValueError:
            color_rgb = None
    return _StyleSpec(
        style_id=_value(style, "styleId") or "",
        based_on=_value(style.find(_q("basedOn")), "val"),
        font_size_pt=_font_size(_value(size, "val")),
        western_font=_value(fonts, "ascii") or _value(fonts, "hAnsi"),
        chinese_font=_value(fonts, "eastAsia"),
        bold=_bool_property(None if r_pr is None else r_pr.find(_q("b"))),
        italic=_bool_property(None if r_pr is None else r_pr.find(_q("i"))),
        font_color_rgb=color_rgb,
        line_height=_parse_line_height(spacing),
        space_before_pt=_points(_value(spacing, "before")),
        space_after_pt=_points(_value(spacing, "after")),
        alignment=_value(None if p_pr is None else p_pr.find(_q("jc")), "val"),
        first_line_indent=first_line,
        left_indent_pt=_points(_value(indentation, "left")),
        right_indent_pt=_points(_value(indentation, "right")),
    )


def _merge(parent: _StyleSpec, child: _StyleSpec) -> _StyleSpec:
    """Merge explicitly declared child properties over inherited values."""
    merged = deepcopy(parent)
    for field in _StyleSpec.__dataclass_fields__:
        value = getattr(child, field)
        if value is not None and (field != "style_id" or value):
            setattr(merged, field, value)
    return merged


def _read_reference_styles(path: Path) -> dict[str, _StyleSpec]:
    """Read and resolve basedOn inheritance from the current reference XML."""
    root = ElementTree.parse(path).getroot()
    style_elements: dict[str, ElementTree.Element] = {}
    names: dict[str, str] = {}
    for style in root.findall(_q("style")):
        style_id = _value(style, "styleId")
        name = _value(style.find(_q("name")), "val")
        if style_id and name:
            style_elements[style_id] = style
            names[name.casefold()] = style_id

    defaults = _StyleSpec()
    p_default = root.find(f"{_q('docDefaults')}/{_q('pPrDefault')}/{_q('pPr')}")
    r_default = root.find(f"{_q('docDefaults')}/{_q('rPrDefault')}/{_q('rPr')}")
    if p_default is not None:
        spacing = p_default.find(_q("spacing"))
        defaults.line_height = _parse_line_height(spacing)
        defaults.space_before_pt = _points(_value(spacing, "before"))
        defaults.space_after_pt = _points(_value(spacing, "after"))
    if r_default is not None:
        defaults.font_size_pt = _font_size(_value(r_default.find(_q("sz")), "val"))
        fonts = r_default.find(_q("rFonts"))
        defaults.western_font = _value(fonts, "ascii") or _value(fonts, "hAnsi")
        defaults.chinese_font = _value(fonts, "eastAsia")

    resolved: dict[str, _StyleSpec] = {}

    def resolve(style_id: str, stack: set[str] | None = None) -> _StyleSpec:
        """Resolve basedOn inheritance while tolerating malformed cycles."""
        if style_id in resolved:
            return resolved[style_id]
        stack = set() if stack is None else stack
        if style_id in stack or style_id not in style_elements:
            return deepcopy(defaults)
        stack.add(style_id)
        element = style_elements[style_id]
        direct = _read_direct_style(element)
        style_type = _value(element, "type")
        # docDefaults are paragraph defaults; applying them to a table style would
        # incorrectly add Normal's paragraph spacing to every HTML table.
        empty_parent = _StyleSpec()
        if direct.based_on:
            parent = resolve(direct.based_on, stack)
        else:
            parent = defaults if style_type == "paragraph" else empty_parent
        resolved[style_id] = _merge(parent, direct)
        return resolved[style_id]

    for style_id in style_elements:
        resolve(style_id)
    return {name: resolved[style_id] for name, style_id in names.items()}


def _css_quote(value: str) -> str:
    """Quote one font family for CSS while preserving non-ASCII Word names."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _font_css(style: _StyleSpec) -> str | None:
    """Build a CSS font-family list from Word western and East Asian fonts."""
    families: list[str] = []
    for value in (style.western_font, style.chinese_font):
        if value and value not in families:
            families.append(value)
    if not families:
        return None
    fallbacks = ["SimSun", "serif"] if style.chinese_font else ["serif"]
    return ", ".join([*(_css_quote(value) for value in families), *fallbacks])


def _style_css(
    selector: str,
    style: _StyleSpec,
    *,
    label: str,
    include_paragraph_metrics: bool = True,
) -> str:
    """Render effective Word properties as a CSS rule."""
    safe_label = label.replace("*/", "* /")
    values = {
        "font-size": None if style.font_size_pt is None else f"{style.font_size_pt:g}pt",
        "font-family": _font_css(style),
        "font-weight": None if style.bold is None else ("bold" if style.bold else "normal"),
        "font-style": None if style.italic is None else ("italic" if style.italic else "normal"),
        "color": None
        if style.font_color_rgb is None
        else "#%02X%02X%02X" % style.font_color_rgb,
        "line-height": style.line_height,
        "margin-top": None
        if not include_paragraph_metrics or style.space_before_pt is None
        else f"{style.space_before_pt:g}pt",
        "margin-bottom": None
        if not include_paragraph_metrics or style.space_after_pt is None
        else f"{style.space_after_pt:g}pt",
        "text-align": {"both": "justify", "distribute": "justify"}.get(style.alignment, style.alignment),
        "text-indent": style.first_line_indent if include_paragraph_metrics else None,
        "margin-left": None
        if not include_paragraph_metrics or style.left_indent_pt is None
        else f"{style.left_indent_pt:g}pt",
        "margin-right": None
        if not include_paragraph_metrics or style.right_indent_pt is None
        else f"{style.right_indent_pt:g}pt",
    }
    declarations = [f"  /* {safe_label} from reference-doc/word/styles.xml */"]
    declarations.extend(f"  {name}: {value};" for name, value in values.items() if value is not None)
    return f"{selector} {{\n" + "\n".join(declarations) + "\n}"


def _table_cell_padding_css(style: _StyleSpec, *, label: str) -> str:
    """Map Table Text paragraph spacing to the cell padding that HTML supports."""
    values = {
        "padding-top": None if style.space_before_pt is None else f"{style.space_before_pt:g}pt",
        "padding-bottom": None if style.space_after_pt is None else f"{style.space_after_pt:g}pt",
    }
    declarations = [f"  /* {label} cell spacing from reference-doc/word/styles.xml */"]
    declarations.extend(f"  {name}: {value};" for name, value in values.items() if value is not None)
    return "table td, table th {\n" + "\n".join(declarations) + "\n}"


def _style_key(name: str) -> str:
    """Normalize style names for semantic HTML selector mapping."""
    return "".join(name.casefold().split())


def _point_value(value: object) -> float | None:
    """Read points from python-docx length values used by normalized settings."""
    if value is None:
        return None
    return float(getattr(value, "pt", value))


def _override_css(settings: PmtSettings) -> list[str]:
    """Render configured docxStyle fields after reference defaults."""
    normalized = normalize_docx_style_settings(settings) or []
    selectors = {
        "normal": ".pmt-page > p",
        "正文": ".pmt-page > p",
        # First Paragraph is based on Body Text in styles.xml, so a Body Text
        # override must also win over the more specific first-paragraph rule.
        "正文文本": ".pmt-page > p, .pmt-page > p:first-of-type",
        "bodytext": ".pmt-page > p, .pmt-page > p:first-of-type",
        "firstparagraph": ".pmt-page > p:first-of-type",
        "首段": ".pmt-page > p:first-of-type",
        "caption": "figure figcaption, table caption",
        "题注": "figure figcaption, table caption",
        "tablecaption": "table caption",
        "imagecaption": "figure figcaption",
        "tabletext": "table td, table th, table td p, table th p",
        "table": "table, table td, table th",
        "title": "h1.title",
        "标题": "h1.title",
        "subtitle": "p.subtitle",
        "副标题": "p.subtitle",
    }
    for level in range(1, 10):
        selectors[f"heading{level}"] = f"h{level}"
        selectors[f"标题{level}"] = f"h{level}"
    rules: list[str] = []
    for record in normalized:
        selector = selectors.get(_style_key(record["style_name"]))
        if selector is None:
            continue
        spacing = record.get("line_spacing")
        line_points = _point_value(spacing)
        if line_points is not None and not isinstance(spacing, (int, float)):
            line_height = f"{line_points:g}pt"
        elif line_points is not None:
            line_height = f"{line_points:g}"
        else:
            line_height = None
        indent = record.get("first_line_indent_chars")
        if indent is not None:
            first_line = f"{indent:g}em"
        else:
            first_line_value = _point_value(record.get("first_line_indent"))
            first_line = None if first_line_value is None else f"{first_line_value:g}pt"
        family = record.get("font_family") or {}
        color = record.get("font_color_rgb")
        style = _StyleSpec(
            font_size_pt=record.get("font_size_pt"),
            western_font=family.get("western"),
            chinese_font=family.get("chinese"),
            bold=record.get("bold"),
            font_color_rgb=None if color is None else tuple(color),
            line_height=line_height,
            space_before_pt=record.get("space_before_pt"),
            space_after_pt=record.get("space_after_pt"),
            alignment=record.get("alignment_display"),
            first_line_indent=first_line,
            left_indent_pt=_point_value(record.get("left_indent")),
            right_indent_pt=_point_value(record.get("right_indent")),
        )
        override_label = f"docxStyle.{record['style_name']} override"
        if _style_key(record["style_name"]) == "tabletext":
            rules.append(_style_css("table td, table th", style, label=override_label, include_paragraph_metrics=False))
            rules.append(_style_css("table td p, table th p", style, label=f"{override_label} paragraphs"))
        else:
            rules.append(_style_css(selector, style, label=override_label))
        if _style_key(record["style_name"]) == "tabletext":
            rules.append(_table_cell_padding_css(style, label=override_label))
    return rules


def build_reference_style_css(styles_path: str | Path, settings: PmtSettings) -> str:
    """Build CSS from reference styles, then append higher-priority docxStyle overrides."""
    styles = _read_reference_styles(Path(styles_path))
    mappings = [
        ("h1.title", "title", "Title"),
        ("p.subtitle", "subtitle", "Subtitle"),
        (".pmt-page > p", "body text", "Body Text"),
        (".pmt-page > p:first-of-type", "first paragraph", "First Paragraph"),
        *[(f"h{i}", f"heading {i}", f"Heading {i}") for i in range(1, 7)],
        ("figure figcaption, table caption", "caption", "Caption"),
        ("table caption", "table caption", "Table Caption"),
        ("figure figcaption", "image caption", "Image Caption"),
        ("table, table td, table th", "table", "Table"),
        ("table td, table th, table td p, table th p", "table text", "Table Text"),
    ]
    rules: list[str] = []
    body_style = styles.get("body text", _StyleSpec())
    rules.append(
        _style_css(
            ".pmt-page",
            body_style,
            label="Body Text inherited",
            include_paragraph_metrics=False,
        )
    )
    for selector, key, label in mappings:
        style = styles.get(key)
        if style is not None:
            if key == "table text":
                rules.append(_style_css("table td, table th", style, label=label, include_paragraph_metrics=False))
                rules.append(_style_css("table td p, table th p", style, label=f"{label} paragraphs"))
                rules.append(_table_cell_padding_css(style, label=label))
            else:
                rules.append(_style_css(selector, style, label=label))
    rules.extend(_override_css(settings))
    return "\n\n".join(rules)


__all__ = ["build_reference_style_css"]
