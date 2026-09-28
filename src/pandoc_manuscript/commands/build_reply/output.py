"""Output builders and runtime helpers for reply DOCX/TXT targets."""

from __future__ import annotations

import errno
import os
import shutil
import time
import uuid
from pathlib import Path
from collections.abc import Sequence
from ...runtime.logging import log_info, log_success, log_warning, log_debug
from ...runtime.metadata import PmtSettings
from ...runtime.paths import PMT_MATHTYPE_WORK_DIR, PMT_REPLY_PROBE_DIR, PMT_REPLY_WORK_DIR
from ...runtime.resources import template_root
from ...mathtype.convert_marked_docx import convert_marked_docx
from ...mathtype.marked_docx import extract_marked_equation_requests
from ...mathtype.ole_parts import check_mathtype_availability, normalize_conversion_method
from ...mathtype.preflight import warn_mathtype_hat_style_order
from ...docx import svg_filters as svg_filter_helpers
from ...docx.page_margins import write_reference_doc_with_page_margins
from ...docx.postprocess import postprocess_docx
from ...docx.postprocess.final_docx_syntax_check import validate_final_docx_syntax
from ...docx.svg_filters import (
    should_convert_docx_svg_to_png,
    should_embed_docx_svg_images,
    svg_embed_images_filter_args,
    svg_to_png_filter_args,
)
from ..setup import pandoc_command, pandoc_tools_env
from . import resolve as reply_resolve
from .settings import (
    DEFAULT_OUTPUT_DIR,
    DEFAULT_REPLY_OUTPUT_FILE,
)


REPLY_PROBE_DIR = PMT_REPLY_PROBE_DIR


def resolve_mathtype_enabled(requested: bool, conversion_method: object | None = None) -> bool:
    """Return whether MathType conversion should run for this reply build."""
    if not requested:
        return False

    log_debug("[DEBUG] MathType DOCX equations enabled by reply metadata: mathtype: true")
    availability = check_mathtype_availability(conversion_method)
    if availability.usable:
        return True

    log_warning(
        availability.format_failure(
            "[WARN] MathType was requested by reply metadata, but MathType conversion will be skipped."
        )
    )
    log_warning("[WARN] Building reply DOCX with Pandoc/Word equations instead.\n")
    return False


def mathtype_marked_docx_path(output: Path) -> Path:
    """Return the intermediate reply DOCX path carrying hidden LaTeX markers."""
    work_dir = PMT_MATHTYPE_WORK_DIR / "reply"
    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir / f"{output.stem}.marked.docx"


def docx_metadata_filter_args() -> list[str]:
    """Return Pandoc args for hidden DOCX metadata markers used by reply builds."""
    filter_path = template_root() / "pandoc" / "filters" / "docx" / "docx_metadata.lua"
    if not filter_path.exists():
        raise FileNotFoundError(f"DOCX metadata Pandoc filter not found: {filter_path}")
    return ["--lua-filter", reply_resolve.to_pandoc_path(filter_path)]


def reply_svg_base_dirs(reply: Path) -> list[Path]:
    """Return lookup roots shared by reply DOCX SVG filters."""
    return svg_filter_helpers.unique_resolved_dirs([Path.cwd(), reply.parent])


def svg_embed_images_filter_env(
    reply: Path,
    settings: PmtSettings,
    embed_images: bool | None = None,
) -> dict[str, str]:
    """Return environment settings consumed by the reply SVG embedding filter."""
    return svg_filter_helpers.svg_embed_images_filter_env(
        reply_svg_base_dirs(reply),
        settings,
        embed_images=embed_images,
    )


def svg_to_png_filter_env(
    reply: Path,
    settings: PmtSettings,
    convert_all: bool | None = None,
) -> dict[str, str]:
    """Return environment settings consumed by the reply SVG-to-PNG filter."""
    return svg_filter_helpers.svg_to_png_filter_env(
        reply_svg_base_dirs(reply),
        settings,
        convert_all=convert_all,
    )


def run_mathtype_conversion(marked_docx: Path, target_docx: Path, pmt_settings: PmtSettings) -> None:
    """Convert a marked reply DOCX's OMML equations into MathType OLE equations."""
    if not extract_marked_equation_requests(marked_docx):
        log_debug("[DEBUG] No MathType equation markers found; keeping Pandoc DOCX equations unchanged.")
        target_docx.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(marked_docx, target_docx)
        return

    log_debug("\n[DEBUG] Converting reply equations to MathType OLE objects...\n")
    convert_marked_docx(
        source=marked_docx,
        target=target_docx,
        work_dir=PMT_MATHTYPE_WORK_DIR / "reply" / target_docx.stem,
        pmt_settings=pmt_settings,
    )


def ensure_output_writable(output: Path) -> None:
    """Fail early when an existing output is locked by another app."""
    if not output.exists():
        return
    if output.is_dir():
        raise RuntimeError(f"Output path is a directory and cannot be overwritten: {output}")

    try:
        with output.open("r+b"):
            pass
    except OSError as exc:
        lock_like_errors = {errno.EACCES, errno.EPERM}
        lock_like_winerrors = {5, 32, 33}
        if exc.errno in lock_like_errors or getattr(exc, "winerror", None) in lock_like_winerrors:
            raise RuntimeError(f"Output file appears to be open or locked. Close it and retry: {output}") from exc
        raise


def resolved_reply_path(reply: Path) -> Path:
    """Return a temporary reply path outside the source tree's visible files."""
    REPLY_PROBE_DIR.mkdir(parents=True, exist_ok=True)
    return REPLY_PROBE_DIR / f"{reply.stem}.{uuid.uuid4().hex}.resolved.md"


def reply_resource_path(reply: Path) -> str:
    """Return Pandoc resource search paths that preserve reply-relative assets."""
    candidates = [reply.resolve().parent, Path.cwd().resolve()]
    unique: list[Path] = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    return os.pathsep.join(reply_resolve.to_pandoc_path(path) for path in unique)


def cleanup_resolved_reply_path(path: Path) -> None:
    """Remove the temporary reply file, tolerating short Windows file locks."""
    lock_like_winerrors = {5, 32, 33}
    for attempt in range(5):
        try:
            path.unlink(missing_ok=True)
            return
        except OSError as exc:
            is_locked = exc.errno in {errno.EACCES, errno.EPERM} or getattr(exc, "winerror", None) in lock_like_winerrors
            if not is_locked:
                raise
            if attempt < 4:
                time.sleep(0.1)
                continue
            log_warning(f"[WARN] Could not remove temporary reply file because it is still locked: {path}")


def reply_reference_doc_for_pandoc(reference_doc: Path, output: Path, pmt_settings: PmtSettings) -> Path:
    """Return a reference DOCX with docxPageMargins already applied for Pandoc."""
    target = PMT_REPLY_WORK_DIR / "reference-doc" / f"{output.stem}.reference.docx"
    result = write_reference_doc_with_page_margins(reference_doc, target, pmt_settings)
    if result is None:
        return reference_doc

    log_debug(
        "[DEBUG] Prepared reply reference DOCX with docxPageMargins: "
        f"{reply_resolve.to_pandoc_path(target)} margins={result['margins']}"
    )
    return target


def build_reply_docx(
    reply: Path,
    manuscript: Path,
    manuscript_line_source: Path,
    output: Path,
    reference_doc: Path,
    style: Path | Sequence[str | Path],
    from_format: str,
    *,
    warn_hat_order: bool = True,
) -> None:
    """Build a reviewer-reply DOCX with manuscript references resolved first."""
    ensure_output_writable(output)
    reply_text = reply.read_text(encoding="utf-8")
    effective = reply_resolve.load_reply_metadata(reply, style)
    pmt_settings = effective.pmt_settings
    pandoc_reference_doc = reply_reference_doc_for_pandoc(reference_doc, output, pmt_settings)
    conversion_method = normalize_conversion_method(pmt_settings.mathtype_conversion_method)
    use_mathtype = resolve_mathtype_enabled(
        pmt_settings.mathtype,
        conversion_method,
    )
    flattened_style = reply_resolve.write_reply_style_metadata_file(
        effective,
        use_mathtype=use_mathtype,
    )
    if use_mathtype and warn_hat_order:
        warn_mathtype_hat_style_order(reply)
        if manuscript.exists() and manuscript.is_file() and manuscript.resolve() != reply.resolve():
            warn_mathtype_hat_style_order(manuscript)
    pandoc_output = mathtype_marked_docx_path(output) if use_mathtype else output
    if use_mathtype:
        ensure_output_writable(pandoc_output)

    resolved_text = reply_resolve.resolve_reply_markdown(
        reply_text,
        manuscript,
        manuscript_line_source,
        flattened_style,
        effective,
        from_format,
        format_labeled_equations=True,
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    temp_reply_path = resolved_reply_path(reply)
    temp_reply_path.write_text(resolved_text, encoding="utf-8")

    try:
        embed_svg_images = should_embed_docx_svg_images(pmt_settings)
        convert_all_svg = should_convert_docx_svg_to_png(pmt_settings)
        if convert_all_svg and svg_filter_helpers.requested_docx_svg_image_embedding(pmt_settings):
            log_debug("[DEBUG] Skipping reply SVG child-image embedding because docxConvertSvgToPng is enabled")
        elif embed_svg_images:
            log_debug("[DEBUG] Embedding linked child images inside reply SVG files for DOCX")
        if convert_all_svg:
            log_debug("[DEBUG] Converting referenced reply SVG images to PNG for DOCX")
        svg_filter_args = [
            *svg_embed_images_filter_args(),
            *svg_to_png_filter_args(),
        ]
        svg_filter_env = {
            **svg_embed_images_filter_env(reply, pmt_settings, embed_images=embed_svg_images),
            **svg_to_png_filter_env(reply, pmt_settings, convert_all=convert_all_svg),
        }
        if use_mathtype:
            svg_filter_env["PMT_ENABLE_MATHTYPE_MARKERS"] = "true"
        cmd = [
            pandoc_command(),
            str(temp_reply_path),
            "-f",
            from_format,
            "-o",
            str(pandoc_output),
            "--reference-doc",
            str(pandoc_reference_doc),
            "--resource-path",
            reply_resource_path(reply),
            "--metadata-file",
            str(flattened_style),
            *docx_metadata_filter_args(),
            *svg_filter_args,
        ]
        reply_resolve.run_command(cmd, env=pandoc_tools_env(svg_filter_env))

        log_debug("[DEBUG] Running reply DOCX post-processing...")
        if not postprocess_docx(
            str(pandoc_output),
            pmt_settings=pmt_settings,
            pandoc_metadata=effective.pandoc_metadata,
            skip_author_info=True,
            reply_style_formatting=True,
        ):
            raise RuntimeError(f"Reply DOCX post-processing failed: {pandoc_output}")

        if use_mathtype:
            run_mathtype_conversion(pandoc_output, output, pmt_settings)

        syntax_findings = validate_final_docx_syntax(output)
        if syntax_findings:
            raise RuntimeError("Reply DOCX still contains unrendered Pandoc syntax")
    finally:
        cleanup_resolved_reply_path(temp_reply_path)

    log_success(f"[OK] Reply DOCX created: {output}")


def build_reply_txt(
    reply: Path,
    manuscript: Path,
    manuscript_line_source: Path,
    output: Path,
    style: Path | Sequence[str | Path],
    from_format: str,
) -> None:
    """Build a reviewer-reply TXT file with resolved manuscript placeholders."""
    ensure_output_writable(output)
    reply_text = reply.read_text(encoding="utf-8")
    effective = reply_resolve.load_reply_metadata(reply, style)
    flattened_style = reply_resolve.write_reply_style_metadata_file(
        effective,
        use_mathtype=False,
    )
    resolved_text = reply_resolve.resolve_reply_markdown(
        reply_text,
        manuscript,
        manuscript_line_source,
        flattened_style,
        effective,
        from_format,
        format_labeled_equations=False,
    )
    txt_text = reply_resolve.render_reply_txt_markdown(resolved_text)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(txt_text, encoding="utf-8")
    log_success(f"[OK] Reply TXT created: {output}")


def reply_output_path(reply: Path, output_file: str | None) -> Path:
    """Return the exact output file for a reply build."""
    if output_file and output_file != DEFAULT_REPLY_OUTPUT_FILE:
        return Path(output_file)
    return Path(DEFAULT_OUTPUT_DIR) / "docx" / f"{reply.stem}.docx"


def reply_output_format(output: Path) -> str:
    """Return the reply output format selected by the output file suffix."""
    suffix = output.suffix.lower()
    if suffix in {".docx", ".txt"}:
        return suffix.removeprefix(".")
    raise ValueError(f"Unsupported reply output suffix `{output.suffix}`; use .docx or .txt")


def reply_reference_doc_path(reference_doc: str | None) -> Path:
    """Return the active reference DOCX for reply builds."""
    if reference_doc:
        return Path(reference_doc)
    return template_root() / "pandoc" / "manuscript-template" / "reference-doc.docx"
