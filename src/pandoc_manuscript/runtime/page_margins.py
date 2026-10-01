"""Validate page-margin metadata without importing the DOCX backend."""
from __future__ import annotations

import re
from typing import Any, TYPE_CHECKING
from .style_values import LayoutLength

if TYPE_CHECKING:
    from .metadata import PmtSettings

MARGIN_SIDE_ALIASES = {
    "top": ("top",),
    "bottom": ("bottom",),
    "left": ("left", "inside"),
    "right": ("right", "outside"),
}



class PageMarginValues:
    """Normalize shared page margins with the existing units and validation."""

    @staticmethod
    def first_present(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
        """Return the first configured metadata value for a group of alias keys."""
        for key in keys:
            if key in mapping:
                return mapping[key]
        return None


    @staticmethod
    def parse_margin_length(value: Any, field_name: str):
        """Parse a DOCX page margin length with common Word-friendly units."""
        if isinstance(value, (int, float)):
            return LayoutLength.from_unit(float(value), "pt")
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
            return LayoutLength.from_unit(amount, "pt")
        if unit in ("cm", "厘米"):
            return LayoutLength.from_unit(amount, "cm")
        if unit in ("mm", "毫米"):
            return LayoutLength.from_unit(amount, "mm")
        return LayoutLength.from_unit(amount, "in")


    @staticmethod
    def normalize_page_margins(settings: PmtSettings) -> tuple[dict[str, Any], dict[str, str]] | None:
        """Normalize docxPageMargins into section attributes without defaulting missing sides."""
        raw_margins = settings.docx_page_margins
        if raw_margins is None:
            return None

        margins: dict[str, Any] = {}
        display_values: dict[str, str] = {}
        for side, aliases in MARGIN_SIDE_ALIASES.items():
            value = PageMarginValues.first_present(raw_margins, aliases)
            if value is None:
                continue
            margins[side] = PageMarginValues.parse_margin_length(value, f"docxPageMargins.{side}")
            display_values[side] = str(value)

        return margins, display_values


