# HTML regression tests

HTML content snapshots and browser screenshots protect different contracts.
`tests/snapshots/*/html.snap` retains text, document structure, metadata, reference
targets, table spans, math modes and semantic `data-*` attributes. It excludes
CSS, scripts, generator versions and presentation classes. DOM wrappers and
semantic attributes remain reviewable; this is not a general HTML equivalence
checker. Code whitespace, inline spaces and nonbreaking spaces are significant.

The 12 existing HTML cases also have full-page Chromium screenshots. These catch
shared CSS regressions, table/caption geometry, fonts, revision colors, images
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

KaTeX 0.16.22 is served from the locked npm installation, including its fonts.
Generated CDN URLs are routed locally without editing the HTML or replacing
the rendering script. Unexpected network requests, broken image paths,
JavaScript errors and formula rendering errors fail before recording pixels.
Screenshots wait for fonts/images/math and must match on two successive captures.

## Baselines and review

The initial Windows AMD64 baselines were generated from
`6080a4c12ad3c89c067faf5335fd5110645ebb8e`. Each platform/architecture directory
contains `environment.json`, recording the source revision, Chromium version,
Playwright version, viewport, math asset digest and relevant Windows font hashes.
Baseline comparisons verify this environment before checking images. Other OSes
need separately reviewed baselines and consistent fonts; Linux must not reuse
the Windows PNGs. Chromium is the bundled headless browser, never user Chrome.

On failure, inspect `tests/visual/results/<case>/expected.png`, `actual.png`,
`diff.png` (changed pixels highlighted in magenta) and `difference.json`
(changed pixel count, image dimensions and bounding rectangle). These generated
artifacts are ignored by Git. The committed PNGs and manifest are review inputs.

Update only after reviewing an intentional change:

```powershell
uv run --no-sync pytest tests/test_build_snapshots.py -k html --snapshot-update
uv run --no-sync pytest tests/test_html_visual.py --visual-update
```

The update switches are independent. `--snapshot-update` cannot overwrite
visual baselines. Select individual cases with `-k` when an intentional change is
limited to a fixture. Browser/font upgrades require a reviewed regeneration of
all baselines for that environment, not just the first failing case. Do not
refresh old visual baselines simply to make an equivalent-CSS refactor pass.
