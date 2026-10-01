"""HTML manuscript build entry point and HTML-specific post-processing."""

from __future__ import annotations

import os
from pathlib import Path

from ..runtime.page_margins import PageMarginValues
from ..runtime.metadata import EffectiveMetadata
from .postprocess import postprocess_html
from .styles import build_reference_style_css


# HTML keeps display equations as native MathML blocks instead of DOCX-style
# equation layout tables and inline equation-number workarounds.
HTML_EQUATION_METADATA = {
    "equationNumberTeX": "\\\\tag",
    "eqnIndexTemplate": "$$i$$",
    "eqnBlockInlineMath": False,
    "tableEqns": False,
}

# The bundled reference DOCX uses A4 paper with these margins when a project
# does not override them in docxPageMargins.
HTML_PAGE_DEFAULTS = {
    "width": "210mm",
    "height": "297mm",
    "margin_top": "1.905cm",
    "margin_bottom": "1.905cm",
    "margin_left": "1.905cm",
    "margin_right": "1.905cm",
}


def _css_length(length: object) -> str:
    """Render a validated layout length as a CSS point value."""
    # Shared normalization validates lengths before they reach CSS.
    points = float(getattr(length, "pt"))
    rendered = f"{points:.4f}".rstrip("0").rstrip(".")
    return f"{rendered}pt"


def apply_html_page_metadata(effective: EffectiveMetadata) -> None:
    """Expose A4 and effective DOCX margins to the standalone HTML template."""
    metadata = effective.pandoc_metadata
    metadata.update(
        {
            "html-page-width": HTML_PAGE_DEFAULTS["width"],
            "html-page-height": HTML_PAGE_DEFAULTS["height"],
            "html-page-margin-top": HTML_PAGE_DEFAULTS["margin_top"],
            "html-page-margin-bottom": HTML_PAGE_DEFAULTS["margin_bottom"],
            "html-page-margin-left": HTML_PAGE_DEFAULTS["margin_left"],
            "html-page-margin-right": HTML_PAGE_DEFAULTS["margin_right"],
        }
    )
    normalized = PageMarginValues.normalize_page_margins(effective.pmt_settings)
    if normalized is None:
        return

    margins, _ = normalized
    for side, length in margins.items():
        metadata[f"html-page-margin-{side}"] = _css_length(length)


def append_reference_style_block(effective: EffectiveMetadata, css: str) -> None:
    """Append generated reference styles as a raw HTML header include."""
    existing = effective.pandoc_metadata.get("header-includes")
    if existing is None:
        includes: list[object] = []
    elif isinstance(existing, list):
        includes = list(existing)
    else:
        includes = [existing]
    # Keep user-supplied header includes after generated defaults so an explicit
    # project-level CSS rule can still refine the reference-document styling.
    includes.insert(0, f"<style>\n{css}\n</style>")
    effective.pandoc_metadata["header-includes"] = includes


def prepare_html_metadata(effective: EffectiveMetadata, project_name: str) -> None:
    """Apply identical title, equation, reference CSS, and page defaults to HTML."""
    from ..commands.build import resource_path

    metadata = effective.pandoc_metadata
    if not metadata.get("title") and not metadata.get("pagetitle"):
        metadata["pagetitle"] = project_name
    metadata.update(HTML_EQUATION_METADATA)
    append_reference_style_block(
        effective,
        build_reference_style_css(
            resource_path("pandoc/manuscript-template/reference-doc/word/styles.xml"),
            effective.pmt_settings,
        ),
    )
    apply_html_page_metadata(effective)


def build_html(
    *,
    start_server: bool = False,
    server_host: str = "127.0.0.1",
    server_port: int = 3030,
    server_command: str | None = None,
) -> None:
    """Generate HTML through the CLI or an explicitly requested reusable service."""
    # Import the shared build primitives lazily to keep HTML implementation
    # details in this package without creating a module import cycle.
    from ..commands.build import (
        SETTINGS,
        ensure_output_parent,
        load_build_metadata,
        manuscript_output_file,
        manuscript_resource_paths,
        manuscript_style_paths,
        resource_path,
        pandoc_filter_env,
        style_metadata_args,
        run_pandoc,
    )
    from ..runtime.logging import log_debug, log_info, log_success
    from ..commands.pandoc_server import build_with_pandoc_server, ensure_pandoc_server, write_pmt_server_config
    from ..commands.setup import pandoc_tools_env

    log_info("\n[HTML] Building HTML...\n")
    html_file = manuscript_output_file(SETTINGS.html_dir, "html")
    ensure_output_parent(html_file)
    effective = load_build_metadata()
    # Pandoc derives the page title from the temporary header-free input
    # filename when a manuscript has no explicit title; keep the title stable
    # without adding a visible title block to the document body.
    prepare_html_metadata(effective, SETTINGS.project_name)
    log_debug(
        "[HTML] Page layout: A4 with margins "
        f"top={effective.pandoc_metadata['html-page-margin-top']}, "
        f"bottom={effective.pandoc_metadata['html-page-margin-bottom']}, "
        f"left={effective.pandoc_metadata['html-page-margin-left']}, "
        f"right={effective.pandoc_metadata['html-page-margin-right']}"
    )
    resource_path_option = (
        SETTINGS.resource_path
        if SETTINGS.resource_path is not None
        else os.pathsep.join(str(path) for path in manuscript_resource_paths())
    )
    if start_server:
        server_config = write_pmt_server_config(
            project_dir=Path.cwd(),
            pandoc_args=[
                "--defaults",
                str(resource_path("pandoc/pandoc-html.yml")),
                *style_metadata_args(effective),
                "--resource-path",
                resource_path_option,
            ],
            pandoc_metadata=effective.pandoc_metadata,
            resource_paths=[Path(path) for path in resource_path_option.split(os.pathsep)],
            metadata_sources={
                "style_file": SETTINGS.style_file,
                "style_paths": [str(path) for path in manuscript_style_paths()],
                "project_name": SETTINGS.project_name,
                "resource_path_explicit": SETTINGS.resource_path is not None,
            },
        )
        server = ensure_pandoc_server(
            host=server_host,
            port=server_port,
            command=server_command,
            config_path=server_config,
            environment=pandoc_tools_env(pandoc_filter_env(effective.pmt_settings)),
        )
        # Explicit external CLI inputs keep the normal Pandoc path; the public
        # service remains bound to the project that launched it.
        source = Path(SETTINGS.manuscript_file)
        if source.resolve().is_relative_to(Path.cwd().resolve()) and build_with_pandoc_server(server, source, html_file):
            log_success(f"\n[OK] HTML created: {html_file}")
            return
    run_pandoc(
        resource_path("pandoc/pandoc-html.yml"),
        html_file,
        effective,
    )
    postprocess_html(
        html_file,
        pandoc_metadata=effective.pandoc_metadata,
    )
    log_success(f"\n[OK] HTML created: {html_file}")
