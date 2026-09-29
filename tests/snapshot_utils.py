"""Canonical snapshot helpers for standalone HTML and decompressed DOCX output."""

from __future__ import annotations

import difflib
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

from lxml import etree

from pandoc_manuscript.runtime.paths import project_cache_dir


_VOLATILE_DOCX_TAGS = {"created", "modified"}
_VOLATILE_DOCX_ATTRIBUTES = {"rsidR", "rsidRPr", "rsidP", "rsidDel", "rsidSect"}


def _canonical_xml(data: bytes, path_replacements: tuple[tuple[str, str], ...]) -> str:
    """Normalize XML formatting and replace build-time core-property timestamps."""
    root = etree.fromstring(data)
    for element in root.iter():
        if etree.QName(element).localname in _VOLATILE_DOCX_TAGS:
            element.text = "<normalized>"
        if element.text:
            for source, replacement in path_replacements:
                element.text = element.text.replace(source, replacement)
        for attribute in list(element.attrib):
            if etree.QName(attribute).localname in _VOLATILE_DOCX_ATTRIBUTES:
                del element.attrib[attribute]
            else:
                value = element.attrib[attribute]
                for source, replacement in path_replacements:
                    value = value.replace(source, replacement)
                element.attrib[attribute] = value
    canonical = etree.tostring(root, method="c14n", with_comments=True).decode("utf-8")
    return canonical.replace("><", ">\n<")


def canonical_html(path: Path) -> str:
    """Return standalone HTML with stable line endings and final newline."""
    text = path.read_text(encoding="utf-8")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.rstrip() + "\n"


def canonical_docx(path: Path, *, repository_root: Path) -> str:
    """Serialize every decompressed DOCX ZIP member for a reviewable snapshot.

    XML members are canonicalized and split at tag boundaries. Binary members
    retain their exact SHA-256 and byte size without ZIP-container timestamps.
    """
    root = repository_root.resolve()
    cache = project_cache_dir(root)
    path_replacements = (
        (cache.as_posix(), "__PAPPER_CACHE__"),
        (str(cache), "__PAPPER_CACHE__"),
        (root.as_posix(), "__REPO__"),
        (str(root), "__REPO__"),
    )
    entries: dict[str, object] = {}
    with ZipFile(path) as archive:
        for info in sorted(archive.infolist(), key=lambda item: item.filename):
            data = archive.read(info.filename)
            if info.filename.lower().endswith((".xml", ".rels")):
                entries[info.filename] = {
                    "xml": _canonical_xml(data, path_replacements).splitlines()
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
