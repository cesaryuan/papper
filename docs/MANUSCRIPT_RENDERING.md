# Manuscript Rendering Internals

This document is for maintainers of Papper's manuscript rendering pipeline.
Author-facing syntax lives in
[`manuscript-syntax.md`](manuscript-syntax.md) (`papper guide syntax`), and project
configuration lives in
[`style-configuration.md`](style-configuration.md) (`papper guide style`).
Keep pipeline order, source locations, intermediate artifacts, and rendering
algorithms here rather than in the user guides exposed by `papper guide`.

## DOCX Package Finalization

Manuscript and reviewer-reply builds use `docx::finalize_docx` to read the
Pandoc-generated package once. Formatting or mandatory content finalization
edits that package in memory, then MathType injection adds its OLE and WMF parts
to the same package. Ordinary OPC normalization still runs before equation
injection, preserving the former intermediate document's XML and declarations.
The final complete archive is published atomically once; failed conversion
retains the native equation, and failed publication preserves the previous output.
This avoids recompressing unchanged figures into an intermediate formatted DOCX.

When `PMT_MATHTYPE_WORK_DIR` explicitly requests manuscript inspection artifacts,
the build also publishes `<project-name>.marked.docx` before equation injection
and retains formula sidecars. That diagnostic mode intentionally writes an
additional archive. Standalone postprocessing and marked-DOCX conversion APIs
remain available for callers that already have separate input files.

## DOCX Custom Property Compatibility

Word treats custom property names case-insensitively. Pandoc may export distinct
metadata keys such as crossref's `numberSections` and the manuscript's
`numbersections` as conflicting entries in `docProps/custom.xml`, causing Word
to reject the document as unreadable even though its ZIP and XML are valid.
The package publisher retains the first name and appends a unique numeric suffix
to later collisions. Existing names are reserved before assigning suffixes;
values, property IDs and value types remain intact. This changes only exported
document properties, after Pandoc has consumed the original metadata keys.
Mandatory content finalization also performs this repair before the unchanged
file-copy shortcut, so disabling optional DOCX postprocessing cannot bypass it.
Equation-free MathType conversion applies the same check before its early return.
Packages without conflicting names keep their custom property part bytes unchanged.

## Author and Style Processing

The shared [`author_info.lua`](../pandoc/filters/shared/author_info.lua) filter
normalizes keyed and inline author affiliations for DOCX and HTML. Templates
place the resulting author content immediately after the title. Reply defaults
exclude this filter; fragment previews do not render metadata.

DOCX uses Pandoc's native `author` inlines for correspondence Notes and a separate
`papper-affiliations` MetaBlocks field for institution paragraphs. Pandoc 3.12
collects DOCX footnotes before rendering arbitrary metadata, so putting a Note
in a custom metadata block creates a dangling reference. Native author Notes
receive unique IDs alongside body notes. The filter places a hidden custom-mark
request in each correspondence Note. `docx/note_marks.rs` consumes that request,
sets `customMarkFollows` on its document reference, and replaces its note label
with `*`. This package finalization also runs when optional DOCX postprocessing
is disabled; author content and note allocation still come from Lua and Pandoc.
HTML uses a `papper-authors` MetaBlocks field and an inline contact paragraph.
The Rust DOCX/HTML postprocessors no longer insert author content or create
author styles. `Author`, `Affiliation`, `Correspondence`, and `Correspondence Text`
are defined in the bundled reference styles and can receive `docxStyle` overrides
before writing.

Caption typography comes from the reference DOCX styles, including
`Image Caption` and `Table Caption`. The bundled editable definitions are in
`pandoc/manuscript-template/reference-doc/word/styles.xml`, with the matching
`reference-doc.docx`. HTML styling also reads the reference DOCX definitions.

`docxStyle` overrides and page margins are applied to a temporary reference
DOCX before Pandoc conversion. Styles are edited in `word/styles.xml`, even
when `docxPageMargins` is null; the final DOCX postprocessor does not reapply
`docxStyle`. Pandoc inherits the prepared styles and sizes images against the
writable width of the final document. The source reference is left unchanged. Page
numbers use Word `PAGE` fields and the `page number` character style. Removing
page-number fields through the legacy visibility setting preserves other footer
content; builds without directory fields do not request a document-wide field update.

The CLI writes exact `docxHeader.content` and `docxFooter.content` into separate request-local Markdown files.
[`docx_footer.lua`](../pandoc/filters/docx/docx_footer.lua) uses Pandoc's YAML
metadata Markdown reader, preserving single-line inline parsing like `abstract`.
It appends separately bounded header/footer blocks after manuscript numbering/citeproc and before image
filters. The main DOCX writer supplies typography, numbering, media and links.
[`footer.rs`](../crates/papper-document/src/docx/footer.rs) removes boundary
paragraphs and moves the rendered blocks into each header/footer before body-specific
postprocessing. Styled page-marker runs become complex `PAGE` fields with a
validated general-format switch and a format-appropriate initial result;
enclosing Markdown emphasis is preserved. Header/footer relationships are rebuilt from
used body relationships, with shared media bytes and unique picture/bookmark IDs.
Document namespaces are copied to header/footer roots. Outer paragraphs use explicit
alignment; table cells keep their Markdown alignment. A final paragraph is added
after tables. Header/footer finalization runs even with optional postprocessing disabled,
so internal boundaries never become published body content. Word-invalid notes
and unsupported citations fail in the filter before publication.
A missing first-section primary header/footer receives a unique part and
relationship without changing first/even-page modes or section page numbering.
`null` bypasses edits to the corresponding story; bundled headers default to null. Header mappings default to empty content and centered alignment, independently of legacy footer visibility. Header references are inserted before footer references, as required by Word. Papper settings merge the new footer mapping
recursively but remove the lower-priority legacy `docxShowPageNumbers` field
when a new footer layer is supplied, or remove the lower-priority footer when
a legacy layer is supplied. A same-source `docxFooter`, including null, wins.
Explicit header/footer nulls survive reply override serialization and merging.

`docx_directives.lua` runs before global story rendering, after manuscript
numbering, and before final image handling. `docx_fields.lua` shares PAGE/DATE
and custom `field` validation and cached field markers with `docx_footer.lua`.
Custom instructions are passed through as single-line text, with an optional
plain-text cache; Word performs validation and evaluation on field update.
Native field finalization preserves the writer's enclosing run properties,
escapes instruction/result XML text, and preserves boundary spaces in results.
The result uses `w:t` even when it equals the instruction. Section story
blocks have separately numbered boundaries; `docx/directives.rs` consumes them
after global stories have been applied, creates unique OPC story parts and
relationships, and closes preceding sections with paragraph-level `sectPr`.
Options belong to the following section; page starts are removed when carrying
layout forward. Linked stories omit references; unlinked copies duplicate parts
and media relationships, including default/first/even variants. Shared section
content sets all variants; `headerDefault`/`headerEven`/`headerFirst` and matching
footer keys override individual references. Each separately rendered content
gets its own boundary and OPC part. Omitted variants inherit unless shared
content supplies them; explicit empty content creates a blank story.
First-page content sets section `titlePg`; `differentFirstPage` can disable it
later. On newly enabling first-page mode, unspecified first stories use the
effective normal story rather than a dormant first story. Explicit even content
enables `settings.xml`'s document-wide `evenAndOddHeaders`. If that mode was
previously disabled, the starting section's even references are first mapped to
its normal parts, so unaffected earlier sections and the other story retain
their appearance. Normal-only overrides before the first authored even variant
are also mirrored onto even references. Already enabled reference modes retain
their active variants.
`headerDistance`/`footerDistance` are validated as nonnegative Word lengths and
written as rounded twips in section `pgMar`, without replacing its body margins.
Mode and distance overrides carry forward; numbering starts do not. This
mandatory finalization runs even with optional body postprocessing disabled.

Figure/table lists use TOC style switches rather than SEQ labels, supporting
both static and native caption numbering. Primary-caption markers become
inherited paragraph styles after body/caption formatting; translated captions
retain their original styles, and panels use a separate style. This keeps list
entries live when captions are edited, without copying cached caption text into
TC fields. Directory fields are dirty and set `updateFields`; layout and final
directory results are computed by Word. DATE results are normalized only in
test snapshots because the actual published cache uses the build date.

Named character styles receive only run typography from `docxStyle`; reference
preparation must not create `w:pPr` on them. Paragraph styles also receive
paragraph formatting. HTML character-style targets likewise exclude paragraph
layout, including line height and alignment.

The shared `paragraph_format.lua` filter carries fenced-Div local typography
to each eligible paragraph after the ordinary style filters. Its payload keeps
the ordered scopes, allowing the Rust core to validate every scope, expand
dotted fields and merge only supplied properties. Inner first-line indent modes
replace inherited modes; conflicts within one scope remain errors. Ordinary
list and block-quote paragraphs inherit scopes; tables, figures and notes form
independent boundaries. The filter preserves IDs/classes and unrelated attributes.

HTML consumes empty span markers into inline CSS on each paragraph after named
style and reply processing. DOCX consumes hidden run markers after body formatting,
including the mandatory finalization path, and merges direct paragraph/run
properties without adding named styles. Existing run properties and properties
declared by the run's character-style ancestry take priority over local text
defaults. Story/note parts are finalized too when they contain markers. Local
exact line spacing and physical indents preserve CSS units independently of the
historical named-style compatibility conversions.
Direct physical indents explicitly zero their inherited character-unit counterparts;
Word otherwise lets nonzero style character indents override the authored lengths.
Zero character first-line indents also reset inherited physical first-line/hanging
values. The local-format Word visual contract checks PDF text positions as well
as XML values to protect this inheritance boundary.

Paragraph border shorthand is parsed in the core as complete side declarations
with Word eighth-point widths. `all` expands before local scopes merge; a whole
reset includes `between`, while a side declaration replaces its line as a unit.
Named DOCX border changes are collected before resolving `basedOn`, avoiding
configuration-order dependencies when a child changes only text distance.
Direct local borders resolve selected-style and existing paragraph borders first,
so changing a line retains inherited distance and changing distance retains lines.
Fixed-color replacements remove direct theme-color attributes that would otherwise
override the new RGB value. Border children are written in schema order.

HTML keeps paragraph borders separate from table geometry. Reference/configuration
side sets are inherited through typed styles, then combined with local paragraph
markers. A DOM pass joins matching adjacent paragraph sets across transparent
Divs, emits internal separators once, and moves inter-paragraph margin gaps into
padding to keep vertical rules continuous. Tables, cells, list items, quotes and
figures break groups. CSS approximates compound Word line styles and pagination;
it does not claim pixel-identical Word border geometry.

`build docx --reference-doc` overrides `PMT_REFERENCE_DOC`; otherwise the
bundled reference is selected. `docx_pipeline::prepare_reference` returns the
actual reference path, including a temporary copy with effective text styles
and page margins.
`--export-reference-doc [PATH]` atomically publishes that reference and returns
before engine discovery, reply resolution, image processing or MathType checks.
With no selected source and no default manuscript, metadata is loaded from an
empty input using project/bundled styles. Existing Markdown contributes only
its YAML metadata and reply mode. It does not export the
postprocessed manuscript or apply output-only line-number or footer
changes to the reference. Export path aliases are resolved before conversion
to prevent replacement of the manuscript, selected reference or final output.
Project style merges preserve explicitly supplied nulls, so
`docxPageMargins: null` can disable bundled margin overrides for a custom reference.

## Native Word Numbering and Bilingual Captions

The native cross-reference path uses Word `SEQ` fields for Arabic figure,
table, and equation numbers, and `REF` fields for their references. Numbered
headings use a multilevel list linked to heading styles; section references use
`REF ... \r \t \h`; `\t` suppresses list-label decorations such as `第` and
`章` so Word's updated references match crossref's cached numeric results.
Subfigure references combine the parent's number bookmark
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
| `mathtype` | Installed MathType's Windows TeX input OLE conversion. |
| `rust-sdk` | `mathtype-rust` generates OLE/MTEF, then the prebuilt helper's `sdk-xform-ole` generates WMF/JSON. |
| `auto` | Windows with MathType: try `mathtype`, then `rust`; otherwise use `rust`. |
| `both` | Compare `rust` and `mathtype` MTEF streams extracted from OLE, warn on differences, and use `mathtype` output. |

The `both` comparison excludes WMF previews and JSON metadata. Runtime
conversion does not build the .NET helper; Windows wheels contain its
executable. Paths that use the SDK require Windows, MathType registration, and
the helper.

The public `mathtype` method accepts the historical `set-data` and `setdata`
aliases. The C# helper still receives its `set-data` method argument, and the
equation cache retains that backend identity so the public rename reuses
existing native MathType artifacts.

### Equation Cache Identity

Equation caches live under
`<PAPPER_HOME>/projects/<project-key>/cache/mathtype/native-v2/`, where
`PAPPER_HOME` defaults to `~/.papper`. The project key hashes the canonical
project directory, with Windows case normalization. Moving a project therefore
selects a different cache namespace. Each entry stores OLE, WMF and metadata;
the manifest verifies all three content digests before reuse. Missing,
incomplete or corrupt entries are regenerated, and failed conversions are not
cached.

All backends key normalized TeX input, effective equation font size, generated
EQP preference file contents and the actual conversion method. `auto` and
`both` use the selected backend's cache, rather than separate mode-wide caches.
Additional inputs depend on the backend:

| Backend | Additional cache inputs |
| --- | --- |
| `mathtype` | C# helper executable content digest. |
| `rust-sdk` | Compiled equation-engine fingerprint and C# helper executable content digest. |
| `rust` | Compiled equation-engine fingerprint, SVG renderer, inline/display style, and, for Typst, both font selections plus explicit font-file content digests. |

The engine fingerprint is generated by
[`papper-platform/build.rs`](../crates/papper-platform/build.rs). It covers
equation-library sources/assets, platform integration sources, DOCX MathType
conversion sources, relevant Cargo manifests, the workspace lockfile, target,
build profile, target features and the MiTeX spec-generation feature. The root
`Cargo.toml` contains the Papper version, so even a version-only release bump
changes this fingerprint and invalidates `rust`/`rust-sdk` cache reuse. A cache
schema change can also select a new namespace or key version. Old entries are
left on disk rather than deleted by a key change.

The `mathtype` path does not include the Papper version or this engine
fingerprint. It can reuse entries across updates if conversion inputs, helper
bytes and the cache schema remain unchanged. The installed MathType application's
version/global preferences are not separately fingerprinted. Likewise, Typst
font selections by installed family name are keyed by name rather than the
installed font's bytes; content digests apply to explicit font-file paths.

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
[`papper-svg`](../crates/papper-svg/src/main.rs) renderer using `skia-safe` CPU
surfaces. `usvg` resolves CSS, DPI-relative units, font outlines and adjacent
resources before serializing a canonical SVG for Skia. Nested SVG images are
rendered recursively with a 16-level limit; WebP images are decoded separately
because the official Windows Skia SVG binary omits the WebP codec. There is no
resvg raster backend or fallback backend.

Image filters receive the same ordered resource roots as Pandoc, including an
explicit `--resource-path`. Font fingerprints are requested only when an SVG
contains text or potentially text-bearing nested images. Both explicit PNG
conversion and Pandoc's fallback cache include font identity for text images;
vector-only rendering skips loading the system font database.

Office-exported diagrams can contain hundreds of Gaussian-blur shadows whose
`userSpaceOnUse` filter regions cover the whole SVG viewport. Rasterization cost
then follows those intermediate regions, not the SVG file size or the image's
display width in Word. Skia handles these regions on CPU without the former
resvg pixel-loop bottleneck. Its native precompiled libraries are optimized
even in Rust development builds. A new fallback-cache namespace prevents reuse
of pixels produced by the previous backend. For direct helper diagnostics,
`PAPPER_SVG_TRACE=1` reports font loading,
SVG parsing, rasterization, and PNG encoding boundaries on stderr.

Skia 0.153.3 uses the official SVG+textlayout archive with GL enabled; no GPU
context is created. Linux adds X11 and macOS adds Metal to match published
archives. Linux build images provide fontconfig, FreeType and GL development
libraries. Windows helper builds use the dynamic CRT required by that archive;
the existing wheel dependency stage bundles its transitive release CRT DLLs.

On Windows, each Pandoc conversion or persistent worker belongs to an unnamed
kill-on-close Job Object whose non-inherited handle stays in the Rust owner.
The child starts suspended, joins the job, and resumes before conversion begins;
its SVG descendants inherit membership. Owner termination (including Ctrl+C)
closes the job handle even without Rust stack unwinding, and worker cleanup
closes its job before joining the protocol reader. Independent background
services do not belong to a conversion job.

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

The shared effective-metadata loader extracts the exact `papperSettings` key
from the input header and validates it through the same style-domain parser
used for files. Alternate container spellings are rejected before transport.
File-based base and reply settings are applied before this manuscript layer;
its own `reply` mapping is applied last for replies. Explicit nullable settings
are retained during this merge, and `reply: null` clears inherited reply
configuration. Font paths resolve against the Markdown source. Ordinary
header fields are merged last into the Pandoc domain, and the `papperSettings`
container is excluded from transported metadata. CLI and persistent HTML
requests both consume this effective snapshot.

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
