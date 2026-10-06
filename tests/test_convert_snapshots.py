"""Snapshot DOCX imports as Markdown and a normalized media filename tree.

Each Convert case keeps its input DOCX beside ``snapshots-content/``. Tests run
the public Rust Convert command against the checked-in DOCX, without rebuilding
it. Temporary output and golden PNG/WMF files use tiny white images with black
borders; their paths, not bytes, are the contract here. Refresh explicitly with
``--snapshot-update`` and review the diff.
"""

from __future__ import annotations

import os
import struct
import subprocess
import zlib
from pathlib import Path

import pytest

from native_support import ROOT
from snapshot_utils import assert_snapshot


CASE_ROOT = Path(__file__).with_name("snapshot_cases_convert")


def png_chunk(kind: bytes, data: bytes) -> bytes:
    """Encode a PNG chunk with its length and CRC, without ancillary metadata."""
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def media_placeholder(extension: str) -> bytes:
    """Create small valid 16×16 white/black PNG and vector WMF placeholders."""
    if extension == ".png":
        # One-bit grayscale avoids palettes and RGB overhead. Zero scanline
        # filters plus maximum DEFLATE compression keep the simple border tiny.
        pixels = b"\x00\x00\x00" + b"\x00\x7f\xfe" * 14 + b"\x00\x00\x00"
        header = struct.pack(">IIBBBBB", 16, 16, 1, 0, 0, 0, 0)
        return (b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", header)
                + png_chunk(b"IDAT", zlib.compress(pixels, level=9)) + png_chunk(b"IEND", b""))
    if extension == ".wmf":
        # A placeable WMF scales to 16 pixels at 96 dpi. Draw one rectangle
        # using a white solid brush and a black pen; no raster payload is needed.
        operations = [
            (0x0103, (8,)),                         # Anisotropic mapping
            (0x020B, (0, 0)),                       # Window origin
            (0x020C, (160, 160)),                   # Window extent
            (0x02FA, (0, 10, 0, 0, 0)),             # Solid black pen
            (0x012D, (0,)),                         # Select pen
            (0x02FC, (0, 0xFFFF, 0x00FF, 0)),       # Solid white brush
            (0x012D, (1,)),                         # Select brush
            (0x041B, (155, 155, 5, 5)),             # Inset rectangle
            (0x0000, ()),                           # End metafile
        ]
        records = [struct.pack("<IH", 3 + len(parameters), operation)
                   + struct.pack(f"<{len(parameters)}H", *parameters)
                   for operation, parameters in operations]
        placeable = struct.pack("<IHhhhhHI", 0x9AC6CDD7, 0, 0, 0, 160, 160, 960, 0)
        checksum = 0
        for word in struct.unpack("<10H", placeable):
            checksum ^= word
        body = b"".join(records)
        header = struct.pack("<HHHIHIH", 1, 9, 0x0300, 9 + len(body) // 2, 2,
                             max(len(record) // 2 for record in records), 0)
        return placeable + struct.pack("<H", checksum) + header + body
    # Other extracted media still use filename-only placeholders until a
    # format-specific stand-in is needed, rather than copying document content.
    return b""


def output_tree(directory: Path) -> list[str]:
    """Represent the complete published tree, including unexpected or empty directories."""
    return sorted(path.relative_to(directory).as_posix() + ("/" if path.is_dir() else "")
                  for path in directory.rglob("*"))


def normalize_output_media(directory: Path) -> None:
    """Replace extracted media in the test output before snapshot comparison/copy."""
    # Normalize actual output too: previously only golden files were replaced,
    # so inspecting pytest's converted/media directory still displayed originals.
    for path in (directory / "media").rglob("*"):
        if path.is_file():
            path.write_bytes(media_placeholder(path.suffix.lower()))


@pytest.mark.parametrize("case_name", ["comprehensive"])
def test_convert_output_matches_snapshot(
    case_name: str, tmp_path: Path, rust_executable: Path, snapshot_update: bool,
) -> None:
    """Detect changed imported syntax, missing pictures, and leaked formula previews."""
    source = CASE_ROOT / case_name / f"{case_name}.docx"
    output = tmp_path / "converted"
    result = subprocess.run(
        [str(rust_executable), "convert", str(source), "-o", str(output)],
        cwd=tmp_path,
        env={**os.environ, "PAPPER_RESOURCE_ROOT": str(ROOT), "PAPPER_HOME": str(tmp_path / "home")},
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    normalize_output_media(output)
    expected = CASE_ROOT / case_name / "snapshots-content"
    markdown_name = f"{case_name}.md"
    # read_bytes preserves line endings: portable LF is part of Convert output.
    markdown = (output / markdown_name).read_bytes().decode("utf-8")
    # A Word save can merge fallback previews with genuine equations. Updating
    # snapshots must not silently accept a case that stopped covering recovery.
    assert "@eq:_RefConvertTwoCell" in markdown and "@eq:_RefConvertTextLabel" in markdown, (
        "Comprehensive fixture lost equation recovery; check shared Word previews before refreshing snapshots"
    )
    if snapshot_update:
        # Delete only this case's previous golden output so removed media names
        # cannot silently remain in the expected directory after a refresh.
        assert expected.resolve().parent == (CASE_ROOT / case_name).resolve(), (
            "Snapshot update must remain inside its case root"
        )
        for path in sorted(expected.rglob("*"), key=lambda value: len(value.parts), reverse=True):
            if path.is_dir():
                path.rmdir()
            else:
                path.unlink()
        for path in output.rglob("*"):
            target = expected / path.relative_to(output)
            if path.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            elif path.relative_to(output).parts[0] == "media":
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(path.read_bytes())
    assert_snapshot(markdown, expected / markdown_name, update=snapshot_update)
    assert output_tree(output) == output_tree(expected), "Convert output file/directory tree differs from the snapshot"
