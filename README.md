[English](README.md) | [简体中文](README.zh-CN.md)

# Papper

Write in Markdown. Submit in Word.

Papper is a DOCX-first academic writing workflow built for the AI era. AI tools are already great at drafting, revising, and restructuring Markdown. The problem is that many journals, editors, and collaborators still expect `.docx`. Papper bridges that gap: you keep the clarity and version-control friendliness of Markdown, while generating submission-ready Word documents when it is time to deliver.

<!--
Hero image idea for the README:
- Use a wide 3-panel workflow graphic instead of a logo-only banner.
- Left panel: a clean Markdown manuscript in an editor, with citations, cross-references, and a short AI chat prompt visible.
- Middle panel: a terminal running `papper build docx` and `papper build-reply`.
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
- **Reviewer reply support**: build response letters as DOCX or TXT, while resolving manuscript references and citations.
- **Managed Pandoc tools**: if `pandoc` or `pandoc-crossref` are missing, Papper can install user-scoped copies under `~/.papper/tools`.
- **Optional HTML, LaTeX, and JSON output**: keep a Markdown-centered workflow without giving up other export targets.

## What You Get

- Manuscript scaffolding with `papper init`
- Environment checks with `papper doctor`
- Project-local tool setup with `papper setup`
- DOCX, LaTeX, and JSON builds with `papper build`
- Reviewer reply builds with `papper build-reply`
- Cross-references for figures, tables, equations, and sections
- CSL-based citations
- Reference DOCX support for Word styling
- DOCX post-processing for author blocks, table behavior, styles, and line-number-related workflows
- Tab-layout equation paragraphs automatically use `Para Equation`, based on `Body Text`, with 0.5 line spacing after and single line spacing. To apply only this step to an existing DOCX in place, run `uv run python -m pandoc_manuscript.docx.postprocess.para_equation_style path/to/file.docx` (add `--no-save` for a dry run).
  The style's center and right tab stops use half and all of the first DOCX section's writable width (page width minus left/right margins). Direct paragraph tab stops are removed so equations inherit the style's positions; rerun the step after changing page margins.
- SVG handling and DOCX fallbacks for figures that Word does not handle well
- Cross-platform MathType-compatible OLE/WMF equations, with an optional native MathType comparison path on Windows

## Quick Start

### Prerequisites

Install these tools first:

1. `uv` for running the CLI and Python environment
2. `pandoc` 3.11+ and `pandoc-crossref`
3. For line-number source workflows, Windows requires Microsoft Word; other platforms can use `soffice`.
4. Optional: MathType on Windows only if you select `rust-sdk`, `set-data`, `auto`, or `both`; the default `rust` path is self-contained

Papper requires Pandoc 3.11 or newer. Older or unusable `pandoc` executables on `PATH`
are ignored; Papper automatically runs the managed setup and downloads a shared copy into
`~/.papper/tools` instead. The same copy is reused by every Papper project for this user.
Reusable caches and persistent build state live under
`~/.papper/projects/<project-id>/cache` and `~/.papper/projects/<project-id>/work`.
The project ID is derived from its absolute directory, so projects cannot overwrite
each other's state. One-build intermediates such as MathType OLE/WMF previews and
line-source conversion files use the system temporary directory and are removed
when the Papper process exits. `papper clean` clears the current project's work;
`papper distclean` also clears its cache. Existing project-local `.pmt` or `.papper`
directories are not migrated or deleted automatically.
Missing or unusable `pandoc-crossref` executables are also installed automatically.
Downloads and executable installation use temporary files followed by atomic
replacement, so an interrupted build can be rerun. Invalid cached archives are
discarded and downloaded again once; unusable managed executables are reinstalled.
Bundled Python filters, including the compatibility `to_mathbfit` filter, run with
Papper's Python interpreter and its installed dependencies.
On Windows, Papper puts that interpreter first on Pandoc's `PATH` and runs the
Python filters directly, including for manuscript projects on UNC network paths.

Tool downloads automatically use `HTTPS_PROXY` (or `https_proxy`) when set,
otherwise the configured Windows/macOS system HTTP/HTTPS proxy, and otherwise
a direct connection. This applies to both GitHub release metadata and archive
downloads during `papper build docx`, `papper setup`, and `papper init --setup`.
On Windows, enable your proxy application's **system proxy** option before
building; no extra Papper setting is needed. Papper logs `[TOOLS] Using system proxy for
downloads.` when it selects that route. An explicit `HTTPS_PROXY` takes precedence
over the system setting. PAC scripts and automatic proxy discovery are not
evaluated by this downloader.

### Rough Python Compatibility Check

If you just want a quick syntax-level check against the project's minimum Python target, use Ruff:

```bash
uvx ruff check .
```

This is only a rough version-compatibility check. It can catch syntax that does not fit the configured Python target, but it does not prove runtime compatibility.

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

# Build one standalone HTML file with embedded resources
papper build html -o build/paper.html

# Pass a raw Pandoc resource path value through unchanged
papper build html --resource-path 'assets;shared-assets'

# Build a reviewer reply
papper build-reply reply.md --reply-manuscript manuscript.md -o output/docx/reply.docx
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
The bundled `template/style.yml` and `template/style-cn.yml` provide the
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

- [`template/.agents/manuscript-syntax.md`](template/.agents/manuscript-syntax.md): manuscript syntax, citations, cross-references, pseudocode, revision markup, and style metadata
- [`template/manuscript.md`](template/manuscript.md): example manuscript content
- [`AGENTS.md`](AGENTS.md): repository-specific guidance for coding agents

In generated projects, `style.yml` keeps Papper-owned build settings at the top
level and places metadata sent to Pandoc under `pandocMetadata`. Manuscript YAML
overrides only the Pandoc metadata domain.

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
papper build-reply reply.md -o output/docx/reply.docx
papper clean
papper distclean
```

Use `papper --help` to see the full CLI.

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
supports the official `/`, `/batch`, and `/version` API. State is recorded in
`~/.papper/projects/<project-id>/pandoc-server.json` and output in the sibling
`pandoc-server.log`.

The server is an integration service for repeated Markdown conversions. It
loads the current project's PMT defaults and metadata, and uses the same
`pandoc-crossref` and Lua filter chain as the normal HTML build. The normal PMT
HTML build still runs its existing CLI path, so `--start-server` does not
change cross-reference or resource behavior. Set a generic server command
explicitly only when needed:

```powershell
$env:PMT_PANDOC_SERVER_COMMAND = 'C:\path\to\pandoc-server.exe'
papper build html --start-server --server-port 3030
```

The repository includes a minimal executable wrapper under
`scripts/pandoc-server`. With GHC/Cabal and the matching Pandoc 3.11 package
available, build it into the project-managed tool directory with:

```powershell
Push-Location .\scripts\pandoc-server
cabal install . --installdir "$HOME\.papper\tools\bin" --overwrite-policy=always
Pop-Location
```

The PMT wrapper exposes a deliberately smaller API than the generic Pandoc
server. Use `GET /version` for health and `POST /convert` with
`{"path":"manuscript.md"}` for one result (the path defaults to
`manuscript.md` when omitted), or `POST /batch` with an array of
`{"path": ...}` objects. Paths are resolved inside the project that started
the server. The response contains the generated HTML, after the same PMT HTML
post-processing used by `papper build html`, together with stage timings and
the HTML result-cache status.

For clients that want to avoid the JSON envelope, `POST /convert/raw` returns
the exact PMT HTML directly as `text/html`. `POST /preview` returns a faster
HTML fragment path: it reuses a project-scoped Pandoc AST cache, disables the
standalone template and author block, and keeps the PMT filter chain for
cross-references and citations. The first preview for a changed manuscript
returns immediately through the normal preview path while warming the section
AST cache in the background; subsequent requests can reuse unchanged section
ASTs and the rendered HTML cache. `GET /metrics` exposes the current in-memory
cache counts.

Every raw response includes `X-PMT-Cache`, `X-PMT-Mode`, and a `Server-Timing`
header. The timing fields cover asset-cache lookup, AST construction when
needed, Pandoc worker time, output reading, HTML post-processing, and total
request time.

## Maintainer releases and native build caches

Release with `uvx bump-my-version bump patch` followed by
`git push origin main --tags`. The **Publish to PyPI** workflow publishes only
on `v*` tag pushes. Its `main` builds and manual runs build and verify the same
three platform wheels without publishing them.

Changes to either Rust submodule, the native build hook, helper inputs, or the
publishing workflow trigger cache warming on `main`. To warm or refresh caches
manually, run **Publish to PyPI** with **Run workflow**, selecting `main`.
Complete the first warm-up before creating the next release tag: GitHub allows
tags to restore default-branch caches, but not caches saved under other tags.
Pushing `main` and a tag together does not make the tag wait for cache warming;
it can use an older `main` cache and compile any changed dependencies.

The workflow pins Rust to `1.98.0` and caches Cargo downloads and release build
outputs separately for Windows, macOS 14, and manylinux 2.28. Cache keys include
the toolchain, native lockfiles, submodule revisions, build configuration, and
equation preferences, rather than the Python package version. Only successful
`main` builds save caches; tag builds restore them and still run Cargo with
`--locked`, package the wheel, and verify its native libraries.

The wheel builds and ships only the `mathtype-rust` shared library. Its versioned
C ABI handles OLE/MTEF conversion and `operation="render_wmf"` requests, linking
`latex2wmf` once as a pinned Git dependency. The renderer retains backend, style,
font size, and math-font options. The standalone `latex2wmf` crate and CLI remain
available for development; its dynamic library is not shipped.

CI sets `CARGO_TARGET_DIR` to persist native build artifacts. On Linux this directory and Cargo's download cache live on the host via
the container's `/host` mount, so they survive the manylinux container. Local
builds retain their normal per-project target directories unless this environment
variable is set. Build logs report elapsed time for each Rust library and the
Windows .NET helper. A cold cache or a toolchain change still requires compilation;
actual release speedups should be measured after a successful warm-up.

Before saving the Linux cache, the workflow transfers its container-created files
to the runner user and checks directory sizes and readability. A lookup after
saving requires the exact cache entry to exist remotely; an archive/upload failure
therefore fails the warm-up instead of silently leaving future releases uncached.

## Acknowledgments

- [Pandoc](https://pandoc.org/)
- [pandoc-crossref](https://github.com/lierdakil/pandoc-crossref)

## Support

- Review the syntax guide in [`template/.agents/manuscript-syntax.md`](template/.agents/manuscript-syntax.md)
- Open an issue with a minimal reproducible example

### Typst equation font

Configure `style.yml` to select an installed OpenType math font:

```yaml
mathtype: true
mathtypeConversionMethod: rust
mathtypeSvgBackend: typst
mathtypeTypstMathFont: Cambria Math
```

Bundled math families are `XITS Math` (the default) and `New Computer Modern Math`.
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

If the `mathtype-rust` native library reports a formula conversion error, the build warns with the
exit code and LaTeX input and continues. The affected formula retains its original
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

`papper build` also accepts `--resource-path`. Its value is passed to Pandoc
unchanged as the value of one `--resource-path` option. Supplying it replaces
Papper's automatic Markdown-directory and working-directory resource path for
that build.
