#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "python-docx>=1.1.0",
# ]
# ///
"""
Clear formatting for subfigure layout tables in DOCX files.

This script finds paragraphs styled as 'Image Caption'. If the immediately
preceding block is a table, that table is treated as a subfigure layout table.
The script then:
  1. Applies Word's neutral table style (`TableNormal`)
  2. Sets all cell margins to 0

Usage:
    uv run clear_subfigure_table_format.py path/to/file.docx
    or as a module: clear_subfigure_table_format(doc)
"""

import argparse
import sys
from pathlib import Path
from typing import Optional

from .autofit_tables import set_table_autofit_window
from .common import (
    get_or_add_tbl_pr,
    open_docx,
    print_error,
    print_debug,
    print_debug_success,
    save_docx,
)

try:
    from docx.document import Document as DocumentObject
    from docx.table import Table
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
except ImportError:
    print("Error: python-docx is not installed. Install it with: pip install python-docx")
    sys.exit(1)


def get_normal_table_style_id(doc: DocumentObject) -> str:
    """Return the actual style id used by this document for 'Normal Table'."""
    styles_element = doc.styles.element

    for style in styles_element:
        if style.tag != qn('w:style'):
            continue
        if style.get(qn('w:type')) != 'table':
            continue
        if style.get(qn('w:default')) == '1':
            return style.get(qn('w:styleId'))

    for style in styles_element:
        if style.tag != qn('w:style'):
            continue
        if style.get(qn('w:type')) != 'table':
            continue
        name = style.find(qn('w:name'))
        if name is not None and name.get(qn('w:val')) == 'Normal Table':
            return style.get(qn('w:styleId'))

    return 'TableNormal'


def set_neutral_table_style(table: Table, style_id: str) -> str:
    """Apply this document's neutral table style using its real style id."""
    tbl_pr = get_or_add_tbl_pr(table)

    tbl_style = tbl_pr.find(qn('w:tblStyle'))
    if tbl_style is not None:
        tbl_pr.remove(tbl_style)

    tbl_style = OxmlElement('w:tblStyle')
    tbl_style.set(qn('w:val'), style_id)
    tbl_pr.insert(0, tbl_style)

    tbl_look = tbl_pr.find(qn('w:tblLook'))
    if tbl_look is not None:
        tbl_pr.remove(tbl_look)

    return style_id


def set_zero_cell_margins(table: Table) -> None:
    """Set all table cell margins to zero."""
    tbl_pr = get_or_add_tbl_pr(table)

    tbl_cell_mar = tbl_pr.find(qn('w:tblCellMar'))
    if tbl_cell_mar is None:
        tbl_cell_mar = OxmlElement('w:tblCellMar')
        tbl_pr.append(tbl_cell_mar)

    for side in ['top', 'left', 'bottom', 'right']:
        margin = tbl_cell_mar.find(qn(f'w:{side}'))
        if margin is not None:
            tbl_cell_mar.remove(margin)

        margin = OxmlElement(f'w:{side}')
        margin.set(qn('w:w'), '0')
        margin.set(qn('w:type'), 'dxa')
        tbl_cell_mar.append(margin)


def format_subfigure_table(table: Table, style_id: str) -> None:
    set_neutral_table_style(table, style_id)
    set_zero_cell_margins(table)

    for row in table.rows:
        for cell in row.cells:
            for nested_table in cell.tables:
                format_subfigure_table(nested_table, style_id)
                set_table_autofit_window(nested_table)


def clear_subfigure_table_format(doc: DocumentObject, caption_style: str = 'Image Caption') -> int:
    """Format tables immediately above image-caption paragraphs."""
    processed_count = 0
    seen_tables = set()
    normal_table_style_id = get_normal_table_style_id(doc)

    for paragraph in doc.paragraphs:
        try:
            if paragraph.style.name != caption_style: # type: ignore
                continue
        except Exception:
            continue

        previous = paragraph._element.getprevious()
        if previous is None or previous.tag != qn('w:tbl'):
            continue

        table = Table(previous, paragraph._parent)
        table_id = id(previous)
        if table_id in seen_tables:
            continue

        format_subfigure_table(table, normal_table_style_id)
        seen_tables.add(table_id)
        processed_count += 1

    if processed_count == 0:
        print_debug(f"No tables found above '{caption_style}' paragraphs")
    else:
        print_debug_success(f"Processed {processed_count} subfigure table(s)")

    return processed_count


def process_file(docx_path: str, save: bool = True) -> Optional[DocumentObject]:
    try:
        doc, docx_path_abs = open_docx(docx_path)
        if doc is None or docx_path_abs is None:
            return None

        processed_count = clear_subfigure_table_format(doc)

        if save:
            if processed_count > 0:
                save_docx(doc, docx_path_abs)
            else:
                print_debug("No changes made, skipping save")

        print_debug_success("\nSubfigure table formatting cleanup completed successfully!")
        return doc

    except Exception as e:
        print_error(f"\nSubfigure table formatting cleanup failed: {e}")
        import traceback
        traceback.print_exc()
        return None


def main():
    parser = argparse.ArgumentParser(
        description="Format tables above 'Image Caption' paragraphs in DOCX files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  uv run clear_subfigure_table_format.py manuscript.docx
  uv run clear_subfigure_table_format.py output/docx/manuscript.docx
  uv run clear_subfigure_table_format.py manuscript.docx --no-save
        """
    )
    parser.add_argument("docx_path", help="Path to the DOCX file to process")
    parser.add_argument("--no-save", action="store_true", help="Don't save the document")

    args = parser.parse_args()
    result = process_file(args.docx_path, save=not args.no_save)
    sys.exit(0 if result else 1)


if __name__ == "__main__":
    main()
