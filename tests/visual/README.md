# HTML and DOCX visual regression tests

DOCX visual runs use both Microsoft Word and DOCXIDE by default. Each renderer
has independent baselines and pytest results; neither renderer must match the
other pixel for pixel. Word remains the reference for field and layout semantics.

For the DOCXIDE 0.19.1 versus Word experiment and six isolated
reproductions (mixed-script footnotes, nested images, SVG fallback, paragraph
wrapping, header tables and body fields), see
[DOCX renderer evaluation](../../docs/DOCX_RENDERER_EVALUATION.md). The known
differences are retained in DOCXIDE's initial baselines, not ignored with a pixel
tolerance or accepted as Word-equivalent behavior.

HTML content snapshots and browser screenshots protect different contracts.
`tests/snapshot_cases/<case>/snapshots-content/html.snap` retains text, document
structure, metadata, reference targets, table spans, math modes and semantic
`data-*` attributes. It excludes
CSS, scripts, generator versions and presentation classes. DOM wrappers and
semantic attributes remain reviewable; this is not a general HTML equivalence
checker. Code whitespace, inline spaces and nonbreaking spaces are significant.

HTML cases also have full-page Chromium screenshots at
`tests/snapshot_cases/<case>/snapshots-visual/<platform>-<architecture>/html.png`.
These catch shared CSS regressions, table/caption geometry, fonts, revision colors, images
and math rendering without comparing CSS source. The screenshot comparison is
exact: a changed pixel or page size fails. No ratio threshold can hide a small
formula or caption change. This initial suite covers screen media at 1280×900
CSS pixels with device scale 1; it does not establish mobile or print contracts.

## Setup and verification

Use the pinned uv lock and npm lock. Tests compile the current Rust sources and
require the retained native Pandoc engine, just like existing build tests.

```powershell
git submodule update --init --recursive
uv sync --locked --no-install-project
uv run --no-sync python -m playwright install chromium
npm ci --prefix tests/visual --ignore-scripts --no-audit --no-fund
uv run --no-sync pytest tests/test_build_snapshots.py -k html
uv run --no-sync pytest tests/test_html_visual.py --visual
```

Browser tests are opt-in, so normal pytest runs need no browser installation or
npm assets. `--visual` explicitly requests verification and fails if baselines,
assets or the recorded environment are missing; it never generates a baseline.

DOCX visual tests are also opt-in. The default pair requires Windows, Microsoft
Word, the locked Python dependencies, and this pinned external DOCXIDE build:

```powershell
cargo install --git https://github.com/sverrejb/docxide-pdf.git --rev 123aae5755ef77e51608b069221d53b526ffd162 --locked --root tests/visual/.tools/docxide docxide-pdf
uv run --no-sync pytest tests/test_docx_visual.py --visual
```

The tool installation is ignored by Git and is not a Papper runtime dependency.
`PAPPER_DOCXIDE_RENDERER` can point at another Cargo-installed executable; retain
its `.crates2.json` in the installation root. `PAPPER_DOCXIDE_TIMEOUT` controls the
per-export timeout in seconds (default 90). DOCXIDE needs no Word process or printer.
Missing binaries, environment records or baselines fail an explicit visual run;
the suite never silently drops one of the default backends.

Use `--docx-backend word` or `--docx-backend docxide` to select one renderer:

```powershell
uv run --no-sync pytest tests/test_docx_visual.py --visual --docx-backend word
uv run --no-sync pytest tests/test_docx_visual.py --visual --docx-backend docxide
```

This selector applies to `test_docx_visual.py`; dedicated native Word tests such
as the TOC numbering check still require Word. The native TOC regression checks actual
Word field updates and list labels without image baselines; run it with
`uv run pytest tests/test_native_crossrefs.py --visual -k native_toc`.
Word opens a unique read-only copy of every generated
DOCX, exports print-quality PDF pages, and closes only that copy. The test never
calls `Application.Quit()` and checks that documents which were already open stay
open. LibreOffice is not a substitute: these baselines intentionally exercise
Word's pagination, table borders, fonts, OMML equations, revisions and image
layout.

Before opening each test-owned copy, the export helper disables its automatic
field-update setting and clears dirty-field flags. This prevents Word's
"fields may refer to other files" confirmation during automated tests. It
preserves the generated source DOCX and the user's Word options. Cases requiring
updated fields request them explicitly after the temporary copy is open.

```powershell
uv run --no-sync pytest tests/test_docx_visual.py --visual
```

Both DOCX renderers rasterize PDF pages with PyMuPDF at 144 DPI in RGB. Each page
must have the same physical geometry and exact pixels as its reviewed
`page-001.png`, `page-002.png`, ... baseline for that backend. Both engines receive
the same generated DOCX bytes and export every case twice before comparison;
a non-deterministic export fails without updating a baseline. Results include
`[word-<case>]` and `[docxide-<case>]`, so a failure in one engine does not hide
the other's result. The color/pagination harness also exercises both engines. The
current suite covers the manuscript fixtures plus the reviewer reply fixture,
including both `template/manuscript.md` and `template/manuscript-cn.md`.
The generated DOCX build uses `--no-mathtype` unless a fixture style explicitly
selects another mode, so these visual cases cover native Word/OMML output rather
than MathType OLE rendering.

The `docx_directives` case renders all four directories after Word updates their
entries and page numbers. Its visual-only DOCX copy locks DATE results to
`2000-01-02`; the generated manuscript and its dynamic fields remain intact.
The export helper updates body and header/footer fields, repaginates, and repeats
until both page count and directory results stabilize. Two independent exports
must still match exactly. The case protects directory titles, panel indentation,
page breaks, page-number restarts, section header/footer alignment, inheritance,
and clearing. Its `pages.json` records the field-update mode; other cases retain
their existing export behavior without explicit field updates.

The existing `docx_footer` case also covers section-specific normal/even/first
Markdown stories, a common footer under alternating headers, inherited and
unlinked variants, first-page mode reset, a cleared even header, and header/footer
edge distances. Its rendered text is checked alongside pixels, including live
page fields in each variant and the first-page Markdown table.

```powershell
uv run --no-sync pytest tests/test_docx_visual.py -k docx_directives --visual
```

KaTeX 0.16.22 is served from the locked npm installation, including its fonts.
Generated CDN URLs are routed locally without editing the HTML or replacing
the rendering script. Unexpected network requests, broken image paths,
JavaScript errors and formula rendering errors fail before recording pixels.
Screenshots wait for fonts/images/math and must match on two successive captures.

## Baselines and review

The initial Windows AMD64 baselines were generated from
`6080a4c12ad3c89c067faf5335fd5110645ebb8e`. Each platform/architecture directory
under `tests/visual/environments/` contains `environment.json`, recording the
source revision, Chromium version, Playwright version, viewport, math asset digest
and relevant Windows font hashes.

DOCX baselines use the separate `docx-word.json` environment file. It records the
Word executable hash and build, active printer, print options, installed font
hashes, and PyMuPDF/MuPDF versions. A changed Word build, printer, font set, or
rasterizer fails before image comparison; review the new environment and refresh
all DOCX pages together when such a change is intentional. Baselines are
platform-specific and must not be reused on another OS or architecture.
Baseline comparisons verify this environment before checking images. Other OSes
need separately reviewed baselines and consistent fonts; Linux must not reuse
the Windows PNGs. Chromium is the bundled headless browser, never user Chrome.

DOCXIDE uses `docx-docxide.json` and case directories named `docx-docxide/`,
alongside the existing `docx-word/` directories. Its environment records the Cargo
package/revision, binary SHA-256, build settings, installed fonts, any
`DOCXIDE_FONTS` directories and their font hashes, and PyMuPDF/MuPDF versions.
It has no Word/printer identity. The initial reviewed environment is Windows AMD64;
other platforms require their own environment and page baselines. Upgrading the
renderer requires an explicit review and update of its baselines.

Word-only semantic checks still verify computed fields, TOCs, story selection and
paragraph layout. DOCXIDE captures its current supported-field evaluation and
cached results for unsupported fields. Both use the fixed-date input for dynamic
field fixtures. Keeping separate snapshots allows DOCXIDE to protect regressions
now while Word coverage is reduced deliberately as upstream support improves.

Every visual run compares newly rendered images with the last committed
baselines in Git `HEAD`, including runs with `--visual-update`. Review differences
beside the baselines under each case's `snapshots-visual/<platform>-<architecture>/`:

- HTML: `diff.png`, beside `html.png`.
- DOCX: `docx-word/page-001-diff.png` or `docx-docxide/page-001-diff.png`, ... beside the page
  baselines, for each changed, added, or removed page.

Only diff PNGs are saved, with changed pixels highlighted in magenta. Added or
removed pages, including blank pages, are highlighted across the page. No extra
old/new screenshots, JSON reports or failure PDFs are saved beside the snapshots.
Comparison statistics remain in pytest's failure message; Word's intermediate
PDF exports remain in pytest's temporary rendering directory.

Diff PNGs are not ignored by Git and can be committed with reviewed baselines.
Repeated updates keep comparing with `HEAD`, so overwriting a local baseline
does not erase the review difference. Images unchanged from `HEAD` are skipped:
their existing diff PNGs are preserved without rewriting or deleting them.
Rerunning a changed case replaces only the diff PNGs for images with visible changes.
The first baseline for a new case is reported as added.

Verification still compares exact pixels with the working baseline and fails on
any mismatch; recording differences against `HEAD` does not accept a baseline.

Update only after reviewing an intentional change:

```powershell
uv run --no-sync pytest tests/test_build_snapshots.py -k html --snapshot-update
uv run --no-sync pytest tests/test_html_visual.py --visual-update
uv run --no-sync pytest tests/test_docx_visual.py --visual-update
uv run --no-sync pytest tests/test_docx_visual.py --visual-update --docx-backend docxide
```

The unqualified DOCX update command updates both renderers. Select a backend to
refresh only its baseline and environment; a DOCXIDE upgrade does not require
replacing Word baselines.

The update switches are independent. `--snapshot-update` cannot overwrite
visual baselines. Select individual cases with `-k` when an intentional change is
limited to a fixture. Browser/font upgrades require a reviewed regeneration of
all baselines for that environment, not just the first failing case. Word or
printer upgrades require the same treatment for every DOCX page. Do not refresh
old visual baselines simply to make an equivalent-CSS or DOCX serialization
refactor pass.
