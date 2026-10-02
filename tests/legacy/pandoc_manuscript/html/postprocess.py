"""Ordered HTML post-processing pipeline for Papper manuscript builds."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..runtime.logging import log_debug, log_success
from .common import load_html_document, render_html_document, save_html_document
from .insert_author_info import insert_author_info
from .table_cell_margins import apply_html_table_cell_margins


def postprocess_html(
    html_path: str | Path,
    *,
    pandoc_metadata: dict[str, Any] | None = None,
    skip_author_info: bool = False,
) -> bool:
    """Apply the remaining HTML-only author metadata post-processing step."""
    path = Path(html_path)
    if not path.exists():
        log_debug(f"[HTML] Output not found, skipping post-processing: {path}")
        return False

    document = load_html_document(path)
    _postprocess_document(document, pandoc_metadata=pandoc_metadata, skip_author_info=skip_author_info)
    save_html_document(document, path)
    log_success(f"[OK] HTML post-processing completed: {path}")
    return True


def postprocess_html_text(
    html_text: str,
    *,
    pandoc_metadata: dict[str, Any] | None = None,
    skip_author_info: bool = False,
) -> str:
    """Post-process HTML in memory for the server's raw response path."""
    from lxml import etree, html

    document = etree.ElementTree(html.document_fromstring(html_text))
    _postprocess_document(document, pandoc_metadata=pandoc_metadata, skip_author_info=skip_author_info)
    return render_html_document(document)


def _postprocess_document(
    document: Any,
    *,
    pandoc_metadata: dict[str, Any] | None,
    skip_author_info: bool,
) -> None:
    """Apply the shared HTML mutations to an already parsed document."""
    metadata = pandoc_metadata or {}
    if skip_author_info:
        log_debug("[HTML] Skipping author information")
    else:
        authors, affiliations, footnote = insert_author_info(document, metadata)
        log_debug(
            f"[HTML] Authors: {authors}, Affiliations: {affiliations}, "
            f"Footnote: {'yes' if footnote else 'no'}"
        )
    table_margin_count = apply_html_table_cell_margins(document)
    if table_margin_count:
        log_debug(f"[HTML] Applied cell-margin attributes to {table_margin_count} table(s)")
