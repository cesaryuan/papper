"""Low-level DOCX package helpers for inserting MathType OLE objects."""

import copy
import itertools
import re
import struct
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "v": "urn:schemas-microsoft-com:vml",
    "o": "urn:schemas-microsoft-com:office:office",
    "w14": "http://schemas.microsoft.com/office/word/2010/wordml",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
}

REL_IMAGE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
REL_OLE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/oleObject"
CONTENT_OLE = "application/vnd.openxmlformats-officedocument.oleObject"
CONTENT_WMF = "image/x-wmf"
PLACEABLE_WMF_KEY = 0x9AC6CDD7
BASELINE_REFERENCE_HEIGHT_PT = 15.85
BASELINE_REFERENCE_POSITION_HALF_POINTS = -10


for prefix, uri in NS.items():
    if prefix not in {"rel", "ct"}:
        ET.register_namespace(prefix, uri)


@dataclass
class MathTypeTemplate:
    """Package parts needed to build a Word MathType OLE object."""

    object_element: ET.Element
    ole_bytes: bytes
    image_bytes: bytes
    baseline_from_bottom_pt: float | None = None


MATHTYPE_OBJECT_TEMPLATE_XML = f"""
<w:object
    xmlns:w="{NS['w']}"
    xmlns:r="{NS['r']}"
    xmlns:v="{NS['v']}"
    xmlns:o="{NS['o']}"
    xmlns:w14="{NS['w14']}"
    w:dxaOrig="240"
    w:dyaOrig="620"
    w14:anchorId="5FA61A83">
  <v:shapetype
      id="_x0000_t75"
      coordsize="21600,21600"
      o:spt="75"
      o:preferrelative="t"
      path="m@4@5l@4@11@9@11@9@5xe"
      filled="f"
      stroked="f">
    <v:stroke joinstyle="miter" />
    <v:formulas>
      <v:f eqn="if lineDrawn pixelLineWidth 0" />
      <v:f eqn="sum @0 1 0" />
      <v:f eqn="sum 0 0 @1" />
      <v:f eqn="prod @2 1 2" />
      <v:f eqn="prod @3 21600 pixelWidth" />
      <v:f eqn="prod @3 21600 pixelHeight" />
      <v:f eqn="sum @0 0 1" />
      <v:f eqn="prod @6 1 2" />
      <v:f eqn="prod @7 21600 pixelWidth" />
      <v:f eqn="sum @8 21600 0" />
      <v:f eqn="prod @7 21600 pixelHeight" />
      <v:f eqn="sum @10 21600 0" />
    </v:formulas>
    <v:path o:extrusionok="f" gradientshapeok="t" o:connecttype="rect" />
    <o:lock v:ext="edit" aspectratio="t" />
  </v:shapetype>
  <v:shape id="_x0000_i1027" type="#_x0000_t75" style="width:18.85pt;height:15.85pt" o:ole="">
    <v:imagedata r:id="rIdPreview" o:title="" />
  </v:shape>
  <o:OLEObject
      Type="Embed"
      ProgID="Equation.DSMT4"
      ShapeID="_x0000_i1027"
      DrawAspect="Content"
      ObjectID="_1841993762"
      r:id="rIdObject" />
</w:object>
""".strip()


def qn(prefix: str, local: str) -> str:
    """Build a namespaced XML name."""
    return f"{{{NS[prefix]}}}{local}"


def read_xml(archive: zipfile.ZipFile, name: str) -> ET.Element:
    """Read and parse an XML part from a DOCX zip package."""
    return ET.fromstring(archive.read(name))


def serialize_xml(root: ET.Element) -> bytes:
    """Serialize an XML element with declaration."""
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def wmf_size_points(wmf_bytes: bytes) -> tuple[float, float] | None:
    """Return a placeable WMF preview size in points, or None if unavailable."""
    if len(wmf_bytes) < 22:
        return None

    key, _handle, left, top, right, bottom, inch, _reserved, _checksum = struct.unpack(
        "<I H h h h h H I H",
        wmf_bytes[:22],
    )
    if key != PLACEABLE_WMF_KEY or inch == 0:
        return None

    width = max(0, right - left) * 72 / inch
    height = max(0, bottom - top) * 72 / inch
    if width <= 0 or height <= 0:
        return None
    return width, height


def format_point_size(value: float) -> str:
    """Format a point value for a VML style declaration."""
    return f"{value:.2f}".rstrip("0").rstrip(".") + "pt"


def set_style_property(style: str, name: str, value: str) -> str:
    """Set one semicolon-separated VML style property while preserving others."""
    parts = [part.strip() for part in style.split(";") if part.strip()]
    prefix = f"{name}:"
    for index, part in enumerate(parts):
        if part.lower().startswith(prefix.lower()):
            parts[index] = f"{name}:{value}"
            break
    else:
        parts.append(f"{name}:{value}")
    return ";".join(parts)


def sync_shape_size_to_wmf(shape: ET.Element, wmf_bytes: bytes) -> None:
    """Update a cloned OLE shape to match its WMF preview size.

    This fixes the MathType probe bug where every inserted equation inherited
    the sample equation's VML display box, which squeezed valid WMF previews
    into a tiny fixed width in Word.
    """
    size = wmf_size_points(wmf_bytes)
    if size is None:
        return

    width, height = size
    style = shape.get("style", "")
    style = set_style_property(style, "width", format_point_size(width))
    style = set_style_property(style, "height", format_point_size(height))
    shape.set("style", style)


def sync_object_extent_to_wmf(obj: ET.Element, wmf_bytes: bytes) -> None:
    """Update Word's original OLE extent to match the WMF preview.

    Word can re-read `w:dxaOrig`/`w:dyaOrig` after a MathType edit. Keeping the
    template values here made edited equations reflow with stale dimensions.
    """
    size = wmf_size_points(wmf_bytes)
    if size is None:
        return

    width, height = size
    obj.set(qn("w", "dxaOrig"), str(max(1, round(width * 20))))
    obj.set(qn("w", "dyaOrig"), str(max(1, round(height * 20))))


def apply_run_position(run: ET.Element, position_half_points: int) -> None:
    """Set the Word run baseline offset used by MathType objects.

    MathType OLE equations in Word commonly carry w:position=-10; without it,
    generated objects can sit visibly above the intended baseline even when
    their WMF dimensions are correct.
    """
    rpr = run.find("w:rPr", NS)
    if rpr is None:
        rpr = ET.Element(qn("w", "rPr"))
        run.insert(0, rpr)

    position = rpr.find("w:position", NS)
    if position is None:
        position = ET.Element(qn("w", "position"))
        rpr.append(position)
    position.set(qn("w", "val"), str(position_half_points))


def mathtype_position_half_points(template: MathTypeTemplate) -> int:
    """Return Word's MathType baseline offset in half-points.

    Prefer the preview renderer's baseline distance when available. Word's
    w:position uses half-points, and a negative value lowers the object so the
    equation baseline, not the bottom of the preview box, aligns with the line.
    """
    if template.baseline_from_bottom_pt is not None and template.baseline_from_bottom_pt >= 0:
        return -round(template.baseline_from_bottom_pt * 2)

    # Fallback for structural probes that clone a sample object without fresh
    # MathType metadata. The real conversion path should provide the baseline.
    size = wmf_size_points(template.image_bytes)
    if size is None:
        return BASELINE_REFERENCE_POSITION_HALF_POINTS

    _width, height = size
    scale = abs(BASELINE_REFERENCE_POSITION_HALF_POINTS) / BASELINE_REFERENCE_HEIGHT_PT
    return -max(1, round(height * scale))


def find_next_numeric_id(existing: list[str], prefix: str) -> itertools.count:
    """Return a counter starting after the highest numeric suffix in existing IDs."""
    max_id = 0
    pattern = re.compile(rf"^{re.escape(prefix)}(\d+)$")
    for value in existing:
        match = pattern.match(value)
        if match:
            max_id = max(max_id, int(match.group(1)))
    return itertools.count(max_id + 1)


def build_mathtype_template() -> MathTypeTemplate:
    """Build a minimal MathType Word object template without a sample DOCX.

    Word only needs a valid `w:object` shell with VML preview plumbing plus the
    later-added relationship IDs. The actual equation payload and preview WMF
    bytes come from MathType's generated outputs for each formula.
    """
    return MathTypeTemplate(
        object_element=ET.fromstring(MATHTYPE_OBJECT_TEMPLATE_XML),
        ole_bytes=b"",
        image_bytes=b"",
    )


def extract_mathtype_template(sample_docx: Path) -> MathTypeTemplate:
    """Extract the first MathType OLE object, preview image, and object XML."""
    with zipfile.ZipFile(sample_docx) as archive:
        document = read_xml(archive, "word/document.xml")
        rels = read_xml(archive, "word/_rels/document.xml.rels")
        rel_by_id = {rel.get("Id"): rel.get("Target") for rel in rels}

        ole_object = document.find(".//w:object", NS)
        if ole_object is None:
            raise ValueError(f"No w:object found in sample DOCX: {sample_docx}")

        image = ole_object.find(".//v:imagedata", NS)
        ole = ole_object.find(".//o:OLEObject", NS)
        if image is None or ole is None:
            raise ValueError("Sample object is missing v:imagedata or o:OLEObject")

        image_target = rel_by_id[image.get(qn("r", "id"))]
        ole_target = rel_by_id[ole.get(qn("r", "id"))]

        return MathTypeTemplate(
            object_element=copy.deepcopy(ole_object),
            ole_bytes=archive.read(f"word/{ole_target}"),
            image_bytes=archive.read(f"word/{image_target}"),
        )


def collect_parent_map(root: ET.Element) -> dict[ET.Element, ET.Element]:
    """Build a child-to-parent map because ElementTree does not expose parents."""
    return {child: parent for parent in root.iter() for child in parent}


def top_level_omml_nodes(root: ET.Element) -> list[ET.Element]:
    """Return OMML nodes that can be replaced without also replacing nested math."""
    parent_map = collect_parent_map(root)
    result: list[ET.Element] = []
    for node in root.iter():
        if node.tag not in {qn("m", "oMath"), qn("m", "oMathPara")}:
            continue
        parent = parent_map.get(node)
        if parent is not None and parent.tag == qn("m", "oMathPara"):
            continue
        result.append(node)
    return result


def make_object_run(
    template: MathTypeTemplate,
    image_rid: str,
    ole_rid: str,
    index: int,
    apply_position: bool = True,
) -> ET.Element:
    """Create a Word run containing a cloned MathType OLE object."""
    run = ET.Element(qn("w", "r"))
    if apply_position:
        apply_run_position(run, mathtype_position_half_points(template))
    obj = copy.deepcopy(template.object_element)

    shape = obj.find(".//v:shape", NS)
    image = obj.find(".//v:imagedata", NS)
    ole = obj.find(".//o:OLEObject", NS)
    if shape is None or image is None or ole is None:
        raise ValueError("Template object is missing shape/image/OLE child")

    shape_id = f"_x0000_i{3000 + index}"
    shape.set("id", shape_id)
    sync_shape_size_to_wmf(shape, template.image_bytes)
    sync_object_extent_to_wmf(obj, template.image_bytes)
    image.set(qn("r", "id"), image_rid)
    ole.set(qn("r", "id"), ole_rid)
    ole.set("ShapeID", shape_id)
    # Word only requires ObjectID uniqueness inside the document.
    ole.set("ObjectID", f"_{1841932809 + index}")

    run.append(obj)
    return run


def append_relationship(rels_root: ET.Element, rid: str, rel_type: str, target: str) -> None:
    """Append a document relationship for a newly added part."""
    rel = ET.Element(f"{{{NS['rel']}}}Relationship")
    rel.set("Id", rid)
    rel.set("Type", rel_type)
    rel.set("Target", target)
    rels_root.append(rel)


def ensure_default_content_type(types_root: ET.Element, extension: str, content_type: str) -> None:
    """Ensure the DOCX content type table can resolve a newly added part extension."""
    for default in types_root.findall("ct:Default", NS):
        if default.get("Extension") == extension:
            default.set("ContentType", content_type)
            return
    default = ET.Element(f"{{{NS['ct']}}}Default")
    default.set("Extension", extension)
    default.set("ContentType", content_type)
    types_root.insert(0, default)


def replace_omml_with_template(source: Path, sample: Path, target: Path, limit: int) -> int:
    """Create a DOCX where the first OMML nodes are replaced by sample MathType OLE objects."""
    template = extract_mathtype_template(sample)
    target.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(source) as in_zip:
        document = read_xml(in_zip, "word/document.xml")
        rels = read_xml(in_zip, "word/_rels/document.xml.rels")
        content_types = read_xml(in_zip, "[Content_Types].xml")
        existing_names = set(in_zip.namelist())

        existing_rids = [rel.get("Id", "") for rel in rels.findall("rel:Relationship", NS)]
        rid_counter = find_next_numeric_id(existing_rids, "rId")
        parent_map = collect_parent_map(document)

        replaced = 0
        added_parts: dict[str, bytes] = {}
        for node in top_level_omml_nodes(document):
            if replaced >= limit:
                break
            parent = parent_map[node]
            child_index = list(parent).index(node)

            replacement_index = replaced + 1
            image_rid = f"rId{next(rid_counter)}"
            ole_rid = f"rId{next(rid_counter)}"
            image_name = f"word/media/mathtype_probe_{replacement_index}.wmf"
            ole_name = f"word/embeddings/mathtype_probe_{replacement_index}.bin"

            parent.remove(node)
            parent.insert(child_index, make_object_run(template, image_rid, ole_rid, replacement_index))
            append_relationship(rels, image_rid, REL_IMAGE, image_name.removeprefix("word/"))
            append_relationship(rels, ole_rid, REL_OLE, ole_name.removeprefix("word/"))
            added_parts[image_name] = template.image_bytes
            added_parts[ole_name] = template.ole_bytes
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
