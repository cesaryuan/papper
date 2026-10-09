"""Verify review artifacts survive updates and expose added or removed DOCX pages."""

from __future__ import annotations

import io
import subprocess
from pathlib import Path

import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo

import visual_support
import docx_visual_support


def png(color: str) -> bytes:
    """Create a small real screenshot whose visible pixels can be checked independently."""
    output = io.BytesIO()
    Image.new("RGB", (3, 2), color).save(output, format="PNG")
    return output.getvalue()


@pytest.fixture
def snapshot_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Commit real baseline images in an isolated repository, including an older diff."""
    baseline = tmp_path / "snapshots-visual"
    baseline.mkdir()
    (baseline / "page-001.png").write_bytes(png("black"))
    (baseline / "page-002.png").write_bytes(png("white"))
    (baseline / "page-001-diff.png").write_bytes(png("magenta"))
    for command in (
        ["git", "init", "--quiet"],
        ["git", "add", "."],
        ["git", "-c", "user.name=Visual Test", "-c", "user.email=visual@example.invalid",
         "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "Reviewed baseline"],
    ):
        subprocess.run(command, cwd=tmp_path, check=True, capture_output=True)
    monkeypatch.setattr(visual_support, "ROOT", tmp_path.resolve())
    return baseline


def test_previous_version_diff_survives_updates_and_unchanged_render(snapshot_repository: Path) -> None:
    """Repeated updates retain HEAD's comparison; an unchanged render preserves the last diff."""
    baseline = snapshot_repository
    images = {"page-001.png": png("red"), "page-002.png": png("white")}
    visual_support.record_visual_changes(images, baseline)
    first = (baseline / "page-001-diff.png").read_bytes()
    (baseline / "page-001.png").write_bytes(images["page-001.png"])
    visual_support.record_visual_changes(images, baseline)
    assert (baseline / "page-001-diff.png").read_bytes() == first
    assert {path.name for path in baseline.iterdir()} == {"page-001.png", "page-002.png", "page-001-diff.png"}
    with Image.open(baseline / "page-001-diff.png") as difference:
        assert difference.getpixel((0, 0)) != (0, 0, 0)
    images["page-001.png"] = png("black")
    modified = (baseline / "page-001-diff.png").stat().st_mtime_ns
    visual_support.record_visual_changes(images, baseline)
    assert (baseline / "page-001-diff.png").read_bytes() == first
    assert (baseline / "page-001-diff.png").stat().st_mtime_ns == modified


def test_previous_version_diff_exposes_added_and_removed_blank_pages(snapshot_repository: Path) -> None:
    """Pagination changes must remain reviewable even when added or removed pages contain no ink."""
    baseline = snapshot_repository
    images = {"page-001.png": png("black"), "page-003.png": png("white")}
    visual_support.record_visual_changes(images, baseline)
    assert {path.name for path in baseline.glob("*-diff.png")} == {"page-001-diff.png", "page-002-diff.png", "page-003-diff.png"}
    for name in ("page-002", "page-003"):
        with Image.open(baseline / f"{name}-diff.png") as difference:
            assert difference.getpixel((0, 0)) != (255, 255, 255)


@pytest.mark.parametrize("different_encoding", [False, True])
def test_unchanged_html_pixels_preserve_existing_diff(tmp_path: Path, different_encoding: bool) -> None:
    """Identical HTML pixels must leave the previous diff untouched, even if PNG metadata changes."""
    expected = png("black")
    actual = expected
    if different_encoding:
        metadata = PngInfo()
        metadata.add_text("render", "same pixels with new metadata")
        output = io.BytesIO()
        Image.new("RGB", (3, 2), "black").save(output, format="PNG", pnginfo=metadata)
        actual = output.getvalue()
        assert actual != expected
    diff_path = tmp_path / "diff.png"
    existing = png("magenta")
    diff_path.write_bytes(existing)
    modified = diff_path.stat().st_mtime_ns
    assert visual_support.write_visual_difference(actual, expected, diff_path) is None
    assert diff_path.read_bytes() == existing
    assert diff_path.stat().st_mtime_ns == modified


def test_docx_update_preserves_deleted_page_diff(snapshot_repository: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Shorter DOCX updates remove obsolete baselines but retain their visible review diffs."""
    session = object.__new__(docx_visual_support.WordVisualSession)
    session.update = True

    def capture(source: Path, directory: Path) -> tuple[list[bytes], list[dict]]:
        """Supply deterministic output at the external Word renderer boundary."""
        return [png("red")], [{"width_px": 3, "height_px": 2}]

    def save_environment() -> None:
        """Leave the machine's real Word environment untouched in this isolated update."""

    monkeypatch.setattr(session, "capture", capture)
    monkeypatch.setattr(session, "save_environment", save_environment)
    session.check(snapshot_repository / "rendered.docx", snapshot_repository)
    assert (snapshot_repository / "page-001.png").read_bytes() == png("red")
    assert not (snapshot_repository / "page-002.png").exists()
    assert {path.name for path in snapshot_repository.glob("*-diff.png")} == {"page-001-diff.png", "page-002-diff.png"}
    with Image.open(snapshot_repository / "page-002-diff.png") as difference:
        assert difference.getpixel((0, 0)) != (255, 255, 255)
