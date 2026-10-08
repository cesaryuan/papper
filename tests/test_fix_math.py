"""Protect roman math scopes, styled hats, and real writer output across build defaults."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from zipfile import ZipFile

import pytest
from lxml import etree

from native_support import ROOT, native_pandoc_executable
from snapshot_utils import assert_snapshot


@pytest.fixture(scope="module")
def math_pandoc() -> str:
    """Use a real retained engine, falling back to standalone Pandoc for filter checks."""
    executable = native_pandoc_executable() or shutil.which("pandoc")
    if executable is None:
        pytest.skip("Math correction requires Pandoc")
    return str(executable)


def test_math_correction_snapshot(math_pandoc: str, snapshot_update: bool) -> None:
    """Catch altered operands or damaged comments/code and ensure the fix is idempotent."""
    fixture = ROOT / "tests/snapshot_cases/fix_math"
    arguments = [
        math_pandoc, "--from=markdown", "--to=markdown", "--wrap=none",
        "--lua-filter", str(ROOT / "pandoc/filters/shared/fix_math.lua"),
    ]
    source = (fixture / "input.md").read_text(encoding="utf-8")
    result = subprocess.run(
        arguments, input=source, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert_snapshot(
        result.stdout, fixture / "snapshots-content/normalized.md", update=snapshot_update,
    )
    repeated = subprocess.run(
        arguments, input=result.stdout, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert repeated.returncode == 0, repeated.stderr
    assert repeated.stdout == result.stdout


@pytest.mark.parametrize(("target", "markers"), [
    pytest.param("docx", False, id="docx"),
    pytest.param("docx", True, id="docx-mathtype-markers"),
    pytest.param("html", False, id="html"),
    pytest.param("latex", False, id="latex"),
])
def test_corrected_formula_renders_with_defaults(
    math_pandoc: str, target: str, markers: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Render corrected hats and a roman transpose across the actual default chains."""
    if native_pandoc_executable() is None and shutil.which("pandoc-crossref") is None:
        pytest.skip("Default filter chains require the retained engine or pandoc-crossref")
    formula = (
        r"\hat{\mathbf{C}}+\hat{\mathcal{C}}+"
        r"\mathbf{\Sigma }_i^t=(E-\boldsymbol{\mu }_i^t)"
        r"(E-\boldsymbol{\mu }_i^t)^{{\rm T}}"
    )
    monkeypatch.setenv("PMT_ENABLE_MATHTYPE_MARKERS", str(markers).lower())
    output = tmp_path / f"formula.{target}"
    result = subprocess.run(
        [math_pandoc, "--defaults", str(ROOT / f"pandoc/pandoc-{target}.yml"),
         "--from=markdown", "--to", target, "--output", str(output)],
        input=f"Inline ${formula}$.\n\n$$\n{formula}\n$$\n", cwd=tmp_path,
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "Could not convert TeX math" not in result.stderr
    if target == "docx":
        with ZipFile(output) as archive:
            document = etree.fromstring(archive.read("word/document.xml"))
        namespace = {"m": "http://schemas.openxmlformats.org/officeDocument/2006/math"}
        equations = document.xpath("//m:oMath", namespaces=namespace)
        assert len(equations) == 2
        for equation in equations:
            assert len(equation.xpath(".//m:acc", namespaces=namespace)) >= 2
            # Assert an actual roman transpose in OMML, rather than raw fallback text.
            assert equation.xpath(".//m:sSup/m:sup//m:t[text()='T']", namespaces=namespace)
            assert equation.xpath(
                ".//m:r[m:t='T']/m:rPr/m:nor", namespaces=namespace,
            )
        if markers:
            sources = document.xpath(
                "//w:t[starts-with(text(), 'MTLATEX:')]/text()",
                namespaces={"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"},
            )
            assert len(sources) == 2
            assert sources[0].startswith("MTLATEX:inline:")
            assert sources[1].startswith("MTLATEX:display:")
            assert all(source.strip().endswith(r")^{{\textrm{T}}}") for source in sources)
            assert all(r"\mathbf{\hat{C}}+\mathcal{\hat{C}}" in source for source in sources)
    else:
        content = output.read_text(encoding="utf-8")
        assert r"\textrm{T}" in content
        assert r"\mathbf{\hat{C}}+\mathcal{\hat{C}}" in content
