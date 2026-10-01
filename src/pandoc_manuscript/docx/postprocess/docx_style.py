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
from docx.shared import Emu, Pt, RGBColor
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

from ...runtime.style_values import (
    BUILTIN_STYLE_NAME_ALIASES, CHINESE_FONT_SIZE_POINTS, ParagraphStyleValues,
)

first_present = ParagraphStyleValues.first_present
unique_ordered = ParagraphStyleValues.unique_ordered
candidate_style_names_for = ParagraphStyleValues.candidate_style_names_for
parse_number = ParagraphStyleValues.parse_number
parse_points = ParagraphStyleValues.parse_points
parse_font_size_points = ParagraphStyleValues.parse_font_size_points
parse_font_color = ParagraphStyleValues.parse_font_color
parse_font_family = ParagraphStyleValues.parse_font_family


def parse_length(value: Any, field_name: str):
    """Convert shared length validation to the native DOCX length type."""
    return Emu(ParagraphStyleValues.parse_length(value, field_name))


def parse_line_spacing(value: Any, field_name: str) -> tuple[str, Any, str]:
    """Preserve DOCX's distinction between exact and multiple line spacing."""
    kind, spacing, display = ParagraphStyleValues.parse_line_spacing(value, field_name)
    return kind, Emu(spacing) if kind == "points" else spacing, display


def parse_alignment(value: Any, field_name: str):
    """Adapt validated alignment names to the native DOCX enum."""
    _, display = ParagraphStyleValues.parse_alignment(value, field_name)
    return ALIGNMENT_VALUES[display], display


def normalize_paragraph_style_settings(raw_settings: dict[str, Any], field_prefix: str) -> dict[str, Any]:
    """Normalize paragraph metadata using native DOCX value adapters."""
    return ParagraphStyleValues.normalize_paragraph_style_settings(
        raw_settings, field_prefix, parse_length, parse_alignment, parse_line_spacing,
    )


def normalize_docx_style_settings(settings: PmtSettings) -> list[dict[str, Any]] | None:
    """Normalize configured Word styles while retaining native DOCX values."""
    return ParagraphStyleValues.normalize_docx_style_settings(settings, normalize_paragraph_style_settings)


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
