#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "python-docx>=1.1.0",
# ]
# ///
"""
Process equation revision markers collected from Pandoc display-equation attributes.

Usage:
    uv run process_equation_metadata.py path/to/file.docx
    or as a module: process_equation_metadata(doc)
"""

import argparse
import json
import sys
from typing import Any

from docx.document import Document as DocumentObject
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph

from .common import open_docx, print_debug, print_debug_success, print_error, print_warning, save_docx


EQUATION_METADATA_MARKER_PREFIX = "PMT_EQUATION_METADATA:"
REVISION_COLOR_HEX = "FF0000"


def parse_equation_metadata_marker(text: str) -> dict[str, Any] | None:
    """Return one equation metadata record from a hidden marker paragraph."""
    try:
        record = json.loads(text[len(EQUATION_METADATA_MARKER_PREFIX):])
    except json.JSONDecodeError as exc:
        print_warning(f"Invalid equation metadata marker ignored: {exc}")
        return None
    return record if isinstance(record, dict) else None


def remove_paragraph(paragraph: Paragraph) -> None:
    """Remove a marker paragraph after its metadata has been consumed."""
    parent = paragraph._element.getparent()
    if parent is not None:
        parent.remove(paragraph._element)


def paragraph_has_display_math(paragraph: Paragraph) -> bool:
    """Return whether a paragraph contains native Word display math."""
    return bool(paragraph._p.findall(f".//{qn('m:oMathPara')}"))


def equation_paragraphs_from_doc(doc: DocumentObject) -> list[tuple[Paragraph, dict[str, Any]]]:
    """Pair hidden markers with display equations, including equation-layout cells."""
    pairs: list[tuple[Paragraph, dict[str, Any]]] = []
    pending_record: dict[str, Any] | None = None
    marker_paragraphs: list[Paragraph] = []

    # The default numbered-equation layout puts both marker and math in a cell;
    # scanning body-level paragraphs alone misses those revised equations.
    for element in doc.element.body.iter(qn("w:p")):
        block = Paragraph(element, doc)

        if block.text.startswith(EQUATION_METADATA_MARKER_PREFIX):
            marker_paragraphs.append(block)
            pending_record = parse_equation_metadata_marker(block.text)
            continue

        if pending_record is None:
            continue

        if paragraph_has_display_math(block):
            pairs.append((block, pending_record))
        else:
            print_warning("Equation revision marker was not followed by a native Word display equation")
        pending_record = None

    for paragraph in marker_paragraphs:
        remove_paragraph(paragraph)

    return pairs


def get_or_add_math_run_properties(math_run) -> Any:
    """Return the math-run properties element, creating it for revision coloring."""
    math_rpr = math_run.find(qn("m:rPr"))
    if math_rpr is None:
        math_rpr = OxmlElement("m:rPr")
        math_run.insert(0, math_rpr)
    return math_rpr


def get_or_add_word_run_properties(math_rpr) -> Any:
    """Return the nested Word run-properties element used by Word-native math color."""
    word_rpr = math_rpr.find(qn("w:rPr"))
    if word_rpr is None:
        word_rpr = OxmlElement("w:rPr")
        math_rpr.append(word_rpr)
    return word_rpr


def apply_revision_color_to_math_run(math_run) -> bool:
    """Color one OMML math run red for the equation revision bug fix."""
    math_rpr = get_or_add_math_run_properties(math_run)
    word_rpr = get_or_add_word_run_properties(math_rpr)
    color = word_rpr.find(qn("w:color"))
    if color is None:
        color = OxmlElement("w:color")
        word_rpr.append(color)

    changed = color.get(qn("w:val")) != REVISION_COLOR_HEX
    color.set(qn("w:val"), REVISION_COLOR_HEX)
    return changed


def mark_equation_paragraph_as_revision(paragraph: Paragraph) -> int:
    """Color all native Word math runs red inside one display-equation paragraph."""
    updated = 0
    for math_run in paragraph._p.findall(f".//{qn('m:r')}"):
        if apply_revision_color_to_math_run(math_run):
            updated += 1
    return updated


def process_equation_metadata(doc: DocumentObject) -> tuple[int, int]:
    """Apply collected equation revision metadata to native Word display equations."""
    processed = 0
    total_runs = 0
    equation_pairs = equation_paragraphs_from_doc(doc)
    if not equation_pairs:
        print_debug("No equation revision markers found")
        return processed, total_runs

    print_debug(f"Processing {len(equation_pairs)} revised display equation(s)...")
    for paragraph, record in equation_pairs:
        if str(record.get("revision", "")).lower() != "true":
            continue
        updated_runs = mark_equation_paragraph_as_revision(paragraph)
        total_runs += updated_runs
        processed += 1

    return processed, total_runs


def process_file(docx_path: str, save: bool = True) -> int | None:
    """Process a DOCX file to color revised native Word display equations."""
    doc, docx_path_abs = open_docx(docx_path)
    if doc is None or docx_path_abs is None:
        return None

    processed, updated_runs = process_equation_metadata(doc)
    if save and processed:
        save_docx(doc, docx_path_abs)

    print_debug_success(
        f"Processed {processed} revised display equation(s), Updated {updated_runs} math run(s)"
    )
    return processed


def main() -> None:
    """Main entry point for command-line usage."""
    parser = argparse.ArgumentParser(
        description="Process equation revision markers in DOCX files"
    )
    parser.add_argument("docx_path", help="Path to the DOCX file to process")
    parser.add_argument("--no-save", action="store_true", help="Do not save changes")
    args = parser.parse_args()

    result = process_file(args.docx_path, save=not args.no_save)
    sys.exit(0 if result is not None else 1)


if __name__ == "__main__":
    main()
