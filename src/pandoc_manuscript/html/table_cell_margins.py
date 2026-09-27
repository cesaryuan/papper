"""Apply per-table cell-margin attributes to generated HTML tables."""

from __future__ import annotations

import re
from typing import Any

from lxml import etree

from ..runtime.logging import log_warning

_DIMENSION_RE = re.compile(r"^\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>cm|mm|in|pt)?\s*$", re.IGNORECASE)
_SIDES = ("top", "right", "bottom", "left")


def _parse_css_points(value: str) -> float:
    """Convert a supported cell-margin value to non-negative CSS points."""
    match = _DIMENSION_RE.match(value)
    if match is None:
        raise ValueError("expected a non-negative value with cm, mm, in, or pt")
    amount = float(match.group("value"))
    unit = (match.group("unit") or "pt").casefold()
    factors = {"pt": 1.0, "in": 72.0, "cm": 72.0 / 2.54, "mm": 72.0 / 25.4}
    return amount * factors[unit]


def _format_css_points(points: float) -> str:
    """Format points compactly for an inline CSS custom property."""
    return f"{points:.4f}".rstrip("0").rstrip(".") + "pt"


def _attribute(table: etree._Element, name: str) -> str | None:
    """Read underscore and hyphen spellings of one table data attribute."""
    for key in (f"data-{name}", f"data-{name.replace('_', '-')}", name, name.replace("_", "-")):
        value = table.get(key)
        if value is not None:
            return value
    return None


def _table_margin_values(table: etree._Element) -> dict[str, float] | None:
    """Resolve all and per-side cell-margin attributes for one table."""
    values: dict[str, float] = {}
    all_value = _attribute(table, "cell_margin")
    if all_value is not None:
        parsed = _parse_css_points(all_value)
        values.update({side: parsed for side in _SIDES})
    for side in _SIDES:
        side_value = _attribute(table, f"cell_margin_{side}")
        if side_value is not None:
            values[side] = _parse_css_points(side_value)
    return values or None


def apply_html_table_cell_margins(document: Any) -> int:
    """Apply custom table cell margins as CSS variables inherited by cells."""
    applied = 0
    for table in document.xpath("//table"):
        try:
            values = _table_margin_values(table)
        except ValueError as exc:
            identifier = table.get("id") or "unnamed table"
            log_warning(f"[WARN] Ignoring invalid cell margin on {identifier}: {exc}")
            continue
        if values is None:
            continue
        declarations = [
            f"--pmt-table-cell-margin-{side}: {_format_css_points(value)}"
            for side, value in values.items()
        ]
        existing = table.get("style", "").strip().rstrip(";")
        table.set("style", "; ".join(filter(None, [existing, *declarations])) + ";")
        applied += 1
    return applied


__all__ = ["apply_html_table_cell_margins"]
