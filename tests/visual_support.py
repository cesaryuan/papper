"""Shared pixel comparison for browser screenshots and Word-rendered DOCX pages."""

from __future__ import annotations

import io
import json
import shutil
from pathlib import Path

from PIL import Image, ImageChops


def assert_visual(actual: bytes, expected: Path, result_dir: Path) -> None:
    """Compare exact pixels and preserve readable artifacts, including size changes."""
    with Image.open(expected) as reference, Image.open(io.BytesIO(actual)) as received:
        width, height = max(reference.width, received.width), max(reference.height, received.height)
        before, after = Image.new("RGB", (width, height), "white"), Image.new("RGB", (width, height), "white")
        before.paste(reference.convert("RGB"))
        after.paste(received.convert("RGB"))
        difference = ImageChops.difference(before, after)
        red, green, blue = difference.split()
        mask = ImageChops.lighter(ImageChops.lighter(red, green), blue).point([0] + [255] * 255)
        changed = mask.histogram()[255]
        if changed or reference.size != received.size:
            result_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(expected, result_dir / "expected.png")
            (result_dir / "actual.png").write_bytes(actual)
            overlay = Image.blend(before, Image.new("RGB", before.size, "magenta"), 0.65)
            Image.composite(overlay, before, mask).save(result_dir / "diff.png")
            details = {
                "changed_pixels": changed, "total_pixels": width * height,
                "expected_size": reference.size, "actual_size": received.size,
                "changed_bounds": mask.getbbox(),
            }
            (result_dir / "difference.json").write_text(json.dumps(details, indent=2) + "\n", encoding="utf-8")
            raise AssertionError(f"Visual mismatch: {details}; artifacts: {result_dir}")
