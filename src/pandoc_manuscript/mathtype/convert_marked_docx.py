#!/usr/bin/env python3
"""Convert a marker-bearing DOCX from OMML equations to MathType OLE equations."""

import argparse
from pathlib import Path
from ..runtime.logging import log_info, log_debug
from ..runtime.metadata import PmtSettings
from ..runtime.paths import process_temp_dir

from .marked_docx import extract_marked_equation_requests, inspect_docx, replace_marked_omml_with_generated
from .ole_parts import generate_equation_parts, normalize_conversion_method, normalize_svg_backend


def convert_marked_docx(
    source: Path,
    target: Path,
    work_dir: Path,
    pmt_settings: PmtSettings | None = None,
) -> int:
    """Convert all hidden-marker-bound OMML nodes in a DOCX to MathType OLE."""
    requests = extract_marked_equation_requests(source)
    if not requests:
        raise ValueError(
            f"No hidden MathType markers found in {source}; "
            "build the DOCX with the packaged MathType marker Lua filter first"
        )

    size_summary = sorted({request.font_size_pt for request in requests if request.font_size_pt is not None})
    log_debug(f"[mathtype] marked DOCX math nodes: {len(requests)}")
    if size_summary:
        log_debug(f"[mathtype] detected Word font sizes (pt): {', '.join(f'{size:g}' for size in size_summary)}")
    settings = pmt_settings or PmtSettings.model_validate({})
    conversion_method = normalize_conversion_method(settings.mathtype_conversion_method)
    svg_backend = normalize_svg_backend(settings.mathtype_svg_backend)
    log_debug(f"[mathtype] conversion method: {conversion_method}")
    log_debug(f"[mathtype] SVG backend: {svg_backend}")
    equations = generate_equation_parts(
        requests,
        work_dir,
        conversion_method=conversion_method,
        svg_backend=svg_backend,
        math_font=settings.mathtype_typst_math_font,
    )
    replaced = replace_marked_omml_with_generated(source, target, equations)
    log_debug(f"[mathtype] replaced top-level OMML nodes: {replaced}")
    inspect_docx(target)
    return replaced


def main() -> int:
    """Run marker-bound DOCX conversion to MathType OLE objects."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=str(process_temp_dir() / "mathtype-build" / "marker-source.docx"), help="DOCX containing hidden MathType markers")
    parser.add_argument("--target", default=str(process_temp_dir() / "mathtype-build" / "marker-ole-probe.docx"), help="Output DOCX")
    parser.add_argument("--mode", choices=["all"], default="all", help="Convert all marker-bound formulas")
    parser.add_argument("--work-dir", default=str(process_temp_dir() / "mathtype-build" / "all"), help="Directory for generated OLE and WMF parts")
    args = parser.parse_args()

    convert_marked_docx(
        source=Path(args.source),
        target=Path(args.target),
        work_dir=Path(args.work_dir),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
