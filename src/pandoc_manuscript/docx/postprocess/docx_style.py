"""
Apply DOCX paragraph style settings from merged YAML metadata.

Supported metadata:
    docxStyle:
      '正文文本':
        fontSize: 小五
        fontFamily: 宋体  # or {western: "Times New Roman", chinese: "宋体"}
        bold: false
        fontColor: '#000000'
        lineSpacing: 1.5
        alignment: justify
        firstLineIndentChars: 2
        paragraphSpacing:
          before: 0pt
          after: 0pt
        indentation:
          left: 0pt
          right: 0pt
      'Para After Table':
        paragraphSpacing:
          before: 0pt
          after: 6pt
"""

import re
from typing import Any

from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.document import Document as DocumentObject
from docx.oxml.ns import qn
from docx.shared import Cm, Inches, Mm, Pt, RGBColor
from docx.styles.style import _ParagraphStyle

from .common import (
    get_or_add_child,
    print_warning,
    set_style_first_line_indent_chars as set_common_style_first_line_indent_chars,
)

from ...runtime.metadata import PmtSettings


ALIGNMENT_VALUES = {
    "left": WD_ALIGN_PARAGRAPH.LEFT,
    "center": WD_ALIGN_PARAGRAPH.CENTER,
    "centre": WD_ALIGN_PARAGRAPH.CENTER,
    "right": WD_ALIGN_PARAGRAPH.RIGHT,
    "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
    "justified": WD_ALIGN_PARAGRAPH.JUSTIFY,
    "both": WD_ALIGN_PARAGRAPH.JUSTIFY,
    "distribute": getattr(WD_ALIGN_PARAGRAPH, "DISTRIBUTE", WD_ALIGN_PARAGRAPH.JUSTIFY),
}
CHINESE_FONT_SIZE_POINTS = {
    "初号": 42.0,
    "小初": 36.0,
    "小初号": 36.0,
    "一号": 26.0,
    "小一": 24.0,
    "小一号": 24.0,
    "二号": 22.0,
    "小二": 18.0,
    "小二号": 18.0,
    "三号": 16.0,
    "小三": 15.0,
    "小三号": 15.0,
    "四号": 14.0,
    "小四": 12.0,
    "小四号": 12.0,
    "五号": 10.5,
    "小五": 9.0,
    "小五号": 9.0,
    "六号": 7.5,
    "小六": 6.5,
    "小六号": 6.5,
    "七号": 5.5,
    "八号": 5.0,
}
# Word's built-in styles are localized in the UI, but python-docx looks them up by the
# underlying built-in style names. Map common Chinese display names to those lookup names.
BUILTIN_STYLE_NAME_ALIASES = {
    "正文": "Normal",
    "正文文本": "Body Text",
    "标题": "Title",
    "副标题": "Subtitle",
    "题注": "Caption",
    "页眉": "Header",
    "页脚": "Footer",
    "脚注文本": "Footnote Text",
    "脚注引用": "Footnote Reference",
    "尾注文本": "Endnote Text",
    "尾注引用": "Endnote Reference",
    "引用": "Quote",
    "明显引用": "Intense Quote",
    "列表段落": "List Paragraph",
    "无间隔": "No Spacing",
}
for level in range(1, 10):
    BUILTIN_STYLE_NAME_ALIASES[f"标题{level}"] = f"Heading {level}"
    BUILTIN_STYLE_NAME_ALIASES[f"标题 {level}"] = f"Heading {level}"
    BUILTIN_STYLE_NAME_ALIASES[f"目录{level}"] = f"TOC {level}"
    BUILTIN_STYLE_NAME_ALIASES[f"目录 {level}"] = f"TOC {level}"
def first_present(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    """Return the first present value from a mapping for a group of alias keys."""
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def unique_ordered(values: tuple[str, ...]) -> tuple[str, ...]:
    """Return values without duplicates while preserving lookup order."""
    seen: set[str] = set()
    unique_values: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique_values.append(value)
    return tuple(unique_values)


def candidate_style_names_for(style_name: str) -> tuple[str, ...]:
    """Return exact style name plus common Chinese built-in style aliases."""
    normalized_style_name = re.sub(r"\s+", "", style_name.strip())
    alias = BUILTIN_STYLE_NAME_ALIASES.get(style_name.strip())
    alias = alias or BUILTIN_STYLE_NAME_ALIASES.get(normalized_style_name)
    if alias is None:
        return (style_name,)
    return unique_ordered((style_name, alias, alias.lower()))


def parse_number(value: Any, field_name: str) -> float:
    """Parse a numeric metadata value."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            raise ValueError(f"{field_name} must be a number")
        match = re.match(r'^(-?\d+(?:\.\d+)?)\s*(chars?|characters?|ch|字符)?$', cleaned, re.IGNORECASE)
        if not match:
            raise ValueError(f"{field_name} must be a number, got: {value}")
        return float(match.group(1))
    raise ValueError(f"{field_name} must be a number, got: {value!r}")


def parse_points(value: Any, field_name: str) -> float:
    """Parse a point value from YAML metadata."""
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a point value, got: {value!r}")

    cleaned = value.strip().lower()
    if not cleaned:
        raise ValueError(f"{field_name} must use points, for example 0pt or 6pt")

    match = re.match(r'^(-?\d+(?:\.\d+)?)\s*(pt|磅)?$', cleaned)
    if not match:
        raise ValueError(f"{field_name} must use points, for example 0pt or 6pt")
    return float(match.group(1))


def parse_font_size_points(value: Any, field_name: str) -> float:
    """Parse a font size in points, including common Chinese Word size names."""
    if isinstance(value, str):
        cleaned = re.sub(r"\s+", "", value.strip())
        if cleaned in CHINESE_FONT_SIZE_POINTS:
            return CHINESE_FONT_SIZE_POINTS[cleaned]
    try:
        return parse_points(value, field_name)
    except ValueError as e:
        raise ValueError(f"{field_name} must be a point value or Chinese Word size such as 10.5pt, 小五, or 四号") from e


def parse_length(value: Any, field_name: str):
    """Parse an indentation length with common Word-friendly units."""
    if isinstance(value, (int, float)):
        return Pt(float(value))
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a length value, got: {value!r}")

    cleaned = value.strip().lower()
    match = re.match(r'^(-?\d+(?:\.\d+)?)\s*(pt|磅|cm|厘米|mm|毫米|in|inch|inches|英寸)?$', cleaned)
    if not match:
        raise ValueError(f"{field_name} must be a length such as 12pt, 0.5cm, or 0.2in")

    amount = float(match.group(1))
    unit = match.group(2) or "pt"
    if unit in ("pt", "磅"):
        return Pt(amount)
    if unit in ("cm", "厘米"):
        return Cm(amount)
    if unit in ("mm", "毫米"):
        return Mm(amount)
    return Inches(amount)


def parse_font_color(value: Any, field_name: str) -> tuple[int, int, int]:
    """Parse a font color as #RRGGBB, RRGGBB, rgb(r,g,b), or a three-item list."""
    if isinstance(value, (list, tuple)) and len(value) == 3:
        channels = [int(channel) for channel in value]
    elif isinstance(value, str):
        cleaned = value.strip()
        hex_match = re.match(r"^#?([0-9a-fA-F]{6})$", cleaned)
        rgb_match = re.match(r"^rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)$", cleaned)
        if hex_match:
            hex_value = hex_match.group(1)
            channels = [int(hex_value[index:index + 2], 16) for index in (0, 2, 4)]
        elif rgb_match:
            channels = [int(rgb_match.group(index)) for index in (1, 2, 3)]
        else:
            raise ValueError(f"{field_name} must be a color such as '#1A2B3C' or rgb(26,43,60)")
    else:
        raise ValueError(f"{field_name} must be a color value, got: {value!r}")

    if any(channel < 0 or channel > 255 for channel in channels):
        raise ValueError(f"{field_name} RGB channels must be between 0 and 255")
    return channels[0], channels[1], channels[2]


def parse_line_spacing(value: Any, field_name: str) -> tuple[str, Any, str]:
    """Parse line spacing as a multiple such as 1.5 or an exact point value such as 18pt."""
    if isinstance(value, dict):
        value = first_present(value, ("value", "spacing", "lineSpacing", "line-spacing", "line_spacing"))
    if isinstance(value, (int, float)):
        spacing = float(value)
        if spacing <= 0:
            raise ValueError(f"{field_name} must be greater than 0")
        return "multiple", spacing, f"{spacing:g}"
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a number or point value, got: {value!r}")

    cleaned = value.strip().lower()
    named_values = {
        "single": 1.0,
        "1": 1.0,
        "one-half": 1.5,
        "onehalf": 1.5,
        "1.5": 1.5,
        "double": 2.0,
        "2": 2.0,
    }
    if cleaned in named_values:
        spacing = named_values[cleaned]
        return "multiple", spacing, f"{spacing:g}"
    if re.match(r"^-?\d+(?:\.\d+)?\s*(pt|磅)$", cleaned):
        points = parse_points(cleaned, field_name)
        if points <= 0:
            raise ValueError(f"{field_name} must be greater than 0")
        return "points", Pt(points), f"{points:g}pt"

    spacing = parse_number(cleaned, field_name)
    if spacing <= 0:
        raise ValueError(f"{field_name} must be greater than 0")
    return "multiple", spacing, f"{spacing:g}"


def parse_alignment(value: Any, field_name: str):
    """Parse paragraph alignment metadata into a python-docx alignment enum value."""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an alignment string")
    normalized = value.strip().lower()
    if normalized not in ALIGNMENT_VALUES:
        expected = ", ".join(sorted(ALIGNMENT_VALUES))
        raise ValueError(f"{field_name} must be one of: {expected}")
    return ALIGNMENT_VALUES[normalized], normalized


def parse_font_family(value: Any, field_name: str) -> dict[str, str]:
    """Parse one font family into separate Western and Chinese font names."""
    if isinstance(value, str):
        family = value.strip()
        if not family:
            raise ValueError(f"{field_name} must be a non-empty font name")
        return {"western": family, "chinese": family}
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a font name or a mapping")

    parsed: dict[str, str] = {}
    for target, raw_family in value.items():
        if target not in {"western", "chinese"}:
            raise ValueError(f"{field_name} keys must be western or chinese")
        if not isinstance(raw_family, str) or not raw_family.strip():
            raise ValueError(f"{field_name}.{target} must be a non-empty font name")
        parsed[target] = raw_family.strip()
    if not parsed:
        raise ValueError(f"{field_name} must set western or chinese")
    return parsed


def normalize_paragraph_style_settings(
    raw_settings: dict[str, Any],
    field_prefix: str,
) -> dict[str, Any]:
    """Normalize one paragraph style metadata block without inventing unspecified formatting."""
    spacing = first_present(raw_settings, ("paragraphSpacing", "paragraph-spacing", "paragraph_spacing")) or {}
    if not isinstance(spacing, dict):
        raise ValueError(f"{field_prefix}.paragraphSpacing metadata must be a mapping")
    indentation = first_present(raw_settings, ("indentation", "indent", "paragraphIndent", "paragraph-indent")) or {}
    if not isinstance(indentation, dict):
        raise ValueError(f"{field_prefix}.indentation metadata must be a mapping")
    font = first_present(raw_settings, ("font", "textStyle", "text-style", "text_style")) or {}
    if not isinstance(font, dict):
        raise ValueError(f"{field_prefix}.font metadata must be a mapping")

    indent_value = first_present(
        raw_settings,
        ("firstLineIndentChars", "first-line-indent-chars", "first_line_indent_chars"),
    )
    indent_value = indent_value if indent_value is not None else first_present(
        indentation,
        ("firstLineChars", "first-line-chars", "first_line_chars", "firstLineIndentChars", "first-line-indent-chars"),
    )
    before_value = first_present(spacing, ("before", "spaceBefore", "space-before", "space_before"))
    after_value = first_present(spacing, ("after", "spaceAfter", "space-after", "space_after"))
    font_size_value = first_present(raw_settings, ("fontSize", "font-size", "font_size"))
    font_size_value = font_size_value if font_size_value is not None else first_present(font, ("size", "fontSize", "font-size"))
    font_family_value = first_present(
        raw_settings,
        ("fontFamily", "font-family", "font_family", "fontName", "font-name", "font_name"),
    )
    font_family_value = font_family_value if font_family_value is not None else first_present(
        font,
        ("family", "name", "fontFamily", "font-family", "fontName", "font-name"),
    )
    bold_value = first_present(raw_settings, ("bold", "fontBold", "font-bold", "font_bold"))
    bold_value = bold_value if bold_value is not None else first_present(
        font,
        ("bold", "fontBold", "font-bold", "font_bold"),
    )
    font_color_value = first_present(raw_settings, ("fontColor", "font-color", "font_color", "color"))
    font_color_value = font_color_value if font_color_value is not None else first_present(font, ("color", "fontColor", "font-color"))
    line_spacing_value = first_present(raw_settings, ("lineSpacing", "line-spacing", "line_spacing"))
    alignment_value = first_present(raw_settings, ("alignment", "align", "paragraphAlignment", "paragraph-alignment"))
    left_indent_value = first_present(raw_settings, ("leftIndent", "left-indent", "left_indent"))
    left_indent_value = left_indent_value if left_indent_value is not None else first_present(indentation, ("left", "leftIndent", "left-indent"))
    right_indent_value = first_present(raw_settings, ("rightIndent", "right-indent", "right_indent"))
    right_indent_value = right_indent_value if right_indent_value is not None else first_present(indentation, ("right", "rightIndent", "right-indent"))
    first_line_indent_value = first_present(raw_settings, ("firstLineIndent", "first-line-indent", "first_line_indent"))
    first_line_indent_value = first_line_indent_value if first_line_indent_value is not None else first_present(indentation, ("firstLine", "first-line", "first_line", "firstLineIndent"))
    hanging_indent_value = first_present(raw_settings, ("hangingIndent", "hanging-indent", "hanging_indent"))
    hanging_indent_value = hanging_indent_value if hanging_indent_value is not None else first_present(indentation, ("hanging", "hangingIndent", "hanging-indent"))

    if indent_value is not None and (first_line_indent_value is not None or hanging_indent_value is not None):
        raise ValueError(f"{field_prefix} cannot set character first-line indent together with length-based first-line or hanging indent")
    if first_line_indent_value is not None and hanging_indent_value is not None:
        raise ValueError(f"{field_prefix} cannot set both firstLineIndent and hangingIndent")

    normalized: dict[str, Any] = {}
    if font_size_value is not None:
        font_size_pt = parse_font_size_points(font_size_value, f"{field_prefix}.fontSize")
        if font_size_pt <= 0:
            raise ValueError(f"{field_prefix}.fontSize must be greater than 0")
        normalized["font_size_pt"] = font_size_pt
    if font_family_value is not None:
        normalized["font_family"] = parse_font_family(font_family_value, f"{field_prefix}.fontFamily")
    if bold_value is not None:
        if isinstance(bold_value, bool):
            normalized["bold"] = bold_value
        elif isinstance(bold_value, str) and bold_value.strip().casefold() in {"true", "false"}:
            normalized["bold"] = bold_value.strip().casefold() == "true"
        else:
            raise ValueError(f"{field_prefix}.bold must be true or false")
    if font_color_value is not None:
        normalized["font_color_rgb"] = parse_font_color(font_color_value, f"{field_prefix}.fontColor")
    if line_spacing_value is not None:
        _, line_spacing, line_spacing_display = parse_line_spacing(line_spacing_value, f"{field_prefix}.lineSpacing")
        normalized["line_spacing"] = line_spacing
        normalized["line_spacing_display"] = line_spacing_display
    if alignment_value is not None:
        alignment, alignment_display = parse_alignment(alignment_value, f"{field_prefix}.alignment")
        normalized["alignment"] = alignment
        normalized["alignment_display"] = alignment_display
    if indent_value is not None:
        normalized["first_line_indent_chars"] = parse_number(
            indent_value,
            f"{field_prefix}.firstLineIndentChars",
        )
    if left_indent_value is not None:
        normalized["left_indent"] = parse_length(left_indent_value, f"{field_prefix}.indentation.left")
        normalized["left_indent_display"] = str(left_indent_value)
    if right_indent_value is not None:
        normalized["right_indent"] = parse_length(right_indent_value, f"{field_prefix}.indentation.right")
        normalized["right_indent_display"] = str(right_indent_value)
    if first_line_indent_value is not None:
        normalized["first_line_indent"] = parse_length(first_line_indent_value, f"{field_prefix}.indentation.firstLine")
        normalized["first_line_indent_display"] = str(first_line_indent_value)
    if hanging_indent_value is not None:
        normalized["first_line_indent"] = -parse_length(hanging_indent_value, f"{field_prefix}.indentation.hanging")
        normalized["hanging_indent_display"] = str(hanging_indent_value)
    if before_value is not None:
        normalized["space_before_pt"] = parse_points(
            before_value,
            f"{field_prefix}.paragraphSpacing.before",
        )
    if after_value is not None:
        normalized["space_after_pt"] = parse_points(
            after_value,
            f"{field_prefix}.paragraphSpacing.after",
        )
    return normalized


def normalize_docx_style_settings(settings: PmtSettings) -> list[dict[str, Any]] | None:
    """Normalize all configured docxStyle entries into style application records."""
    style_map = settings.docx_style
    if style_map is None:
        return None

    metadata_key = "docxStyle"
    normalized_styles: list[dict[str, Any]] = []
    for style_name, raw_settings in style_map.items():
        field_prefix = f"{metadata_key}.{style_name}"
        if not isinstance(style_name, str) or not style_name.strip():
            raise ValueError(f"{metadata_key} style names must be non-empty strings")
        if not isinstance(raw_settings, dict):
            raise ValueError(f"{field_prefix} metadata must be a mapping")
        normalized_styles.append(
            {
                "style_name": style_name,
                "candidate_style_names": candidate_style_names_for(style_name),
                **normalize_paragraph_style_settings(raw_settings, field_prefix),
            }
        )

    return normalized_styles


def set_style_first_line_indent_chars(style: _ParagraphStyle, chars: float, field_name: str) -> None:
    """Set a paragraph style first-line indent using the metadata field name in errors."""
    set_common_style_first_line_indent_chars(
        style,
        chars,
        field_name,
    )


def clear_style_character_indent(style: _ParagraphStyle) -> None:
    """Remove character-indent attributes before applying length-based first-line or hanging indent."""
    p_pr = style.element.get_or_add_pPr()
    ind = get_or_add_child(p_pr, "w:ind")
    for attr_name in ("w:firstLineChars", "w:hangingChars"):
        qname = qn(attr_name)
        if qname in ind.attrib:
            del ind.attrib[qname]


def get_paragraph_style(
    doc: DocumentObject,
    style_name: str,
    candidate_style_names: tuple[str, ...],
) -> tuple[_ParagraphStyle, str] | None:
    """Return a paragraph style by exact DOCX style name, or None when it is missing or unsuitable."""
    for candidate_style_name in candidate_style_names:
        try:
            style = doc.styles[candidate_style_name]
        except KeyError:
            continue
        if not isinstance(style, _ParagraphStyle):
            print_warning(f"DOCX style '{candidate_style_name}' is not a paragraph style, skipping")
            return None
        return style, candidate_style_name

    print_warning(f"DOCX style '{style_name}' was not found, skipping")
    return None


def apply_paragraph_style_settings(doc: DocumentObject, settings: dict[str, Any]) -> dict[str, Any] | None:
    """Apply normalized paragraph style settings to one DOCX style if it exists."""
    style_name = settings["style_name"]
    style_result = get_paragraph_style(doc, style_name, settings["candidate_style_names"])
    if style_result is None:
        return None
    style, applied_style_name = style_result

    if "font_size_pt" in settings:
        style.font.size = Pt(settings["font_size_pt"])
    if "font_family" in settings:
        font_family = settings["font_family"]
        r_fonts = style.element.get_or_add_rPr().get_or_add_rFonts()
        if "western" in font_family:
            style.font.name = font_family["western"]
            for script in ("ascii", "hAnsi"):
                r_fonts.set(qn(f"w:{script}"), font_family["western"])
            for theme in ("asciiTheme", "hAnsiTheme"):
                r_fonts.attrib.pop(qn(f"w:{theme}"), None)
        if "chinese" in font_family:
            r_fonts.set(qn("w:eastAsia"), font_family["chinese"])
            r_fonts.attrib.pop(qn("w:eastAsiaTheme"), None)
    if "bold" in settings:
        style.font.bold = settings["bold"]
        style.element.get_or_add_rPr().get_or_add_bCs().val = settings["bold"]
    if "font_color_rgb" in settings:
        red, green, blue = settings["font_color_rgb"]
        style.font.color.rgb = RGBColor(red, green, blue)
    if "first_line_indent_chars" in settings:
        set_style_first_line_indent_chars(
            style,
            settings["first_line_indent_chars"],
            f"docxStyle.{style_name}.firstLineIndentChars",
        )
    paragraph_format = style.paragraph_format
    if "space_before_pt" in settings:
        paragraph_format.space_before = Pt(settings["space_before_pt"])
    if "space_after_pt" in settings:
        paragraph_format.space_after = Pt(settings["space_after_pt"])
    if "line_spacing" in settings:
        paragraph_format.line_spacing = settings["line_spacing"]
    if "alignment" in settings:
        paragraph_format.alignment = settings["alignment"]
    if "left_indent" in settings:
        paragraph_format.left_indent = settings["left_indent"]
    if "right_indent" in settings:
        paragraph_format.right_indent = settings["right_indent"]
    if "first_line_indent" in settings:
        # Length-based first-line and hanging indents conflict with Word's character-indent attrs.
        clear_style_character_indent(style)
        paragraph_format.first_line_indent = settings["first_line_indent"]

    return {
        **settings,
        "applied_style_name": applied_style_name,
    }


def format_applied_style_summary(applied: dict[str, Any]) -> str:
    """Return one concise debug summary for an applied DOCX style update."""
    details = []
    if "font_size_pt" in applied:
        details.append(f"font size {applied['font_size_pt']:g} pt")
    if "font_color_rgb" in applied:
        red, green, blue = applied["font_color_rgb"]
        details.append(f"font color #{red:02X}{green:02X}{blue:02X}")
    if "font_family" in applied:
        families = applied["font_family"]
        details.append("font family " + ", ".join(f"{key}={value}" for key, value in families.items()))
    if "bold" in applied:
        details.append(f"bold {str(applied['bold']).lower()}")
    if "line_spacing_display" in applied:
        details.append(f"line spacing {applied['line_spacing_display']}")
    if "alignment_display" in applied:
        details.append(f"alignment {applied['alignment_display']}")
    if "first_line_indent_chars" in applied:
        details.append(f"first-line indent {applied['first_line_indent_chars']:g} chars")
    if "left_indent_display" in applied:
        details.append(f"left indent {applied['left_indent_display']}")
    if "right_indent_display" in applied:
        details.append(f"right indent {applied['right_indent_display']}")
    if "first_line_indent_display" in applied:
        details.append(f"first-line indent {applied['first_line_indent_display']}")
    if "hanging_indent_display" in applied:
        details.append(f"hanging indent {applied['hanging_indent_display']}")
    if "space_before_pt" in applied:
        details.append(f"before {applied['space_before_pt']:g} pt")
    if "space_after_pt" in applied:
        details.append(f"after {applied['space_after_pt']:g} pt")
    detail_text = ", ".join(details) if details else "no supported fields changed"
    return f"Applied style '{applied['applied_style_name']}': {detail_text}"


def apply_docx_style_settings(doc: DocumentObject, settings: PmtSettings) -> dict[str, Any] | None:
    """Apply all paragraph styles configured in typed Papper settings."""
    normalized_styles = normalize_docx_style_settings(settings)
    if normalized_styles is None:
        return None

    applied_styles: list[dict[str, Any]] = []
    skipped_styles: list[str] = []
    for settings in normalized_styles:
        result = apply_paragraph_style_settings(doc, settings)
        if result is None:
            skipped_styles.append(settings["style_name"])
            continue
        applied_styles.append(result)

    return {
        "applied": applied_styles,
        "skipped": skipped_styles,
    }
