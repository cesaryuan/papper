#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "python-docx>=1.1.0",
# ]
# ///
"""
Process table metadata collected from Pandoc table attributes.
Pandoc does not preserve arbitrary table attributes in DOCX, so a Lua filter
writes hidden WordprocessingML marker paragraphs before attributed tables.

Usage:
    uv run process_table_metadata.py path/to/file.docx
    or as a module: process_table_metadata(doc)
"""

import argparse
import json
import re
import sys
from typing import Any, Mapping, Optional

from docx.document import Document as DocumentObject
from docx.table import Table
from docx.shared import Pt, Cm, Mm, Inches, RGBColor
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph

from .common import (
    get_or_add_tbl_pr,
    iter_body_blocks,
    open_docx,
    print_error,
    print_debug,
    print_debug_success,
    print_warning,
    save_docx,
)


TABLE_METADATA_KEYS = {
    "cell_margin",
    "cell_margin_top",
    "cell_margin_bottom",
    "cell_margin_left",
    "cell_margin_right",
    "cell_spacing",
    "row_height",
    "revision_columns",
    "revision_rows",
    "alignment",
    "autofit",
}
TABLE_METADATA_MARKER_PREFIX = "PMT_TABLE_METADATA:"
REVISION_TEXT_COLOR = RGBColor(0xFF, 0x00, 0x00)


def normalize_table_metadata_key(key: str) -> str:
    """Return the canonical table metadata key used by the DOCX postprocessor."""
    return key.strip().lower().replace("-", "_")


def normalize_table_metadata(metadata: Mapping[str, Any]) -> dict[str, str]:
    """Keep supported table metadata attributes and normalize values to strings."""
    normalized: dict[str, str] = {}
    for key, value in metadata.items():
        canonical_key = normalize_table_metadata_key(str(key))
        if canonical_key in TABLE_METADATA_KEYS:
            normalized[canonical_key] = str(value)
    return normalized


def parse_table_metadata_marker(text: str) -> dict[str, Any] | None:
    """Return a metadata record from a hidden marker paragraph, if present."""
    try:
        record = json.loads(text[len(TABLE_METADATA_MARKER_PREFIX):])
    except json.JSONDecodeError as exc:
        print_warning(f"Invalid table metadata marker ignored: {exc}")
        return None
    return record if isinstance(record, dict) else None


def remove_paragraph(para: Paragraph) -> None:
    """Remove a marker paragraph from the DOCX body after its payload is read."""
    parent = para._element.getparent()
    if parent is not None:
        parent.remove(para._element)


def is_table_caption_paragraph(para: Paragraph) -> bool:
    """Return True when a paragraph looks like a Pandoc table caption."""
    style_name = para.style.name if para.style else ""
    return bool(style_name and ("Caption" in style_name or "题注" in style_name or "Table" in style_name))


def table_metadata_records_from_doc(doc: DocumentObject) -> list[tuple[Table, Paragraph | None, dict[str, Any]]]:
    """Pair hidden marker records and optional captions with the next table."""
    pairs: list[tuple[Table, Paragraph | None, dict[str, Any]]] = []
    pending_record: dict[str, Any] | None = None
    pending_caption: Paragraph | None = None
    marker_paragraphs: list[Paragraph] = []

    for block in iter_body_blocks(doc):
        if isinstance(block, Paragraph):
            if block.text.startswith(TABLE_METADATA_MARKER_PREFIX):
                marker_paragraphs.append(block)
                record = parse_table_metadata_marker(block.text)
                if record is not None:
                    pending_record = record
                    pending_caption = None
            elif pending_record is not None and is_table_caption_paragraph(block):
                pending_caption = block
            continue

        if isinstance(block, Table) and pending_record is not None:
            pairs.append((block, pending_caption, pending_record))
            pending_record = None
            pending_caption = None

    for para in marker_paragraphs:
        remove_paragraph(para)

    return pairs


def convert_to_points(dimension: str) -> float:
    """
    Convert dimension string to points (Word uses points internally).

    Args:
        dimension: Dimension with unit (e.g., "0.10cm", "5pt", "0.5in")

    Returns:
        Value in points
    """
    # Parse dimension with unit
    match = re.match(r'^([\d.]+)(cm|mm|in|pt)$', dimension.lower())
    if match:
        value = float(match.group(1))
        unit = match.group(2)

        if unit == 'cm':
            return Cm(value).pt
        elif unit == 'mm':
            return Mm(value).pt
        elif unit == 'in':
            return Inches(value).pt
        elif unit == 'pt':
            return Pt(value).pt

    # Default: assume points if no unit specified
    return float(dimension)


def is_revision_all_marker(value: str) -> bool:
    """Return True when revision metadata requests the whole table."""
    return value.strip() == "*"


def parse_revision_indices(value: str, field_name: str) -> list[int]:
    """Parse 1-based revision row/column numbers from a comma-separated value."""
    indices: list[int] = []
    tokens = [token for token in re.split(r"[,，;；\s]+", value.strip()) if token]
    for token in tokens:
        try:
            index = int(token)
        except ValueError as exc:
            raise ValueError(f"{field_name} contains a non-integer index: {token!r}") from exc
        if index < 1:
            raise ValueError(f"{field_name} indices are 1-based and must be positive: {token!r}")
        indices.append(index)
    return indices


def mark_cell_text_as_revision(cell) -> int:
    """Color all text runs in one table cell red for revision highlighting."""
    run_count = 0
    for paragraph in cell.paragraphs:
        for run in paragraph.runs:
            run.font.color.rgb = REVISION_TEXT_COLOR
            run_count += 1
    return run_count


def mark_paragraph_text_as_revision(paragraph: Paragraph | None) -> int:
    """Color all runs in a table caption red when whole-table revision is requested."""
    if paragraph is None:
        return 0
    run_count = 0
    for run in paragraph.runs:
        run.font.color.rgb = REVISION_TEXT_COLOR
        run_count += 1
    return run_count


def mark_revision_table(table: Table, caption: Paragraph | None = None) -> int:
    """Color the table and its caption red for revision_rows/columns='*'."""
    run_count = 0
    for row in table.rows:
        for cell in row.cells:
            run_count += mark_cell_text_as_revision(cell)
    run_count += mark_paragraph_text_as_revision(caption)
    return run_count


def mark_revision_rows(table: Table, row_indices: list[int]) -> int:
    """Color text in selected 1-based table rows red."""
    run_count = 0
    row_count = len(table.rows)
    for row_index in row_indices:
        if row_index > row_count:
            print_warning(f"revision_rows index {row_index} is out of range for a table with {row_count} row(s)")
            continue
        for cell in table.rows[row_index - 1].cells:
            run_count += mark_cell_text_as_revision(cell)
    return run_count


def mark_revision_columns(table: Table, column_indices: list[int]) -> int:
    """Color text in selected 1-based table columns red."""
    run_count = 0
    column_count = len(table.columns)
    for column_index in column_indices:
        if column_index > column_count:
            print_warning(
                f"revision_columns index {column_index} is out of range for a table with {column_count} column(s)"
            )
            continue
        for row in table.rows:
            cells = row.cells
            if column_index <= len(cells):
                run_count += mark_cell_text_as_revision(cells[column_index - 1])
    return run_count


def set_cell_margins(table: Table, top=None, bottom=None, left=None, right=None):
    """
    Set cell margins for a table using XML manipulation.

    Args:
        table: The table to modify
        top, bottom, left, right: Margin values in points (optional)
    """
    tblPr = get_or_add_tbl_pr(table)

    # Find or create tblCellMar element
    tblCellMar = tblPr.find(qn('w:tblCellMar'))
    if tblCellMar is None:
        tblCellMar = OxmlElement('w:tblCellMar')
        tblPr.append(tblCellMar)

    # Set margins
    margins = {
        'top': top,
        'bottom': bottom,
        'left': left,
        'right': right
    }

    for side, value in margins.items():
        if value is not None:
            # Remove existing margin element
            existing = tblCellMar.find(qn(f'w:{side}'))
            if existing is not None:
                tblCellMar.remove(existing)

            # Create new margin element
            margin_elem = OxmlElement(f'w:{side}')
            margin_elem.set(qn('w:w'), str(int(value * 20)))  # Convert to twips (1/20 of a point)
            margin_elem.set(qn('w:type'), 'dxa')
            tblCellMar.append(margin_elem)


def set_table_alignment(table: Table, alignment: str):
    """
    Set table alignment using XML manipulation.

    Args:
        table: The table to modify
        alignment: 'left', 'center', or 'right'
    """
    tblPr = get_or_add_tbl_pr(table)

    # Find or create jc (justification) element
    jc = tblPr.find(qn('w:jc'))
    if jc is None:
        jc = OxmlElement('w:jc')
        tblPr.append(jc)

    # Set alignment
    alignment_map = {
        'left': 'left',
        'center': 'center',
        'right': 'right'
    }
    jc.set(qn('w:val'), alignment_map.get(alignment.lower(), 'center'))


def set_autofit_behavior(table: Table, behavior: str):
    """
    Set table autofit behavior using XML manipulation.

    Args:
        table: The table to modify
        behavior: 'fixed', 'content', or 'window'
    """
    tblPr = get_or_add_tbl_pr(table)

    # Set table width based on behavior
    tblW = tblPr.find(qn('w:tblW'))
    if tblW is None:
        tblW = OxmlElement('w:tblW')
        tblPr.insert(0, tblW)

    if behavior.lower() == 'window':
        # Auto width (percentage-based)
        tblW.set(qn('w:type'), 'pct')
        tblW.set(qn('w:w'), '5000')  # 100% (50 * 100)
    elif behavior.lower() == 'content':
        # Auto width (fit content)
        tblW.set(qn('w:type'), 'auto')
        tblW.set(qn('w:w'), '0')
    elif behavior.lower() == 'fixed':
        # Fixed width - preserve current width
        pass


def apply_table_metadata(
    table: Table,
    metadata: Mapping[str, str],
    caption: Paragraph | None = None,
) -> list[str]:
    """
    Apply metadata settings to table.

    Args:
        table: The table to modify
        metadata: Dictionary of metadata key-value pairs
        caption: Adjacent caption paragraph for whole-table revision markers

    Returns:
        List of applied settings descriptions
    """
    applied_settings = []

    for key, value in metadata.items():
        try:
            key_lower = normalize_table_metadata_key(key)

            if key_lower == 'cell_margin':
                # Set cell margins (all sides)
                points = convert_to_points(value)
                set_cell_margins(table, top=points, bottom=points, left=points, right=points)
                applied_settings.append(f"cell_margin={value}")

            elif key_lower == 'cell_margin_top':
                points = convert_to_points(value)
                set_cell_margins(table, top=points)
                applied_settings.append(f"cell_margin_top={value}")

            elif key_lower == 'cell_margin_bottom':
                points = convert_to_points(value)
                set_cell_margins(table, bottom=points)
                applied_settings.append(f"cell_margin_bottom={value}")

            elif key_lower == 'cell_margin_left':
                points = convert_to_points(value)
                set_cell_margins(table, left=points)
                applied_settings.append(f"cell_margin_left={value}")

            elif key_lower == 'cell_margin_right':
                points = convert_to_points(value)
                set_cell_margins(table, right=points)
                applied_settings.append(f"cell_margin_right={value}")

            elif key_lower == 'cell_spacing':
                # Note: python-docx doesn't directly support cell spacing
                # This would require advanced XML manipulation
                # print_warning("cell_spacing is not fully supported in python-docx")
                applied_settings.append(f"cell_spacing={value} (limited support)")

            elif key_lower == 'row_height':
                # Set row height for all rows
                points = convert_to_points(value)
                for row in table.rows:
                    row.height = Pt(points)
                applied_settings.append(f"row_height={value}")

            elif key_lower == 'revision_rows':
                if is_revision_all_marker(value):
                    runs = mark_revision_table(table, caption)
                else:
                    row_indices = parse_revision_indices(value, "revision_rows")
                    runs = mark_revision_rows(table, row_indices)
                applied_settings.append(f"revision_rows={value} ({runs} run(s))")

            elif key_lower == 'revision_columns':
                if is_revision_all_marker(value):
                    runs = mark_revision_table(table, caption)
                else:
                    column_indices = parse_revision_indices(value, "revision_columns")
                    runs = mark_revision_columns(table, column_indices)
                applied_settings.append(f"revision_columns={value} ({runs} run(s))")

            elif key_lower == 'alignment':
                # Set table alignment
                set_table_alignment(table, value)
                applied_settings.append(f"alignment={value}")

            elif key_lower == 'autofit':
                # Set autofit behavior
                set_autofit_behavior(table, value)
                applied_settings.append(f"autofit={value}")

            else:
                print_warning(f"Unknown metadata key: {key}")

        except Exception as e:
            print_warning(f"Failed to apply setting {key}={value}: {e}")

    return applied_settings


def process_table_metadata(doc: DocumentObject) -> tuple[int, int]:
    """
    Process table metadata collected from Pandoc table attributes.

    Args:
        doc: python-docx Document object

    Returns:
        Tuple of (processed_count, total_settings_applied)
    """
    processed_count = 0
    total_settings_applied = 0

    table_count = len(doc.tables)

    if table_count == 0:
        print_debug("No tables found in document")
        return processed_count, total_settings_applied

    table_record_pairs = table_metadata_records_from_doc(doc)
    if not table_record_pairs:
        print_debug("No table metadata attributes found")
        return processed_count, total_settings_applied

    print_debug(f"Processing {table_count} DOCX table(s) with {len(table_record_pairs)} metadata marker(s)...")

    for table, caption, record in table_record_pairs:
        attributes = record.get("attributes", {})
        metadata = normalize_table_metadata(attributes) if isinstance(attributes, Mapping) else {}
        if not metadata:
            continue

        print_debug(f"Processing Table {record.get('index', '?')}...")
        applied_settings = apply_table_metadata(table, metadata, caption)

        if applied_settings:
            print_debug_success(f"  Applied: {', '.join(applied_settings)}")
            total_settings_applied += len(applied_settings)

        processed_count += 1

    return processed_count, total_settings_applied


def process_file(
    docx_path: str,
    save: bool = True,
) -> Optional[DocumentObject]:
    """
    Process a DOCX file to apply table metadata.

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

        print_debug("Processing table metadata from hidden Pandoc attribute markers...")
        processed, settings_applied = process_table_metadata(doc)

        print_debug_success(f"\nProcessed {processed} of {len(doc.tables)} table(s)")
        print_debug_success(f"Total settings applied: {settings_applied}")

        # Save the document
        if save:
            save_docx(doc, docx_path_abs)

        print_debug_success("\nTable metadata processing completed successfully!")
        return doc

    except Exception as e:
        print_error(f"\nTable metadata processing failed: {e}")
        import traceback
        traceback.print_exc()
        return None


def main():
    """Main entry point for command-line usage"""
    parser = argparse.ArgumentParser(
        description="Process table metadata from Pandoc table attributes in DOCX files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  uv run process_table_metadata.py manuscript.docx
  uv run process_table_metadata.py output/docx/manuscript.docx

Supported metadata attributes:
  cell_margin=0.1cm        - Set all cell margins
  cell_margin_top=0.1cm    - Set top cell margin
  cell_margin_bottom=0.1cm - Set bottom cell margin
  cell_margin_left=0.1cm   - Set left cell margin
  cell_margin_right=0.1cm  - Set right cell margin
  row_height=1cm           - Set row height
  revision_rows=1,2,3      - Mark changed/added rows red (1-based, includes header)
  revision_columns=6,7     - Mark changed/added columns red (1-based)
  revision_rows=*          - Mark the entire table and its caption red
  alignment=center         - Set table alignment (left, center, right)
  autofit=window           - Set autofit behavior (fixed, content, window)

Pandoc caption attribute format:
  : Description {#tbl:demo cell_margin="0.1cm" alignment="center" revision_rows="1,2"}
        """
    )
    parser.add_argument("docx_path", help="Path to the DOCX file to process")
    parser.add_argument("--no-save", action="store_true",
                       help="Don't save the document (for testing)")

    args = parser.parse_args()

    result = process_file(args.docx_path, save=not args.no_save)
    sys.exit(0 if result else 1)


if __name__ == "__main__":
    main()
