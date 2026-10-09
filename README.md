[English](README.md) | [简体中文](README.zh-CN.md)

# Papper

Write in Markdown. Submit in Word.

Papper is a DOCX-first academic writing workflow built for the AI era. AI tools are already great at drafting, revising, and restructuring Markdown. The problem is that many journals, editors, and collaborators still expect `.docx`. Papper bridges that gap: you keep the clarity and version-control friendliness of Markdown, while generating submission-ready Word documents when it is time to deliver.

The CLI, configuration, HTML Server, document post-processing and integration
layers are native Rust. Lua filters, the Haskell Pandoc worker and the Windows
C# MathType helper retain their existing implementations. PyPI wheels install
direct `papper` and `pmt` native executables through `uv tool`; commands do not
start a Python interpreter.

<!--
Hero image idea for the README:
- Use a wide 3-panel workflow graphic instead of a logo-only banner.
- Left panel: a clean Markdown manuscript in an editor, with citations, cross-references, and a short AI chat prompt visible.
- Middle panel: a terminal running `papper build docx` and `papper build docx reply.md`.
- Right panel: a polished Word manuscript page plus a reviewer-reply DOCX page.
- Add 3 short callouts on top of the image: "AI writes Markdown well", "Papper turns it into DOCX", "Journal-ready output".
- The most eye-catching version will show the same content flowing from raw Markdown to polished Word, not abstract icons.
-->

## Why This Exists

Markdown has become a very natural writing format for research teams, especially when AI is part of the drafting loop. It is easier to generate, review, diff, and refine than LaTeX for many authors. LaTeX is still powerful, but it is not always the most approachable tool for collaborators who mainly need to write and revise. Typst is promising, but it is not yet the default format most journals ask for.

DOCX, however, is still the format a lot of publishers, editors, and co-authors want.

Papper is built around that reality:

- Write the manuscript in Markdown.
- Keep sources easy for humans and AI to edit.
- Generate Word-first output for submission.
- Preserve the pieces academic writing actually needs: citations, equations, tables, figures, cross-references, and reviewer replies.

## Why Papper

Papper is not just a generic Pandoc wrapper. It is a manuscript workflow with opinionated support for the annoying parts of real submission work.

- **DOCX-first workflow**: the primary target is a polished Word manuscript, not DOCX as an afterthought.
- **AI-friendly authoring**: Markdown is easier for LLMs to generate and easier for humans to review in Git.
- **One-command project bootstrap**: `papper init` creates a reusable paper workspace with manuscript files, style metadata, references, and agent guidance.
- **Submission-oriented post-processing**: Papper applies format-specific cleanup and formatting after Pandoc runs.
- **Reviewer reply support**: add `reply: manuscript.md` to the YAML header and build response letters with `build html/docx`, resolving manuscript references and citations.
- **Bundled Pandoc engine**: one native binary provides the Pandoc CLI, embedded crossref, and the persistent HTML worker; platform wheels need no separate Pandoc downloads.
- **Optional HTML, LaTeX, and JSON output**: keep a Markdown-centered workflow without giving up other export targets.

## What You Get

- Manuscript scaffolding with `papper init`
- Environment checks with `papper doctor`
- Project-local tool setup with `papper setup`
- DOCX, LaTeX, and JSON builds with `papper build`
- DOCX-to-Markdown manuscript import with `papper convert`
- Reviewer reply builds with `papper build docx reply.md`
- Cross-references for figures, tables, equations, and sections
- CSL-based citations
- Reference DOCX support for Word styling
- DOCX post-processing for author blocks, table behavior, styles, and line-number-related workflows
- Tab-layout equation paragraphs automatically use `Para Equation`, based on `Body Text`, with 0.5 line spacing after and single line spacing during `papper build docx`.
  The style's center and right tab stops use half and all of the first DOCX section's writable width (page width minus left/right margins). Direct paragraph tab stops are removed so equations inherit the style's positions; rerun the step after changing page margins.
- SVG handling and DOCX fallbacks for figures that Word does not handle well
- Cross-platform MathType-compatible OLE/WMF equations, with an optional native MathType comparison path on Windows

## Convert a Word manuscript to Markdown

Use `papper convert` to import a DOCX into a Markdown file and a sibling `media`
directory:

```powershell
uv run papper convert "测试文档.docx" -o converted
```

This writes `converted/测试文档.md` and only the images still needed by the
Markdown under `converted/media`. The command combines ten bundled Lua
filters: it converts MathType OLE equations and MTEF-bearing WMF images to LaTeX, flattens one-row equation
layout tables, removes `_Toc...` bookmarks and navigation links, recovers subfigure layouts, extracts inline
images wider than 2 inches into separate figures, converts one-column image/caption
layout tables into figures, attaches an
immediately following `图1`, `图1‑11`, `Figure 1` or `Fig. 1-11` caption paragraph to a single bare
image (including a sole image inside a block quote created by Word left indentation),
attaches a preceding `表2‑1`, `表2`, `Table 2-1` or `Tbl. 2` paragraph to a captionless
table, and changes recognizable Word bookmark links to pandoc-crossref
references such as `[@fig:_Ref241620557]` and `[@eq:_Ref241620691]`. It keeps
ordinary tables and unrecognized links as Pandoc produced them. When an
equation cannot be decoded, its preview image remains available.

When another import already uses a media filename, identical image bytes reuse
the file; different bytes receive a new filename and updated Markdown links.
Existing images, including edited images, are preserved. Importing the same
DOCX again replaces its generated Markdown file.

Caption pairing retains the original text, formatting, `_Ref...` bookmarks and
image dimensions. English prefixes are case-insensitive and allow an optional
trailing period. TOC cleanup retains visible text and page numbers. Existing
captions, multi-image paragraphs and images separated from captions by prose
are not paired by the single-figure detector.

`detect_subfigures.lua` runs by default. It recognizes horizontal/vertical image
tables, multiple image rows, captions in separate rows or before/after an image
inside a cell, and sequential image/child-caption paragraphs. A unique numbered
overall caption must occur in the table caption, a merged cell, or the next
paragraph. Output uses pandoc-crossref's `::: {#fig:...}` syntax, one paragraph
per image row and the overall caption last, with `subfigGrid: true` metadata.
Bookmarks remain usable; missing IDs derive from the figure number. Grid widths
become percentages; source dimensions remain in `original-width` / `original-height`.
Unequal row lengths become vertical rows in source order because pandoc-crossref
can discard extra columns after its first row. Multiple independent figure
numbers, conflicting captions, row spans and nested tables remain unchanged;
caption conflicts are logged. Retained tables use pipe/grid Markdown to avoid
column boundaries splitting long image syntax in simple/multiline tables.
Reconvert the original DOCX when an older Markdown export already damaged images.

Typed figure/table/equation references can be recovered with the optional
`--fuzzy-crossrefs` flag (disabled by default):

```powershell
uv run papper convert "测试文档.docx" -o converted --fuzzy-crossrefs
```

This runs `crossrefs_fuzz.lua` after bookmark-based conversion. It matches unique
numbered captions to explicit prose phrases such as `如图1-14所示`, `如表1-3所示`,
`如式4-43所示`, `as shown in Fig. 2`, `see Tbl. 3` and `as in Eq. (4-43)`.
Existing `fig:`/`tbl:`/`eq:` IDs are reused;
unlabelled targets receive stable IDs such as `fig:fuzz-1-14` or `tbl:fuzz-1-3`,
with suffixes if those IDs are already used. English aliases, single numbers,
Unicode dashes and references split across formatting runs are supported. Duplicate
caption numbers, unknown targets and mentions without reference cues remain text.
Caption formatting, code, existing links/citations and other authored IDs are preserved.
The leading figure/table prefix and number move into an `original-number` attribute:
`![图1‑14 Vulnerability](image.png)` becomes
`![Vulnerability](image.png){#fig:fuzz-1-14 original-number="图1‑14"}`.
Table captions follow the same rule, keeping their table structure intact.

A standalone formula followed only by a parenthesized number, such as
`$x=y$ (4-43)` becomes `$$x=y$$ {#eq:fuzz-4-43}`; the authored number is
discarded after it has been used to recover references.
`如式 4-43` becomes `如[@eq:fuzz-4-43]`. Numbered standalone inline formulas
are promoted to display math so pandoc-crossref can number and resolve them.
Original equation numbers removed by bookmark conversion are retained internally
until recovery completes, allowing typed references to reuse an existing `eq:_Ref...`
ID. The temporary metadata is omitted from the output. Formulas embedded in prose,
multiple formulas in one paragraph and descriptive trailing labels remain authored.

An inline image wider than 2 inches is moved after its containing paragraph as a
separate figure. Absolute widths in common physical units are supported; images
with relative or invalid widths, exactly 2-inch images, and already standalone
figures remain where they were.

When Han characters make up at least 60% of the letters and numbers in the DOCX
body, footnotes, and endnotes, Convert adds `lang: zh-CN` to a leading YAML header
in the Markdown. Whitespace, punctuation, and formatting do not affect the ratio;
empty and picture-only documents do not receive an automatic language tag.

If an equation's OLE object is missing or damaged, Convert can recover MTEF from
the WMF preview's MathType comments. It validates WMF records and comment lengths;
ordinary WMF pictures, unrelated OLE attachments, and ambiguous shared OLE previews
remain images. Formula recovery does not require MathType to be installed.

MathType decoding uses the same directly linked Rust equation library as
LaTeX-to-MTEF export. `papper convert` decodes objects in its Rust process and
passes the results to Lua; wheels need no separate MathType DLL or executable.

To run the MathType Lua filter directly from a source checkout, put the native
`papper` executable on Pandoc's PATH:

```powershell
pandoc input.docx -f docx -t markdown -L pandoc/filters/convert/mtef_parser.lua -o converted.md
```

In standalone mode Lua invokes the native decoder once for the input batch.
Set `PAPPER_EXECUTABLE` to the native executable path if `papper` is unavailable
on Pandoc's PATH. DOCX equations are converted to Pandoc Math nodes, so the
Markdown writer emits their math delimiters without a separate unescaping step.

## Quick Start

### Prerequisites

Install these tools first:

1. `uv` for installing the platform wheel (the product itself runs natively)
2. A Papper platform wheel, which includes Pandoc 3.12 and embedded crossref
3. For line-number source workflows, Windows requires Microsoft Word; other platforms can use `soffice`.
4. Optional: MathType on Windows only if you select `rust-sdk`, `set-data`, `auto`, or `both`; the default `rust` path is self-contained

Platform wheels use the bundled `pmt-pandoc-worker` for all Pandoc conversions,
including DOCX/LaTeX/HTML/JSON builds, reviewer replies, and DOCX imports. Ordinary
arguments use Pandoc's CLI; the HTML service uses the same binary's private worker
mode. The standard `pandoc-crossref` filter runs in-process. `papper setup` and
`papper init --setup` validate this engine locally, even with `--force`; they do
not download additional Pandoc or crossref executables. `papper doctor` reports
the linked versions. Lua filters and custom external JSON filters remain supported.

Source checkouts prefer a binary built under `scripts/pandoc-server/dist-newstyle`,
then a worker installed in `~/.papper/tools/bin`. Rebuild older workers to enable
the CLI. An unbuilt checkout retains the legacy PATH/managed-tool fallback,
requiring Pandoc 3.11+ and a separate crossref; it can install shared copies into
`~/.papper/tools` when missing. An unusable bundled engine fails explicitly rather
than silently downloading a different conversion engine.
Reusable caches and persistent build state live under
`~/.papper/projects/<project-id>/cache` and `~/.papper/projects/<project-id>/work`.
The project ID is derived from its absolute directory, so projects cannot overwrite
each other's state. One-build intermediates such as MathType OLE/WMF previews and
line-source conversion files use the system temporary directory and are removed
when the Papper process exits. `papper clean` removes generated outputs and clears
the current project's work and caches, including MathType equation caches and the
legacy `.pandoc-cache`. Existing project-local `.pmt` or `.papper`
directories are not migrated or deleted automatically.
Legacy tool downloads and executable installation use temporary files followed by atomic
replacement, so an interrupted build can be rerun. Invalid cached archives are
discarded and downloaded again once; unusable managed executables are reinstalled.
The six formerly Python-based bundled filters run as Lua inside Pandoc. AST
transforms and resource handling share Lua modules; a separate `papper-svg`
native helper handles SVGZ decompression and PNG rendering when needed. It
carries no embedded runtime archive, and filters do not copy or launch the
complete `papper` executable or exchange whole documents through JSON.
DOCX builds also supply a bundled `rsvg-convert` PNG adapter on the conversion
child's PATH. Pandoc uses it to retain the original SVG and embed a PNG fallback
through `papper-svg`, without requiring a separate librsvg installation or changing
the system PATH. The adapter reuses content-addressed fallback PNGs under the
project cache when the SVG is self-contained; edits to the SVG, renderer, DPI, or
system fonts produce a new entry. SVGs with unresolved external image dependencies
continue through the normal renderer. `docxConvertSvgToPng: true` still replaces
the SVG with PNG when full rasterization is requested.
Fallback entries live in `cache/svg-rsvg` within the project state and are cleared
by `papper clean`. Set `PAPPER_SVG_CACHE=0` to bypass this adapter cache temporarily.
Cache hits skip SVG rendering and per-image system font loading; Pandoc still
launches its converter for each request. Font identity is collected once per DOCX
build. Unreadable or damaged caches fall back to rendering and cannot prevent a
successful document build.

Legacy tool downloads automatically use `HTTPS_PROXY` (or `https_proxy`) when set,
otherwise the configured Windows/macOS system HTTP/HTTPS proxy, and otherwise
a direct connection. This applies to both GitHub release metadata and archive
downloads during `papper build docx`, `papper setup`, and `papper init --setup`.
On Windows, enable your proxy application's **system proxy** option before
building; no extra Papper setting is needed. Papper logs `[TOOLS] Using system proxy for
downloads.` when it selects that route. An explicit `HTTPS_PROXY` takes precedence
over the system setting. PAC scripts and automatic proxy discovery are not
evaluated by this downloader.

### Source Development and Validation

Install the pinned Rust toolchain, GHC 9.14.1 and Cabal 3.18.1.0, then run:

```bash
cargo check --workspace --locked
cargo test --workspace --locked
cargo run -p papper-dev -- worker
uv sync
uv run pytest
```

`uv sync` installs a native development CLI. Python only drives integration
tests and inspects artifacts produced by Rust and the active Haskell/Lua filters.
Pytest builds the current Rust CLI once per session, so tests do not depend on
the installed development executable being up to date. `tests/legacy/` is a
historical archive excluded from active tests and development installations.
Build the worker once with the command above so native format checks use the
current pinned profile rather than an older full-format development binary.
After changing native CLI code, use `uv sync --reinstall-package papper` to
refresh that development executable. Release wheels omit the archive and
have no Python runtime dependencies. Build and validate a release wheel with
`cargo run -p papper-dev -- wheel --output dist` and
`cargo run -p papper-dev -- smoke --wheel <wheel-path>`.

Papper-owned configuration uses typed Rust fields and controlled updates;
historical YAML aliases and explicit false/null precedence remain supported.
Arbitrary Pandoc metadata stays separate. MathType OLE/MTEF conversion and
LaTeX-to-WMF rendering link Rust libraries directly, with owned results and
ordinary Rust errors. PDF geometry extraction links MuPDF 1.27.2 through
`mupdf-sys` 0.8.0 with PDF and Base14 fonts enabled. Its C exception wrappers
return native errors to the isolated Rust PDF helper; no MuPDF DLL is loaded.
Source builds require a C/C++ compiler and libclang, as described in
[DEVELOPMENT.md](DEVELOPMENT.md).

The bundled Worker registers Papper's Markdown, HTML, LaTeX, DOCX, JSON/native
and bibliography formats. Other Pandoc formats such as Org, EPUB, ODT, PPTX and
RST are excluded from its registry. This also applies to Lua reader/writer calls.
The source profile and build procedure are documented in
[scripts/pandoc-server/README.md](scripts/pandoc-server/README.md).

The former `pandoc_manuscript` Python import API and `python -m` utilities are
not included in native wheels. Use the public native commands or the Rust
workspace libraries for integrations. The reference remains available to
repository tests only. Existing manuscript files and style configuration
continue to work; native state uses a separate `work/rust-v1` namespace.
The background HTML service runs from an independent, content-addressed binary,
so `uv tool upgrade papper` can replace the Windows CLI entry points while the
service remains running. The next `papper build html --start-server` checks the
actual service version and binary identity. It gracefully stops an older native
service for the same project and starts the installed version on the same port,
preserving project outputs and disk caches. An unchanged service is reused;
another project's service is never stopped. Services started by releases before
this isolation fix still need to be stopped once before upgrading on Windows.

### Create Your First Project

```bash
uvx --from papper papper init my-paper
cd my-paper
papper doctor
papper build docx
```

For a Chinese manuscript and reviewer-reply starter, use `papper init my-paper --lang zh-cn`.

To initialize the manuscript project in the current directory, omit the target directory:

```bash
papper init
```

That produces:

```text
output/docx/manuscript.docx
```

If you prefer installing the tool once:

```bash
uv tool install --upgrade papper
papper init my-paper
```

After each `papper` invocation, Papper reads its cached PyPI update status and prints an upgrade hint when one is available. A silent background worker refreshes that cache at most once every hour, so commands do not wait for network I/O. Upgrade an installed Papper tool with:

```bash
uv tool upgrade papper
```

## Typical Workflow

```bash
# Create a new manuscript project
papper init my-paper --setup

# Check dependencies and project files
papper doctor

# Build the main manuscript
papper build docx

# Build a Chinese-primary DOCX with localized cross-references and chapter-numbered figures/tables
papper build docx --lang zh-cn

# Build another Markdown file explicitly
papper build docx paper.md -o build/paper.docx

# Choose a style file for this build
papper build docx paper.md --style-file styles/journal.yml

# Build one standalone HTML file with embedded resources
papper build html -o build/paper.html

# Pass a raw Pandoc resource path value through unchanged
papper build html --resource-path 'assets;shared-assets'

# Build a reviewer reply whose YAML header contains reply: manuscript.md
papper build docx reply.md -o output/docx/reply.docx
papper build html reply.md
```

HTML builds use the shared Pandoc filters for Chinese nested numbering and
`!<!`/`!^!` table-cell merge markers, plus HTML post-processing for author
information. DOCX also uses the shared AST filter for standalone inline-math
spacing before Word conversion.
HTML builds force native display-equation settings (`\tag`, `$$i$$`, no
inline block math, and no equation tables) so equations remain suitable for
the standalone HTML output.
The three-line table appearance comes from Pandoc's built-in standalone HTML
CSS. A direct Pandoc command needs `-s`/`--standalone` to include that CSS;
without it, Pandoc writes only an HTML fragment.

Chinese builds use the bundled GB/T 7714—2015 bilingual numeric CSL by default across DOCX, HTML, LaTeX, and JSON. An explicit `csl` in manuscript metadata or `style.yml` overrides it. Other builds use the bundled Elsevier Vancouver CSL unless overridden.

Papper selects language-specific Pandoc defaults after reading `style.yml` and
the manuscript YAML header. It then overlays `style.yml:pandocMetadata` and the
manuscript YAML in that order. Explicit values for captions, cross-reference
prefixes, bibliography titles, and `csl` therefore take priority over the
language defaults. For DOCX, `--lang zh-cn` selects Chinese defaults for that
build even when the manuscript declares another language.
The bundled `defaults/style.yml` and `defaults/style-cn.yml` provide the
English and Chinese build defaults. `papper init` creates a small project
`style.yml` for overrides; values omitted there come from the YAML selected
for the build language.
Chinese DOCX and HTML builds render section headings and section references
with dotted numbers such as `3.1`, while figures, tables, and equations retain
chapter numbers such as `3-1`. The JSON AST carries the resolved reference
text. LaTeX output keeps native `\ref` commands, whose displayed numbers are
determined when the TeX document is compiled.

## Standout Features

### 1. Markdown that stays pleasant to edit

Papper leans into plain-text authoring instead of fighting it. Your manuscript remains easy to diff, refactor, prompt into AI tools, and review collaboratively.

### 2. DOCX output that is actually the point

Many academic writing pipelines treat DOCX as a secondary export. Papper treats it as the main delivery format, with Word-oriented defaults and post-processing built into the workflow.

### 3. Better fit for real submission tasks

Papper goes beyond "convert Markdown to Word" by helping with the parts that tend to break late in the process:

- reviewer replies
- figure and table references
- equation numbering
- citation formatting
- Word reference documents
- DOCX figure edge cases such as SVG conversion or embedding

### 4. Friendly to automation without hiding the files

The output is scripted, reproducible, and version-controlled, but the source project still looks like a normal manuscript folder that a researcher can understand quickly.

## Documentation Map

Run `papper guide` to list syntax and style topics with brief descriptions. Read
only what you need, for example `papper guide syntax equations` or
`papper guide style docx-text-styles`. Parent topics list their subtopics;
`papper guide syntax advanced-table-formatting/cell-merging` reads one such
subtopic. Use `papper guide syntax --full` or `papper guide style --full` for a
complete guide. Topic names come from the current index.

- `papper guide syntax`: manuscript YAML and Markdown syntax, citations, cross-references, figures, tables, and revision markup ([source guide](docs/manuscript-syntax.md))
- `papper guide style`: project formatting, citation styles, DOCX options, and build settings ([source guide](docs/style-configuration.md))
- [`template/manuscript.md`](template/manuscript.md): example manuscript content
- [`AGENTS.md`](AGENTS.md): repository-specific guidance for coding agents
- [`docs/MANUSCRIPT_RENDERING.md`](docs/MANUSCRIPT_RENDERING.md): rendering internals for Papper maintainers

In generated projects, `style.yml` keeps Papper-owned build settings at the top
level and places metadata sent to Pandoc under `pandocMetadata`. Manuscript YAML
overrides only the Pandoc metadata domain.

Native Word cross-references are opt-in for manuscript DOCX builds. Set the
following at the top level of `style.yml`:

```yaml
docxNativeCrossref: true
```

The default `false` preserves the original Pandoc numbering and hyperlink
references. With `true`, figures, tables, and equations use `SEQ` numbering and
`REF` references. Numbered headings use a Word multilevel list linked to heading
styles, and section references use `REF ... \r \h`. Heading numbering still
respects `numberSections`, `sectionsDepth`, and unnumbered headings. Standard
level-1 chapter prefixes use `STYLEREF`, with `SEQ \s 1` restarting item numbers.
Figure and table sequence names follow the trimmed `figureTitle` and `tableTitle`;
empty titles fall back to `Figure` and `Table`. Equation sequences use `Equation`.
Numeric CSL bibliographies use `SEQ PapperBibliography` for their entry numbers;
citation numbers use `REF ... \h \* MERGEFORMAT` pointing to those numbers.
CSL brackets, superscripts, locators, and collapsed range delimiters are preserved;
range endpoints are separate REF fields. Author-date styles and unsupported
bibliography labels retain citeproc output. Explicit `link-citations: false` keeps
in-text citations as plain text. Word can renumber bibliography entries and their
REFs, but changing citation grouping, sorting, or CSL formatting requires a rebuild.
Native reference bookmark names use `PapperRef-` followed by nine random lowercase
letters/digits, with collision checks within each build. Bookmark
start/end IDs are randomized in matching pairs to reduce collisions when merging
documents, including bookmarks in footnotes, headers, and footers.
Update fields in Word after editing; forward references may need two updates.
Custom non-Arabic numbers and unsupported templates retain their Pandoc result
with a warning. This setting applies to `papper build docx`; other targets and
reviewer replies keep their existing workflow.

HTML and DOCX support bilingual figure/table captions through a `caption-en`
attribute, for example
`![中文题注](figure.svg){#fig:example caption-en="English caption"}` or
`: 中文表题注 {#tbl:example caption-en="English table caption"}`. The English
caption appears on a separate line with the same number; each object is counted
once and crossref's figure/table lists retain only the primary title. With native
Word references enabled, the English number references the primary number's
bookmark instead of introducing another `SEQ`. See the manuscript syntax guide
for inline Markdown, English caption styles, and supported scope.

HTML builds and previews read named Word styles from
`pandoc/manuscript-template/reference-doc/word/styles.xml`. A paragraph Div such
as `::: {custom-style="Reply to Reviewers"}`, an inline span such as
`[updated wording]{custom-style="Revision Char"}`, or a table caption attribute
such as `: Results {custom-style="TableNoBorder"}` selects the corresponding
paragraph, character, or table style by its Word display name. HTML generates
CSS only for custom styles used in that document and their `basedOn` ancestors.
Parent declarations share the selectors of their descendants; child rules
contain only changed properties and follow their parents in the CSS cascade.
Named `docxStyle` configuration overrides are applied before resolving this
inheritance: custom descendants inherit the configured parent, while explicit
child properties (including zero indentation) retain precedence.
Caption typography is shared by the caption container and its inner paragraphs;
paragraph spacing stays on the container to avoid applying it twice.
Generated comments identify the Word style id, its `basedOn` parent, target
roles, and any HTML adaptation. Configuration overrides are identified as
`docxStyle` rather than attributed to XML. Shared selector lists are printed
one selector per line, with equal-specificity heading tags grouped using `:is`.
Paragraph styles supply spacing, indentation, alignment, and font
formatting; character styles supply font formatting without paragraph layout.
Table styles supply outer and inner borders and cell margins.
Use `custom-text-style` to select the paragraph style inside a table, for example
`: Results {custom-style="TableNoBorder" custom-text-style="Body Text"}`.
It applies to header, body, and footer cell paragraphs in DOCX and HTML, including
paragraphs in lists and quotes. Captions and paragraphs outside the table keep
their own styles. Tables without this attribute retain the default `Table Text`
formatting. The underscore alias `custom_text_style` is also supported.
Table styles' top/bottom cell margins are added to the effective `Table Text` paragraph spacing;
per-table `cell_margin` attributes override the margins. User header styles
remain after the generated CSS. First-row cell borders from Word's `tblStylePr`
apply to the HTML table header and support inheritance and explicit removal.
Enabled borders with `w:sz="0"` use a 0.5pt HTML approximation of Word's
visible hairline; `w:val="nil"` or `w:val="none"` explicitly removes a border.
Unknown styles produce a warning and retain the normal HTML formatting. Other
Word conditional table regions (`tblStylePr`),
theme fonts/colors, and decorative Word border patterns are not fully reproduced.

## When Papper Is a Good Fit

Papper is especially useful if:

- you draft heavily with AI and want a format AI handles naturally
- you want Git-friendly manuscript sources instead of editing Word binaries directly
- your target journal still expects DOCX
- you need a repeatable manuscript and reviewer-reply workflow
- you want Pandoc power without forcing every collaborator into a LaTeX-first workflow

## Commands at a Glance

```bash
papper init [directory]
papper setup
papper doctor
papper build docx
papper build html
papper build latex
papper build json
papper build docx reply.md -o output/docx/reply.docx
papper clean
```

Use `papper --help` to see available commands, and `papper <command> --help`
(for example, `papper build --help`) to see argument descriptions, defaults,
and supported values.

Add `--verbose` to any command, for example `papper build docx --verbose`, to show
detailed debug logs such as complete external command lines. Normal output keeps
the main build stages, warnings, and results concise.

To start or reuse a local Pandoc HTTP server for an editor or extension while
building HTML, use:

```bash
papper build html --start-server
```

The command checks `http://127.0.0.1:3030/version`, reuses a responsive PMT
server, or starts the working-directory-bound PMT runtime. An explicit generic server can
still be selected with `PMT_PANDOC_SERVER_COMMAND`. The endpoint is printed in the build log and
supports the `/`, `/batch`, and `/version` API. Native state is recorded in
`~/.papper/projects/<project-id>/work/rust-v1/server-state.json`, configuration in
`server-config.json`, and output in the sibling `server.log`.

With `--start-server`, the HTML build itself uses `/convert/raw`, reusing a
persistent Pandoc worker and its prepared citation data. Identical configuration
does not restart the worker. The service reloads changed Markdown headers,
`style.yml`, defaults, Lua filters, bibliography, CSL, and template dependencies,
and applies the normal PMT HTML postprocessing. Markdown may live outside the
working-directory project: `--start-server` still starts or reuses that project's
service, and style/resource lookup retains the source file's directory context.
Builds without this flag use the single-shot CLI build.

Ordinary CLI invocations load only the selected command's settings; root help
still describes every command. HTML typography and margins share metadata
validation with DOCX without loading the Word document backend. The local server
client reuses a proxy-free HTTP opener and initializes certificate verification
only if an actual HTTPS request or redirect occurs.

Set a generic server command only when needed:

```powershell
$env:PMT_PANDOC_SERVER_COMMAND = 'C:\path\to\pandoc-server.exe'
papper build html --start-server --server-port 3030
```

The native engine under `scripts/pandoc-server` links Pandoc 3.12 and
pandoc-crossref 0.3.25, with a dependency-aware citeproc adapter. Platform wheels
include this CLI/worker, so PyPI installations need no GHC/Cabal or separate tool
installation. Worker discovery prefers `PMT_PANDOC_SERVER_WORKER_COMMAND`, then
the bundled engine, a source build, and `~/.papper/tools/bin` for development.
An older manually installed worker cannot shadow a newer wheel's worker.
Server configuration records the package version and resource identity.
Run `papper clean` in projects with an active service before upgrading; the
next build starts the newly installed native program and its matching worker.

For source-checkout development, with GHC/Cabal available, install it into
the user-managed tool directory with:

```powershell
Push-Location .\scripts\pandoc-server
cabal install . --installdir "$HOME\.papper\tools\bin" --overwrite-policy=always
Pop-Location
```

The PMT wrapper exposes a deliberately smaller API than the generic Pandoc
server. Use `GET /version` for health and `POST /convert` with
`{"path":"manuscript.md"}` for one result (the path defaults to
`manuscript.md` when omitted), or `POST /batch` with an array of
`{"path": ...}` objects. Relative paths resolve from the project that started
the server; absolute paths and relative paths to external Markdown are also accepted.
The response contains the generated HTML, after the same PMT HTML
post-processing used by `papper build html`, together with stage timings and
the HTML result-cache status.

For clients that want to avoid the JSON envelope, `POST /convert/raw` returns
the exact PMT HTML directly as `text/html`. Editors can send
`{"path":"manuscript.md","text":"Current unsaved Markdown"}` to render a buffer
without saving or creating a Markdown mirror. The path may be outside the project
and supplies the style/resource lookup context; omit `text` to read from disk.
An empty `text` renders an empty document. `GET /version` advertises
`source_text: true` and `project_dir` for safe editor reuse. JSON request bodies
are limited to 32 MiB. Conversions parse the complete
current source, preserving global heading IDs, link definitions, and footnotes.
They reuse prepared citation styles and bibliographies; prose-only changes can
also reuse citation evaluation while
applying the resulting citations to the new document. Unchanged successful
HTML is held in a bounded cache. `GET /metrics` distinguishes HTML and citation
cache reuse. Concurrent identical requests share the completed conversion.

Remote CSL parents and other remote citation assets are downloaded to the
project's Papper state directory. The service checks them every five minutes,
using conditional HTTP requests when validators are available; changed content
invalidates prepared citations and HTML. If validation fails after a successful
download, the last valid snapshot remains available and a retry follows after
30 seconds. First-time downloads and periodic validation can still add network
latency. Local bibliography, CSL, filter, and template edits are checked on each
request. Custom filters retain their normal execution path.

Every raw response includes `X-PMT-Cache`, `X-PMT-Citeproc-Cache`, `X-PMT-Mode`,
and `Server-Timing`. The citeproc header is `hit` or `miss` when the native worker
ran, and `skipped` on a whole-HTML cache hit. Timings cover dependency lookup,
native worker and filter stages, output reading, postprocessing, queue wait,
and total request time.

To reproduce warm HTTP benchmarks, including changed prose, changed citation
locators, and unchanged HTML, run from this checkout:

```powershell
uv run python scripts/benchmark_html_server.py --manuscript template/manuscript.md
uv run python scripts/benchmark_html_server.py --manuscript ../1-3d-mesh/manuscript.md
```

The script copies editable inputs into an isolated project, preserves all timed
samples, and verifies final HTML against fresh CLI conversions. Reports under
`output/benchmarks/server/` exclude worker startup and independent CLI checks.
This historical script compares retained Haskell workers through the frozen
Python frontend; it does not measure the native Rust CLI or its HTTP frontend.
Current migration measurements and their executable/configuration boundaries
are recorded in [RUST_MIGRATION_STATUS.md](RUST_MIGRATION_STATUS.md).
Use `--baseline-worker PATH` to compare an older wrapper through the same HTTP
frontend, or `--no-compare` to measure only the current worker. See
[the native worker notes](scripts/pandoc-server/README.md) for cache boundaries,
build requirements, profiling, and source attribution.
Recorded warm-request improvements and measurement limits are described in
[the performance note](scripts/pandoc-server/PERFORMANCE.md).

## Maintainer releases and native build caches

Release wheel builds require the Typst CLI version locked for `typst-library`
in `Cargo.lock`. CI installs that version from the official release archives.
For local packaging, run `uv run --script tools/ci/prepare-typst.py` and add the
repository's `.pmt/typst/bin` directory to `PATH` before running `papper-dev wheel`.
The packager rejects a missing or mismatched CLI, removes cached MiTeX release
specifications, and enables `papper-platform/generate-mitex-spec` so the upstream
prebuilt specification cannot silently enter a newly compiled wheel. Installed-wheel
checks require circled math operators to become MathType objects without OMML fallback.

Release with `uvx bump-my-version bump patch` followed by
`git push origin main --tags`. The **Publish to PyPI** workflow publishes only
on `v*` tag pushes. Its `main` builds and manual runs build and verify the same
three platform wheels without publishing them, retaining the tested wheel
artifacts for seven days. A tag run waits for the `main` build of the exact same
commit and publishes those wheels without compiling the platforms again. If no
matching build appears, that build failed, or its artifacts expired, the tag run
builds and checks fresh wheels. An unfinished matching build has a bounded wait;
rerun the tag workflow if that wait expires.

Changes to either Rust submodule, the Haskell worker, the native build hook, helper inputs, or the
publishing workflow trigger cache warming on `main`. To warm or refresh caches
manually, run **Publish to PyPI** with **Run workflow**, selecting `main`.
GitHub allows tag builds to restore default-branch caches, but not caches saved
under other tags. Pushing `main` and a tag together is supported: the tag waits
for the tested artifacts instead of racing the branch's cache warm-up.

The workflow pins Rust to `1.98.0` and caches Cargo downloads and release build
outputs separately for Windows, macOS 14, and manylinux 2.28. Cache keys include
the toolchain, native lockfiles, Rust sources, embedded resources, submodule
revisions, build configuration, and equation preferences. Source changes create
new immutable snapshots even when dependencies stay unchanged; older compatible
snapshots seed those builds. Only successful `main` builds save caches. After all
platform builds succeed, cache housekeeping keeps the newest snapshot per
platform and cache kind, including replacing the legacy release cache layouts.
Unrelated workflow caches and caches under tag refs are preserved.

The workflow also pins GHC to `9.14.1` and Cabal to `3.18.1.0`, compiles the HTML
worker on all three platforms, and caches its Haskell dependencies, package
index, `.pmt/pandoc-worker` build directory and `.pmt/pandoc-source` tree separately
from Rust. macOS retains the pinned GHC/Cabal installation; Linux retains Rust
and GHC/Cabal toolchains at stable paths inside the manylinux container. A changed
source profile or index-state refreshes the Hackage index before resolving
dependencies. The first run seeds compatible legacy caches where available.
Linux compiles native components inside manylinux 2.28. The Rust wheel builder
stages and audits shared dependencies before embedding the runtime; it repairs
Linux RUNPATH and macOS load paths, and bundles official Windows CRT dependencies
only where the staged components' PE imports require them.
The Windows CLI uses a static CRT. The builder leaves original component files intact.
Each installed wheel must render citations and table cross-references through
its real background HTML server, reuse unchanged HTML, and invalidate edited
source before it can be published. GHC/Cabal are build-time tools only.

For MathType, the CLI directly links `mathtype-rust` and the same pinned Git
revision of `latex2wmf`. A safe Rust API returns owned OLE/MTEF bytes and previews;
no formula DLL, dynamic symbol lookup, or binary hex/JSON C ABI is shipped.
The renderer retains backend, style, font size, and math-font options.
`mathtypeTypstMathFont` accepts a string for one font throughout, or an object with
required `font` and `calligraphicFont` fields. Its default value explicitly selects
XITS Math and New Computer Modern Math respectively. Both default fonts are bundled.
Formula caches use `native-v2` and a compile-time engine fingerprint, plus the current
preferences, normalized font pair, both font-file digests, and optional helper inputs.
Equivalent string/object selections share cached previews. The standalone `latex2wmf` crate
and CLI remain available for development. The separate image helper is built
without `papper-core` or the embedded runtime archive. MuPDF is linked into the
CLI through `mupdf-sys`; the optional Windows C# helper remains a packaged
external component.

```yaml
# One font for ordinary math and calligraphy:
mathtypeTypstMathFont: XITS Math
```

```yaml
# Explicit default, with separate calligraphy for \mathcal, \mathscr, and \cal:
mathtypeTypstMathFont:
  font: XITS Math
  calligraphicFont: New Computer Modern Math
```

Both object fields accept a family name or `.otf`, `.ttf`, `.ttc`, or `.otc` file
path. Relative paths resolve from the owning style file. Effective metadata
normalizes string shorthand to the same two-field object used for rendering and
cache keys; the renderer metadata reports both selected fonts.

CI sets `CARGO_TARGET_DIR` to persist native build artifacts. On Linux this directory and Cargo's download cache live on the host via
the container's `/host` mount, so they survive the manylinux container. Local
builds retain their normal per-project target directories unless this environment
variable is set. Cabal dependency caches also persist through the Linux `/host`
mount. Build logs report elapsed time for the Haskell worker, native Rust executables, and
Windows .NET helper. A cold cache or a toolchain change still requires compilation;
actual release speedups should be measured after a successful warm-up.

The GPL HTML worker's adapted Haskell sources, Pandoc license/copyright notices,
and resolved dependency source links are included under
`share/papper/bin/pandoc-worker-source` in each wheel's data directory. Non-editable local
wheel builds require GHC/Cabal as well as Cargo and, on Windows, .NET; editable
native development installations retain the existing manually built worker path.

Before saving the Linux cache, the workflow transfers its container-created files
to the runner user and checks directory sizes and readability. A lookup after
saving requires the exact cache entry to exist remotely; an archive/upload failure
therefore fails the warm-up instead of silently leaving future releases uncached.

## Acknowledgments

- [Pandoc](https://pandoc.org/)
- [pandoc-crossref](https://github.com/lierdakil/pandoc-crossref)

## Support

- Run `papper guide syntax` for the manuscript syntax guide and `papper guide style` for configuration guidance
- Open an issue with a minimal reproducible example

### Typst equation font

Configure `style.yml` to select an installed OpenType math font:

```yaml
mathtype: true
mathtypeConversionMethod: rust
mathtypeSvgBackend: typst
mathtypeTypstMathFont: Cambria Math
```

Bundled math families are `XITS Math` (the default primary font),
`New Computer Modern Math` (the default calligraphic font), and `STIX Two Math`.
Use `mathtypeTypstMathFont: STIX Two Math` for STIX throughout, or select it for
either role in the object configuration. These families require no system installation.
You can also set `mathtypeTypstMathFont: fonts/STIXTwoMath-Regular.otf` to load a font
file without installing it. Relative paths resolve beside the style file; absolute
paths are supported. Accepted files are `.otf`, `.ttf`, `.ttc`, and `.otc`, and must
contain an OpenType math font. Collections select their first math family.
When using a custom family name, install it in every build environment.
Changing the family or font file contents invalidates the formula preview cache.
This controls Typst SVG/WMF previews, not editable MathType OLE font preferences.
It has no effect on RaTeX or native MathType previews (`set-data` / `rust-sdk`).
`auto` tries native MathType `set-data` first and falls back directly to `rust`;
`both` compares both backends, prefers the native MathType result, and uses the Rust
result if native conversion fails.

If the linked equation library reports a formula conversion error, the build
warns with the error and LaTeX input and continues. The affected formula retains its original
Word equation (OMML); other formulas are converted normally. In `both` and `auto`
modes, conversion tries `set-data` as each formula is converted when that backend
is available. These modes do not run a separate startup probe for the per-formula
failure policy. If three consecutive formulas fail with the helper message
`由于 Exception.ToString() 失败，因此无法打印异常字符串`, PMT treats set-data as
unavailable on that computer for the remainder of the current build and uses Rust
for subsequent formulas. A successful set-data conversion or a different failure
resets the consecutive counter. If all selected backends fail for a formula, its
original Word equation is retained. Failed conversions are not cached.

### Markdown in a subdirectory

Papper does not use a `--project-dir` option. Run commands from the directory that should receive generated output, and pass a Markdown path when the source is elsewhere:

```powershell
papper build docx .\chapters\paper.md
```

For each build, Pandoc resource lookup is passed explicitly in this order:

1. the directory containing the Markdown file;
2. the current working directory.

The same order is used for `style.yml`. If both directories contain that file, Papper merges them and the Markdown directory has higher priority. Relative assets named by metadata, such as `csl`, are resolved with the same lookup order.

Use `--style-file PATH` to select a single style file for a DOCX, LaTeX, HTML,
or JSON build. Relative paths resolve from the current working directory;
absolute paths are also accepted. The selected file replaces automatic
`style.yml` discovery for that build. Missing files and directory paths produce
an error. Bundled language defaults still apply, and manuscript YAML continues
to override the selected file's `pandocMetadata`. Omitting the option keeps
the automatic discovery and merging behavior described above.

`papper build` also accepts `--resource-path`. Its value is passed to Pandoc
unchanged as the value of one `--resource-path` option. Supplying it replaces
Papper's automatic Markdown-directory and working-directory resource path for
that build.
