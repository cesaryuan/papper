"""End-to-end snapshots for manuscript HTML/DOCX and reviewer-reply DOCX/TXT."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from snapshot_utils import assert_snapshot, canonical_docx, canonical_html
from pandoc_manuscript.runtime.resources import native_pandoc_executable


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
    "table_attributes": (
        ROOT / "tests" / "snapshot_cases" / "table_attributes", "table_attributes.md",
    ),
    "text_styles": (ROOT / "tests" / "snapshot_cases" / "text_styles", "text_styles.md"),
    "author_affiliations": (
        ROOT / "tests" / "snapshot_cases" / "author_affiliations", "author_affiliations.md",
    ),
    "equation_attributes": (
        ROOT / "tests" / "snapshot_cases" / "equation_attributes", "equation_attributes.md",
    ),
    "where_comments": (
        ROOT / "tests" / "snapshot_cases" / "where_comments", "where_comments.md",
    ),
    "svg_rasterization": (
        ROOT / "tests" / "snapshot_cases" / "svg_rasterization", "svg_rasterization.md",
    ),
}
# Trailing equation revision attributes are supported by the DOCX pipeline only.
DOCX_ONLY_CASES = {"native_crossrefs", "equation_attributes"}

pytestmark = pytest.mark.skipif(
    native_pandoc_executable() is None and (shutil.which("pandoc") is None or shutil.which("pandoc-crossref") is None),
    reason="snapshot builds require the native engine or legacy Pandoc tools",
)


def build_case(
    case_dir: Path, markdown: str, target: str, output: Path, *, style_file: Path | None = None,
) -> None:
    """Build one fixture through the public Papper CLI into a temporary file."""
    command = [
        "uv",
        "run",
        "--project",
        str(ROOT),
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
    # Metadata files are keyed by cwd. Shared repository state lets concurrent
    # CLI/server tests overwrite one another's metadata and corrupt snapshots.
    output.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        command, cwd=output.parent, check=False, capture_output=True,
        text=True, encoding="utf-8",
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("target", "case_name"),
    [
        pytest.param(target, case_name, id=f"{target}-{case_name}")
        for target in ["html", "docx"]
        for case_name in CASES
        if case_name not in DOCX_ONLY_CASES or target == "docx"
    ],
)
def test_build_output_matches_snapshot(
    case_name: str,
    target: str,
    tmp_path: Path,
    snapshot_update: bool,
) -> None:
    """Snapshot shared manuscript syntax and target-specific DOCX contracts."""
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
            output, repository_root=ROOT, project_dir=tmp_path,
            normalize_native_crossrefs=native_crossrefs,
        )
    )
    snapshot_path = SNAPSHOT_ROOT / case_name / f"{target}.snap"
    assert_snapshot(actual, snapshot_path, update=snapshot_update)


@pytest.mark.parametrize("target", ["docx", "txt"])
def test_build_reply_output_matches_snapshot(
    target: str,
    tmp_path: Path,
    snapshot_update: bool,
) -> None:
    """Snapshot real reply builds with manuscript numbering, PDF lines, and styles."""
    case_dir = ROOT / "tests" / "snapshot_cases" / "reply"
    # Keep Word COM out of test execution by using the real manuscript's
    # checked-in export, which must be refreshed when the source changes.
    line_source = case_dir / "manuscript.pdf"

    output = tmp_path / f"reply.{target}"
    result = subprocess.run(
        [
            "uv", "run", "--project", str(ROOT), "papper", "build-reply",
            str(case_dir / "reply.md"),
            "--manuscript-line-source", str(line_source),
            "-o", str(output),
        ],
        cwd=tmp_path, check=False, capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    actual = (
        canonical_docx(output, repository_root=ROOT, project_dir=tmp_path)
        if target == "docx"
        else output.read_text(encoding="utf-8")
    )
    assert_snapshot(actual, SNAPSHOT_ROOT / "reply" / f"{target}.snap", update=snapshot_update)
