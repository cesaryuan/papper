"""End-to-end snapshots for Papper's HTML and DOCX build contracts."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from snapshot_utils import assert_snapshot, canonical_docx, canonical_html


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_ROOT = Path(__file__).with_name("snapshots")
CASES = {
    "template": (ROOT / "template", "manuscript.md"),
    "references": (ROOT / "tests" / "snapshot_cases" / "references", "references.md"),
    "crossrefs": (ROOT / "tests" / "snapshot_cases" / "crossrefs", "crossrefs.md"),
    "metadata": (ROOT / "tests" / "snapshot_cases" / "metadata", "metadata.md"),
    "style": (ROOT / "tests" / "snapshot_cases" / "style", "style.md"),
}

pytestmark = pytest.mark.skipif(
    shutil.which("pandoc") is None or shutil.which("pandoc-crossref") is None,
    reason="snapshot builds require pandoc and pandoc-crossref",
)


def build_case(case_dir: Path, markdown: str, target: str, output: Path) -> None:
    """Build one fixture through the public Papper CLI into a temporary file."""
    command = [
        "uv",
        "run",
        "papper",
        "build",
        target,
        "--project-dir",
        str(case_dir),
        "-m",
        markdown,
        "-o",
        str(output),
    ]
    if target == "docx":
        command.append("--no-mathtype")
    subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)


@pytest.mark.parametrize("case_name", CASES)
@pytest.mark.parametrize("target", ["html", "docx"])
def test_build_output_matches_snapshot(
    case_name: str,
    target: str,
    tmp_path: Path,
    snapshot_update: bool,
) -> None:
    """Keep each public build target stable for the complete and focused fixtures."""
    case_dir, markdown = CASES[case_name]
    output = tmp_path / f"{case_name}.{target}"
    build_case(case_dir, markdown, target, output)
    actual = (
        canonical_html(output)
        if target == "html"
        else canonical_docx(output, repository_root=ROOT)
    )
    snapshot_path = SNAPSHOT_ROOT / case_name / f"{target}.snap"
    assert_snapshot(actual, snapshot_path, update=snapshot_update)


def test_chinese_crossref_docx_matches_snapshot(tmp_path: Path, snapshot_update: bool) -> None:
    """Capture dotted Chinese section references alongside chapter-numbered content."""
    case_dir = tmp_path / "chinese_crossrefs"
    shutil.copytree(ROOT / "tests" / "snapshot_cases" / "chinese_crossrefs", case_dir)
    output = tmp_path / "chinese_crossrefs.docx"
    build_case(case_dir, "chinese_crossrefs.md", "docx", output)
    actual = canonical_docx(output, repository_root=ROOT)
    snapshot_path = SNAPSHOT_ROOT / "chinese_crossrefs" / "docx.snap"
    assert_snapshot(actual, snapshot_path, update=snapshot_update)
