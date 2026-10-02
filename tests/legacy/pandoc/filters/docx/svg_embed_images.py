"""
Pandoc filter that embeds local image resources referenced inside SVG files.

Word does not reliably resolve relative ``<image href="...">`` resources inside
embedded SVG parts. This filter rewrites only the Pandoc image URL to a cached,
self-contained SVG whose local child images are stored as data URIs. Source
Markdown and source SVG files are not rewritten.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import mimetypes
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

import panflute as pf
from lxml import etree


SVG_SUFFIXES = {".svg", ".svgz"}
XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
CACHE_METADATA_VERSION = 1
LOG_LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "WARN": 30, "ERROR": 40}
EMBEDDED: set[Path] = set()
REUSED: set[Path] = set()
SKIPPED: set[str] = set()


@dataclass(frozen=True)
class SvgImageRef:
    """Store one local child image reference found inside an SVG file."""

    elem: etree._Element
    href: str
    source: Path
    mime_type: str


@dataclass(frozen=True)
class EmbeddedSvg:
    """Store one cached SVG and whether Word requires PNG fallback."""

    path: Path
    requires_png_fallback: bool


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


def parse_bool_value(value: object) -> bool:
    """Parse metadata-style booleans used by env vars."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


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
    raw_dirs = os.getenv("PMT_SVG_EMBED_BASE_DIRS", ".")
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
    """Return a stable self-contained SVG cache path for one SVG source."""
    source = source.resolve()
    for base_dir in base_dirs:
        try:
            relative = source.relative_to(base_dir)
        except ValueError:
            continue
        return (output_root / relative).with_suffix(".svg")

    # Outside-project absolute paths need a hash to avoid filename collisions.
    digest = hashlib.sha256(str(source).encode("utf-8")).hexdigest()[:12]
    return output_root / f"{source.stem}-{digest}.svg"


def read_svg_bytes(source: Path) -> bytes:
    """Read plain SVG or SVGZ source bytes."""
    data = source.read_bytes()
    if source.suffix.lower() == ".svgz":
        return gzip.decompress(data)
    return data


def parse_svg(source: Path) -> etree._ElementTree:
    """Parse SVG XML with blank text preserved for stable serialization."""
    parser = etree.XMLParser(remove_blank_text=False, resolve_entities=False)
    return etree.fromstring(read_svg_bytes(source), parser=parser).getroottree()


def href_from_image(elem: etree._Element) -> str | None:
    """Return the best href value from an SVG image element."""
    return elem.get("href") or elem.get(XLINK_HREF)


def path_from_svg_href(href: str) -> str | None:
    """Return a local path for an SVG child image href."""
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


def guess_image_mime_type(path: Path) -> str:
    """Return the MIME type used in a data URI for a local image file."""
    guessed, _ = mimetypes.guess_type(path.name)
    if guessed:
        return guessed
    return "application/octet-stream"


def collect_image_refs(tree: etree._ElementTree, source_svg: Path) -> list[SvgImageRef]:
    """Find local child images that can be embedded into a self-contained SVG."""
    refs: list[SvgImageRef] = []
    for elem in tree.xpath("//*[local-name() = 'image']"):
        href = href_from_image(elem)
        if href is None or href.strip().lower().startswith("data:"):
            continue

        child = resolve_child_path(source_svg, href)
        if child is None:
            if path_from_svg_href(href) is not None:
                SKIPPED.add(href)
                log_warning(f"[WARN] SVG child image not found, leaving unchanged: {href}")
            continue

        refs.append(
            SvgImageRef(
                elem=elem,
                href=href,
                source=child,
                mime_type=guess_image_mime_type(child),
            )
        )
    return refs


def data_uri_for(ref: SvgImageRef) -> str:
    """Return a base64 data URI for one child image reference."""
    encoded = base64.b64encode(ref.source.read_bytes()).decode("ascii")
    return f"data:{ref.mime_type};base64,{encoded}"


def embed_refs(refs: list[SvgImageRef]) -> None:
    """Replace each local child href with an equivalent data URI."""
    for ref in refs:
        data_uri = data_uri_for(ref)
        ref.elem.set("href", data_uri)
        ref.elem.set(XLINK_HREF, data_uri)


def metadata_for_file(path: Path) -> dict[str, object]:
    """Return cache metadata for one local dependency file."""
    stat_result = path.stat()
    return {
        "path": str(path.resolve()),
        "mtime_ns": stat_result.st_mtime_ns,
        "size": stat_result.st_size,
    }


def expected_cache_metadata(
    source: Path,
    refs: list[SvgImageRef],
    pmt_version: str,
) -> dict[str, object]:
    """Build cache metadata that changes when SVG or child images change."""
    return {
        "version": CACHE_METADATA_VERSION,
        "source": metadata_for_file(source),
        "resources": [
            {
                "href": ref.href,
                "mime_type": ref.mime_type,
                **metadata_for_file(ref.source),
            }
            for ref in refs
        ],
        "pmt_version": pmt_version,
    }


def cache_metadata_path(target: Path) -> Path:
    """Return the sidecar metadata path used to validate a generated SVG."""
    return target.with_suffix(target.suffix + ".meta.json")


def cache_metadata_matches(target: Path, expected: dict[str, object]) -> bool:
    """Return True only when the self-contained SVG cache is current."""
    metadata_path = cache_metadata_path(target)
    if not target.exists() or not metadata_path.exists():
        return False
    try:
        actual = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log_debug(f"[svg-embed] Ignoring stale cache metadata {metadata_path}: {exc}")
        return False
    return actual == expected


def write_cache_metadata(target: Path, metadata: dict[str, object]) -> None:
    """Write sidecar metadata so SVG or child-image changes invalidate cache."""
    cache_metadata_path(target).write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_embedded_svg(tree: etree._ElementTree, target: Path) -> None:
    """Write the self-contained SVG cache file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(
        etree.tostring(
            tree,
            encoding="utf-8",
            xml_declaration=True,
        )
    )


def ensure_embedded_svg(
    source: Path,
    target: Path,
    pmt_version: str,
) -> EmbeddedSvg | None:
    """Create or reuse a self-contained SVG cache file for one SVG source."""
    tree = parse_svg(source)
    refs = collect_image_refs(tree, source)
    if not refs:
        return None

    # Word cannot render an SVG stored as a data URI inside another SVG, so
    # these composed figures must continue through the following PNG filter.
    requires_png_fallback = any(ref.source.suffix.lower() in SVG_SUFFIXES for ref in refs)

    expected_metadata = expected_cache_metadata(source, refs, pmt_version)
    if cache_metadata_matches(target, expected_metadata):
        REUSED.add(target)
        log_debug(f"[svg-embed] Reusing {target}")
        return EmbeddedSvg(target, requires_png_fallback)

    embed_refs(refs)
    write_embedded_svg(tree, target)
    write_cache_metadata(target, expected_metadata)
    EMBEDDED.add(target)
    log_debug(f"[svg-embed] Embedded {len(refs)} child image(s): {source} -> {target}")
    return EmbeddedSvg(target, requires_png_fallback)


def rewrite_image(
    elem: pf.Image,
    base_dirs: list[Path],
    output_root: Path,
    pmt_version: str,
) -> pf.Image | None:
    """Rewrite one local SVG image URL to its self-contained SVG cache path."""
    path_text = path_from_url(elem.url)
    if path_text is None or not is_svg_path(path_text):
        return None

    source = resolve_source_path(path_text, base_dirs)
    if source is None:
        SKIPPED.add(elem.url)
        log_warning(f"[WARN] SVG image not found, leaving unchanged: {elem.url}")
        return None

    embedded = ensure_embedded_svg(
        source,
        output_path_for(source, output_root, base_dirs),
        pmt_version,
    )
    if embedded is None:
        return None
    elem.url = embedded.path.as_posix()
    if embedded.requires_png_fallback:
        elem.attributes["to-png"] = "true"
        log_debug(f"[svg-embed] Marked {source} for PNG fallback because it embeds an SVG child image")
    return elem


def action(elem: pf.Element, doc: pf.Doc) -> pf.Element | None:
    """Panflute action that rewrites SVG images to self-contained SVG caches."""
    if not isinstance(elem, pf.Image) or not doc.pmt_svg_embed_images:
        return None
    return rewrite_image(
        elem,
        doc.pmt_svg_embed_base_dirs,
        doc.pmt_svg_embed_output_root,
        doc.pmt_svg_embed_pmt_version,
    )


def prepare(doc: pf.Doc) -> None:
    """Load conversion settings from the build process environment."""
    doc.pmt_svg_embed_images = parse_bool_value(os.getenv("PMT_SVG_EMBED_IMAGES"))
    doc.pmt_svg_embed_base_dirs = configured_base_dirs()
    doc.pmt_svg_embed_output_root = Path(
        os.getenv("PMT_SVG_EMBED_DIR", "tmp/svg-embedded")
    ).resolve()
    doc.pmt_svg_embed_pmt_version = os.getenv("PMT_SVG_EMBED_PMT_VERSION", "unknown")


def finalize(doc: pf.Doc) -> None:
    """Report an INFO-level summary after all images have been inspected."""
    total = len(EMBEDDED) + len(REUSED)
    if total:
        log_debug(f"[svg-embed] Self-contained SVG cache ready: {total} file(s)")
    if SKIPPED:
        log_warning(f"[WARN] SVG child image embedding skipped for {len(SKIPPED)} file(s)")


def main(doc: pf.Doc | None = None) -> pf.Doc:
    """Run the SVG child-image embedding Pandoc filter."""
    return pf.run_filter(action, prepare=prepare, finalize=finalize, doc=doc)


if __name__ == "__main__":
    main()
