"""Shared pixel comparison for browser screenshots and Word-rendered DOCX pages."""

from __future__ import annotations

import io
import logging
import re
import subprocess
from pathlib import Path

from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[1]
LOGGER = logging.getLogger(__name__)


def write_visual_difference(actual: bytes | None, expected: bytes | None, diff_path: Path | None = None) -> dict | None:
    """Save only visible changes, preserving the existing diff when pixels are unchanged."""
    if actual == expected:
        return None
    # A missing DOCX page is a visible pagination change, even if the page was blank.
    reference = Image.open(io.BytesIO(expected)) if expected is not None else None
    received = Image.open(io.BytesIO(actual)) if actual is not None else None
    try:
        expected_size = reference.size if reference is not None else None
        actual_size = received.size if received is not None else None
        sizes = [size for size in (expected_size, actual_size) if size is not None]
        width, height = max(size[0] for size in sizes), max(size[1] for size in sizes)
        before, after = Image.new("RGB", (width, height), "white"), Image.new("RGB", (width, height), "white")
        if reference is not None:
            before.paste(reference.convert("RGB"))
        if received is not None:
            after.paste(received.convert("RGB"))
        difference = ImageChops.difference(before, after)
        red, green, blue = difference.split()
        mask = ImageChops.lighter(ImageChops.lighter(red, green), blue).point([0] + [255] * 255)
        if reference is None or received is None:
            mask = Image.new("L", (width, height), 255)
        changed = mask.histogram()[255]
        if changed or expected_size != actual_size:
            if diff_path is not None:
                diff_path.parent.mkdir(parents=True, exist_ok=True)
                overlay = Image.blend(before, Image.new("RGB", before.size, "magenta"), 0.65)
                Image.composite(overlay, before, mask).save(diff_path)
            details = {
                "changed_pixels": changed, "total_pixels": width * height,
                "expected_size": expected_size, "actual_size": actual_size,
                "changed_bounds": mask.getbbox(),
                "status": "added" if reference is None else "removed" if received is None else "changed",
            }
            return details
        return None
    finally:
        if reference is not None:
            reference.close()
        if received is not None:
            received.close()


def assert_visual(actual: bytes, expected: Path, diff_path: Path | None = None) -> None:
    """Reject changed working-baseline pixels without overwriting the separate HEAD comparison."""
    details = write_visual_difference(actual, expected.read_bytes(), diff_path)
    if details is not None:
        raise AssertionError(f"Visual mismatch: {details}; baseline: {expected}")


def record_visual_changes(images: dict[str, bytes], baseline: Path) -> None:
    """Compare rendered images with Git HEAD, retaining diffs across repeated baseline updates."""
    if not baseline.resolve().is_relative_to(ROOT):
        # Harness checks use temporary baselines and must not compare unrelated Git files.
        return
    relative = baseline.resolve().relative_to(ROOT).as_posix()
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True,
    ).stdout.strip()
    tree = subprocess.run(
        ["git", "ls-tree", "-rz", revision, "--", relative], cwd=ROOT, check=True, capture_output=True,
    ).stdout
    previous = {}
    for entry in tree.split(b"\0"):
        if not entry:
            continue
        metadata, filename = entry.split(b"\t", 1)
        path = Path(filename.decode("utf-8"))
        # Diff PNGs share the baseline directory, so never compare them as source pages.
        if path.parent.as_posix() == relative and (path.name == "html.png" or re.fullmatch(r"page-\d+\.png", path.name)):
            previous[path.name] = metadata.split()[2].decode("ascii")
    # Unchanged pages retain their last review diff, including after a baseline commit.
    changed = 0
    for name in sorted(images.keys() | previous.keys()):
        expected = None
        if name in previous:
            expected = subprocess.run(
                ["git", "cat-file", "blob", previous[name]], cwd=ROOT, check=True, capture_output=True,
            ).stdout
        diff_path = baseline / ("diff.png" if name == "html.png" else f"{Path(name).stem}-diff.png")
        details = write_visual_difference(images.get(name), expected, diff_path)
        if details is not None:
            changed += 1
    if changed:
        LOGGER.info("Saved visual diff PNGs against %s: %s (%s images)", revision[:12], baseline, changed)
