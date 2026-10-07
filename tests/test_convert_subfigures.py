"""Protect recovered subfigures and downstream cross-reference image retention.

The Markdown fixture covers public syntax, formatting, malformed table exports,
bookmarks and near misses. The real pandoc-crossref engine must
retain every image and resolve parent/child IDs.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from native_support import ROOT, native_pandoc_executable
from snapshot_utils import assert_snapshot


def ast_elements(value: object, kind: str) -> list[dict]:
    """Collect public AST elements for image retention and reference assertions."""
    result = []
    if isinstance(value, dict):
        if value.get("t") == kind:
            result.append(value)
        for child in value.values():
            result.extend(ast_elements(child, kind))
    elif isinstance(value, list):
        for child in value:
            result.extend(ast_elements(child, kind))
    return result


def run_pandoc(source: Path, *options: str) -> subprocess.CompletedProcess:
    """Run the retained engine with the real convert filters and crossref support."""
    pandoc = native_pandoc_executable()
    assert pandoc is not None, "Subfigure conversion requires the retained Pandoc engine"
    result = subprocess.run(
        [str(pandoc), str(source), *options], cwd=source.parent,
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def test_subfigure_markdown_snapshot(tmp_path: Path, snapshot_update: bool) -> None:
    """Preserve group references and round source sizes after detection rewrites layout sizes."""
    fixture = ROOT / "tests/snapshot_cases_convert/subfigures"
    options = ["-f", "markdown", "-t", "markdown-simple_tables-multiline_tables", "--standalone", "--wrap=none"]
    for name in ["detect_subfigures", "extract_inline_images", "detect_figure", "crossrefs",
                 "round_image_dimensions", "crossrefs_fuzz"]:
        options.extend(["-L", str(ROOT / f"pandoc/filters/convert/{name}.lua")])
    converted = run_pandoc(fixture / "input.md", *options).stdout
    assert_snapshot(converted, fixture / "snapshots-content/subfigures.md", update=snapshot_update)
    output = tmp_path / "groups.md"
    output.write_text(converted, encoding="utf-8")
    resolved = run_pandoc(output, "-f", "markdown", "-t", "json", "--filter=pandoc-crossref",
                          "-M", "linkReferences:true")
    assert "Undefined cross-reference" not in resolved.stderr
    ast = json.loads(resolved.stdout)["blocks"]
    before = json.loads(run_pandoc(fixture / "input.md", "-t", "json").stdout)["blocks"]
    assert [image["c"][2][0] for image in ast_elements(ast, "Image")] == [
        image["c"][2][0] for image in ast_elements(before, "Image")]
    links = {link["c"][2][0] for link in ast_elements(ast, "Link")}
    assert {"#fig:_RefGrid", "#fig:_RefPanel", "#fig:existing"} <= links
    assert "This paragraph must survive." in converted
    # Repeated conversion must not nest groups or allocate new panel IDs.
    repeated = run_pandoc(output, "-t", "markdown-simple_tables-multiline_tables", "--standalone", "--wrap=none", "-L",
                          str(ROOT / "pandoc/filters/convert/detect_subfigures.lua"))
    assert repeated.stdout == converted
