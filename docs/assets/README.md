# README image assets

The README uses a generated workflow illustration and a separate preview of actual Word output. Keep these roles distinct when updating the images.

## Workflow illustration

`papper-workflow.png` was generated with the `codex-image` skill, using its CLI and the `gpt-image-2` model. It illustrates the authoring workflow and is not an application screenshot or a measured rendering result. The delivered image is 2172 × 724 pixels; the generation service returned a 3:1 composition. README rendering preserves that aspect ratio.

Generation prompt:

> Create a refined wide GitHub README workflow illustration for Papper, a Markdown academic manuscript CLI. Ivory background, navy and muted teal, clean editorial design, ample whitespace. Three stages left to right: Markdown editor card with short academic Markdown text, small terminal card displaying exactly papper build docx, two elegant academic document sheets representing manuscript and reviewer replies with serif typography, tiny chart, equation and gray rules. The only large labels are Markdown, Papper, DOCX. No headline, slogan, footer copy, claim of submission readiness, metrics, logos, chatbot or invented application interface. The paper text is illustrative. Make a conceptual illustration, not a screenshot. Balanced front-facing layout, crisp text, no watermark.

Follow-up edit prompt:

> Keep the exact illustration composition, colors, cards, document sheets, all labels and command unchanged. Fix ONLY the Markdown editor source text: replace the LaTeX citation command backslash cite with valid Pandoc Markdown citation [@smith2020]. The source lines should read As shown in [@smith2020], followed by these effects are heterogeneous followed by across industries. Do not include any backslash cite commands in the left editor. Everything else must remain the same.

## Actual Word output

`docx-examples.png` combines freshly rendered Microsoft Word pages, in order:

1. [Manuscript title page](examples/manuscript-page-001.png)
2. [Manuscript tables and equations](examples/manuscript-page-003.png)
3. [Reviewer response](examples/reviewer-reply-page-001.png)

The manuscript comes from [the manuscript template](../../template/manuscript.md). The response comes from [the focused reply fixture](../../tests/snapshot_cases/reply/reply.md), which demonstrates cross-references and copied manuscript items. Both were rebuilt in temporary projects with Papper 0.11.2 and these settings:

```yaml
mathtype: true
mathtypeConversionMethod: mathtype
```

The installed MathType application handled conversion. The main DOCX contains 28 `Equation.DSMT4` OLE objects and the reply contains two; neither contains remaining OMML equation nodes. The build logs report zero retained equations. [Build information](examples/build-info.json) records source hashes, Word version, conversion counts, and rendering resolution. The generated [manuscript DOCX](examples/manuscript.docx) and [reviewer-reply DOCX](examples/reviewer-reply.docx) are included for inspection.

To regenerate, initialize a temporary manuscript project, set the two options above in its `style.yml`, and run `papper build docx`. For the reply, copy the focused fixture's input files to another temporary project, change its existing `mathtypeConversionMethod` to `mathtype`, and run `papper build docx reply.md --manuscript-line-source manuscript.pdf`.

Render both generated documents through `tests/visual/export_word_pdf.ps1`, invoked with Windows PowerShell's `-NoProfile -ExecutionPolicy Bypass -File` arguments and an export JSON request. The helper exports unique document copies and preserves pre-existing user documents. Rasterize the PDF pages at 144 DPI in RGB with PyMuPDF, then save the three selected page images under `examples/`. These README images are independent of the test snapshot baselines.

Only resizing, a neutral outer background, and page spacing were applied. Document content was not generated or retouched. Recreate the montage from the repository root with ImageMagick:

```powershell
magick montage docs/assets/examples/manuscript-page-001.png docs/assets/examples/manuscript-page-003.png docs/assets/examples/reviewer-reply-page-001.png -thumbnail 640x906 -background '#f4f5f6' -tile 3x1 -geometry +12+12 -depth 8 -strip docs/assets/docx-examples.png
```

## Brand assets

The `brand/` directory contains the Papper wordmark, capital-P icon, app icon sizes and Windows ICO in the project brand color. See [`brand/README.md`](brand/README.md) for the file map and background variants.
