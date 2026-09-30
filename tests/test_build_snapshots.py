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
    "native_crossrefs": (
        ROOT / "tests" / "snapshot_cases" / "native_crossrefs",
        "native_crossrefs.md",
    ),
    "chinese_crossrefs": (
        ROOT / "tests" / "snapshot_cases" / "chinese_crossrefs",
        "chinese_crossrefs.md",
    ),
    "metadata": (ROOT / "tests" / "snapshot_cases" / "metadata", "metadata.md"),
    "style": (ROOT / "tests" / "snapshot_cases" / "style", "style.md"),
}

pytestmark = pytest.mark.skipif(
    shutil.which("pandoc") is None or shutil.which("pandoc-crossref") is None,
    reason="snapshot builds require pandoc and pandoc-crossref",
)


def build_case(
    case_dir: Path, markdown: str, target: str, output: Path, *, style_file: Path | None = None,
) -> None:
    """Build one fixture through the public Papper CLI into a temporary file."""
    command = [
        "uv",
        "run",
        "papper",
        "build",
        target,
        "-m",
        str(case_dir / markdown),
        "-o",
        str(output),
    ]
    if target == "docx":
        command.append("--no-mathtype")
    if style_file is not None:
        command.extend(["--style-file", str(style_file)])
    subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)


@pytest.mark.parametrize(
    ("target", "case_name"),
    [
        pytest.param(target, case_name, id=f"{target}-{case_name}")
        for target in ["html", "docx"]
        for case_name in CASES
        if case_name != "native_crossrefs" or target == "docx"
    ],
)
def test_build_output_matches_snapshot(
    case_name: str,
    target: str,
    tmp_path: Path,
    snapshot_update: bool,
) -> None:
    """Snapshot both build targets and the DOCX-only native cross-reference contract."""
    case_dir, markdown = CASES[case_name]
    if case_name == "chinese_crossrefs":
        # This fixture was built from a copy to keep generated files out of its source.
        copied_case_dir = tmp_path / case_name
        shutil.copytree(case_dir, copied_case_dir)
        case_dir = copied_case_dir
    output = tmp_path / f"{case_name}.{target}"
    native_crossrefs = case_name == "native_crossrefs"
    build_case(
        case_dir, markdown, target, output,
        style_file=case_dir / "style.yml" if native_crossrefs else None,
    )
    actual = (
        canonical_html(output)
        if target == "html"
        else canonical_docx(
            output, repository_root=ROOT, normalize_native_crossrefs=native_crossrefs,
        )
    )
    snapshot_path = SNAPSHOT_ROOT / case_name / f"{target}.snap"
    assert_snapshot(actual, snapshot_path, update=snapshot_update)
