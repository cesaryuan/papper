# HTML and DOCX visual regression tests

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

DOCX visual tests are also opt-in and require Windows, Microsoft Word, and the
locked Python dependencies. Word opens a unique read-only copy of every generated
DOCX, exports print-quality PDF pages, and closes only that copy. The test never
calls `Application.Quit()` and checks that documents which were already open stay
open. LibreOffice is not a substitute: these baselines intentionally exercise
Word's pagination, table borders, fonts, OMML equations, revisions and image
layout.

```powershell
uv run --no-sync pytest tests/test_docx_visual.py --visual
```

The DOCX renderer rasterizes PDF pages with PyMuPDF at 144 DPI in RGB. Each page
must have the same physical geometry and exact pixels as its reviewed
`page-001.png`, `page-002.png`, ... baseline. Word exports every case twice before
comparison; a non-deterministic export fails without updating a baseline. The
current suite covers the 15 manuscript fixtures plus the reviewer reply fixture.
The generated DOCX build uses `--no-mathtype` unless a fixture style explicitly
selects another mode, so these visual cases cover native Word/OMML output rather
than MathType OLE rendering.

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

Every visual run compares newly rendered images with the last committed
baselines in Git `HEAD`, including runs with `--visual-update`. Review differences
beside the baselines under each case's `snapshots-visual/<platform>-<architecture>/`:

- HTML: `diff.png`, beside `html.png`.
- DOCX: `docx-word/page-001-diff.png`, `page-002-diff.png`, ... beside the page
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
```

The update switches are independent. `--snapshot-update` cannot overwrite
visual baselines. Select individual cases with `-k` when an intentional change is
limited to a fixture. Browser/font upgrades require a reviewed regeneration of
all baselines for that environment, not just the first failing case. Word or
printer upgrades require the same treatment for every DOCX page. Do not refresh
old visual baselines simply to make an equivalent-CSS or DOCX serialization
refactor pass.
