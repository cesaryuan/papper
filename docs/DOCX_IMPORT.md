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

Word's rectangular image crops are retained as editable image attributes by
default (`--image-crop attributes`). Original image files are preserved, and
the resulting Markdown uses attributes such as `crop-right="52.014"`. Values
are percentages of the original image, and `papper build docx` restores them as
Word cropping. HTML, LaTeX, and PDF currently show the original image in this
mode. You can select it explicitly with:

```bash
papper convert existing-paper.docx -o converted --image-crop attributes
```

To apply crops to the exported image files instead, use:

```bash
papper convert existing-paper.docx -o converted --image-crop apply
```

For example, a cropped PNG is exported with the hidden pixels removed. PNG, JPEG, GIF, BMP,
TIFF, and WebP sources are supported; cropped raster results are lossless PNG files.
GIF and TIFF use their first decoded frame/image. SVG and EMF crops retain their
vector format; common placeable WMF images are also supported. Vector crops
change the visible canvas and keep the original drawing content, so hidden
paths may still exist inside the exported file. They do not
rasterize vectors or remove hidden content irreversibly. Metafile crop edges
round to their native coordinate precision; GDI and GDI+ players can differ by a
reference-device pixel at an EMF's lower/right clip edges. WMFs with certain
coordinate transforms or an overriding embedded EMF are currently unsupported.
Unsupported or invalid crops retain the original image with a warning; use
attribute mode to keep their crops editable in DOCX.

Both modes leave the source DOCX unchanged and support independent
crops of a shared image. They recover rectangular DrawingML cropping, including
supported pictures in tables, notes, headers, and footers; legacy VML crops,
crop-to-shape masks, effects, and rotations are outside this support. See
[editable image crops](manuscript-syntax.md#editable-image-crops-in-docx) for the
attribute syntax and sizing rules.

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

Zotero document preferences also identify the source citation style. Convert
preserves that ID as `zotero-style-id`, finds the matching CSL locally or in the
public CSL repository, and writes `existing-paper.csl` beside the Markdown.
The generated YAML selects it with `csl: existing-paper.csl`. Local styles take
priority over downloaded versions; missing parent styles are resolved so the
exported CSL is usable offline. Explicit Zotero citation locales are retained.
Use `--zotero-data-dir 'D:/Zotero'` for a custom data directory or `--offline` to
prevent downloads. Missing styles produce a warning while preserving the style
identity and completing the content import. Reimports replace regenerated `.csl`
files. A public style's current version may differ from the original document's
version or local modifications; use the original local CSL when exact formatting
matters.

Without `--zotero-data-dir`, Convert checks profile-configured data directories
and `~/Zotero`, including hidden parent styles. `--zotero-data-dir` selects the
directory containing `styles/`. `--offline` permits installed styles and the
previously downloaded CSL cache, with no style downloads. Custom style IDs must
be available locally. The optional `zotero-style-locale` records the citation
language; it is applied to the exported CSL without changing manuscript `lang`.
If the style cannot be recovered, obtain the original `.csl` and set manuscript
`csl` before building to preserve its citation formatting.

Supported MathType OLE objects and WMF previews carrying MathType equation data become LaTeX math, including formulas with explicit MathType spacing and multiline subscripts. A supported preview can also recover a formula when its OLE object is missing or damaged. Unrecognized or undecodable equations retain their preview images.

Recognizable Word bookmark links become figure, table, equation, or section references, using the target labels recovered during import. Unknown links remain as imported by Pandoc. Supported equation layout tables are flattened, and confirmed numbered standalone formulas can become numbered display equations. For the detailed behavior, see [Importing Word Documents](manuscript-syntax.md#importing-word-documents).

Import does not require you to decode every formula successfully before inspecting the Markdown. Check warnings and retained images when reviewing a document with many equations.

Thesis symbol tables become editable equation-term lists. The label can be
`式中，`, and repeated hyphens such as `------` also work as the separator.

## Figures, tables, and captions

Import recognizes common manuscript layouts:

- A numbered `Figure`, `Fig.`, or `图` caption immediately after a single bare image.
- A numbered `Table`, `Tbl.`, or `表` caption immediately before a captionless table.
- Adjacent Chinese and English table captions above the same table. When their
  numbers agree, the Chinese title becomes the main caption and the English
  title is saved as `caption-en`. A missing Chinese chapter number can be
  recovered from the English caption if the remaining table number matches.
- One-column image/caption layout tables and supported multi-panel figure tables.
- Sequential images and child captions with a unique numbered overall caption.
- Inline images wider than 2 inches, which are moved after their paragraph as separate figures.

English caption prefixes are case-insensitive and allow a trailing period. Caption text and supported formatting are retained. Figure grid widths become percentages; original dimensions remain available in `original-width` and `original-height` attributes.

For a bilingual subfigure table, put the English subcaption after the Chinese
one, for example `(a) 基线` followed by `(a) Baseline`. Panel letters may be omitted
when both captions share the image's cell or appear in consecutive caption rows
aligned with its column. A manual line break (Shift+Enter in Word) can also
separate the two captions within a cell.
Pair the overall titles with the same figure number, such as `图 2‑3` and `Fig. 2‑3`.
Crossref export stores the translations as `caption-en`. SVG export places the
English panel text on a separate editable line and keeps the translated overall
title in the Markdown.
Text drawn into a source picture stays part of that picture.

A single image with matching Chinese and English captions in a layout table
becomes a normal Markdown figure with `caption-en`. In a multi-image table,
`--svg` can keep a bilingual `(a)` caption in a merged row as one editable text
element centered below the pictures it describes. For adjacent full-width
overall captions, import can recover a missing Word number field such as
`图 ‑14` or `Fig. ‑.` from the other language's complete chapter and figure
number when any remaining digits agree. In a grid whose images and two caption
rows align column by column, `--svg` can pair the captions despite one differing
English panel letter in the row. The Chinese letter remains the panel label and
the original English letter stays in the editable English text. Figure tables
whose captions cannot be matched unambiguously remain intact.

Use `papper convert paper.docx -o converted --subfigure-svg` (also `--svg`) to
export recognized groups as editable combination figures. A group with ID
`fig:comparison` produces `converted/media/subfigure-comparison.svg`. The SVG
links to the source pictures in the same directory. Keep `media/` together when moving the manuscript.
TIFF subfigure sources use PNG copies of the first image at its original
pixel dimensions so the panels display and remain editable in the SVG.
Hover over the Markdown image in VS Code and choose **Edit subfigure layout** in
Papper Tools. The editor restores the panels, labels, editable crops and formatted
subcaptions. The overall caption and figure ID stay in the manuscript.
Groups with content outside the editor’s caption or image formats retain their
crossref layout. A changed existing SVG at the same path is preserved and the
import reports the conflict. Choose another output directory to keep both versions.

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
