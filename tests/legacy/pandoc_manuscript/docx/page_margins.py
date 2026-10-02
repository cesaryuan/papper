"""Prepare DOCX reference documents with metadata-driven page margins."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from docx import Document
from docx.document import Document as DocumentObject
from docx.shared import Emu

from ..runtime.metadata import PmtSettings

from ..runtime.page_margins import MARGIN_SIDE_ALIASES, PageMarginValues

first_present = PageMarginValues.first_present


def parse_margin_length(value: Any, field_name: str):
    """Adapt shared margin validation to a native DOCX length."""
    return Emu(PageMarginValues.parse_margin_length(value, field_name))


def normalize_page_margins(settings: PmtSettings) -> tuple[dict[str, Any], dict[str, str]] | None:
    """Preserve native DOCX section values after shared margin validation."""
    normalized = PageMarginValues.normalize_page_margins(settings)
    if normalized is None:
        return None
    margins, display = normalized
    return {side: Emu(value) for side, value in margins.items()}, display


def apply_page_margins(doc: DocumentObject, margins: dict[str, Any]) -> int:
    """Apply normalized page margins to every section in a DOCX document."""
    updated = 0
    for section in doc.sections:
        if "top" in margins:
            section.top_margin = margins["top"]
        if "bottom" in margins:
            section.bottom_margin = margins["bottom"]
        if "left" in margins:
            section.left_margin = margins["left"]
        if "right" in margins:
            section.right_margin = margins["right"]
        updated += 1
    return updated


def apply_page_margin_settings(doc: DocumentObject, settings: PmtSettings) -> dict[str, Any] | None:
    """Apply typed DOCX page-margin settings to a document object."""
    normalized = normalize_page_margins(settings)
    if normalized is None:
        return None

    margins, display_values = normalized
    if not margins:
        return None

    return {
        "margins": display_values,
        "sections": apply_page_margins(doc, margins),
    }


def write_reference_doc_with_page_margins(
    source_docx: Path,
    target_docx: Path,
    settings: PmtSettings,
) -> dict[str, Any] | None:
    """Copy a reference DOCX and apply docxPageMargins before Pandoc reads it.

    Pandoc sizes DOCX images from the reference document's writable page width,
    so page margins must be present in the reference DOCX instead of applied
    after conversion.
    """
    normalized = normalize_page_margins(settings)
    if normalized is None:
        return None

    margins, display_values = normalized
    if not margins:
        return None
    if not source_docx.exists():
        raise FileNotFoundError(f"Reference DOCX not found: {source_docx}")

    target_docx.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_docx, target_docx)
    doc = Document(str(target_docx))
    sections = apply_page_margins(doc, margins)
    doc.save(str(target_docx))
    return {
        "source": source_docx,
        "target": target_docx,
        "margins": display_values,
        "sections": sections,
    }
