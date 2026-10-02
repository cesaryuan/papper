"""Decode DOCX MathType objects in bulk using Papper's existing Rust library.

The convert command calls this module in process and passes a temporary JSON
map to the Lua filter. Standalone Pandoc invokes this module once with source
DOCX paths; it writes the same map to stdout and keeps diagnostics on stderr.
SHA-1 keys identify OLE content so Lua can associate results with its previews.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import sys
from contextlib import redirect_stdout
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from pydantic import Field
from pydantic_settings import BaseSettings, CliApp, CliPositionalArg

from .native import ole_to_latex
from ..runtime.logging import log_warning


class DecodeDocxSettings(BaseSettings):
    """Source documents for the standalone Lua filter's batch decoder."""

    docx: CliPositionalArg[list[Path]] = Field(default=[Path("manuscript.docx")], description="DOCX sources")  # Input files supplied by Pandoc


def mathtype_objects(docx: Path) -> list[bytes]:
    """Read only OLE objects declared as equations by the Word XML parts."""
    relationship_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    ole_tag = "{urn:schemas-microsoft-com:office:office}OLEObject"
    objects: list[bytes] = []
    with ZipFile(docx) as archive:
        names = set(archive.namelist())
        for part in sorted(names):
            if not part.startswith("word/") or not part.endswith(".xml") or "/_rels/" in part:
                continue
            equations = [
                node for node in ET.fromstring(archive.read(part)).iter(ole_tag)
                if node.get("ProgID", "").startswith("Equation.") or "MathType" in node.get("ProgID", "")
            ]
            if not equations:
                continue
            folder, filename = posixpath.split(part)
            rels_path = f"{folder}/_rels/{filename}.rels"
            if rels_path not in names:
                continue
            rels = {
                rel.get("Id"): rel.get("Target", "")
                for rel in ET.fromstring(archive.read(rels_path))
                if rel.get("Type", "").endswith("/oleObject") and rel.get("TargetMode") != "External"
            }
            for equation in equations:
                target = rels.get(equation.get(f"{{{relationship_ns}}}id"))
                if not target:
                    continue
                path = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(folder, target))
                if path in names:
                    objects.append(archive.read(path))
    return objects


def decode_documents(documents: list[Path]) -> dict[str, str | bool]:
    """Decode each unique OLE once and preserve unsupported objects as images."""
    decoded: dict[str, str | bool] = {}
    for docx in documents:
        for ole in mathtype_objects(docx):
            key = hashlib.sha1(ole).hexdigest()
            if key in decoded:
                continue
            try:
                decoded[key] = ole_to_latex(ole)
            except RuntimeError as exc:
                # A malformed or unsupported equation must retain its preview.
                log_warning(f"[mtef-parser] Could not decode object {key} in {docx.name}: {exc}")
                decoded[key] = False
    return decoded


def main() -> int:
    """Emit one JSON map for Lua without mixing native setup logs into stdout."""
    try:
        settings = CliApp.run(DecodeDocxSettings)
        with redirect_stdout(sys.stderr):
            decoded = decode_documents(settings.docx)
        sys.stdout.write(json.dumps(decoded, ensure_ascii=True) + "\n")
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"[mtef-parser] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
