from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pandoc_manuscript.commands import build_reply as reply_build
from pandoc_manuscript.commands.build_reply import extract_citation_clusters, replace_citations
from pandoc_manuscript.commands.build_reply import line_source as reply_line_source
from pandoc_manuscript.commands.build_reply import resolve as reply_resolve


def write_test_docx(
    path: Path,
    *,
    created: str = "2026-01-01T00:00:00Z",
    document_text: str = "manuscript",
    zip_timestamp: tuple[int, int, int, int, int, int] = (2026, 1, 1, 0, 0, 0),
) -> None:
    """Write a minimal DOCX package for line-source cache tests."""
    path.parent.mkdir(parents=True, exist_ok=True)
    core_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<cp:coreProperties
  xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
  xmlns:dcterms="http://purl.org/dc/terms/">
  <dcterms:created>{created}</dcterms:created>
  <dcterms:modified>{created}</dcterms:modified>
</cp:coreProperties>
"""
    document_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:p><w:r><w:t>{document_text}</w:t></w:r></w:p></w:body>
</w:document>
"""
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in (
            ("docProps/core.xml", core_xml),
            ("word/document.xml", document_xml),
        ):
            entry = zipfile.ZipInfo(name, date_time=zip_timestamp)
            archive.writestr(entry, data)


def test_add_manuscript_caption_numbers_handles_quoted_figures_and_tables() -> None:
    """Number copied figure/table captions while preserving manual and unresolved prefixes."""
    markdown = """> ![Overview](figures/overview.png) {#fig:overview width=80%}
>
> | A | B |
> |---|---|
> | 1 | 2 |
>
> : Metrics. {#tbl:metrics}

![Figure @fig:manual Existing prefix](figures/manual.png){#fig:manual}

: Local-only table. {#tbl:local}
"""
    reference_map = {
        "fig:overview": "Figure 3",
        "tbl:metrics": "Table 2",
        "fig:manual": "Figure 4",
    }

    resolved = reply_resolve.add_manuscript_caption_numbers(markdown, reference_map)

    assert "> ![Figure 3 Overview](figures/overview.png){#fig:overview width=80%}" in resolved
    assert "> : Table 2 Metrics. {#tbl:metrics}" in resolved
    assert "![Figure @fig:manual Existing prefix]" in resolved
    assert ": Local-only table. {#tbl:local}" in resolved
    assert reply_resolve.add_manuscript_caption_numbers(resolved, reference_map) == resolved


def test_resolve_reply_markdown_probes_labels_from_figure_table_definitions(tmp_path, monkeypatch) -> None:
    """Probe definition labels before automatically adding manuscript caption numbers."""
    markdown = """![Overview](figures/overview.png){#fig:overview}

: Metrics. {#tbl:metrics}
"""
    observed_labels: list[str] = []

    def fake_resolve_reference_map(manuscript, style, labels, from_format):
        """Capture labels passed to the manuscript numbering probe."""
        observed_labels.extend(labels)
        return {"fig:overview": "Figure 3", "tbl:metrics": "Table 2"}

    monkeypatch.setattr(reply_resolve, "resolve_reference_map", fake_resolve_reference_map)
    monkeypatch.setattr(reply_resolve, "resolve_citation_map", lambda *args: {})
    monkeypatch.setattr(reply_resolve, "resolve_citation_cluster_map", lambda *args: {})
    monkeypatch.setattr(reply_line_source, "resolve_line_regexes", lambda text, source: text)

    resolved = reply_build.resolve_reply_markdown(
        markdown,
        tmp_path / "manuscript.md",
        tmp_path / "manuscript.pdf",
        tmp_path / "style.yml",
        SimpleNamespace(pandoc_metadata={}, pmt_settings=None),
        "markdown",
        format_labeled_equations=False,
    )

    assert observed_labels == ["fig:overview", "tbl:metrics"]
    assert "![Figure 3 Overview](figures/overview.png){#fig:overview}" in resolved
    assert ": Table 2 Metrics. {#tbl:metrics}" in resolved


def test_replace_labeled_equation_blocks_uses_manuscript_number_and_tabs() -> None:
    """Rewrite labeled reply equations as tab-layout Word formulas with manuscript numbers."""
    markdown = r"""
The revised metric is:

$$
\mathrm{MFR}=\frac{1}{|\Omega_{\mathrm{ROI}}|}\sum_{\mathbf{p}\in\Omega_{\mathrm{ROI}}}\mathbf{1}\left[V(\mathbf{p})=0\right]
$$ {#eq:missing-face-ratio}
"""

    resolved = reply_build.replace_labeled_equation_blocks(
        markdown,
        {"eq:missing-face-ratio": "Equation 12"},
    )

    assert "{#eq:missing-face-ratio}" not in resolved
    assert '<w:tab w:val="center"' in resolved
    assert '<w:tab w:val="right"' in resolved
    assert "$\\mathrm{MFR}=" in resolved
    assert "(12)" in resolved


def test_replace_labeled_equation_blocks_keeps_unresolved_equations() -> None:
    """Leave equation blocks unchanged when the manuscript probe cannot resolve them."""
    markdown = "$$ a+b $$ {#eq:missing}"

    assert reply_build.replace_labeled_equation_blocks(markdown, {}) == markdown


def test_extract_citation_clusters_skips_crossrefs() -> None:
    """Extract bibliography clusters without treating cross-references as citations."""
    markdown = "See [@zhang2022critical; @li2023neuralangelo] and [@fig:overview]."

    assert extract_citation_clusters(markdown) == ["[@zhang2022critical; @li2023neuralangelo]"]


def test_replace_citations_prefers_resolved_cluster_display() -> None:
    """Use citeproc-resolved cluster text so CSL delimiters and sorting survive."""
    markdown = "Prior work [@zhang2022critical; @li2023neuralangelo] is relevant."

    resolved = replace_citations(
        markdown,
        {
            "zhang2022critical": "[53]",
            "li2023neuralangelo": "[54]",
        },
        {"[@zhang2022critical; @li2023neuralangelo]": "[53, 54]"},
    )

    assert resolved == "Prior work [53, 54] is relevant."


def test_replace_citations_keeps_unresolved_clusters() -> None:
    """Keep unresolved citation clusters instead of doing unsafe partial replacements."""
    markdown = "Prior work [@zhang2022critical; @li2023neuralangelo] is relevant."

    resolved = replace_citations(
        markdown,
        {
            "zhang2022critical": "[53]",
            "li2023neuralangelo": "[54]",
        },
    )

    assert resolved == markdown


def test_replace_citations_protects_unresolved_clusters() -> None:
    """Avoid partial replacements that recreate the old double-bracket bug."""
    markdown = "Cluster [@zhang2022critical; @missing] and bare @zhang2022critical."

    resolved = replace_citations(markdown, {"zhang2022critical": "[53]"})

    assert resolved == "Cluster [@zhang2022critical; @missing] and bare [53]."


def test_extract_probe_map_reads_superscript_csl_citation_without_probe_space() -> None:
    """Read CSL superscript citations when citeproc removes the space before Cite."""
    document = {
        "blocks": [
            {
                "t": "Para",
                "c": [
                    {"t": "Str", "c": reply_build.CITATION_PROBE_SENTINEL},
                    {"t": "Space"},
                    {"t": "Str", "c": "smith2023machine"},
                    {
                        "t": "Cite",
                        "c": [
                            [],
                            [{"t": "Superscript", "c": [{"t": "Str", "c": "1"}]}],
                        ],
                    },
                ],
            }
        ]
    }

    resolved = reply_build.extract_probe_map(
        document,
        reply_build.CITATION_PROBE_SENTINEL,
        ["smith2023machine"],
        ("???",),
    )

    assert resolved == {"smith2023machine": "^1^"}


def test_prepare_line_source_pdf_uses_soffice_on_non_windows(tmp_path, monkeypatch) -> None:
    """Convert DOCX line sources with soffice when Word COM is unavailable."""
    source_docx = tmp_path / "manuscript.docx"
    write_test_docx(source_docx)
    pdf_dir = tmp_path / "reply-line-source-pdf"
    cache_dir = tmp_path / "line-source-cache"
    calls = []

    def fake_run(cmd, **kwargs):
        """Pretend soffice created the PDF path that its CLI derives from DOCX stem."""
        calls.append((cmd, kwargs))
        outdir = Path(cmd[cmd.index("--outdir") + 1])
        outdir.mkdir(parents=True, exist_ok=True)
        (outdir / "manuscript.pdf").write_bytes(b"%PDF")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(reply_line_source.sys, "platform", "linux")
    monkeypatch.setattr(reply_line_source, "line_source_pdf_dir", lambda: pdf_dir)
    monkeypatch.setattr(reply_line_source, "line_source_cache_dir", lambda: cache_dir)
    monkeypatch.setattr(reply_line_source.subprocess, "run", fake_run)

    result = reply_build.prepare_line_source_pdf(source_docx)
    cached_result = reply_build.prepare_line_source_pdf(source_docx)
    pdf_key = reply_build.docx_line_source_pdf_cache_key(source_docx)

    assert result == pdf_dir / f"manuscript.{pdf_key[:12]}.pdf"
    assert cached_result == result
    assert result.exists()
    assert (cache_dir / "pdf" / f"{pdf_key}.pdf").exists()
    assert calls == [
        (
            [
                "soffice",
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(pdf_dir.resolve()),
                str(source_docx.resolve()),
            ],
            {
                "capture_output": True,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
            },
        )
    ]


def test_docx_line_source_cache_key_ignores_volatile_package_timestamps(tmp_path) -> None:
    """Reuse PDF cache keys when only DOCX package and core timestamps change."""
    source_docx = tmp_path / "manuscript.docx"
    write_test_docx(source_docx)
    first_key = reply_build.docx_line_source_pdf_cache_key(source_docx)

    write_test_docx(
        source_docx,
        created="2026-07-15T04:49:21Z",
        zip_timestamp=(2026, 7, 15, 4, 49, 20),
    )

    assert reply_build.docx_line_source_pdf_cache_key(source_docx) == first_key


def test_docx_line_source_cache_key_changes_with_document_content(tmp_path) -> None:
    """Invalidate the PDF cache when layout-relevant DOCX content changes."""
    source_docx = tmp_path / "manuscript.docx"
    write_test_docx(source_docx, document_text="first manuscript")
    first_key = reply_build.docx_line_source_pdf_cache_key(source_docx)

    write_test_docx(source_docx, document_text="revised manuscript")

    assert reply_build.docx_line_source_pdf_cache_key(source_docx) != first_key


def test_prepare_line_source_pdf_builds_markdown_before_pdf(tmp_path, monkeypatch) -> None:
    """Convert Markdown line sources through a temporary DOCX before PDF extraction."""
    source_markdown = tmp_path / "manuscript.md"
    source_markdown.write_text("# Manuscript\n", encoding="utf-8")
    docx_dir = tmp_path / "line-source-docx"
    pdf_dir = tmp_path / "line-source-pdf"
    cache_dir = tmp_path / "line-source-cache"
    calls = []

    def fake_build_markdown_line_source_docx(source, target):
        """Pretend the normal manuscript DOCX build created the intermediate file."""
        calls.append(("build", source, target))
        write_test_docx(target)

    def fake_export_docx_to_pdf_with_word(source, target):
        """Pretend Word exported the intermediate DOCX to PDF."""
        calls.append(("pdf", source, target))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"%PDF")

    monkeypatch.setattr(reply_line_source, "line_source_docx_dir", lambda: docx_dir)
    monkeypatch.setattr(reply_line_source, "line_source_pdf_dir", lambda: pdf_dir)
    monkeypatch.setattr(reply_line_source, "line_source_cache_dir", lambda: cache_dir)
    monkeypatch.setattr(reply_line_source.sys, "platform", "win32")
    monkeypatch.setattr(reply_line_source, "build_markdown_line_source_docx", fake_build_markdown_line_source_docx)
    monkeypatch.setattr(reply_line_source, "export_docx_to_pdf_with_word", fake_export_docx_to_pdf_with_word)

    result = reply_build.prepare_line_source_pdf(source_markdown)
    cached_result = reply_build.prepare_line_source_pdf(source_markdown)

    expected_docx = docx_dir / "manuscript.docx"
    pdf_key = reply_build.docx_line_source_pdf_cache_key(expected_docx)
    expected_pdf = pdf_dir / f"manuscript.{pdf_key[:12]}.pdf"
    assert result == expected_pdf
    assert cached_result == expected_pdf
    assert not (cache_dir / "docx").exists()
    assert (cache_dir / "pdf" / f"{pdf_key}.pdf").exists()
    assert calls == [
        ("build", source_markdown, expected_docx),
        ("pdf", expected_docx, expected_pdf),
        ("build", source_markdown, expected_docx),
    ]


def test_prepare_line_source_pdf_reports_missing_word_on_windows(tmp_path, monkeypatch) -> None:
    """Give Windows users an install instruction before Word COM conversion fails."""
    source_docx = tmp_path / "manuscript.docx"
    write_test_docx(source_docx)

    monkeypatch.setattr(reply_line_source.sys, "platform", "win32")
    monkeypatch.setattr(reply_line_source, "word_com_registration_status", lambda: False)

    with pytest.raises(RuntimeError, match=r"Microsoft Word.*Please install Microsoft Word"):
        reply_build.prepare_line_source_pdf(source_docx)


def test_render_reply_txt_markdown_keeps_markdown_but_removes_output_only_syntax() -> None:
    """Keep TXT-safe Markdown while replacing images and removing block labels."""
    markdown = """<br>

::: {custom-style="Reply to Reviewers"}
**Bold** and _emphasis_ stay.<br/>
:::

1\\. Ordered item

![Layout](figures/layout.png){#fig:layout width=80%}

Table: Metrics {#tbl:metrics}

Next paragraph after caption.

| A | B |
|---|---|
| 1 | 2 |

: Table 8 Caption line.
3\\. Ordered item after caption

$$
a+b
$$ {#eq:sum}
"""

    text = reply_build.render_reply_txt_markdown(markdown)

    assert not text.startswith("\n")
    assert "**Bold** and _emphasis_ stay." in text
    assert "1. Ordered item" in text
    assert "1\\. Ordered item" not in text
    assert "[Image: Layout]" in text
    assert "Table: Metrics" in text
    assert "Table: Metrics\n\nNext paragraph after caption." in text
    assert "| A | B |" in text
    assert ": Table 8 Caption line.\n\n3. Ordered item after caption" in text
    assert "$$\na+b\n$$" in text
    assert "\n\n\n" not in text
    assert "custom-style" not in text
    assert ":::" not in text
    assert "<br" not in text.lower()
    assert "{#fig:layout" not in text
    assert "{#tbl:metrics}" not in text
    assert "{#eq:sum}" not in text


def test_build_reply_txt_resolves_placeholders_without_docx_equation_layout(tmp_path, monkeypatch) -> None:
    """Write resolved TXT output without DOCX-only equation tab markup."""
    reply = tmp_path / "reply.md"
    reply.write_text(
        """See Figure @fig:layout at (Line `stable prose`).

Prior work [@a; @b] remains relevant.

![Layout](figures/layout.png){#fig:layout width=80%}

$$ x+y $$ {#eq:sum}
""",
        encoding="utf-8",
    )
    manuscript = tmp_path / "manuscript.md"
    manuscript.write_text("# Manuscript\n", encoding="utf-8")
    style = tmp_path / "style.yml"
    style.write_text("reply: {}\n", encoding="utf-8")
    output = tmp_path / "reply.txt"
    flattened_style = tmp_path / "style.reply.flat.yml"

    monkeypatch.setattr(reply_resolve, "write_reply_style_metadata_file", lambda *args, **kwargs: flattened_style)
    monkeypatch.setattr(reply_resolve, "resolve_reference_map", lambda *args: {"fig:layout": "Figure 3"})
    monkeypatch.setattr(reply_resolve, "resolve_citation_map", lambda *args: {"a": "[1]", "b": "[2]"})
    monkeypatch.setattr(reply_resolve, "resolve_citation_cluster_map", lambda *args: {"[@a; @b]": "[1, 2]"})
    monkeypatch.setattr(reply_line_source, "resolve_line_regexes", lambda text, source: text.replace("(Line `stable prose`)", "(Line 42)"))

    reply_build.build_reply_txt(
        reply=reply,
        manuscript=manuscript,
        manuscript_line_source=manuscript,
        output=output,
        style=style,
        from_format="markdown",
    )

    text = output.read_text(encoding="utf-8")
    assert "See Figure 3 at (Line 42)." in text
    assert "Prior work [1, 2] remains relevant." in text
    assert "[Image: Figure 3 Layout]" in text
    assert "$$ x+y $$" in text
    assert "{#eq:sum}" not in text
    assert "<w:tab" not in text


def test_reply_output_format_rejects_unknown_suffix(tmp_path) -> None:
    """Require an explicit supported reply output suffix."""
    with pytest.raises(ValueError, match=r"use \.docx or \.txt"):
        reply_build.reply_output_format(tmp_path / "reply.md")


class FakePdfPage:
    """Minimal PyMuPDF page double for line-number extraction tests."""

    def __init__(self, text: str = "", blocks: list[dict] | None = None) -> None:
        self.text = text
        self.blocks = blocks or []

    def get_text(self, kind: str):
        """Return fake text or dict output matching the PyMuPDF API shape we use."""
        if kind == "text":
            return self.text
        if kind == "dict":
            return {"blocks": self.blocks}
        raise ValueError(kind)


class FakePdfDocument(list):
    """Context-manager list of pages with PyMuPDF-like metadata."""

    def __init__(self, pages: list[FakePdfPage], metadata: dict[str, str]) -> None:
        super().__init__(pages)
        self.metadata = metadata

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None


def fake_text_line(text: str, bbox: tuple[float, float, float, float]) -> dict:
    """Build a PyMuPDF dict text-line entry for layout matching tests."""
    return {"bbox": bbox, "spans": [{"text": text}]}


def fake_text_block(lines: list[dict]) -> dict:
    """Build a PyMuPDF dict text block containing fake line entries."""
    return {"type": 0, "lines": lines}


def test_extract_pdf_numbered_lines_uses_layout_for_libreoffice(tmp_path, monkeypatch) -> None:
    """LibreOffice PDFs list line numbers after body text, so pair by y coordinate first."""
    pdf = tmp_path / "line-source.pdf"
    pdf.write_bytes(b"%PDF")
    document = FakePdfDocument(
        [
            FakePdfPage(
                text="Body text A\nBody text B\n10\n11\n",
                blocks=[
                    fake_text_block(
                        [
                            fake_text_line("Body text A", (54.0, 100.0, 250.0, 112.0)),
                            fake_text_line("Body text B", (54.0, 120.0, 250.0, 132.0)),
                            fake_text_line("Footnote text", (54.0, 700.0, 250.0, 712.0)),
                            fake_text_line("10", (24.0, 100.0, 40.0, 112.0)),
                            fake_text_line("11", (24.0, 120.0, 40.0, 132.0)),
                            fake_text_line("1", (24.0, 700.0, 40.0, 712.0)),
                        ]
                    )
                ],
            )
        ],
        {"producer": "LibreOffice 25.2"},
    )
    monkeypatch.setitem(sys.modules, "pymupdf", SimpleNamespace(open=lambda _: document))

    assert reply_build.extract_pdf_numbered_lines(pdf) == [
        (10, 1, "Body text A"),
        (11, 1, "Body text B"),
    ]


def test_extract_pdf_numbered_lines_prefers_layout_for_word(tmp_path, monkeypatch) -> None:
    """Microsoft Word PDFs try layout matching before the text-order fallback."""
    pdf = tmp_path / "line-source.pdf"
    pdf.write_bytes(b"%PDF")
    document = FakePdfDocument(
        [
            FakePdfPage(
                text="Text-order body\n99\n",
                blocks=[
                    fake_text_block(
                        [
                            fake_text_line("Layout body", (54.0, 100.0, 250.0, 112.0)),
                            fake_text_line("10", (24.0, 100.0, 40.0, 112.0)),
                        ]
                    )
                ],
            )
        ],
        {"producer": "Microsoft® Word for Microsoft 365"},
    )
    monkeypatch.setitem(sys.modules, "pymupdf", SimpleNamespace(open=lambda _: document))

    assert reply_build.extract_pdf_numbered_lines(pdf) == [(10, 1, "Layout body")]


def test_extract_pdf_numbered_lines_falls_back_for_word_when_layout_empty(tmp_path, monkeypatch) -> None:
    """Microsoft Word PDFs use the old text-order logic when layout matching finds no lines."""
    pdf = tmp_path / "line-source.pdf"
    pdf.write_bytes(b"%PDF")
    document = FakePdfDocument(
        [
            FakePdfPage(
                text="Text-order body\n99\n",
                blocks=[fake_text_block([fake_text_line("Text-order body", (54.0, 100.0, 250.0, 112.0))])],
            )
        ],
        {"producer": "Microsoft® Word for Microsoft 365"},
    )
    monkeypatch.setitem(sys.modules, "pymupdf", SimpleNamespace(open=lambda _: document))

    assert reply_build.extract_pdf_numbered_lines(pdf) == [(99, 1, "Text-order body")]


def test_extract_pdf_numbered_lines_never_falls_back_for_libreoffice(tmp_path, monkeypatch) -> None:
    """LibreOffice PDFs rely only on layout matching because text order separates numbers."""
    pdf = tmp_path / "line-source.pdf"
    pdf.write_bytes(b"%PDF")
    document = FakePdfDocument(
        [
            FakePdfPage(
                text="Text-order body\n99\n",
                blocks=[fake_text_block([fake_text_line("Text-order body", (54.0, 100.0, 250.0, 112.0))])],
            )
        ],
        {"producer": "LibreOffice 25.2"},
    )
    monkeypatch.setitem(sys.modules, "pymupdf", SimpleNamespace(open=lambda _: document))

    assert reply_build.extract_pdf_numbered_lines(pdf) == []


def template_manuscript_pdf_path() -> Path:
    """Return the generated template/manuscript.md PDF used for extraction regression tests."""
    return Path("tests/fixtures/template-manuscript.pdf")


def expected_template_line_anchors() -> list[tuple[int, str]]:
    """Return stable anchors sampled from the generated template/manuscript.md PDF."""
    return [
        (16, "coherent narrative that guides readers from the general context"),
        (45, "caption attributes above are applied to the DOCX table"),
        (68, "procedure is useful. In this template, pseudocode"),
        (75, "synthetic trend chart in Figure 1"),
        (140, "authors declare no conflict of interest"),
    ]


def assert_extracted_line_anchors(numbered_lines: list[tuple[int, int, str]]) -> None:
    """Assert expected template PDF anchors are present at their sampled line numbers."""
    for expected_line, anchor in expected_template_line_anchors():
        matches = [line for line, _, text in numbered_lines if anchor in text]
        assert matches == [expected_line]


def test_template_manuscript_pdf_line_extractors_agree_on_line_regex_anchors() -> None:
    """Use the generated template PDF to verify both line-number extraction methods."""
    pymupdf = pytest.importorskip("pymupdf")
    pdf = template_manuscript_pdf_path()
    if not pdf.exists():
        pytest.skip(f"Build template/manuscript.md PDF first: {pdf}")

    with pymupdf.open(pdf) as document:
        layout_lines = reply_build.extract_pdf_numbered_lines_by_layout(document)
        text_order_lines = reply_build.extract_pdf_numbered_lines_by_text_order(document)

    assert_extracted_line_anchors(layout_lines)
    assert_extracted_line_anchors(text_order_lines)


def test_resolve_line_regexes_with_generated_template_manuscript_pdf(monkeypatch) -> None:
    """Resolve representative Line regexes against the generated template/manuscript.md PDF."""
    pytest.importorskip("pymupdf")
    line_source = template_manuscript_pdf_path()
    if not line_source.exists():
        pytest.skip(f"Build template/manuscript.md PDF first: {line_source}")
    monkeypatch.setattr(reply_line_source, "prepare_line_source_pdf", lambda path: path)

    markdown = "\n".join(
        [
            "Intro starts at (Line `coherent narrative that guides readers from the general context\\s+to your specific research`).",
            "Caption note at (Line `caption attributes above are applied to the DOCX table`).",
            "Pseudocode note at (Line `procedure is useful\\. In this template, pseudocode.*normal table`).",
            "Figure note at (Line `synthetic trend chart in Figure 1`).",
            "Disclosure note at (Line `authors declare no conflict of interest`).",
        ]
    )

    resolved = reply_build.resolve_line_regexes(markdown, line_source)

    assert "Intro starts at (Line 16)." in resolved
    assert "Caption note at (Line 45)." in resolved
    assert "Pseudocode note at (Line 68)." in resolved
    assert "Figure note at (Line 75)." in resolved
    assert "Disclosure note at (Line 140)." in resolved
