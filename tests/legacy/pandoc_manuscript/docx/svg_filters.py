"""Shared Pandoc SVG filter helpers for manuscript and reply DOCX builds."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from .. import runtime_cache_version
from ..runtime.metadata import PmtSettings
from ..runtime.paths import process_temp_dir, project_cache_dir
from ..runtime.resources import template_root

def to_pandoc_path(path: Path) -> str:
    """Return a Pandoc-friendly path string."""
    return path.as_posix()


def configured_svg_to_png_controls(settings: PmtSettings) -> list[str]:
    """Return explicitly enabled SVG rasterization size controls."""
    controls: list[str] = []
    if settings.docx_svg_to_png_width is not None:
        controls.append("docxSvgToPngWidth")
    if settings.docx_svg_to_png_scale is not None:
        controls.append("docxSvgToPngScale")
    if settings.docx_svg_to_png_dpi is not None:
        controls.append("docxSvgToPngDpi")
    return controls


def validate_svg_to_png_controls(settings: PmtSettings) -> None:
    """Reject ambiguous SVG rasterization controls before Pandoc runs."""
    controls = configured_svg_to_png_controls(settings)
    if len(controls) > 1:
        options = ", ".join(controls)
        raise ValueError(f"Only one of docxSvgToPngWidth, docxSvgToPngScale, docxSvgToPngDpi can be set; got: {options}")


def requested_docx_svg_image_embedding(settings: PmtSettings) -> bool:
    """Return the raw SVG child-image embedding flag before PNG conversion overrides it."""
    return settings.docx_embed_svg_images


def should_convert_docx_svg_to_png(settings: PmtSettings) -> bool:
    """Return True when DOCX builds should rasterize all SVG images."""
    return settings.docx_convert_svg_to_png


def should_embed_docx_svg_images(settings: PmtSettings) -> bool:
    """Return True when DOCX builds should inline child images inside SVG files."""
    if should_convert_docx_svg_to_png(settings):
        return False
    return requested_docx_svg_image_embedding(settings)


def python_filter_wrapper(filter_path: Path, name: str) -> Path:
    """Return a Pandoc filter launcher that uses pmt's Python interpreter.

    Pandoc executes JSON filters as external programs. Installed package data
    filters may otherwise run under a system Python that cannot import pmt's
    dependencies, which caused the SVG filter to miss panflute in uv tool installs.
    """
    filter_path = filter_path.resolve()
    if os.name == "nt":
        # Pandoc runs .py filters with python from PATH on Windows. Avoiding a
        # cmd wrapper also preserves UNC working directories for the filter.
        return filter_path

    wrapper_dir = process_temp_dir() / "filters"
    wrapper_dir.mkdir(parents=True, exist_ok=True)
    wrapper_path = wrapper_dir / name
    wrapper_path.write_text(
        f"#!{sys.executable}\n"
        "import runpy\n"
        f"runpy.run_path({str(filter_path)!r}, run_name=\"__main__\")\n",
        encoding="utf-8",
        newline="\n",
    )
    # Shared workspaces can reuse a wrapper owned by another user when it is
    # already executable, so only touch the mode when the execute bit is missing.
    if not os.access(wrapper_path, os.X_OK):
        wrapper_path.chmod(wrapper_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return wrapper_path


def svg_filter_path(name: str) -> Path:
    """Return the repository or package path for one bundled SVG filter."""
    filter_path = template_root() / "tests" / "legacy" / "pandoc" / "filters" / "docx" / name
    if not filter_path.exists():
        raise FileNotFoundError(f"SVG Pandoc filter not found: {filter_path}")
    return filter_path


def svg_embed_images_filter_args() -> list[str]:
    """Return Pandoc args for the self-contained SVG image filter."""
    return [
        "--filter",
        to_pandoc_path(python_filter_wrapper(svg_filter_path("svg_embed_images.py"), "svg_embed_images_filter")),
    ]


def svg_to_png_filter_args() -> list[str]:
    """Return Pandoc args for the SVG-to-PNG image filter."""
    return [
        "--filter",
        to_pandoc_path(python_filter_wrapper(svg_filter_path("svg_to_png.py"), "svg_to_png_filter")),
    ]


def unique_resolved_dirs(candidates: list[Path]) -> list[Path]:
    """Return unique resolved lookup roots while preserving order."""
    unique: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved not in unique:
            unique.append(resolved)
    return unique


def svg_embed_images_filter_env(
    base_dirs: list[Path],
    settings: PmtSettings,
    embed_images: bool | None = None,
) -> dict[str, str]:
    """Return environment settings consumed by the SVG child-image embedding filter."""
    if embed_images is None:
        embed_images = should_embed_docx_svg_images(settings)
    return {
        "PMT_SVG_EMBED_DIR": str((project_cache_dir() / "svg-embedded").resolve()),
        "PMT_SVG_EMBED_BASE_DIRS": os.pathsep.join(str(path) for path in unique_resolved_dirs(base_dirs)),
        "PMT_SVG_EMBED_PMT_VERSION": runtime_cache_version(),
        "PMT_SVG_EMBED_IMAGES": "true" if embed_images else "false",
    }


def svg_to_png_filter_env(
    base_dirs: list[Path],
    settings: PmtSettings,
    convert_all: bool | None = None,
) -> dict[str, str]:
    """Return environment settings consumed by the SVG-to-PNG filter."""
    if convert_all is None:
        convert_all = should_convert_docx_svg_to_png(settings)
    validate_svg_to_png_controls(settings)
    env = {
        "PMT_SVG_TO_PNG_DIR": str((project_cache_dir() / "svg-png").resolve()),
        "PMT_SVG_TO_PNG_BASE_DIRS": os.pathsep.join(str(path) for path in unique_resolved_dirs(base_dirs)),
        "PMT_SVG_TO_PNG_DPI": str(settings.docx_svg_to_png_dpi or 300),
        "PMT_SVG_TO_PNG_SCALE": str(settings.docx_svg_to_png_scale or 1),
        "PMT_SVG_TO_PNG_PMT_VERSION": runtime_cache_version(),
        "PMT_SVG_TO_PNG_CONVERT_ALL": "true" if convert_all else "false",
    }
    if settings.docx_svg_to_png_width is not None:
        env["PMT_SVG_TO_PNG_WIDTH"] = str(settings.docx_svg_to_png_width)
    return env
