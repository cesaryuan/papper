# Importing DOCX manuscripts

[Back to the README](../README.md) · [Manuscript guide](manuscript-syntax.md#importing-word-documents)

Use `papper convert` to move an existing Word manuscript into editable Markdown, recovering supported equations, captions, images, and references. Import focuses on recovering content; compare the result with the original before continuing your revisions.

## Start with a separate output directory

```bash
papper convert existing-paper.docx -o converted
```

The result contains:

```text
converted/
├── existing-paper.md
└── media/
```

Only images still needed by the Markdown are saved. MathType recovery does not require a MathType installation.

Existing media are preserved. Identical images reuse their filenames; different images with a conflicting filename receive a new name and updated Markdown links. User-edited images are preserved too. **Reimporting the same DOCX replaces its generated Markdown file.** Choose a different output directory if you want to keep an earlier Markdown version.

When the DOCX contains supported structured citation data, Convert also creates
`existing-paper.bib` in BibLaTeX format and selects it with
`bibliography: existing-paper.bib` in the Markdown YAML. In-text citations retain
matching keys such as `[@smith2023]`. No empty `.bib` is created for documents
without recovered bibliography data. Reimporting replaces the companion `.bib`
when it is regenerated, so keep manual edits in a separate output directory.
Plain-text references still need review; the final AI prompt asks your AI to
finish those while preserving already converted references and citations.

## Equations and references

Supported MathType OLE objects and WMF previews carrying MathType equation data become LaTeX math. A supported preview can also recover a formula when its OLE object is missing or damaged. Unrecognized or undecodable equations retain their preview images.

Recognizable Word bookmark links become figure, table, equation, or section references, using the target labels recovered during import. Unknown links remain as imported by Pandoc. Supported equation layout tables are flattened, and confirmed numbered standalone formulas can become numbered display equations. For the detailed behavior, see [Importing Word Documents](manuscript-syntax.md#importing-word-documents).

Import does not require you to decode every formula successfully before inspecting the Markdown. Check warnings and retained images when reviewing a document with many equations.

## Figures, tables, and captions

Import recognizes common manuscript layouts:

- A numbered `Figure`, `Fig.`, or `图` caption immediately after a single bare image.
- A numbered `Table`, `Tbl.`, or `表` caption immediately before a captionless table.
- One-column image/caption layout tables and supported multi-panel figure tables.
- Sequential images and child captions with a unique numbered overall caption.
- Inline images wider than 2 inches, which are moved after their paragraph as separate figures.

English caption prefixes are case-insensitive and allow a trailing period. Caption text and supported formatting are retained. Figure grid widths become percentages; original dimensions remain available in `original-width` and `original-height` attributes.

Ambiguous structures remain unchanged. Examples include multiple independent figure numbers in one layout, conflicting overall captions, row spans, and nested tables. Multiple images in an ordinary paragraph are not automatically paired with a following single-figure caption. Images separated from captions by prose are not paired either.

Relative or invalid image widths, images exactly 2 inches wide, and images already presented as separate figures are not moved by the inline-image rule. Ordinary data tables remain tables.

## Optional recovery of typed references

When a document uses plain text such as “see Fig. 2” instead of Word bookmark links, add:

```bash
papper convert existing-paper.docx -o converted --fuzzy-crossrefs
```

This option is disabled by default. It matches explicit reference phrases to uniquely numbered captions, including `如图1-14所示`, `如表1-3所示`, `如式4-43所示`, `see Fig. 2`, and `as in Eq. (4-43)`.

Recovered references reuse existing target labels when possible. For example:

```markdown
![图1‑14 Vulnerability](image.png)
```

can become:

```markdown
![Vulnerability](image.png){#fig:fuzz-1-14 original-number="图1‑14"}
```

The caption prefix and source number are kept in `original-number`; subsequent builds assign their own numbers. A standalone formula such as `$x=y$ (4-43)` can become `$$x=y$$ {#eq:fuzz-4-43}`, with typed references linked to that formula.

Duplicate caption numbers, unknown targets, and mentions without a reference cue remain text. Code, existing links and citations, formulas embedded in prose, multiple formulas in one paragraph, and descriptive trailing labels are not rewritten by this recovery rule. Inspect inferred references after import.

## Language, pagination, and page stories

When Han characters account for at least 60% of the letters and numbers in the body and notes, import adds `lang: zh-CN` to the Markdown header. Empty and picture-only documents receive no automatic language tag.

Supported manual page breaks, section breaks, page-number fields, headers, and footers can also be recovered. See the [manuscript guide](manuscript-syntax.md#importing-word-documents) for supported options and examples. Word recalculates dynamic fields when updated; import does not calculate page numbers.

Navigation links and table-of-contents bookmarks are cleaned up while retaining their visible text and page numbers.

## Review before rebuilding

Check equations that remain images, caption pairing, inferred references, and the order of multi-panel figures. Complex floating layouts, text boxes, columns, tab-stop layouts, and arbitrary Word styles are not guaranteed to survive import.

Keep the original DOCX. Import is a content migration step, not an automatic synchronization mechanism between Word and Markdown. If an older export damaged image links or layouts, reconvert the original Word document rather than treating that Markdown as the only source.
