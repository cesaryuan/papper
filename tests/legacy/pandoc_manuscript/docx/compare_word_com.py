#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "lxml>=4.9.0",
#   "pywin32>=306; platform_system == 'Windows'",
# ]
# ///
"""Compare two DOCX files with Microsoft Word COM and save tracked changes.

This Windows-only script delegates the comparison to Word's own Compare engine.
It requires Microsoft Word and pywin32, but it produces the closest result to
using Word's Review > Compare UI by hand.
"""

import argparse
import hashlib
import sys
import tempfile
import zipfile
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lxml import etree

from ..runtime.logging import log_error, log_info, log_warning


WD_ALERTS_NONE = 0
WD_COMPARE_DESTINATION_NEW = 2
WD_DO_NOT_SAVE_CHANGES = 0
WD_FORMAT_XML_DOCUMENT = 12
WD_GRANULARITY_CHAR_LEVEL = 0
WD_GRANULARITY_WORD_LEVEL = 1
NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "o": "urn:schemas-microsoft-com:office:office",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
}
MATH_PLACEHOLDER_PREFIX = "[[MATHTYPE_FORMULA_"
MATH_PLACEHOLDER_SUFFIX = "]]"


@dataclass(frozen=True)
class MathTypeObject:
    """A MathType object run from the revised DOCX and its relationship context."""

    run: etree._Element
    source_rels: dict[str, tuple[str, str]]
    index: int


def print_info(message: str) -> None:
    """Print an informational progress message."""
    log_info(f"[INFO] {message}")


def print_warning(message: str) -> None:
    """Print a warning for non-fatal comparison limitations."""
    log_warning(f"[WARN] {message}")


def print_error(message: str) -> None:
    """Print an error message to stderr."""
    log_error(f"[ERROR] {message}")


def qn(prefix: str, local: str) -> str:
    """Return a Clark-notation XML name for the configured namespace prefix."""
    return f"{{{NS[prefix]}}}{local}"


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the Word COM compare helper."""
    parser = argparse.ArgumentParser(
        description=(
            "Use Microsoft Word COM to compare two DOCX files and save a DOCX "
            "containing Word tracked changes."
        )
    )
    parser.add_argument("old_docx", help="Older/original DOCX file")
    parser.add_argument("new_docx", help="Newer/revised DOCX file")
    parser.add_argument("output_docx", help="Output DOCX file with Word comparison revisions")
    parser.add_argument(
        "--author",
        default="Pandoc Manuscript Diff",
        help="Revision author shown by Word for comparison changes",
    )
    parser.add_argument(
        "--allow-overwrite",
        action="store_true",
        help="Allow replacing an existing output file",
    )
    parser.add_argument(
        "--visible",
        action="store_true",
        help="Show Word while comparing; useful when debugging Word prompts",
    )
    parser.add_argument(
        "--char-level",
        action="store_true",
        help="Compare at character granularity instead of Word's word-level default",
    )
    parser.add_argument(
        "--ignore-formatting",
        action="store_true",
        help="Ignore formatting-only differences during comparison",
    )
    parser.add_argument(
        "--ignore-moves",
        action="store_true",
        help="Do not ask Word to detect moved text",
    )
    parser.add_argument(
        "--keep-word-open",
        action="store_true",
        help="Leave Word open after saving the result for manual inspection",
    )
    return parser.parse_args()


def validate_docx_path(path: str | Path, label: str) -> Path:
    """Validate that a DOCX path exists and looks like a file."""
    candidate = Path(path)
    if not candidate.exists():
        raise FileNotFoundError(f"{label} not found: {candidate}")
    if not candidate.is_file():
        raise ValueError(f"{label} is not a file: {candidate}")
    if candidate.suffix.lower() != ".docx":
        raise ValueError(f"{label} must use a .docx extension: {candidate}")
    return candidate.resolve()


def prepare_output_path(path: str | Path, allow_overwrite: bool) -> Path:
    """Validate and prepare an output DOCX path before Word writes it."""
    output = Path(path)
    if output.exists() and not allow_overwrite:
        raise FileExistsError(f"Output already exists; pass --allow-overwrite to replace it: {output}")
    if output.exists() and output.is_dir():
        raise ValueError(f"Output path is a directory: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    return output.resolve()


def read_zip_part(docx_path: Path, part_name: str) -> bytes:
    """Read one part from a DOCX zip package."""
    with zipfile.ZipFile(docx_path, "r") as package:
        return package.read(part_name)


def load_xml_part(docx_path: Path, part_name: str) -> etree._Element:
    """Load one XML part from a DOCX package."""
    parser = etree.XMLParser(remove_blank_text=False)
    return etree.fromstring(read_zip_part(docx_path, part_name), parser)


def serialize_xml(root: etree._Element) -> bytes:
    """Serialize an XML element for storage in a DOCX package."""
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def write_docx_copy_with_overrides(
    source_docx: Path,
    target_docx: Path,
    overrides: dict[str, bytes],
    extra_parts: dict[str, bytes] | None = None,
) -> None:
    """Copy a DOCX package while replacing or adding selected package parts."""
    extra_parts = extra_parts or {}
    with zipfile.ZipFile(source_docx, "r") as source, zipfile.ZipFile(target_docx, "w", zipfile.ZIP_DEFLATED) as target:
        written = set()
        for item in source.infolist():
            if item.filename in overrides:
                target.writestr(item, overrides[item.filename])
                written.add(item.filename)
            elif item.filename in extra_parts:
                target.writestr(item, extra_parts[item.filename])
                written.add(item.filename)
            else:
                target.writestr(item, source.read(item.filename))
        for name, data in {**overrides, **extra_parts}.items():
            if name not in written:
                target.writestr(name, data)


def rewrite_docx_in_place(
    docx_path: Path,
    overrides: dict[str, bytes],
    extra_parts: dict[str, bytes] | None = None,
) -> None:
    """Rewrite a DOCX package in place with selected replacement and added parts."""
    extra_parts = extra_parts or {}
    with zipfile.ZipFile(docx_path, "r") as source:
        entries = [(item, source.read(item.filename)) for item in source.infolist()]

    written = set()
    with zipfile.ZipFile(docx_path, "w", zipfile.ZIP_DEFLATED) as target:
        for item, data in entries:
            if item.filename in overrides:
                target.writestr(item, overrides[item.filename])
                written.add(item.filename)
            elif item.filename in extra_parts:
                target.writestr(item, extra_parts[item.filename])
                written.add(item.filename)
            else:
                target.writestr(item, data)
        for name, data in {**overrides, **extra_parts}.items():
            if name not in written:
                target.writestr(name, data)


def is_mathtype_run(run: etree._Element) -> bool:
    """Return True when a Word run contains a MathType OLE equation object."""
    ole = run.find(".//o:OLEObject", NS)
    if ole is None:
        return False
    prog_id = ole.get("ProgID", "")
    return prog_id.startswith("Equation.") or "MathType" in prog_id


def placeholder_text(index: int) -> str:
    """Return the stable placeholder text used during Word comparison."""
    return f"{MATH_PLACEHOLDER_PREFIX}{index:05d}{MATH_PLACEHOLDER_SUFFIX}"


def placeholder_index(text: str) -> int | None:
    """Return a MathType placeholder's formula index, if the text is a placeholder."""
    if not text.startswith(MATH_PLACEHOLDER_PREFIX) or not text.endswith(MATH_PLACEHOLDER_SUFFIX):
        return None
    value = text[len(MATH_PLACEHOLDER_PREFIX) : -len(MATH_PLACEHOLDER_SUFFIX)]
    try:
        return int(value)
    except ValueError:
        return None


def make_placeholder_run(original_run: etree._Element, index: int) -> etree._Element:
    """Create a text run that temporarily stands in for a MathType object."""
    run = etree.Element(qn("w", "r"))
    rpr = original_run.find("w:rPr", NS)
    if rpr is not None:
        run.append(deepcopy(rpr))
    text = etree.SubElement(run, qn("w", "t"))
    text.text = placeholder_text(index)
    return run


def document_relationships(docx_path: Path) -> dict[str, tuple[str, str]]:
    """Return document relationship id to (type, target) mappings."""
    try:
        rels_root = load_xml_part(docx_path, "word/_rels/document.xml.rels")
    except KeyError:
        return {}
    result: dict[str, tuple[str, str]] = {}
    for rel in rels_root.findall("pr:Relationship", NS):
        rel_id = rel.get("Id")
        rel_type = rel.get("Type")
        target = rel.get("Target")
        target_mode = rel.get("TargetMode")
        if rel_id and rel_type and target and target_mode != "External":
            result[rel_id] = (rel_type, target)
    return result


def collect_mathtype_objects(docx_path: Path) -> list[MathTypeObject]:
    """Collect MathType object runs from a DOCX in document order."""
    root = load_xml_part(docx_path, "word/document.xml")
    rels = document_relationships(docx_path)
    objects = []
    for index, run in enumerate((run for run in root.findall(".//w:r", NS) if is_mathtype_run(run)), start=1):
        objects.append(MathTypeObject(run=deepcopy(run), source_rels=rels, index=index))
    return objects


def replace_mathtype_objects_with_placeholders(
    source_docx: Path,
    target_docx: Path,
    replace_indices: set[int] | None = None,
) -> int:
    """Write a temporary DOCX where MathType object runs are stable text placeholders.

    This avoids a Word Compare edge case where every regenerated MathType OLE
    object is treated as a deleted object plus a newly inserted object.
    """
    root = load_xml_part(source_docx, "word/document.xml")
    replacements = 0
    formula_index = 0
    for run in root.findall(".//w:r", NS):
        if not is_mathtype_run(run):
            continue
        formula_index += 1
        if replace_indices is not None and formula_index not in replace_indices:
            continue
        parent = run.getparent()
        if parent is None:
            continue
        child_index = parent.index(run)
        parent.remove(run)
        parent.insert(child_index, make_placeholder_run(run, formula_index))
        replacements += 1

    write_docx_copy_with_overrides(target_docx=target_docx, source_docx=source_docx, overrides={"word/document.xml": serialize_xml(root)})
    return replacements


def is_ole_relationship(rel_type: str, target: str) -> bool:
    """Return True when a relationship points to a MathType OLE payload."""
    source_part = source_part_name_from_target(target)
    return rel_type.endswith("/oleObject") or "embeddings/" in source_part or source_part.lower().endswith(".bin")


def mathtype_mtef_hashes(
    docx_path: Path,
    formula: MathTypeObject,
) -> tuple[tuple[str, str], ...]:
    """Return stable hashes for a formula's MTEF payloads.

    MathType OLE wrappers contain volatile header bytes. Hashing only the MTEF
    payload keeps unchanged formulas from appearing as object replacements.
    """
    hashes: list[tuple[str, str]] = []
    rid_attrs = formula.run.xpath(".//@r:id", namespaces=NS)
    for rid in rid_attrs:
        rel_info = formula.source_rels.get(rid)
        if rel_info is None:
            continue
        rel_type, target = rel_info
        if not is_ole_relationship(rel_type, target):
            continue
        source_part = source_part_name_from_target(target)
        try:
            data = read_zip_part(docx_path, source_part)
            data = mathtype_mtef_payload(mathtype_native_stream(data))
        except KeyError:
            digest = "<missing>"
        except ValueError as exc:
            print_warning(f"Formula {formula.index}: could not read MathType MTEF payload ({exc})")
            digest = "<missing-mtef>"
        else:
            digest = hashlib.sha256(data).hexdigest()
        hashes.append(("mtef", digest))
    return tuple(sorted(hashes))


def mathtype_native_stream(ole_bytes: bytes) -> bytes:
    """Return MathType's Equation Native stream from an OLE compound file.

    The outer OLE compound-file bytes can change even when the formula is the
    same, so this stream is a better binary proxy for the equation payload.
    """
    from ..mathtype.compound_file import CompoundFile

    return CompoundFile(ole_bytes).read_stream("Equation Native")


def mathtype_mtef_payload(native_stream: bytes) -> bytes:
    """Return the MTEF payload after MathType's 28-byte OLE native header.

    MathType stores Equation Native as a small OLE header followed by MTEF. The
    header contains reserved bytes that can change between generated objects, so
    formula-content comparisons should hash the payload after that header.
    """
    if len(native_stream) < 28:
        raise ValueError("Equation Native stream is shorter than the 28-byte MathType OLE header")
    header_size = int.from_bytes(native_stream[:2], byteorder="little")
    if header_size != 28:
        raise ValueError(f"unexpected MathType OLE header size: {header_size}")
    return native_stream[header_size:]


def unchanged_mathtype_indices(old_docx: Path, new_docx: Path) -> set[int]:
    """Return formula positions whose MTEF payloads are identical."""
    old_formulas = collect_mathtype_objects(old_docx)
    new_formulas = collect_mathtype_objects(new_docx)
    if len(old_formulas) != len(new_formulas):
        print_warning(
            "Old and new DOCX contain different MathType object counts; "
            "only matching formula positions can be binary-compared"
        )

    unchanged: set[int] = set()
    compared = 0
    changed = 0
    for old_formula, new_formula in zip(old_formulas, new_formulas):
        compared += 1
        old_hashes = mathtype_mtef_hashes(old_docx, old_formula)
        new_hashes = mathtype_mtef_hashes(new_docx, new_formula)
        if not old_hashes or not new_hashes:
            print_warning(f"Formula {old_formula.index}: MathType MTEF payload was not found")
            changed += 1
            continue
        if old_hashes == new_hashes:
            unchanged.add(old_formula.index)
        else:
            changed += 1
    print_info(
        "MathType MTEF comparison: "
        f"compared={compared}, unchanged={len(unchanged)}, changed_or_unknown={changed}"
    )
    return unchanged


def next_relationship_id(rels_root: etree._Element) -> int:
    """Return the next available numeric relationship id suffix."""
    max_id = 0
    for rel in rels_root.findall("pr:Relationship", NS):
        rel_id = rel.get("Id", "")
        if rel_id.startswith("rId"):
            try:
                max_id = max(max_id, int(rel_id[3:]))
            except ValueError:
                continue
    return max_id + 1


def unique_part_name(existing_names: set[str], base_name: str) -> str:
    """Return a package part name that does not collide with existing DOCX parts."""
    candidate = base_name
    stem, dot, suffix = base_name.rpartition(".")
    counter = 1
    while candidate in existing_names:
        candidate = f"{stem}_{counter}.{suffix}" if dot else f"{base_name}_{counter}"
        counter += 1
    existing_names.add(candidate)
    return candidate


def source_part_name_from_target(target: str) -> str:
    """Convert a document relationship target into a DOCX package part name."""
    normalized = target.lstrip("/")
    if normalized.startswith("word/"):
        return normalized
    return f"word/{normalized}"


def copy_related_parts_for_run(
    run: etree._Element,
    formula: MathTypeObject,
    revised_docx: Path,
    output_docx: Path,
    rels_root: etree._Element,
    existing_names: set[str],
    next_rid: int,
) -> tuple[etree._Element, dict[str, bytes], int]:
    """Copy a formula run's image/OLE parts into the comparison result package."""
    restored_run = deepcopy(run)
    extra_parts: dict[str, bytes] = {}
    rid_attrs = restored_run.xpath(".//@r:id", namespaces=NS)
    for old_rid in rid_attrs:
        rel_info = formula.source_rels.get(old_rid)
        if rel_info is None:
            continue
        rel_type, target = rel_info
        source_part = source_part_name_from_target(target)
        source_suffix = Path(source_part).suffix
        if "embeddings/" in source_part:
            target_part = unique_part_name(existing_names, f"word/embeddings/compare_mathtype_{next_rid}{source_suffix}")
            rel_target = target_part.removeprefix("word/")
        elif "media/" in source_part:
            target_part = unique_part_name(existing_names, f"word/media/compare_mathtype_{next_rid}{source_suffix}")
            rel_target = target_part.removeprefix("word/")
        else:
            target_part = unique_part_name(existing_names, f"word/compare_mathtype_{next_rid}{source_suffix}")
            rel_target = target_part.removeprefix("word/")

        try:
            extra_parts[target_part] = read_zip_part(revised_docx, source_part)
        except KeyError:
            print_warning(f"Could not copy MathType related part: {source_part}")
            continue

        new_rid = f"rId{next_rid}"
        next_rid += 1
        rel = etree.SubElement(rels_root, qn("pr", "Relationship"))
        rel.set("Id", new_rid)
        rel.set("Type", rel_type)
        rel.set("Target", rel_target)
        for node in restored_run.xpath(f'.//*[@r:id="{old_rid}"]', namespaces=NS):
            node.set(qn("r", "id"), new_rid)

    return restored_run, extra_parts, next_rid


def restore_mathtype_objects_from_revised_docx(output_docx: Path, revised_docx: Path) -> int:
    """Replace comparison placeholders with MathType objects from the revised DOCX."""
    formula_by_index = {formula.index: formula for formula in collect_mathtype_objects(revised_docx)}
    if not formula_by_index:
        print_warning("No MathType objects found in revised DOCX for restoration")
        return 0

    document_root = load_xml_part(output_docx, "word/document.xml")
    try:
        rels_root = load_xml_part(output_docx, "word/_rels/document.xml.rels")
    except KeyError:
        rels_root = etree.Element(qn("pr", "Relationships"))

    with zipfile.ZipFile(output_docx, "r") as package:
        existing_names = set(package.namelist())

    placeholders: list[tuple[int, etree._Element]] = []
    for run in document_root.findall(".//w:r", NS):
        text = "".join(node.text or "" for node in run.findall(".//w:t", NS))
        index = placeholder_index(text)
        if index is not None:
            placeholders.append((index, run))

    next_rid = next_relationship_id(rels_root)
    extra_parts: dict[str, bytes] = {}
    restored = 0
    for index, placeholder_run in placeholders:
        formula = formula_by_index.get(index)
        if formula is None:
            print_warning(f"Could not restore MathType placeholder {index}: no revised formula at that position")
            continue
        restored_run, copied_parts, next_rid = copy_related_parts_for_run(
            run=formula.run,
            formula=formula,
            revised_docx=revised_docx,
            output_docx=output_docx,
            rels_root=rels_root,
            existing_names=existing_names,
            next_rid=next_rid,
        )
        extra_parts.update(copied_parts)
        parent = placeholder_run.getparent()
        if parent is None:
            continue
        child_index = parent.index(placeholder_run)
        parent.remove(placeholder_run)
        parent.insert(child_index, restored_run)
        restored += 1

    rewrite_docx_in_place(
        output_docx,
        overrides={
            "word/document.xml": serialize_xml(document_root),
            "word/_rels/document.xml.rels": serialize_xml(rels_root),
        },
        extra_parts=extra_parts,
    )
    return restored


def import_word_com() -> Any:
    """Import pywin32's COM client, raising a clear dependency error if missing."""
    try:
        import win32com.client  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "pywin32 is required for Word COM automation. Run with `uv run` "
            "or install it with `pip install pywin32` on Windows."
        ) from exc
    return win32com.client


def open_word_document(word: Any, path: Path) -> Any:
    """Open a DOCX read-only in Word for comparison.

    OpenAndRepair helps with generated DOCX files that Word can repair
    interactively but would otherwise refuse in a silent COM run.
    """
    print_info(f"Opening: {path}")
    return word.Documents.Open(
        FileName=str(path),
        ConfirmConversions=False,
        ReadOnly=True,
        AddToRecentFiles=False,
        Visible=False,
        OpenAndRepair=True,
    )


def remove_existing_output(output_docx: Path, allow_overwrite: bool) -> None:
    """Remove an allowed existing output file before Word SaveAs2 writes it."""
    if not output_docx.exists():
        return
    if not allow_overwrite:
        raise FileExistsError(f"Output already exists; pass --allow-overwrite to replace it: {output_docx}")
    # Word SaveAs2 can still prompt or fail on an existing file; deleting only
    # the explicit output path keeps overwrite behavior deterministic.
    output_docx.unlink()


def compare_documents_with_word(
    old_docx: Path,
    new_docx: Path,
    output_docx: Path,
    author: str,
    visible: bool,
    char_level: bool,
    compare_formatting: bool,
    compare_moves: bool,
    allow_overwrite: bool,
    keep_word_open: bool,
) -> None:
    """Use Word COM to compare two DOCX files and save the comparison document."""
    win32com_client = import_word_com()
    word = None
    original_doc = None
    revised_doc = None
    compared_doc = None
    should_quit_word = True

    with tempfile.TemporaryDirectory(prefix="docx-word-compare-") as temp_dir:
        compare_old_docx = old_docx
        compare_new_docx = new_docx
        restore_mathtype_placeholders = False
        replace_indices = unchanged_mathtype_indices(old_docx, new_docx)
        if replace_indices:
            temp_path = Path(temp_dir)
            compare_old_docx = temp_path / "old_without_unchanged_mathtype.docx"
            compare_new_docx = temp_path / "new_without_unchanged_mathtype.docx"
            print_info(f"Replacing unchanged MathType objects with stable placeholders: {len(replace_indices)}")
            old_count = replace_mathtype_objects_with_placeholders(old_docx, compare_old_docx, replace_indices)
            new_count = replace_mathtype_objects_with_placeholders(new_docx, compare_new_docx, replace_indices)
            print_info(f"MathType placeholders: old={old_count}, new={new_count}")
            if old_count != new_count:
                print_warning(
                    "Old and new DOCX contain different MathType object counts; "
                    "formula restoration will use matching positions only"
                )
            restore_mathtype_placeholders = old_count > 0 or new_count > 0

        try:
            print_info("Starting Microsoft Word COM instance...")
            # DispatchEx starts an isolated Word instance so this script does not
            # accidentally close or reuse the user's already-open Word session.
            word = win32com_client.DispatchEx("Word.Application")
            word.Visible = bool(visible)
            word.DisplayAlerts = WD_ALERTS_NONE

            original_doc = open_word_document(word, compare_old_docx)
            revised_doc = open_word_document(word, compare_new_docx)
            granularity = WD_GRANULARITY_CHAR_LEVEL if char_level else WD_GRANULARITY_WORD_LEVEL

            print_info("Running Word Compare...")
            compared_doc = word.CompareDocuments(
                OriginalDocument=original_doc,
                RevisedDocument=revised_doc,
                Destination=WD_COMPARE_DESTINATION_NEW,
                Granularity=granularity,
                CompareFormatting=compare_formatting,
                CompareCaseChanges=True,
                CompareWhitespace=True,
                CompareTables=True,
                CompareHeaders=True,
                CompareFootnotes=True,
                CompareTextboxes=True,
                CompareFields=True,
                CompareComments=True,
                CompareMoves=compare_moves,
                RevisedAuthor=author,
                IgnoreAllComparisonWarnings=False,
            )

            remove_existing_output(output_docx, allow_overwrite)
            print_info(f"Saving comparison result: {output_docx}")
            compared_doc.SaveAs2(FileName=str(output_docx), FileFormat=WD_FORMAT_XML_DOCUMENT)
            print_info("Comparison document saved")

            if restore_mathtype_placeholders:
                # Word keeps the saved comparison file locked while the document
                # is open. Close it before rewriting placeholders back to OLE.
                compared_doc.Close(SaveChanges=WD_DO_NOT_SAVE_CHANGES)
                compared_doc = None
                print_info("Restoring MathType objects from revised DOCX...")
                restored = restore_mathtype_objects_from_revised_docx(output_docx, new_docx)
                print_info(f"Restored MathType objects: {restored}")

            if keep_word_open:
                print_info("Leaving Word open as requested")
                should_quit_word = False
                word.Visible = True
        finally:
            if not keep_word_open:
                for doc in (compared_doc, revised_doc, original_doc):
                    if doc is not None:
                        try:
                            doc.Close(SaveChanges=WD_DO_NOT_SAVE_CHANGES)
                        except Exception:
                            pass
            if word is not None and should_quit_word:
                word.Quit(SaveChanges=WD_DO_NOT_SAVE_CHANGES)


def main() -> int:
    """Run the Word COM DOCX comparison command-line workflow."""
    args = parse_args()
    try:
        old_docx = validate_docx_path(args.old_docx, "Old DOCX")
        new_docx = validate_docx_path(args.new_docx, "New DOCX")
        output_docx = prepare_output_path(args.output_docx, args.allow_overwrite)
        if old_docx == new_docx:
            raise ValueError("Old and new DOCX paths must be different files")
        if output_docx in {old_docx, new_docx}:
            raise ValueError("Output DOCX must be different from both inputs")
        compare_documents_with_word(
            old_docx=old_docx,
            new_docx=new_docx,
            output_docx=output_docx,
            author=args.author,
            visible=args.visible,
            char_level=args.char_level,
            compare_formatting=not args.ignore_formatting,
            compare_moves=not args.ignore_moves,
            allow_overwrite=args.allow_overwrite,
            keep_word_open=args.keep_word_open,
        )
        print_info(f"Output written: {output_docx}")
        return 0
    except Exception as exc:
        print_error(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
