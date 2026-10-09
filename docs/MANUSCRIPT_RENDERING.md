# Manuscript Rendering Internals

This document is for maintainers of Papper's manuscript rendering pipeline.
Author-facing syntax lives in
[`manuscript-syntax.md`](manuscript-syntax.md) (`papper guide syntax`), and project
configuration lives in
[`style-configuration.md`](style-configuration.md) (`papper guide style`).
Keep pipeline order, source locations, intermediate artifacts, and rendering
algorithms here rather than in the user guides exposed by `papper guide`.

## Author and Style Processing

DOCX author formatting reads manuscript YAML mappings and inserts author names,
affiliations, and corresponding-author footnotes after the title. The native
implementation is in
[`authors.rs`](../crates/papper-document/src/docx/authors.rs).

Caption typography comes from the reference DOCX styles, including
`Image Caption` and `Table Caption`. The bundled editable definitions are in
`pandoc/manuscript-template/reference-doc/word/styles.xml`, with the matching
`reference-doc.docx`. HTML styling also reads the reference DOCX definitions.

Page margins are applied to the reference DOCX before Pandoc conversion so
Pandoc sizes images against the writable width of the final document. Page
numbers use Word `PAGE` fields and the `page number` character style. Removing
page-number fields preserves other footer content; the build does not request
a document-wide field update when Word opens the output.

## Native Word Numbering and Bilingual Captions

The native cross-reference path uses Word `SEQ` fields for Arabic figure,
table, and equation numbers, and `REF` fields for their references. Numbered
headings use a multilevel list linked to heading styles; section references use
`REF ... \r \h`. Subfigure references combine the parent's number bookmark
with the panel-letter bookmark; panels do not increment the figure sequence.

Figure and table sequence identifiers use trimmed `figureTitle` and
`tableTitle`, with `Figure` and `Table` as empty-label fallbacks. Equation
sequences use `Equation`. Reference bookmarks use `PapperRef-` followed by
nine random lowercase letters or digits, with collision checks within a build.
Numeric bookmark IDs are randomized in matched start/end pairs across XML
parts to reduce collisions when documents are combined.

For standard level-1 chapter prefixes, `STYLEREF` reads the heading number and
`SEQ \s 1` restarts item numbering by chapter. Custom chapter prefixes retain
Pandoc text with explicit sequence resets. Unsupported templates and non-Arabic
numbering retain their Pandoc result with a warning. Field results are cached
at build time so the document has usable numbers before Word updates fields.

With native numbering enabled, a bilingual caption's primary number uses the
item's `SEQ` field and the English caption uses a `REF` to the same bookmark.
Both captions therefore refer to one sequence item. Figure and table lists
retain the primary title once. Rendering is split between the shared
[`bilingual_captions.lua`](../pandoc/filters/shared/bilingual_captions.lua),
[`native_crossrefs.lua`](../pandoc/filters/docx/native_crossrefs.lua), and the
native DOCX post-processor under
[`docx/`](../crates/papper-document/src/docx).

## Table Attributes and Layouts

Pandoc's DOCX writer does not preserve arbitrary table attributes. The
[`docx_metadata.lua`](../pandoc/filters/docx/docx_metadata.lua) filter transports
supported settings through hidden WordprocessingML markers. The native
[`tables.rs`](../crates/papper-document/src/docx/tables.rs) implementation
applies the attributes and removes the markers from the final document.

The shared
[`table_autofit.lua`](../pandoc/filters/shared/table_autofit.lua) filter runs
before crossref creates equation and subfigure layout tables. It supplies
defaults only to authored tables; post-processing does not supply a second
default. HTML retains `data-autofit`, allowing `window` tables to use full
width while other tables retain natural or authored widths.

The shared
[`merge_table_cells.lua`](../pandoc/filters/shared/merge_table_cells.lua)
processes `!<!` before `!^!` and updates the AST before DOCX, HTML, and LaTeX
writers run. A marker cell must contain only its marker.

Pandoc writes a table's `custom-style` value as a Word style ID. The bundled
borderless table style uses `TableNoBorder` for both its display name and ID
to avoid ambiguity. `Table Text` is a separate paragraph style used inside
cells; its definition and inheritance are preserved.

The shared
[`subfigure_layout_styles.lua`](../pandoc/filters/shared/subfigure_layout_styles.lua)
marks tables inside subfigure groups, including nested tables, with
`TableSubfigure`. HTML keeps `data-custom-style="TableSubfigure"` so subfigure
layouts can remove ordinary table borders and cell padding.

## Equations and Revision Markers

The shared [`fix_math.lua`](../pandoc/filters/shared/fix_math.lua) filter runs
before crossref and DOCX MathType markers in all three build defaults. It
converts scoped legacy `\rm` declarations to `\textrm` and moves `\hat` through
complete math-font wrappers before equations reach Word math or MathType.
Unmatched groups and operands with extra terms or scripts are not reordered.

[`equation_revision_attr.lua`](../pandoc/filters/docx/equation_revision_attr.lua)
extracts `revision=true` before crossref handles equation attributes, preserving
the equation label. DOCX metadata transports the revision state to the native
post-processor, which colors the native Word equation. This coloring does not
survive conversion to MathType OLE objects.

DOCX equation layout is derived automatically using a three-column layout table
with a separate text number. This keeps Word math and MathType equations
centered while native numbering fields remain right-aligned. Copied, labeled
equations in reviewer replies use the manuscript's number with a DOCX tab-stop
layout instead.

MathType's manuscript template and the copied-equation reply resolver emit only
tab runs around the formula and number. The native `Para Equation` style supplies
the center/right tab stops measured from the final DOCX section, together with
vertical character alignment and equation spacing. Raw inline XML must not emit
`w:pPr`: Pandoc already owns that paragraph's property block, and a second block
can prevent Word from applying the named style on open.
Reply resolution rewrites copied display math as InlineMath for this tab layout.
The MathType converter recovers display context from the paragraph's selected
`Para Equation` style before generating the object, so it does not apply the
baseline shift used for formulas embedded in prose. Reapplying the paragraph
style in Word otherwise clears that direct shift and changes the visible layout.

Word properties are inserted in schema order rather than appended after existing
layout properties. Each author footnote reference keeps the single `w:rPr`
created for its run. Native math revision color is stored in `m:r/w:rPr`, beside
the math-specific `m:rPr`; nesting Word properties inside `m:rPr` is invalid.

[`paragraph_custom_styles.lua`](../pandoc/filters/shared/paragraph_custom_styles.lua)
recognizes `where` paragraphs after display equations or equation-layout tables
and marks them with `Para Where`. HTML uses this marker to remove the usual
first-line indent.

## MathType Conversion and Preview Geometry

Conversion orchestration and caches are managed by
[`backend.rs`](../crates/papper-document/src/docx/mathtype/backend.rs).

| Method | Artifact path |
| --- | --- |
| `rust` | LaTeX to `mathtype-rust` OLE/MTEF, plus LaTeX to SVG to `latex2wmf` WMF/JSON. |
| `set-data` | Installed MathType's Windows TeX input OLE conversion. |
| `rust-sdk` | `mathtype-rust` generates OLE/MTEF, then the prebuilt helper's `sdk-xform-ole` generates WMF/JSON. |
| `auto` | Windows with MathType: try `set-data`, `rust-sdk`, then `rust`; otherwise use `rust`. |
| `both` | Compare `rust` and `set-data` MTEF streams extracted from OLE, warn on differences, and use `set-data` output. |

The `both` comparison excludes WMF previews and JSON metadata. Runtime
conversion does not build the .NET helper; Windows wheels contain its
executable. Paths that use the SDK require Windows, MathType registration, and
the helper.

RaTeX parses LaTeX directly into outlined glyphs and provides layout depth for
Word baseline placement. It preserves inline versus display math style, so
fractions, operators, and limits retain the corresponding layout. Typst uses
the pinned MiTeX 0.2.7 converter and matching official MiTeX scope embedded in
the executable, with separately configurable body and calligraphic fonts.
The bundled defaults select the Typst preview renderer.

Typst reads the labeled formula frame's descent before page composition loses
child baselines, and expands the transparent canvas for overhanging glyph ink.
Both renderers reject SVG features outside the formula vector subset rather
than silently rasterizing unsupported features.

Preview geometry starts with a `0.02em` glyph-overshoot safety margin and expands
transparent canvas space until width and baseline-side extents align with
Word's half-point grid. After Typst's adaptive WMF clipping protection, inline
previews may receive additional bottom whitespace to restore baseline-grid
alignment. This final step does not move, scale, or trim formula paths; display
previews skip it.

## SVG Resources and Metadata Transport

DOCX SVG child-image embedding writes self-contained SVGs under
`.pmt/cache/svg-embedded/`, embedding child resources as data URIs while keeping
text and vector elements. Rasterization writes PNGs under
`.pmt/cache/svg-png/`. Source Markdown and SVG files are not rewritten.
Nested SVG children trigger parent rasterization because Word cannot render an
SVG data URI nested inside an SVG. Global rasterization disables embedding.

The filters are
[`svg_embed_images.lua`](../pandoc/filters/docx/svg_embed_images.lua) and
[`svg_to_png.lua`](../pandoc/filters/docx/svg_to_png.lua). Rasterization is
provided by the native
[`papper-svg`](../crates/papper-svg/src/main.rs) renderer using Rust `resvg`.

Image filters receive the same ordered resource roots as Pandoc, including an
explicit `--resource-path`. Font fingerprints are requested only when an SVG
contains text or potentially text-bearing nested images. Both explicit PNG
conversion and Pandoc's fallback cache include font identity for text images;
vector-only rendering skips loading the system font database.

## Import Media and Package Publication

The equation-table filter flattens confirmed layout rows to InlineMath, retaining
their labels and bookmarks. The cross-reference filter owns promotion to
DisplayMath: a confirmed bookmarked equation at the start of its paragraph is
promoted, including one with a descriptive label, so its exported ID resolves on
rebuild. Equations with preceding prose retain their inline context; unbookmarked
flattened equations remain inline unless fuzzy reference recovery identifies them.

The final convert filter,
[`media_paths.lua`](../pandoc/filters/convert/media_paths.lua), compares imported
media with the destination before Pandoc extracts it. Conflicting bytes receive
a content-hash suffix; an edited hash-named file receives an additional numeric
suffix. References and media-bag entries change together. Native publication
uses no-clobber writes and permits identical bytes, so a concurrent conflicting
import fails safely and can be retried without replacing prior media.

DOCX package saves borrow unchanged binary parts and stream ZIP members and the
central directory into a temporary file beside the destination. XML parts that
require normalization are serialized separately. Only a completed archive
replaces the output; an interrupted write retains the previous document. This
avoids cloning every image and retaining a second complete compressed archive
in memory during publication.

LaTeX resource copying uses the effective resource roots and the actual output
file's parent directory. Absolute paths and paths outside the relative output
tree receive portable URLs under `resources/<source-path-hash>/`.

## Pandoc Metadata Transport

Generated Pandoc-only metadata is written under `.pmt/work/`; source
`style.yml` is preserved. Citation range formatting passes the top-level
`citationNumberRangeDelimiter` to the filter through
`PMT_CITATION_NUMBER_RANGE_DELIMITER` rather than adding it to Pandoc metadata.
Language selection similarly removes `lang` from metadata passed to Pandoc;
a temporary cleaned Markdown copy avoids localization warnings when the
manuscript header contains `lang`.

## Automatic Reply Builds

HTML/DOCX builds recognize only the input header `reply` path. Paths are resolved
beside the reply, and the selector is excluded from transported Pandoc metadata.
HTML reply postprocessing mirrors Word's blue italic captions/table runs and
blue `Para Where` styling after custom CSS generation. Inline declarations on
text-bearing elements also override nested character and revision styles;
formula, SVG, script and stylesheet contents are left to their own renderers.
Shared reply resolution and PDF line lookup live in `papper-document::reply`.
DOCX output applies reply styling and disables manuscript-native bookmarks.
HTML uses the same defaults with crossref/citeproc numbering passes omitted;
copied equations retain their source number through a display-math tag.

Persistent HTML requests resolve replies against current manuscript and line-source
contents on every request, bypassing the manuscript-only output cache. This also
supports editor buffers that add or remove the reply header without restarting
the server. `build-reply` remains hidden for compatibility and TXT exports.
