"""DOCX target orchestration and DOCX-only Pandoc preparation helpers."""

from __future__ import annotations

import errno
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..mathtype.convert_marked_docx import convert_marked_docx
from ..mathtype.ole_parts import check_mathtype_availability, normalize_conversion_method
from ..runtime.logging import log_debug, log_warning
from ..runtime.metadata import EffectiveMetadata, is_chinese_language
from .metadata import prepare_docx_metadata, write_docx_pandoc_metadata
from . import svg_filters as svg_filter_helpers
from .page_margins import write_reference_doc_with_page_margins
from .postprocess import postprocess_docx as run_docx_postprocess
from .postprocess.final_docx_syntax_check import validate_final_docx_syntax
from .svg_filters import (
    should_convert_docx_svg_to_png,
    should_embed_docx_svg_images,
)


@dataclass(frozen=True)
class DocxBuildContext:
    """Shared build services supplied by the CLI without a module cycle."""

    settings: Any
    manuscript_output_file: Callable[[str, str], Path]
    ensure_output_parent: Callable[[Path], None]
    resource_path: Callable[[str], Path]
    to_pandoc_path: Callable[[Path], str]
    run_pandoc: Callable[..., None]


def ensure_docx_target_writable(target: Path) -> None:
    """Fail early when an existing DOCX target cannot be overwritten."""
    if not target.exists():
        return
    if target.is_dir():
        raise RuntimeError(f"Target DOCX path is a directory and cannot be overwritten: {target}")

    try:
        with target.open("r+b"):
            pass
    except OSError as exc:
        lock_like_errors = {errno.EACCES, errno.EPERM}
        lock_like_winerrors = {5, 32, 33}
        if exc.errno in lock_like_errors or getattr(exc, "winerror", None) in lock_like_winerrors:
            raise RuntimeError(
                "目标 DOCX 可能已经在 Word 中打开，或正被其他程序占用，当前无法写入。\n"
                f"请关闭后重试: {target}"
            ) from exc
        raise


def docx_svg_to_png_filter_args() -> list[str]:
    """Return Pandoc args for the DOCX SVG-to-PNG image filter."""
    return svg_filter_helpers.svg_to_png_filter_args()


def docx_svg_embed_images_filter_args() -> list[str]:
    """Return Pandoc args for the DOCX SVG child-image filter."""
    return svg_filter_helpers.svg_embed_images_filter_args()


def docx_svg_base_dirs(manuscript_file: str | Path) -> list[Path]:
    """Return lookup roots shared by the DOCX SVG filters."""
    manuscript_dir = Path(manuscript_file).parent
    return svg_filter_helpers.unique_resolved_dirs([Path.cwd(), manuscript_dir])


def docx_svg_embed_images_filter_env(
    manuscript_file: str | Path,
    settings,
    embed_images: bool | None = None,
) -> dict[str, str]:
    """Return environment settings consumed by SVG child-image embedding."""
    return svg_filter_helpers.svg_embed_images_filter_env(
        docx_svg_base_dirs(manuscript_file),
        settings,
        embed_images=embed_images,
    )


def docx_svg_to_png_filter_env(
    manuscript_file: str | Path,
    settings,
    convert_all: bool | None = None,
) -> dict[str, str]:
    """Return environment settings consumed by SVG-to-PNG conversion."""
    return svg_filter_helpers.svg_to_png_filter_env(
        docx_svg_base_dirs(manuscript_file),
        settings,
        convert_all=convert_all,
    )


def docx_metadata_filter_args(resource_path) -> list[str]:
    """Return Pandoc args for DOCX-only hidden metadata markers."""
    filter_path = resource_path("pandoc/filters/docx/docx_metadata.lua")
    if not filter_path.exists():
        raise FileNotFoundError(f"DOCX metadata Pandoc filter not found: {filter_path}")
    return ["--lua-filter", filter_path.as_posix()]


def mathtype_marked_docx_path(settings) -> Path:
    """Return the intermediate DOCX path carrying hidden MathType markers."""
    work_dir = Path(settings.mathtype_work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    # Keep the marker DOCX outside final output while retaining failed builds.
    return work_dir / f"{settings.project_name}.marked.docx"


def resolve_mathtype_build_enabled(requested: bool, conversion_method: object | None = None) -> bool:
    """Return whether MathType conversion is usable on this machine."""
    if not requested:
        return False

    log_debug("[DEBUG] MathType DOCX equations enabled by metadata: mathtype: true")
    availability = check_mathtype_availability(conversion_method)
    if availability.usable:
        return True

    log_warning(
        availability.format_failure(
            "[WARN] MathType was requested by metadata, but MathType conversion will be skipped."
        )
    )
    log_warning("[WARN] Building DOCX with Pandoc/Word equations instead.\n")
    return False


def run_mathtype_conversion(marked_docx: Path, target_docx: Path, pmt_settings, settings) -> None:
    """Convert a marked DOCX's OMML equations into MathType OLE equations."""
    log_debug("[DOCX] Converting equations to MathType OLE objects...")
    convert_marked_docx(
        source=marked_docx,
        target=target_docx,
        work_dir=Path(settings.mathtype_work_dir) / settings.project_name,
        pmt_settings=pmt_settings,
    )


def active_reference_doc(settings, resource_path) -> Path:
    """Return the source reference DOCX selected for this build."""
    if settings.reference_doc:
        return Path(settings.reference_doc)
    return resource_path("pandoc/manuscript-template/reference-doc.docx")


def generated_reference_doc_path(settings) -> Path:
    """Return the temporary reference DOCX path prepared for Pandoc."""
    from ..runtime.paths import process_temp_dir

    return process_temp_dir() / "reference-doc" / f"{settings.project_name}.reference.docx"


def docx_reference_doc_args(settings, pmt_settings, resource_path, to_pandoc_path) -> list[str]:
    """Return a reference-doc argument after applying configured page margins."""
    source = active_reference_doc(settings, resource_path)
    target = generated_reference_doc_path(settings)
    result = write_reference_doc_with_page_margins(source, target, pmt_settings)
    if result is None:
        if not settings.reference_doc:
            return []
        return ["--reference-doc", to_pandoc_path(Path(settings.reference_doc))]

    log_debug(
        "[DEBUG] Prepared reference DOCX with docxPageMargins: "
        f"{to_pandoc_path(target)} margins={result['margins']}"
    )
    return ["--reference-doc", to_pandoc_path(target)]


def build_docx(
    effective: EffectiveMetadata,
    context: DocxBuildContext,
    *,
    warn_hat_order: bool = True,
    lang: str | None = None,
) -> None:
    """Generate one DOCX target with optional post-processing and MathType."""
    from ..mathtype.preflight import warn_mathtype_hat_style_order

    log_debug("[DOCX] Building DOCX...\n")
    metadata_language = effective.pandoc_metadata.get("lang")
    effective = prepare_docx_metadata(effective)
    chinese_mode = is_chinese_language(effective.pandoc_metadata.get("lang"))
    if lang is not None:
        log_debug(f"[DEBUG] Command-line DOCX language mode: {lang}")
    elif metadata_language is not None:
        log_debug(f"[DEBUG] DOCX language mode from Pandoc metadata: {metadata_language}")
    if chinese_mode:
        log_debug("[DEBUG] Chinese DOCX mode enabled: chapter-numbered figures/tables and non-bold heading styles")

    settings = context.settings
    docx_file = context.manuscript_output_file(settings.docx_dir, "docx")
    context.ensure_output_parent(docx_file)
    ensure_docx_target_writable(docx_file)
    pmt_settings = effective.pmt_settings
    conversion_method = normalize_conversion_method(pmt_settings.mathtype_conversion_method)
    use_mathtype = resolve_mathtype_build_enabled(pmt_settings.mathtype, conversion_method)
    if use_mathtype and warn_hat_order:
        warn_mathtype_hat_style_order(Path(settings.manuscript_file))

    pandoc_output = docx_file
    pandoc_env: dict[str, str] = {"PMT_CHINESE_MODE": "true"} if chinese_mode else {}
    extra_args = docx_reference_doc_args(
        settings,
        pmt_settings,
        context.resource_path,
        context.to_pandoc_path,
    )
    extra_args.extend(docx_metadata_filter_args(context.resource_path))

    embed_svg_images = should_embed_docx_svg_images(pmt_settings)
    convert_all_svg = should_convert_docx_svg_to_png(pmt_settings)
    if convert_all_svg and svg_filter_helpers.requested_docx_svg_image_embedding(pmt_settings):
        log_debug("[DEBUG] Skipping SVG child-image embedding because docxConvertSvgToPng is enabled")
    elif embed_svg_images:
        log_debug("[DEBUG] Embedding linked child images inside SVG files for DOCX")
    extra_args.extend(docx_svg_embed_images_filter_args())
    pandoc_env.update(
        docx_svg_embed_images_filter_env(
            settings.manuscript_file,
            pmt_settings,
            embed_images=embed_svg_images,
        )
    )
    if convert_all_svg:
        log_debug("[DEBUG] Converting referenced SVG images to PNG for DOCX")
    extra_args.extend(docx_svg_to_png_filter_args())
    pandoc_env.update(
        docx_svg_to_png_filter_env(
            settings.manuscript_file,
            pmt_settings,
            convert_all=convert_all_svg,
        )
    )

    if use_mathtype:
        pandoc_output = mathtype_marked_docx_path(settings)
        pandoc_env["PMT_ENABLE_MATHTYPE_MARKERS"] = "true"

    context.run_pandoc(
        context.resource_path("pandoc/pandoc-docx.yml"),
        pandoc_output,
        effective,
        extra_args=extra_args,
        extra_env=pandoc_env,
        metadata_file=write_docx_pandoc_metadata(effective, use_mathtype=use_mathtype),
    )
    if settings.enable_docx_postprocess:
        log_debug("\n[DOCX] Running Python post-processing...\n")
        postprocess_target = pandoc_output if use_mathtype else docx_file
        if not run_docx_postprocess(
            str(postprocess_target),
            pmt_settings=pmt_settings,
            pandoc_metadata=effective.pandoc_metadata,
        ):
            raise RuntimeError("DOCX post-processing failed")
    if use_mathtype:
        run_mathtype_conversion(pandoc_output, docx_file, pmt_settings, settings)
    if validate_final_docx_syntax(docx_file):
        raise RuntimeError("Final DOCX still contains unrendered Pandoc syntax")

    from ..runtime.logging import log_success

    log_success(f"[OK] DOCX created: {docx_file}")
