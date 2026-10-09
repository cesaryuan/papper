# Initial comparison against the old HTML rendering

- Visual baseline source: `6080a4c12ad3c89c067faf5335fd5110645ebb8e`
- Tested product source on `main`: `816e9f4c` (CSS refactoring)
- Test implementation: `c856b449`, branch `test/html-semantic-visual`
- Environment: Windows 11 AMD64, bundled headless Chromium 145.0.7632.6,
  Playwright 1.58.0, screen media, 1280×900 viewport, device scale 1,
  pinned local KaTeX 0.16.22 and recorded Windows font hashes

The old source passes all 12 visual document cases and the additional harness
check (equivalent CSS passes; a one-pixel visible change fails). Its build, CLI,
service, semantic normalization and layout-filter checks pass: 59 tests.

After merging the tests into `main`, 9 of the 12 document images remain identical.
Three cases have real rendering differences. The harness check still passes;
pytest reports **3 failed, 10 passed** for the visual file. The combined build,
CLI, service, semantic normalization, layout-filter and server-regression checks
report **72 passed** on `main`.

| Case | Result | Changed pixels | Old/new image size |
| --- | --- | ---: | --- |
| template | Changed | 417,083 | 1280×7311 → 1280×7336 |
| references | Identical | 0 | Unchanged |
| crossrefs | Identical | 0 | Unchanged |
| chinese_crossrefs | Identical | 0 | Unchanged |
| bilingual_captions | Changed | 554 | 1280×1622 → 1280×1622 |
| metadata | Identical | 0 | Unchanged |
| style | Identical | 0 | Unchanged |
| table_attributes | Changed | 5,623 | 1280×1187 → 1280×1187 |
| text_styles | Identical | 0 | Unchanged |
| author_affiliations | Identical | 0 | Unchanged |
| where_comments | Identical | 0 | Unchanged |
| svg_rasterization | Identical | 0 | Unchanged |

## Observed changes

`bilingual_captions`: the header separator in the revised table changes from
gray to red. Its exact difference rectangle is `(363, 823, 917, 824)`:
one horizontal line, not a font/layout fluctuation.

`table_attributes`: revised header separators become red. The `TableNoBorder`
table loses the previous black outer lines and has a red internal header line.
The difference rectangle is `(363, 285, 917, 543)`.

`template`: revised table separators also change color. More significantly,
the `fig:subfigure-example` figure becomes taller: 230.375 → 255.03125 CSS px.
Inspection of browser-computed styles finds the following old/new changes:

- The subfigure layout table's top/bottom margins: 0 → 14 px
- Its cells' top/bottom padding: 0 → 3.33333 px
- The inner figures' top/bottom margins: 0 → 14 px
- The subfigure table height: 198.375 → 225.03125 px

These changes shift following content and increase total screenshot height by
25 pixels. Changed pixel count includes downstream shifts and the page shadow;
it should not be interpreted as 417,083 separate rendering defects.

## Reproduction and review artifacts

```powershell
uv run --no-sync pytest tests/test_html_visual.py --visual -q --tb=short
uv run --no-sync pytest tests/test_build_snapshots.py tests/test_html_snapshot_contract.py tests/test_html_layout_filters.py tests/test_rust_cli_contract.py tests/test_rust_server_regressions.py -q --tb=short
```

See `README.md` in this directory for dependency/browser setup. At the time of
this initial comparison, each changed case had ignored local artifacts under
`tests/visual/results/<case>/`:
`expected.png`, `actual.png`, `diff.png`, and `difference.json`. During the
initial review, `comparison.png` was also generated with the old rendering on
the left and current rendering on the right; template geometry details were
saved in `template/layout-differences.json`. Current tests instead save only
diff PNGs (`diff.png` for HTML, `page-001-diff.png`, ... for DOCX) beside the
baselines in each case's `snapshots-visual/` directory without Git ignore rules.

The old PNGs remain committed unchanged. This comparison does not change
product CSS or accept a new visual baseline. Whether each observed change is
intentional is a product decision; the tests now expose it independently of CSS
source organization.
