#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "python-docx>=1.1.0",
#   "pyyaml>=6.0",
# ]
# ///
"""
Post-process DOCX file - orchestrator script.
This script calls individual processing scripts in sequence.

Usage:
    from pandoc_manuscript.docx import postprocess
    postprocess.postprocess_docx(
        "path/to/file.docx",
        pmt_settings=pmt_settings,
        pandoc_metadata=pandoc_metadata,
    )
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

from ...runtime.logging import log_debug
from ...runtime.metadata import PmtSettings

try:
    from docx import Document
except ImportError:
    print("Error: python-docx is not installed. Install it with: pip install python-docx")
    sys.exit(1)

# Import processing modules
try:
    from .common import print_error, print_debug_success, print_warning
    from .process_equation_metadata import process_equation_metadata
    from .process_table_metadata import process_table_metadata
    from .autofit_tables import autofit_tables
    from .table_text_style import process_all_tables as convert_table_text_style, ensure_table_text_style_exists
    from .insert_author_info import insert_author_info_to_doc
    from .clear_subfigure_table_format import clear_subfigure_table_format
    from .format_equation_layout_tables import format_equation_layout_tables
    from .docx_style import apply_docx_style_settings, format_applied_style_summary
    from .line_numbers import apply_line_number_settings
    from .page_numbers import apply_page_number_settings
    from .reply_blue_italic_style import apply_reply_blue_italic_style
except ImportError as e:
    print(f"Error: Failed to import processing modules: {e}")
    print("Make sure the pandoc_manuscript package is installed with its postprocess modules:")
    print("  - metadata.py")
    print("  - process_equation_metadata.py")
    print("  - process_table_metadata.py")
    print("  - autofit_tables.py")
    print("  - table_text_style.py")
    print("  - para_equation_style.py")
    print("  - insert_author_info.py")
    print("  - clear_subfigure_table_format.py")
    print("  - format_equation_layout_tables.py")
    print("  - docx_style.py")
    print("  - line_numbers.py")
    print("  - page_numbers.py")
    print("  - reply_blue_italic_style.py")
    sys.exit(1)


def run_pipeline_step(label: str, action: Callable[[], None]) -> None:
    """Run one post-processing step with consistent pipeline logging."""
    log_debug(f"[postprocess] Step: {label}...")
    try:
        action()
        log_debug("[postprocess] Step completed")
    except Exception as e:
        print_error(f"Step failed: {e}")
        raise


def log_skip(label: str, reason: str) -> None:
    """Log a skipped optional pipeline step using the same step format."""
    log_debug(f"[postprocess] Step: Skipping {label} ({reason})")


def postprocess_docx(
    docx_path: str,
    *,
    pmt_settings: PmtSettings | None = None,
    pandoc_metadata: dict[str, Any] | None = None,
    skip_author_info: bool = False,
    reply_style_formatting: bool = False,
) -> bool:
    """
    Post-process a DOCX file with all processing steps.

    Args:
        docx_path: Path to the DOCX file to process
        pmt_settings: Typed Papper-owned DOCX and build settings
        pandoc_metadata: Manuscript metadata such as authors and affiliations
        skip_author_info: Skip author insertion for non-manuscript outputs
        reply_style_formatting: Apply reply-only blue formatting

    Returns:
        True if successful, False otherwise
    """
    # Validate inputs
    log_debug("[postprocess] Validating inputs...")
    docx_file = Path(docx_path)
    pmt_settings = pmt_settings or PmtSettings.model_validate({})
    pandoc_metadata = pandoc_metadata or {}

    if not docx_file.exists():
        print_error(f"DOCX file not found: {docx_path}")
        return False

    docx_path_abs = docx_file.resolve()
    log_debug("[postprocess] Starting DOCX post-processing pipeline")
    log_debug(f"[postprocess] Target file: {docx_path_abs}")
    log_debug(
        f"[postprocess] Papper settings: {len(pmt_settings.model_fields_set)}, "
        f"Pandoc metadata: {len(pandoc_metadata)}"
    )

    try:
        # Open document (shared across all steps)
        # log_info("[postprocess] Initializing document...")
        doc = Document(str(docx_path_abs))
        # log_debug("[postprocess] Document opened successfully")

        if skip_author_info:
            log_skip("author information", "disabled for this build")
        else:
            def insert_author_info_step() -> None:
                """Insert author metadata and log the number of inserted records."""
                authors, affiliations, has_footnote = insert_author_info_to_doc(doc, pandoc_metadata)
                if authors > 0:
                    print_debug_success(
                        f"Authors: {authors}, Affiliations: {affiliations}, "
                        f"Footnote: {'Yes' if has_footnote else 'No'}"
                    )
                else:
                    print_debug_success("No authors found in YAML metadata, skipping")

            run_pipeline_step("Inserting author information", insert_author_info_step)

        def apply_docx_style_step() -> None:
            """Apply merged YAML docxStyle metadata to configured DOCX paragraph styles."""
            result = apply_docx_style_settings(doc, pmt_settings)
            if result is None:
                print_warning("No docxStyle metadata found, skipping")
                return
            for applied in result["applied"]:
                print_debug_success(format_applied_style_summary(applied))

        def apply_line_number_step() -> None:
            """Apply merged YAML line-number metadata to all DOCX sections."""
            result = apply_line_number_settings(doc, pmt_settings)
            if result is None:
                return
            print_debug_success(
                f"Line numbers: restart={result['restart']}, sections={result['sections']}"
            )

        def apply_page_number_step() -> None:
            """Apply an explicit page-number visibility setting to DOCX footers."""
            result = apply_page_number_settings(doc, pmt_settings)
            if result is None:
                return
            print_debug_success(
                "Page numbers: "
                + ", ".join(f"{key}={str(value).lower()}" for key, value in result.items())
            )

        def process_table_metadata_step() -> None:
            """Apply Pandoc table attributes and report the applied setting count."""
            processed, settings = process_table_metadata(doc)
            print_debug_success(f"Processed {processed} table(s), Applied {settings} setting(s)")

        def process_equation_metadata_step() -> None:
            """Apply revision=true display-equation markers before other math layout fixes."""
            processed, runs = process_equation_metadata(doc)
            print_debug_success(f"Processed {processed} equation(s), Updated {runs} math run(s)")

        def clear_subfigure_table_format_step() -> None:
            """Clear formatting from tables used only for subfigure layout."""
            processed_count = clear_subfigure_table_format(doc)
            print_debug_success(f"Cleared formatting for {processed_count} subfigure table(s)")

        def convert_table_text_style_step() -> None:
            """Convert table paragraphs from Compact to the shared Table Text style."""
            if not ensure_table_text_style_exists(doc):
                print_warning("Could not ensure Table Text style exists, skipping style conversion")
                return
            stats = convert_table_text_style(doc)
            print_debug_success(f"Converted {stats['converted']} paragraph(s) from 'Compact' to 'Table Text'")

        def autofit_tables_step() -> None:
            """Auto-fit regular tables while leaving equation layout tables alone."""
            fitted_count = autofit_tables(doc, center_align=True)
            print_debug_success(f"Auto-fitted {fitted_count} table(s)")

        def format_equation_layout_tables_step() -> None:
            """Hide borders and tune widths for equation layout tables."""
            equation_table_count = format_equation_layout_tables(doc)
            print_debug_success(f"Formatted {equation_table_count} equation layout table(s)")

        def apply_para_equation_style_step() -> None:
            """Apply the Body Text-based style to tab-layout equation paragraphs."""
            # Defer the import so this step's module can also run directly with python -m.
            from .para_equation_style import process_para_equation_style

            process_para_equation_style(doc)

        def apply_reply_blue_italic_style_step() -> None:
            """Apply reply-only blue formatting in one post-processing step."""
            stats = apply_reply_blue_italic_style(doc)
            print_debug_success(
                f"Formatted {stats['caption_styles']} caption style(s), "
                f"{stats['caption_paragraphs']} caption paragraph(s), "
                f"{stats['table_runs']} table run(s), "
                f"{stats['where_styles']} where style(s)"
            )

        # Keep this ordered list explicit because DOCX post-processing steps are order-sensitive.
        pipeline_steps: list[tuple[str, Callable[[], None]]] = [
            # Metadata-driven document-wide settings must run before table-specific cleanup.
            ("Applying DOCX style metadata", apply_docx_style_step),
            ("Applying line-number metadata", apply_line_number_step),
            ("Applying page-number metadata", apply_page_number_step),
            ("Clearing subfigure table formatting", clear_subfigure_table_format_step),
            ("Converting table text style", convert_table_text_style_step),
            # Remove revision markers before they can misclassify equation-layout tables.
            ("Applying equation revision metadata", process_equation_metadata_step),
            ("Auto-fitting tables to window", autofit_tables_step),
            ("Applying table attribute metadata", process_table_metadata_step),
            ("Formatting equation layout tables", format_equation_layout_tables_step),
            ("Applying tab-layout equation paragraph style", apply_para_equation_style_step),
        ]
        if reply_style_formatting:
            pipeline_steps.append(("Applying reply-only blue formatting", apply_reply_blue_italic_style_step))

        for label, action in pipeline_steps:
            run_pipeline_step(label, action)

        # ===================================================================
        # Save document (all changes from all scripts)
        # ===================================================================
        doc.save(str(docx_path_abs))
        log_debug("[postprocess] Document saved successfully")

        log_debug("[postprocess] DOCX post-processing completed successfully")
        return True

    except Exception as e:
        print_error("=== Post-Processing Pipeline Failed ===")
        print_error(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Main entry point for command-line usage"""
    parser = argparse.ArgumentParser(
        description="Post-process DOCX files with table formatting and metadata",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Prefer calling postprocess_docx with typed Papper settings and Pandoc metadata.
  For standalone debugging, pass the corresponding JSON files explicitly.

Processing steps:
  - Insert author information from Pandoc metadata (if provided)
  - Apply DOCX paragraph styles from Papper settings (if configured)
  - Apply line numbers from Papper settings (if configured)
  - Apply page numbers from Papper settings (if configured)
  - Clear formatting for tables above 'Image Caption' paragraphs
  - Convert table text style from 'Compact' to 'Table Text'
  - Auto-fit tables to window width and center align
  - Apply table attributes exported by the Pandoc table metadata filter
  - Format equation layout tables
  - Apply 'Para Equation' to tab-layout equations (0.5 line after, single spacing)
  - Optionally apply reply-only blue formatting

This script applies all post-processing steps in sequence.
        """
    )
    parser.add_argument("docx_path", help="Path to the DOCX file to process")
    parser.add_argument(
        "--pmt-settings-json",
        help="Path to a JSON object containing Papper-owned settings",
    )
    parser.add_argument(
        "--pandoc-metadata-json",
        help="Path to a JSON object containing manuscript/Pandoc metadata",
    )
    parser.add_argument(
        "--skip-author-info",
        action="store_true",
        help="Skip author insertion while keeping the remaining DOCX post-processing steps",
    )
    parser.add_argument(
        "--reply-style-formatting",
        action="store_true",
        help="Apply reply-only blue formatting",
    )

    args = parser.parse_args()
    def load_json_mapping(path: str | None, option: str) -> dict[str, Any] | None:
        """Load one optional JSON mapping for standalone postprocess debugging."""
        if path is None:
            return None
        with Path(path).open("r", encoding="utf-8") as file:
            value = json.load(file)
        if not isinstance(value, dict):
            parser.error(f"{option} must contain a JSON object")
        return value

    raw_pmt_settings = load_json_mapping(args.pmt_settings_json, "--pmt-settings-json")
    pmt_settings = PmtSettings.model_validate(raw_pmt_settings or {})
    pandoc_metadata = load_json_mapping(args.pandoc_metadata_json, "--pandoc-metadata-json")

    success = postprocess_docx(
        args.docx_path,
        pmt_settings=pmt_settings,
        pandoc_metadata=pandoc_metadata,
        skip_author_info=args.skip_author_info,
        reply_style_formatting=args.reply_style_formatting,
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
