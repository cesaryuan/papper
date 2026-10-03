"""Keep DOCX equation tab stops aligned with configured page margins."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from docx.shared import Cm, Inches, Mm, Pt

from ..runtime.metadata import PmtSettings

DEFAULT_A4_PAGE_WIDTH_TWIPS = Mm(210).twips
DEFAULT_REFERENCE_MARGIN_TWIPS = Inches(0.75).twips
EQN_BLOCK_TEMPLATE_KEY = "eqnBlockTemplate"
TABLE_EQNS_KEY = "tableEqns"
EQN_BLOCK_INLINE_MATH_KEY = "eqnBlockInlineMath"
WORD_TABLE_EQN_BLOCK_TEMPLATE = """\
+:------+:--------------------------------------------------:+--------:+
|       | $$t$$                                              | $$nmi$$ |
+-------+----------------------------------------------------+---------+
"""
MATHTYPE_TAB_EQN_BLOCK_TEMPLATE = (
    '`<w:pPr><w:tabs><w:tab w:val="center" w:leader="none" w:pos="4156" />'
    '<w:tab w:val="right" w:leader="none" w:pos="8312" /></w:tabs></w:pPr>'
    '<w:r><w:tab /></w:r>`{=openxml}$$t$$`<w:r><w:tab /></w:r>`{=openxml}$$nmi$$'
)
TAB_STOP_PATTERN_TEMPLATE = r'(<w:tab\b(?=[^>]*\bw:val="{val}")(?=[^>]*\bw:pos=")[^>]*\bw:pos=")\d+(")'


def first_present(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    """Return the first configured metadata value for a group of alias keys."""
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def parse_docx_length_twips(value: Any, field_name: str) -> int:
    """Parse a Word length value and return rounded twips."""
    if isinstance(value, (int, float)):
        return Pt(float(value)).twips
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a length value, got: {value!r}")

    cleaned = value.strip().lower()
    match = re.match(r"^(-?\d+(?:\.\d+)?)\s*(pt|磅|cm|厘米|mm|毫米|in|inch|inches|英寸)?$", cleaned)
    if not match:
        raise ValueError(f"{field_name} must be a length such as 72pt, 2.54cm, or 1in")

    amount = float(match.group(1))
    if amount < 0:
        raise ValueError(f"{field_name} must be greater than or equal to 0")

    unit = match.group(2) or "pt"
    if unit in ("pt", "磅"):
        return Pt(amount).twips
    if unit in ("cm", "厘米"):
        return Cm(amount).twips
    if unit in ("mm", "毫米"):
        return Mm(amount).twips
    return Inches(amount).twips


def page_width_twips_from_settings(settings: PmtSettings) -> int:
    """Return configured DOCX page width, defaulting to the template's A4 width."""
    value = settings.docx_page_width
    if value is None:
        return DEFAULT_A4_PAGE_WIDTH_TWIPS
    return parse_docx_length_twips(value, "docxPageWidth")


def margin_twips_from_settings(settings: PmtSettings, side: str) -> int:
    """Return a horizontal DOCX margin, using the reference-doc default when absent."""
    raw_margins = settings.docx_page_margins
    if raw_margins is None:
        return DEFAULT_REFERENCE_MARGIN_TWIPS
    if not isinstance(raw_margins, dict):
        raise ValueError("docxPageMargins metadata must be a mapping")

    value = first_present(raw_margins, (side,))
    if value is None:
        return DEFAULT_REFERENCE_MARGIN_TWIPS
    return parse_docx_length_twips(value, f"docxPageMargins.{side}")


def equation_tab_stops_from_settings(settings: PmtSettings) -> tuple[int, int] | None:
    """Return center/right tab stops when docxPageMargins should drive equations."""
    if settings.docx_page_margins is None:
        return None

    page_width = page_width_twips_from_settings(settings)
    left_margin = margin_twips_from_settings(settings, "left")
    right_margin = margin_twips_from_settings(settings, "right")
    return equation_tab_stops_from_page_width(page_width, left_margin, right_margin)


def equation_tab_stops_from_page_width(
    page_width: int, left_margin: int, right_margin: int
) -> tuple[int, int]:
    """Return center/right equation tab positions in twips for the writable page width."""
    right_tab = page_width - left_margin - right_margin
    if right_tab <= 0:
        raise ValueError("docxPageMargins left/right values leave no positive DOCX text width")
    return round(right_tab / 2), right_tab


def replace_tab_stop_position(template: str, tab_value: str, position: int) -> str:
    """Replace one OpenXML tab stop position inside an eqnBlockTemplate string."""
    pattern = re.compile(TAB_STOP_PATTERN_TEMPLATE.format(val=re.escape(tab_value)))
    return pattern.sub(rf"\g<1>{position}\2", template)


def derive_docx_equation_layout(
    pandoc_metadata: dict[str, Any],
    *,
    use_mathtype: bool,
) -> dict[str, Any]:
    """Derive pandoc-crossref equation layout from the active DOCX backend."""
    derived = deepcopy(pandoc_metadata)
    derived[TABLE_EQNS_KEY] = True
    if use_mathtype:
        derived[EQN_BLOCK_TEMPLATE_KEY] = MATHTYPE_TAB_EQN_BLOCK_TEMPLATE
        derived[EQN_BLOCK_INLINE_MATH_KEY] = True
    else:
        derived[EQN_BLOCK_TEMPLATE_KEY] = WORD_TABLE_EQN_BLOCK_TEMPLATE
        derived.pop(EQN_BLOCK_INLINE_MATH_KEY, None)
    return derived


def sync_eqn_block_template_with_page_margins(
    pandoc_metadata: dict[str, Any],
    pmt_settings: PmtSettings,
) -> tuple[dict[str, Any], tuple[int, int] | None]:
    """Sync Pandoc's equation template from the separately owned Papper margins."""
    tab_stops = equation_tab_stops_from_settings(pmt_settings)
    template = pandoc_metadata.get(EQN_BLOCK_TEMPLATE_KEY)
    if tab_stops is None or not isinstance(template, str) or "w:pos=" not in template:
        return pandoc_metadata, None

    center_tab, right_tab = tab_stops
    synced_metadata = deepcopy(pandoc_metadata)
    synced_template = replace_tab_stop_position(template, "center", center_tab)
    synced_template = replace_tab_stop_position(synced_template, "right", right_tab)
    synced_metadata[EQN_BLOCK_TEMPLATE_KEY] = synced_template
    return synced_metadata, tab_stops
