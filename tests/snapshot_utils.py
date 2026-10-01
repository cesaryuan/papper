"""Canonical snapshot helpers for standalone HTML and decompressed DOCX output."""

from __future__ import annotations

import difflib
import hashlib
import json
from pathlib import Path
import re
from zipfile import ZipFile

from lxml import etree

from pandoc_manuscript.runtime.paths import project_cache_dir


_VOLATILE_DOCX_TAGS = {"created", "modified"}
_VOLATILE_DOCX_ATTRIBUTES = {"rsidR", "rsidRPr", "rsidP", "rsidDel", "rsidSect"}
_WORD_NAMESPACE = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_CACHE_FILENAME_HASH = re.compile(
    r"(__PAPPER_CACHE__/svg-(?:png|embedded)/[^\"<>]*?)-[0-9a-f]{12}(?=\.(?:png|svg)\b)"
)


def _canonical_path_text(
    value: str, path_replacements: tuple[tuple[str, str], ...],
) -> str:
    """Normalize cache paths and the filename hashes derived from temporary paths."""
    for source, replacement in path_replacements:
        value = value.replace(source, replacement)
    # SVG rasterization hashes an embedded source's absolute cache path. A fresh
    # project directory changes this filename without changing the image bytes.
    return _CACHE_FILENAME_HASH.sub(r"\1-__SOURCE_PATH_HASH__", value)


class NativeBookmarkNormalizer:
    """Stabilize random bookmark identities while retaining their targets and pairings."""

    def __init__(self, archive: ZipFile) -> None:
        """Assign canonical identities in XML-part and bookmark-start order."""
        self.names: dict[str, str] = {}
        self.identifiers: dict[tuple[str, str], str] = {}
        for part_name in sorted(archive.namelist()):
            if not part_name.startswith("word/") or not part_name.endswith(".xml"):
                continue
            root = etree.fromstring(archive.read(part_name))
            for bookmark in root.iter(_WORD_NAMESPACE + "bookmarkStart"):
                name = bookmark.get(_WORD_NAMESPACE + "name")
                if name and name.startswith("PapperRef-"):
                    self.names.setdefault(name, f"PapperRef-{len(self.names) + 1:09d}")
                identifier = bookmark.get(_WORD_NAMESPACE + "id")
                if identifier is not None:
                    key = (part_name, identifier)
                    self.identifiers.setdefault(key, str(len(self.identifiers) + 1))

    def instruction(self, text: str) -> str:
        """Replace only a REF's bookmark operand, keeping switches and other text intact."""
        return re.sub(
            r"(\bREF\s+)(\S+)",
            lambda match: match[1] + self.names.get(match[2], match[2]),
            text,
        )

    def normalize(self, root: etree._Element, part_name: str) -> None:
        """Rewrite matching starts, ends, and reference operands using the shared mappings."""
        for element in root.iter():
            if element.tag in {_WORD_NAMESPACE + "bookmarkStart", _WORD_NAMESPACE + "bookmarkEnd"}:
                identifier = element.get(_WORD_NAMESPACE + "id")
                key = (part_name, identifier)
                if key in self.identifiers:
                    element.set(_WORD_NAMESPACE + "id", self.identifiers[key])
                name = element.get(_WORD_NAMESPACE + "name")
                if name in self.names:
                    element.set(_WORD_NAMESPACE + "name", self.names[name])
            elif element.tag == _WORD_NAMESPACE + "instrText" and element.text:
                element.text = self.instruction(element.text)
            elif element.tag == _WORD_NAMESPACE + "fldSimple":
                instruction = element.get(_WORD_NAMESPACE + "instr")
                if instruction:
                    element.set(_WORD_NAMESPACE + "instr", self.instruction(instruction))
            elif element.tag == _WORD_NAMESPACE + "hyperlink":
                anchor = element.get(_WORD_NAMESPACE + "anchor")
                if anchor in self.names:
                    element.set(_WORD_NAMESPACE + "anchor", self.names[anchor])


def _canonical_xml(
    data: bytes,
    path_replacements: tuple[tuple[str, str], ...],
    *,
    part_name: str,
    bookmarks: NativeBookmarkNormalizer | None = None,
) -> str:
    """Normalize XML, timestamps, and optionally the native workflow's random bookmarks."""
    root = etree.fromstring(data)
    if bookmarks is not None:
        bookmarks.normalize(root, part_name)
    for element in root.iter():
        if etree.QName(element).localname in _VOLATILE_DOCX_TAGS:
            element.text = "<normalized>"
        if element.text:
            element.text = _canonical_path_text(element.text, path_replacements)
        for attribute in list(element.attrib):
            if etree.QName(attribute).localname in _VOLATILE_DOCX_ATTRIBUTES:
                del element.attrib[attribute]
            else:
                element.attrib[attribute] = _canonical_path_text(
                    element.attrib[attribute], path_replacements,
                )
    canonical = etree.tostring(root, method="c14n", with_comments=True).decode("utf-8")
    return canonical.replace("><", ">\n<")


def canonical_html(path: Path) -> str:
    """Return standalone HTML with stable line endings and final newline."""
    text = path.read_text(encoding="utf-8")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.rstrip() + "\n"


def canonical_docx(
    path: Path, *, repository_root: Path, project_dir: Path | None = None,
    normalize_native_crossrefs: bool = False,
) -> str:
    """Serialize every decompressed DOCX ZIP member for a reviewable snapshot.

    XML members are canonicalized and split at tag boundaries. Binary members
    retain their exact SHA-256 and byte size without ZIP-container timestamps.
    """
    root = repository_root.resolve()
    cache = project_cache_dir(project_dir or root)
    path_replacements = (
        (cache.as_posix(), "__PAPPER_CACHE__"),
        (str(cache), "__PAPPER_CACHE__"),
        (root.as_posix(), "__REPO__"),
        (str(root), "__REPO__"),
    )
    entries: dict[str, object] = {}
    with ZipFile(path) as archive:
        # Native builds randomize all bookmark IDs. Canonicalize only this fixture;
        # existing legacy snapshots retain their original bookmark serialization.
        bookmarks = NativeBookmarkNormalizer(archive) if normalize_native_crossrefs else None
        for info in sorted(archive.infolist(), key=lambda item: item.filename):
            data = archive.read(info.filename)
            if info.filename.lower().endswith((".xml", ".rels")):
                entries[info.filename] = {
                    "xml": _canonical_xml(
                        data, path_replacements, part_name=info.filename, bookmarks=bookmarks,
                    ).splitlines()
                }
            else:
                entries[info.filename] = {
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "size": len(data),
                }
    return json.dumps(entries, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def assert_snapshot(actual: str, snapshot_path: Path, *, update: bool) -> None:
    """Compare an artifact to its snapshot, or write it when explicitly requested."""
    if update:
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(actual, encoding="utf-8", newline="\n")
        return

    if not snapshot_path.exists():
        raise AssertionError(
            f"Snapshot is missing: {snapshot_path}. Re-run with --snapshot-update to create it."
        )
    expected = snapshot_path.read_text(encoding="utf-8")
    if actual != expected:
        diff = "".join(
            difflib.unified_diff(
                expected.splitlines(keepends=True),
                actual.splitlines(keepends=True),
                fromfile=str(snapshot_path),
                tofile="actual",
            )
        )
        raise AssertionError(f"Snapshot mismatch for {snapshot_path}\n{diff[:20000]}")
