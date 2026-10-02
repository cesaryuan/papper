"""Read hidden LaTeX markers and replace their bound OMML nodes in DOCX."""

import copy
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from ..runtime.logging import log_debug

from .docx_ole import (
    MathTypeTemplate,
    append_relationship,
    build_mathtype_template,
    collect_parent_map,
    ensure_default_content_type,
    find_next_numeric_id,
    make_object_run,
    qn,
    read_xml,
    serialize_xml,
    top_level_omml_nodes,
    CONTENT_OLE,
    CONTENT_WMF,
    REL_IMAGE,
    REL_OLE,
    NS,
)

from .ole_parts import EquationRequest, GeneratedEquation, MathTypeMathStyle


MATH_TYPE_MARKER_PREFIX = "MTLATEX:"


@dataclass
class MarkedFormulaBinding:
    """A hidden LaTeX marker bound to the OMML node that immediately follows it."""

    latex: str
    kind: MathTypeMathStyle
    marker_run: ET.Element
    omml_node: ET.Element


@dataclass(frozen=True)
class StyleFontContext:
    """Resolved DOCX font-size hints needed for MathType generation."""

    doc_default_half_points: int | None
    paragraph_style_sizes: dict[str, int | None]
    table_style_sizes: dict[str, int | None]


@dataclass(frozen=True)
class FontSizeResolution:
    """Resolved font size plus the winning source in the lookup chain."""

    font_size_pt: float | None
    source: str


def marker_from_run(run: ET.Element) -> tuple[str, str] | None:
    """Return (kind, latex) from a hidden MathType marker run, if present."""
    text = "".join(node.text or "" for node in run.findall(".//w:t", NS))
    if not text.startswith(MATH_TYPE_MARKER_PREFIX):
        return None
    remainder = text[len(MATH_TYPE_MARKER_PREFIX) :]
    if ":" not in remainder:
        raise ValueError(f"Malformed MathType marker: {text!r}")
    kind, latex = remainder.split(":", 1)
    if kind not in {"inline", "display"}:
        raise ValueError(f"Unknown MathType marker kind: {kind!r}")
    return kind, latex.strip()


def iter_marker_and_omml_signals(root: ET.Element):
    """Yield hidden marker runs and top-level OMML nodes in document order."""
    top_level_nodes = set(top_level_omml_nodes(root))

    def walk(node: ET.Element):
        if node in top_level_nodes:
            yield "omml", node
            return
        if node.tag == qn("w", "r") and marker_from_run(node) is not None:
            yield "marker", node
            return
        for child in list(node):
            yield from walk(child)

    yield from walk(root)


def find_marked_formula_bindings(root: ET.Element) -> list[MarkedFormulaBinding]:
    """Bind each hidden LaTeX marker to the next top-level OMML node."""
    bindings: list[MarkedFormulaBinding] = []
    pending_marker: tuple[str, str, ET.Element] | None = None

    for signal_type, node in iter_marker_and_omml_signals(root):
        if signal_type == "marker":
            marker = marker_from_run(node)
            if marker is None:
                continue
            if pending_marker is not None:
                _kind, _latex, marker_run = pending_marker
                raise ValueError(f"MathType marker was not followed by OMML: {marker_from_run(marker_run)!r}")
            kind, latex = marker
            pending_marker = (kind, latex, node)
            continue

        if pending_marker is None:
            continue
        kind, latex, marker_run = pending_marker
        bindings.append(MarkedFormulaBinding(latex=latex, kind=kind, marker_run=marker_run, omml_node=node))
        pending_marker = None

    if pending_marker is not None:
        _kind, _latex, marker_run = pending_marker
        raise ValueError(f"Trailing MathType marker was not followed by OMML: {marker_from_run(marker_run)!r}")
    return bindings


def half_points_from_rpr(rpr: ET.Element | None, *, include_complex_script: bool = True) -> int | None:
    """Read Word's half-point font size from a run-properties element.

    MathType formulas are generated from Latin math text, so callers can ignore
    `w:szCs` when a complex-script-only style should not override inherited
    Western text size.
    """
    if rpr is None:
        return None
    tag_names = ("sz", "szCs") if include_complex_script else ("sz",)
    for tag_name in tag_names:
        node = rpr.find(f"w:{tag_name}", NS)
        if node is None:
            continue
        value = node.get(qn("w", "val"))
        if value is None:
            continue
        try:
            return int(value)
        except ValueError:
            continue
    return None


def build_style_font_context(styles_root: ET.Element) -> StyleFontContext:
    """Resolve explicit font sizes from DOCX style definitions.

    The goal is pragmatic rather than a full Word style engine: detect the
    explicit size contributed by paragraph/table styles, and fall back to
    docDefaults only after table styles are checked. That order matters because
    table body text often inherits a smaller size from the table style itself.
    """
    styles_by_id = {
        style.get(qn("w", "styleId")): style
        for style in styles_root.findall("w:style", NS)
        if style.get(qn("w", "styleId"))
    }
    cache: dict[str, int | None] = {}

    def resolve_style_size(style_id: str | None, seen: set[str] | None = None) -> int | None:
        if not style_id:
            return None
        if style_id in cache:
            return cache[style_id]
        if seen is None:
            seen = set()
        if style_id in seen:
            return None
        seen.add(style_id)

        style = styles_by_id.get(style_id)
        if style is None:
            cache[style_id] = None
            return None

        size = half_points_from_rpr(style.find("w:rPr", NS), include_complex_script=False)
        if size is not None:
            cache[style_id] = size
            return size

        based_on = style.find("w:basedOn", NS)
        resolved = resolve_style_size(based_on.get(qn("w", "val")) if based_on is not None else None, seen)
        cache[style_id] = resolved
        return resolved

    paragraph_style_sizes: dict[str, int | None] = {}
    table_style_sizes: dict[str, int | None] = {}
    for style_id, style in styles_by_id.items():
        style_type = style.get(qn("w", "type"))
        if style_type == "paragraph":
            paragraph_style_sizes[style_id] = resolve_style_size(style_id)
        elif style_type == "table":
            table_style_sizes[style_id] = resolve_style_size(style_id)

    doc_default_half_points = half_points_from_rpr(styles_root.find("w:docDefaults/w:rPrDefault/w:rPr", NS))
    return StyleFontContext(
        doc_default_half_points=doc_default_half_points,
        paragraph_style_sizes=paragraph_style_sizes,
        table_style_sizes=table_style_sizes,
    )


def ancestor_with_tag(parent_map: dict[ET.Element, ET.Element], node: ET.Element, tag: str) -> ET.Element | None:
    """Walk upward until an ancestor with the requested tag is found."""
    current = node
    while True:
        current = parent_map.get(current)
        if current is None:
            return None
        if current.tag == tag:
            return current


def paragraph_neighbor_size_half_points(paragraph: ET.Element, marker_run: ET.Element) -> int | None:
    """Look for explicit run font sizes next to the hidden marker run."""
    children = list(paragraph)
    try:
        marker_index = children.index(marker_run)
    except ValueError:
        return None

    offsets = range(1, len(children))
    for offset in offsets:
        for index in (marker_index - offset, marker_index + offset):
            if index < 0 or index >= len(children):
                continue
            child = children[index]
            if child.tag != qn("w", "r"):
                continue
            size = half_points_from_rpr(child.find("w:rPr", NS), include_complex_script=False)
            if size is not None:
                return size
    return None


def truncate_latex_for_log(latex: str, limit: int = 48) -> str:
    """Shorten LaTeX in logs so one line still stays readable."""
    compact = " ".join(latex.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def binding_font_size_resolution(
    binding: MarkedFormulaBinding,
    parent_map: dict[ET.Element, ET.Element],
    font_context: StyleFontContext,
) -> FontSizeResolution:
    """Infer the surrounding Word font size for a formula binding.

    This intentionally mirrors the most common body-vs-table cases in the
    generated DOCX: direct formatting first, then paragraph style, then table
    style, then document defaults.
    """
    size_half_points = half_points_from_rpr(binding.marker_run.find("w:rPr", NS), include_complex_script=False)
    source = "marker_run.rPr"

    paragraph = ancestor_with_tag(parent_map, binding.marker_run, qn("w", "p"))
    if size_half_points is None and paragraph is not None:
        size_half_points = paragraph_neighbor_size_half_points(paragraph, binding.marker_run)
        if size_half_points is not None:
            source = "neighbor_run.rPr"
    if size_half_points is None and paragraph is not None:
        size_half_points = half_points_from_rpr(paragraph.find("w:pPr/w:rPr", NS), include_complex_script=False)
        if size_half_points is not None:
            source = "paragraph.pPr.rPr"
    if size_half_points is None and paragraph is not None:
        p_style = paragraph.find("w:pPr/w:pStyle", NS)
        if p_style is not None:
            p_style_id = p_style.get(qn("w", "val"))
            size_half_points = font_context.paragraph_style_sizes.get(p_style_id)
            if size_half_points is not None:
                source = f"paragraph_style:{p_style_id}"

    if size_half_points is None:
        table = ancestor_with_tag(parent_map, binding.marker_run, qn("w", "tbl"))
        if table is not None:
            tbl_style = table.find("w:tblPr/w:tblStyle", NS)
            if tbl_style is not None:
                tbl_style_id = tbl_style.get(qn("w", "val"))
                size_half_points = font_context.table_style_sizes.get(tbl_style_id)
                if size_half_points is not None:
                    source = f"table_style:{tbl_style_id}"

    if size_half_points is None:
        size_half_points = font_context.doc_default_half_points
        if size_half_points is not None:
            source = "docDefaults.rPrDefault"

    if size_half_points is None or size_half_points <= 0:
        return FontSizeResolution(font_size_pt=None, source="unresolved")
    # Word stores font size in half-points, so 21 means 10.5 pt.
    return FontSizeResolution(font_size_pt=size_half_points / 2.0, source=source)


def log_font_size_resolution(index: int, binding: MarkedFormulaBinding, resolution: FontSizeResolution) -> None:
    """Print one concise line showing where a formula's final font size came from."""
    size_text = f"{resolution.font_size_pt:g}pt" if resolution.font_size_pt is not None else "None"
    log_debug(
        "[mathtype] font-size"
        f" eq={index}"
        f" kind={binding.kind}"
        f" size={size_text}"
        f" source={resolution.source}"
        f" latex={truncate_latex_for_log(binding.latex)!r}"
    )


def extract_marked_equation_requests(source: Path) -> list[EquationRequest]:
    """Read LaTeX plus surrounding font-size hints from a marker-bearing DOCX."""
    with zipfile.ZipFile(source) as archive:
        document = read_xml(archive, "word/document.xml")
        styles = read_xml(archive, "word/styles.xml")

    bindings = find_marked_formula_bindings(document)
    parent_map = collect_parent_map(document)
    font_context = build_style_font_context(styles)
    requests: list[EquationRequest] = []
    for index, binding in enumerate(bindings, start=1):
        resolution = binding_font_size_resolution(binding, parent_map, font_context)
        log_font_size_resolution(index, binding, resolution)
        requests.append(
            EquationRequest(
                latex=binding.latex,
                font_size_pt=resolution.font_size_pt,
                # Preserve the marker context so RaTeX does not render inline
                # formulas with display-style fractions, operators, or limits.
                math_style=binding.kind,
            )
        )
    return requests


def extract_marked_latex_values(source: Path) -> list[str]:
    """Read LaTeX values from hidden marker runs in a DOCX document part."""
    return [request.latex for request in extract_marked_equation_requests(source)]


def remove_marker_run(parent_map: dict[ET.Element, ET.Element], marker_run: ET.Element, index: int) -> None:
    """Remove a marker run and its now-empty paragraph, if Pandoc emitted one."""
    marker_parent = parent_map.get(marker_run)
    if marker_parent is None:
        raise ValueError(f"marker has no parent for equation {index}")
    marker_parent.remove(marker_run)

    # Display math markers can live in their own hidden paragraph. After the run
    # is removed, delete that empty paragraph so the final DOCX has no blank line.
    if marker_parent.tag == qn("w", "p"):
        non_pr_children = [child for child in list(marker_parent) if child.tag != qn("w", "pPr")]
        if not non_pr_children:
            paragraph_parent = parent_map.get(marker_parent)
            if paragraph_parent is not None:
                paragraph_parent.remove(marker_parent)


def replace_marked_omml_with_generated(source: Path, target: Path, equations: list[GeneratedEquation | None]) -> int:
    """Replace converted formulas with MathType objects, retaining OMML for failed conversions."""
    # Build the Word-side object shell in code so the real converter no longer
    # depends on a hand-made sample DOCX being present on disk.
    template = build_mathtype_template()
    target.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(source) as in_zip:
        document = read_xml(in_zip, "word/document.xml")
        rels = read_xml(in_zip, "word/_rels/document.xml.rels")
        content_types = read_xml(in_zip, "[Content_Types].xml")
        existing_names = set(in_zip.namelist())

        bindings = find_marked_formula_bindings(document)
        if len(bindings) != len(equations):
            raise ValueError(f"math count mismatch: markers={len(bindings)}, generated={len(equations)}")

        existing_rids = [rel.get("Id", "") for rel in rels.findall("rel:Relationship", NS)]
        rid_counter = find_next_numeric_id(existing_rids, "rId")
        parent_map = collect_parent_map(document)
        added_parts: dict[str, bytes] = {}
        replaced = 0

        for index, (binding, equation) in enumerate(zip(bindings, equations), start=1):
            if equation is None:
                # A failed conversion leaves the original visible formula, without its hidden marker.
                remove_marker_run(parent_map, binding.marker_run, index)
                continue
            if binding.latex != equation.latex:
                raise ValueError(f"marker/equation mismatch at {index}: {binding.latex!r} != {equation.latex!r}")

            image_rid = f"rId{next(rid_counter)}"
            ole_rid = f"rId{next(rid_counter)}"
            image_name = f"word/media/mathtype_formula_{index}.wmf"
            ole_name = f"word/embeddings/mathtype_formula_{index}.bin"

            item_template = MathTypeTemplate(
                object_element=copy.deepcopy(template.object_element),
                ole_bytes=equation.ole_path.read_bytes(),
                image_bytes=equation.wmf_path.read_bytes(),
                baseline_from_bottom_pt=equation.baseline_from_bottom_pt,
            )

            remove_marker_run(parent_map, binding.marker_run, index)

            parent = parent_map[binding.omml_node]
            child_index = list(parent).index(binding.omml_node)
            parent.remove(binding.omml_node)
            parent.insert(
                child_index,
                make_object_run(
                    item_template,
                    image_rid,
                    ole_rid,
                    index,
                    # Display equations are standalone lines; their visual
                    # placement must not inherit an inline baseline offset.
                    apply_position=equation.math_style == "inline",
                ),
            )
            append_relationship(rels, image_rid, REL_IMAGE, image_name.removeprefix("word/"))
            append_relationship(rels, ole_rid, REL_OLE, ole_name.removeprefix("word/"))
            added_parts[image_name] = item_template.image_bytes
            added_parts[ole_name] = item_template.ole_bytes
            replaced += 1

        ensure_default_content_type(content_types, "bin", CONTENT_OLE)
        ensure_default_content_type(content_types, "wmf", CONTENT_WMF)
        replacements = {
            "word/document.xml": serialize_xml(document),
            "word/_rels/document.xml.rels": serialize_xml(rels),
            "[Content_Types].xml": serialize_xml(content_types),
        }

        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as out_zip:
            for item in in_zip.infolist():
                if item.filename in replacements or item.filename in added_parts:
                    continue
                out_zip.writestr(item, in_zip.read(item.filename))
            for name, data in replacements.items():
                out_zip.writestr(name, data)
            for name, data in added_parts.items():
                if name in existing_names:
                    raise ValueError(f"Generated part already exists: {name}")
                out_zip.writestr(name, data)

    return replaced


def inspect_docx(path: Path) -> None:
    """Report DOCX package markers after injection."""
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        document_xml = archive.read("word/document.xml")
        rels_xml = archive.read("word/_rels/document.xml.rels")
        embeddings = [name for name in names if name.startswith("word/embeddings/")]
        previews = [name for name in names if name.startswith("word/media/mathtype_formula_")]
        log_debug(f"[mathtype] docx={path}")
        log_debug(f"[mathtype] embeddings={len(embeddings)}, Equation.DSMT4={document_xml.count(b'Equation.DSMT4')}")
        log_debug(f"[mathtype] generated WMF previews={len(previews)}")
        log_debug(f"[mathtype] oMath tokens={document_xml.count(b'<m:oMath') + document_xml.count(b'<m:oMathPara')}")
        log_debug(f"[mathtype] MathType markers={document_xml.count(MATH_TYPE_MARKER_PREFIX.encode('utf-8'))}")
        log_debug(f"[mathtype] ole relationships={rels_xml.count(b'/oleObject')}")
