#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "python-docx>=1.1.0",
# ]
# ///
"""
Convert table text style from 'Compact' to 'Table Text'.
This script changes all text in tables that uses 'Compact' style to 'Table Text' style.

Usage:
    uv run table_text_style.py path/to/file.docx
    or as a module: convert_table_text_style(doc)
"""

import argparse
import sys
from pathlib import Path
from typing import Optional, cast

from .common import (
    get_normal_style,
    open_docx,
    print_error,
    print_debug,
    print_debug_success,
    print_warning,
    save_docx,
)
from .autofit_tables import is_equation_layout_table

try:
    from docx.document import Document as DocumentObject
    from docx.enum.style import WD_STYLE_TYPE
    from docx.table import Table, _Cell
    from docx.styles.style import _ParagraphStyle
except ImportError:
    print("Error: python-docx is not installed. Install it with: pip install python-docx")
    sys.exit(1)


def set_table_text_base_style(doc: DocumentObject, table_text_style: _ParagraphStyle) -> bool:
    """Set Table Text to be based on the document's Normal/正文 style."""
    normal_style, normal_style_name = get_normal_style(doc)
    if normal_style is None:
        print_error("Neither 'Normal' nor '正文' style was found, cannot set 'Table Text' base style")
        return False

    table_text_style.base_style = normal_style
    print_debug(f"'Table Text' style based on '{normal_style_name}' style")
    return True


def ensure_table_text_style_exists(doc: DocumentObject) -> bool:
    """
    Ensure 'Table Text' style exists in the document.
    If it doesn't exist, create it based on the Normal/正文 style.

    Args:
        doc: python-docx Document object

    Returns:
        True if style exists or was created successfully, False otherwise
    """
    try:
        # Try to access the style
        table_text_style = cast(_ParagraphStyle, doc.styles['Table Text'])
        if not set_table_text_base_style(doc, table_text_style):
            return False
        print_debug("'Table Text' style already exists")
        return True
    except KeyError:
        # Style doesn't exist, create it
        print_debug("'Table Text' style not found, creating it...")
        try:
            from docx.shared import Cm

            styles = doc.styles
            # Create the style and cast to _ParagraphStyle for proper type handling
            table_text_style_obj = styles.add_style('Table Text', WD_STYLE_TYPE.PARAGRAPH)
            table_text_style = cast(_ParagraphStyle, table_text_style_obj)

            if not set_table_text_base_style(doc, table_text_style):
                return False

            # Set paragraph formatting
            pf = table_text_style.paragraph_format

            # Set spacing before and after to 0.10 cm
            pf.space_before = Cm(0.10)
            pf.space_after = Cm(0.10)

            # Set line spacing to single (1.0)
            pf.line_spacing = 1.0

            # Make the style visible in the style gallery
            table_text_style.hidden = False
            table_text_style.quick_style = True
            table_text_style.priority = 1

            print_debug_success("'Table Text' style created successfully")
            return True
        except Exception as e:
            print_error(f"Failed to create 'Table Text' style: {e}")
            return False


def convert_cell_text_style(cell: _Cell, stats: dict) -> None:
    """
    Convert text style in a single cell from 'Compact' to 'Table Text'.

    Args:
        cell: The table cell to process
        stats: Dictionary to track conversion statistics
    """
    for paragraph in cell.paragraphs:
        # Check if paragraph style is 'Compact'
        if paragraph.style.name == 'Compact': # type: ignore
            paragraph.style = 'Table Text'
            stats['converted'] += 1
        else:
            stats['skipped'] += 1


def convert_table_text_style(table: Table, stats: dict) -> None:
    """
    Convert text style in all cells of a table from 'Compact' to 'Table Text'.

    Args:
        table: The table to process
        stats: Dictionary to track conversion statistics
    """
    for row in table.rows:
        for cell in row.cells:
            convert_cell_text_style(cell, stats)


def process_all_tables(doc: DocumentObject) -> dict:
    """
    Process all tables in the document to convert text styles.

    Args:
        doc: python-docx Document object

    Returns:
        Dictionary with conversion statistics
    """
    table_count = len(doc.tables)
    stats = {
        'tables_processed': 0,
        'tables_skipped_equation_layout': 0,
        'converted': 0,
        'skipped': 0
    }

    if table_count == 0:
        print_debug("No tables found in document")
        return stats

    print_debug(f"Processing {table_count} table(s)...")

    for i, table in enumerate(doc.tables, start=1):
        try:
            # Keep equation layout tables untouched. Their Compact paragraphs are
            # structural placeholders for centered equation/label alignment, not
            # regular table body text.
            if is_equation_layout_table(table):
                stats['tables_skipped_equation_layout'] += 1
                print_debug(f"Skipping equation layout table {i}")
                continue
            convert_table_text_style(table, stats)
            stats['tables_processed'] += 1
        except Exception as e:
            print_warning(f"Failed to process table {i}: {e}")

    if stats['tables_skipped_equation_layout']:
        print_debug(f"Skipped {stats['tables_skipped_equation_layout']} equation layout table(s)")
    print_debug_success(f"Processed {stats['tables_processed']} of {table_count} table(s)")
    print_debug(f"  - Converted: {stats['converted']} paragraph(s) from 'Compact' to 'Table Text'")
    print_debug(f"  - Skipped: {stats['skipped']} paragraph(s) (not 'Compact' style)")

    return stats


def process_file(docx_path: str, save: bool = True) -> Optional[DocumentObject]:
    """
    Process a DOCX file to convert table text styles.

    Args:
        docx_path: Path to the DOCX file
        save: Whether to save the document (default: True)

    Returns:
        Document object if successful, None otherwise
    """
    try:
        doc, docx_path_abs = open_docx(docx_path)
        if doc is None or docx_path_abs is None:
            return None

        # Ensure 'Table Text' style exists
        if not ensure_table_text_style_exists(doc):
            print_error("Cannot proceed without 'Table Text' style")
            return None

        # Process all tables
        stats = process_all_tables(doc)

        # Save the document
        if save:
            save_docx(doc, docx_path_abs)

        print_debug_success("\nTable text style conversion completed successfully!")
        return doc

    except Exception as e:
        print_error(f"\nTable text style conversion failed: {e}")
        import traceback
        traceback.print_exc()
        return None


def main():
    """Main entry point for command-line usage"""
    parser = argparse.ArgumentParser(
        description="Convert table text style from 'Compact' to 'Table Text' in DOCX files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  uv run table_text_style.py manuscript.docx
  uv run table_text_style.py output/docx/manuscript.docx
  uv run table_text_style.py manuscript.docx --no-save

This script converts all text in tables that uses 'Compact' style to 'Table Text' style.
If 'Table Text' style doesn't exist, it will be created automatically.
        """
    )
    parser.add_argument("docx_path", help="Path to the DOCX file to process")
    parser.add_argument("--no-save", action="store_true",
                       help="Don't save the document (for testing)")

    args = parser.parse_args()

    result = process_file(
        args.docx_path,
        save=not args.no_save
    )
    sys.exit(0 if result else 1)


if __name__ == "__main__":
    main()
