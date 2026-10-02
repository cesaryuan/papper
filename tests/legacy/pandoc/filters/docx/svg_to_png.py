"""
Pandoc filter that converts local SVG image references to PNG for DOCX output.

This is useful for journal submission systems that reject SVG files even when
Word can display them. The filter rewrites only local ``.svg``/``.svgz`` image
URLs in the Pandoc AST and leaves the source Markdown unchanged.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

import panflute as pf
from lxml import etree


SVG_SUFFIXES = {".svg", ".svgz"}
XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
CACHE_METADATA_VERSION = 2
LOG_LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "WARN": 30, "ERROR": 40}
TO_PNG_ATTRIBUTE_KEYS = ("to-png", "to_png", "toPng")
TO_PNG_SCALE_ATTRIBUTE_KEYS = ("to-png-scale", "to_png_scale", "toPngScale")
TRUE_VALUES = {"1", "true", "yes", "y", "on"}
FALSE_VALUES = {"0", "false", "no", "n", "off", ""}

# Auto-derive a rasterization pixel width from the Markdown image width= attribute
# when docxSvgToPngWidth is not explicitly configured. A bare percentage (or a
# missing width, treated as 100%) maps to a 3000px reference width, an explicit
# pixel width is doubled, and physical units are converted at a fixed 500 dpi.
AUTO_WIDTH_PERCENT_REFERENCE = 3000
AUTO_WIDTH_PIXEL_SCALE = 2
AUTO_WIDTH_PHYSICAL_DPI = 500
INCHES_PER_UNIT = {
    "in": 1.0,
    "inch": 1.0,
    "cm": 1.0 / 2.54,
    "mm": 1.0 / 25.4,
}
CONVERTED: set[Path] = set()
REUSED: set[Path] = set()
SKIPPED: set[str] = set()


@dataclass(frozen=True)
class SvgResourceNormalization:
    """Store a normalized SVG string and its local child-image dependencies."""

    svg_string: str | None
    resources: list[dict[str, object]]


def should_log(level: str) -> bool:
    """Return True when a filter message should be emitted."""
    configured = os.getenv("PANDOC_TEMPLATE_LOG_LEVEL", "INFO").strip().upper()
    return LOG_LEVELS.get(level, 30) >= LOG_LEVELS.get(configured, 20)


def log_info(message: str) -> None:
    """Emit filter progress at INFO level."""
    if should_log("INFO"):
        print(message, file=sys.stderr)


def log_debug(message: str) -> None:
    """Emit per-image details only when DEBUG logging is enabled."""
    if should_log("DEBUG"):
        print(message, file=sys.stderr)


def log_warning(message: str) -> None:
    """Emit a filter warning at the default log level."""
    if should_log("WARNING"):
        print(message, file=sys.stderr)


def parse_float_env(name: str, default: float) -> float:
    """Parse a positive float environment option with a safe fallback."""
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError:
        log_warning(f"[WARN] Invalid {name}={value!r}; using {default}")
        return default
    if parsed <= 0:
        log_warning(f"[WARN] {name} must be positive; using {default}")
        return default
    return parsed


def parse_int_env(name: str) -> int | None:
    """Parse an optional positive integer environment option."""
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return None
    try:
        parsed = int(value)
    except ValueError:
        log_warning(f"[WARN] Invalid {name}={value!r}; ignoring")
        return None
    if parsed <= 0:
        log_warning(f"[WARN] {name} must be positive; ignoring")
        return None
    return parsed


def parse_bool_value(value: object) -> bool:
    """Parse metadata-style booleans used by env vars and image attributes."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    normalized = str(value).strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    return False


def image_requests_png(elem: pf.Image) -> bool:
    """Return True when one image explicitly requests SVG rasterization."""
    for key in TO_PNG_ATTRIBUTE_KEYS:
        if key in elem.attributes:
            return parse_bool_value(elem.attributes[key])
    return False


def image_scale_override(elem: pf.Image, default: float) -> tuple[float, bool]:
    """Return a per-image rasterization scale override when configured."""
    for key in TO_PNG_SCALE_ATTRIBUTE_KEYS:
        if key not in elem.attributes:
            continue
        value = elem.attributes[key]
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            log_warning(f"[WARN] Invalid {key}={value!r}; using {default}")
            return default, False
        if parsed <= 0:
            log_warning(f"[WARN] {key} must be positive; using {default}")
            return default, False
        return parsed, True
    return default, False


def image_auto_width(elem: pf.Image) -> int | None:
    """Derive a PNG pixel width from the image's width= attribute.

    Rules (only used when docxSvgToPngWidth is not explicitly configured):
    - width=num%               → round(3000 * num / 100)
    - width=numpx              → round(num * 2)
    - width=num{cm,mm,in,inch} → round(num * inches * 500 dpi)

    A missing width attribute returns None so the caller falls back to
    scale/dpi sizing (and any per-image to-png-scale still applies). An
    unparseable width also returns None.
    """
    raw_value = elem.attributes.get("width")
    if raw_value is None:
        return None
    raw = str(raw_value).strip()
    m = re.fullmatch(r"([0-9]*\.?[0-9]+)\s*(%|px|cm|mm|in|inch)", raw, re.IGNORECASE)
    if not m:
        return None
    num, unit = float(m.group(1)), m.group(2).lower()
    if unit == "%":
        return max(1, round(AUTO_WIDTH_PERCENT_REFERENCE * num / 100))
    if unit == "px":
        return max(1, round(num * AUTO_WIDTH_PIXEL_SCALE))
    inches = INCHES_PER_UNIT.get(unit)
    if inches is None:
        return None
    return max(1, round(num * inches * AUTO_WIDTH_PHYSICAL_DPI))


def remove_to_png_attributes(elem: pf.Image) -> None:
    """Remove DOCX-only conversion hints before Pandoc writes the output."""
    for key in (*TO_PNG_ATTRIBUTE_KEYS, *TO_PNG_SCALE_ATTRIBUTE_KEYS):
        elem.attributes.pop(key, None)


def path_from_url(url: str) -> str | None:
    """Return a local filesystem path string for a Pandoc image URL."""
    if not url:
        return None

    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    is_windows_drive = len(scheme) == 1 and len(url) > 2 and url[1:3] in {":\\", ":/"}
    if scheme and scheme != "file" and not is_windows_drive:
        return None

    path_text = parsed.path if scheme == "file" else url
    path_text = path_text.split("?", 1)[0].split("#", 1)[0]
    if not path_text:
        return None
    return unquote(path_text)


def is_svg_path(path_text: str) -> bool:
    """Return True when a local image path points to an SVG file."""
    return Path(path_text).suffix.lower() in SVG_SUFFIXES


def configured_base_dirs() -> list[Path]:
    """Return project-relative lookup roots used for resolving image paths."""
    raw_dirs = os.getenv("PMT_SVG_TO_PNG_BASE_DIRS", ".")
    dirs: list[Path] = []
    for raw_dir in raw_dirs.split(os.pathsep):
        if not raw_dir:
            continue
        path = Path(raw_dir).resolve()
        if path not in dirs:
            dirs.append(path)
    return dirs or [Path.cwd().resolve()]


def resolve_source_path(path_text: str, base_dirs: list[Path]) -> Path | None:
    """Resolve a local SVG reference against the project and manuscript dirs."""
    source = Path(path_text)
    if source.is_absolute():
        return source.resolve() if source.exists() else None

    for base_dir in base_dirs:
        candidate = (base_dir / source).resolve()
        if candidate.exists():
            return candidate
    return None


def output_path_for(source: Path, output_root: Path, base_dirs: list[Path]) -> Path:
    """Return a stable PNG cache path for one SVG source."""
    source = source.resolve()
    for base_dir in base_dirs:
        try:
            relative = source.relative_to(base_dir)
        except ValueError:
            continue
        return (output_root / relative).with_suffix(".png")

    # Outside-project absolute paths need a hash to avoid filename collisions.
    digest = hashlib.sha256(str(source).encode("utf-8")).hexdigest()[:12]
    return output_root / f"{source.stem}-{digest}.png"


def cache_suffix_for_scale(scale: float) -> str:
    """Return a filesystem-safe cache suffix for a local scale override."""
    value = f"{scale:g}".replace(".", "p").replace("-", "m").replace("+", "")
    return f"scale-{value}"


def with_cache_suffix(target: Path, suffix: str | None) -> Path:
    """Return a variant cache path when one SVG is rendered at multiple scales."""
    if not suffix:
        return target
    return target.with_name(f"{target.stem}.{suffix}{target.suffix}")


def read_svg_bytes(source: Path) -> bytes:
    """Read plain SVG or SVGZ source bytes."""
    data = source.read_bytes()
    if source.suffix.lower() == ".svgz":
        return gzip.decompress(data)
    return data


def parse_svg(source: Path) -> etree._ElementTree:
    """Parse SVG XML so local child image hrefs can be normalized."""
    parser = etree.XMLParser(remove_blank_text=False, resolve_entities=False)
    return etree.fromstring(read_svg_bytes(source), parser=parser).getroottree()


def href_from_image(elem: etree._Element) -> str | None:
    """Return the best href value from an SVG image element."""
    return elem.get("href") or elem.get(XLINK_HREF)


def path_from_svg_href(href: str) -> str | None:
    """Return a local path string from an SVG child image href."""
    if not href or href.strip().lower().startswith("data:"):
        return None

    parsed = urlparse(href)
    scheme = parsed.scheme.lower()
    is_windows_drive = len(scheme) == 1 and len(href) > 2 and href[1:3] in {":\\", ":/"}
    if scheme and scheme != "file" and not is_windows_drive:
        return None

    path_text = parsed.path if scheme == "file" else href
    path_text = path_text.split("?", 1)[0].split("#", 1)[0]
    if not path_text:
        return None
    return unquote(path_text)


def resolve_child_path(source_svg: Path, href: str) -> Path | None:
    """Resolve an SVG child image href relative to the SVG file location."""
    path_text = path_from_svg_href(href)
    if path_text is None:
        return None

    child = Path(path_text)
    if child.is_absolute():
        return child.resolve() if child.exists() else None

    candidate = (source_svg.parent / child).resolve()
    return candidate if candidate.exists() else None


def relative_svg_href(source_svg: Path, child: Path) -> str:
    """Return a decoded relative href suitable for resvg resource lookup."""
    relative = os.path.relpath(child, source_svg.parent)
    return relative.replace(os.sep, "/")


def metadata_for_file(path: Path) -> dict[str, object]:
    """Return cache metadata for one local dependency file."""
    stat_result = path.stat()
    return {
        "path": str(path.resolve()),
        "mtime_ns": stat_result.st_mtime_ns,
        "size": stat_result.st_size,
    }


def normalize_svg_resource_hrefs(source: Path) -> SvgResourceNormalization:
    """Decode local child image hrefs before resvg rasterization.

    resvg may fail to resolve URL-encoded non-ASCII filenames on Windows. When
    a decoded local resource exists, rewrite the temporary SVG string to use the
    decoded relative path while leaving the source SVG untouched.
    """
    try:
        tree = parse_svg(source)
    except etree.XMLSyntaxError as exc:
        log_warning(f"[WARN] Could not parse SVG for resource normalization: {source}: {exc}")
        return SvgResourceNormalization(svg_string=None, resources=[])

    changed = False
    resources: list[dict[str, object]] = []
    for elem in tree.xpath("//*[local-name() = 'image']"):
        href = href_from_image(elem)
        if href is None:
            continue

        child = resolve_child_path(source, href)
        if child is None:
            continue

        normalized_href = relative_svg_href(source, child)
        resources.append({"href": href, "normalized_href": normalized_href, **metadata_for_file(child)})
        if normalized_href != href:
            elem.set("href", normalized_href)
            elem.set(XLINK_HREF, normalized_href)
            changed = True

    if not changed:
        return SvgResourceNormalization(svg_string=None, resources=resources)

    svg_string = etree.tostring(tree, encoding="unicode")
    return SvgResourceNormalization(svg_string=svg_string, resources=resources)


def convert_with_resvg_py(
    source: Path,
    target: Path,
    dpi: float,
    scale: float,
    width: int | None = None,
    svg_string: str | None = None,
) -> str:
    """Convert SVG to PNG with the pure package-managed resvg binding."""
    import resvg_py

    render_args = {
        # Resolve relative <image href="..."> assets from the SVG file location.
        "resources_dir": str(source.parent),
        "dpi": dpi,
        "zoom": scale if scale != 1 else None,
    }
    if width is not None:
        render_args["width"] = width
    if svg_string is None:
        png_bytes = resvg_py.svg_to_bytes(svg_path=str(source), **render_args)
    else:
        png_bytes = resvg_py.svg_to_bytes(svg_string=svg_string, **render_args)
    target.write_bytes(png_bytes)
    return "resvg-py"


def convert_svg_to_png(
    source: Path,
    target: Path,
    dpi: float,
    scale: float,
    width: int | None = None,
    svg_string: str | None = None,
) -> str:
    """Convert one SVG file to PNG using the required resvg-py dependency."""
    target.parent.mkdir(parents=True, exist_ok=True)
    return convert_with_resvg_py(source, target, dpi, scale, width=width, svg_string=svg_string)


def cache_metadata_path(target: Path) -> Path:
    """Return the sidecar metadata path used to validate a generated PNG."""
    return target.with_suffix(target.suffix + ".meta.json")


def expected_cache_metadata(
    source: Path,
    dpi: float,
    scale: float,
    width: int | None,
    pmt_version: str,
    resources: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    """Build cache metadata that changes when source, options, or Papper version change."""
    stat_result = source.stat()
    return {
        "version": CACHE_METADATA_VERSION,
        "source": str(source.resolve()),
        "source_mtime_ns": stat_result.st_mtime_ns,
        "source_size": stat_result.st_size,
        "resources": resources or [],
        "dpi": dpi,
        "scale": scale,
        "width": width,
        "pmt_version": pmt_version,
    }


def cache_metadata_matches(target: Path, expected: dict[str, object]) -> bool:
    """Return True only when the PNG and its sidecar metadata are current."""
    metadata_path = cache_metadata_path(target)
    if not target.exists() or not metadata_path.exists():
        return False
    try:
        actual = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log_debug(f"[svg-to-png] Ignoring stale cache metadata {metadata_path}: {exc}")
        return False
    return all(actual.get(key) == value for key, value in expected.items())


def write_cache_metadata(target: Path, metadata: dict[str, object], converter: str) -> None:
    """Write sidecar metadata so option and Papper version changes invalidate old PNGs."""
    payload = {**metadata, "converter": converter}
    cache_metadata_path(target).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def ensure_png(source: Path, target: Path, dpi: float, scale: float, width: int | None, pmt_version: str) -> Path:
    """Create or reuse the PNG cache file for one SVG source."""
    normalization = normalize_svg_resource_hrefs(source)
    expected_metadata = expected_cache_metadata(
        source,
        dpi,
        scale,
        width,
        pmt_version,
        resources=normalization.resources,
    )
    if cache_metadata_matches(target, expected_metadata):
        REUSED.add(target)
        log_debug(f"[svg-to-png] Reusing {target}")
        return target

    converter = convert_svg_to_png(source, target, dpi, scale, width=width, svg_string=normalization.svg_string)
    write_cache_metadata(target, expected_metadata, converter)
    CONVERTED.add(target)
    log_debug(f"[svg-to-png] Converted {source} -> {target} with {converter}")
    return target


def rewrite_image(
    elem: pf.Image,
    base_dirs: list[Path],
    output_root: Path,
    dpi: float,
    scale: float,
    width: int | None,
    pmt_version: str,
    cache_suffix: str | None = None,
) -> pf.Image | None:
    """Rewrite one local SVG image URL to its generated PNG path."""
    path_text = path_from_url(elem.url)
    if path_text is None or not is_svg_path(path_text):
        return None

    source = resolve_source_path(path_text, base_dirs)
    if source is None:
        SKIPPED.add(elem.url)
        log_warning(f"[WARN] SVG image not found, leaving unchanged: {elem.url}")
        return None

    target = ensure_png(
        source,
        with_cache_suffix(output_path_for(source, output_root, base_dirs), cache_suffix),
        dpi,
        scale,
        width,
        pmt_version,
    )
    elem.url = target.as_posix()
    return elem


def action(elem: pf.Element, doc: pf.Doc) -> pf.Element | None:
    """Panflute action that converts and rewrites SVG image elements."""
    if not isinstance(elem, pf.Image):
        return None
    requested_by_image = image_requests_png(elem)
    effective_scale, has_scale_override = image_scale_override(elem, doc.pmt_svg_scale)
    # Determine effective width: explicit metadata value takes priority;
    # otherwise derive per-image from the image's width= attribute.
    effective_width = doc.pmt_svg_width if doc.pmt_svg_width is not None else image_auto_width(elem)
    if effective_width is not None and has_scale_override:
        log_warning("[WARN] Ignoring to-png-scale because docxSvgToPngWidth is set")
        effective_scale = doc.pmt_svg_scale
        has_scale_override = False
    remove_to_png_attributes(elem)
    path_text = path_from_url(elem.url)
    if path_text is None or not is_svg_path(path_text):
        return None
    if not doc.pmt_svg_convert_all and not requested_by_image:
        return None
    cache_suffix = (
        cache_suffix_for_scale(effective_scale)
        if has_scale_override and effective_scale != doc.pmt_svg_scale
        else None
    )
    return rewrite_image(
        elem,
        doc.pmt_svg_base_dirs,
        doc.pmt_svg_output_root,
        doc.pmt_svg_dpi,
        effective_scale,
        effective_width,
        doc.pmt_svg_pmt_version,
        cache_suffix,
    )


def prepare(doc: pf.Doc) -> None:
    """Load conversion settings from the build process environment."""
    doc.pmt_svg_base_dirs = configured_base_dirs()
    doc.pmt_svg_output_root = Path(os.getenv("PMT_SVG_TO_PNG_DIR", "tmp/svg-png")).resolve()
    doc.pmt_svg_dpi = parse_float_env("PMT_SVG_TO_PNG_DPI", 300)
    doc.pmt_svg_scale = parse_float_env("PMT_SVG_TO_PNG_SCALE", 1)
    doc.pmt_svg_width = parse_int_env("PMT_SVG_TO_PNG_WIDTH")
    doc.pmt_svg_pmt_version = os.getenv("PMT_SVG_TO_PNG_PMT_VERSION", "unknown")
    doc.pmt_svg_convert_all = parse_bool_value(os.getenv("PMT_SVG_TO_PNG_CONVERT_ALL"))


def finalize(doc: pf.Doc) -> None:
    """Report an INFO-level summary after all images have been inspected."""
    total = len(CONVERTED) + len(REUSED)
    if total:
        log_debug(f"[svg-to-png] SVG image PNG cache ready: {total} file(s)")
    if SKIPPED:
        log_warning(f"[WARN] SVG image conversion skipped for {len(SKIPPED)} missing file(s)")


def main(doc: pf.Doc | None = None) -> pf.Doc:
    """Run the SVG-to-PNG Pandoc filter."""
    return pf.run_filter(action, prepare=prepare, finalize=finalize, doc=doc)


if __name__ == "__main__":
    main()
