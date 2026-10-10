"""End-to-end snapshots for manuscript HTML/DOCX and reviewer-reply DOCX/TXT."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from snapshot_utils import assert_snapshot, canonical_docx, canonical_html
from native_support import native_pandoc_executable, papper_command


ROOT = Path(__file__).resolve().parents[1]
CASE_ROOT = Path(__file__).with_name("snapshot_cases")
CASES = {
    "template": (ROOT / "template", "manuscript.md"),
    "template_cn": (ROOT / "template", "manuscript-cn.md"),
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
    "bilingual_captions": (
        ROOT / "tests" / "snapshot_cases" / "bilingual_captions", "bilingual_captions.md",
    ),
    "metadata": (ROOT / "tests" / "snapshot_cases" / "metadata", "metadata.md"),
    "style": (ROOT / "tests" / "snapshot_cases" / "style", "style.md"),
    "papper_style": (
        ROOT / "tests" / "snapshot_cases" / "papper_style", "papper_style.md",
    ),
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
    "equation_attributes_no_mathtype": (
        ROOT / "tests" / "snapshot_cases" / "equation_attributes_no_mathtype",
        "equation_attributes_no_mathtype.md",
    ),
    "where_comments": (
        ROOT / "tests" / "snapshot_cases" / "where_comments", "where_comments.md",
    ),
    "svg_rasterization": (
        ROOT / "tests" / "snapshot_cases" / "svg_rasterization", "svg_rasterization.md",
    ),
}
# Trailing equation revision attributes are supported by the DOCX pipeline only.
DOCX_ONLY_CASES = {"native_crossrefs", "equation_attributes", "equation_attributes_no_mathtype"}

pytestmark = pytest.mark.skipif(
    native_pandoc_executable() is None and (shutil.which("pandoc") is None or shutil.which("pandoc-crossref") is None),
    reason="snapshot builds require the native engine or standalone Pandoc and crossref",
)


def copy_case(case_dir: Path, destination: Path) -> None:
    """Copy fixture inputs without colocated baselines entering temporary builds."""
    shutil.copytree(
        case_dir, destination,
        ignore=shutil.ignore_patterns("snapshots-content", "snapshots-visual"),
    )


def build_case(
    case_dir: Path, markdown: str, target: str, output: Path, *, style_file: Path | None = None,
) -> None:
    """Build one fixture through the public Papper CLI into a temporary file."""
    command = [
        *papper_command(),
        "build",
        target,
        "-m",
        str(case_dir / markdown),
        "-o",
        str(output),
    ]
    # Explicit fixture styles control MathType without a CLI override masking them.
    if target == "docx" and style_file is None:
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
        copy_case(case_dir, copied_case_dir)
        case_dir = copied_case_dir
    output = tmp_path / f"{case_name}.{target}"
    native_crossrefs = case_name == "native_crossrefs"
    build_case(
        case_dir, markdown, target, output,
        style_file=(
            case_dir / "style.yml"
            if native_crossrefs or case_name in {"equation_attributes_no_mathtype", "papper_style"}
            else None
        ),
    )
    actual = (
        canonical_html(output)
        if target == "html"
        else canonical_docx(
            output, repository_root=ROOT, project_dir=tmp_path,
            normalize_native_crossrefs=native_crossrefs,
        )
    )
    # Resolve from the checked-in case, even when inputs were copied to tmp_path.
    # Template cases test the real templates but keep baselines outside them.
    snapshot_path = CASE_ROOT / case_name / "snapshots-content" / f"{target}.snap"
    assert_snapshot(actual, snapshot_path, update=snapshot_update)


@pytest.mark.parametrize("target", ["html", "docx", "txt"])
def test_build_reply_output_matches_snapshot(
    target: str,
    tmp_path: Path,
    snapshot_update: bool,
) -> None:
    """Snapshot reply numbering, PDF lines, styles, and horizontal/vertical cell merges."""
    case_dir = ROOT / "tests" / "snapshot_cases" / "reply"
    # The original checked-in Word export keeps line geometry stable without COM.
    line_source = case_dir / "manuscript.pdf"
    output = tmp_path / f"reply.{target}"
    result = subprocess.run(
        [
            *papper_command(), *(["build-reply"] if target == "txt" else ["build", target]),
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
        else canonical_html(output) if target == "html" else output.read_text(encoding="utf-8")
    )
    assert_snapshot(actual, case_dir / "snapshots-content" / f"{target}.snap", update=snapshot_update)
