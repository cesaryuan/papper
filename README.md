[English](README.md) | [简体中文](README.zh-CN.md)

# Papper

**Write in Markdown. Submit in Word.**

Papper turns Markdown manuscripts into Word documents with citations, numbered figures and equations, and reviewer replies. Keep your source easy to edit with AI and review in Git, then build the DOCX your collaborators and journal need.

[Write with an AI agent](#write-with-an-ai-agent) · [VS Code extension](#vs-code-extension) · [Output examples](#output-examples) · [Documentation](#documentation)

## Write with an AI agent

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then install Papper:

```bash
uv tool install papper
```

Open your manuscript folder in an AI agent such as Codex, Claude Code, or Workbuddy. Ask it to use Papper, for example:

- “Use Papper to convert this DOCX into Markdown.”
- “Adapt the cross-references, images, tables, and bibliography in this Markdown file to Papper's syntax.”
- “Use Papper to build this Markdown file as a DOCX.”

For a new paper, `papper init` creates a manuscript project with `AGENTS.md` and `CLAUDE.md` guidance that the agent can read.

![Illustrated workflow: write Markdown, run papper build docx, and produce a manuscript and reviewer replies.](docs/assets/papper-workflow.png)

*Workflow illustration generated with AI. Actual Word output is shown below.*

## Quick start

To create and build a paper yourself after installation:

```bash
papper init my-paper
cd my-paper
papper build docx
```

Open **`output/docx/manuscript.docx`** to inspect the result. Edit `manuscript.md` and run the build again when you want a new Word copy.

For a Chinese manuscript and reviewer-reply starter, replace the initialization command with `papper init my-paper --lang zh-cn`. To check your installation, run `papper doctor`.

## VS Code extension

Install our [Pandoc Manuscript Tools extension](https://marketplace.visualstudio.com/items?itemName=cesaryuan.pandoc-manuscript-tools) for writing in VS Code:

- **Reference navigation:** jump to labels, find references, complete labels after `@`, and detect missing or duplicate labels.
- **Hover previews:** inspect equations and local SVG, EMF, and WMF images while editing.
- **Readable manuscript syntax:** highlight Pandoc blocks and spans, fold revision attributes, and navigate headings with a Pandoc-aware outline.
- **Image browsing:** preview image folders in grid, masonry, or folder layouts.
- **Reviewer replies:** navigate from `reply_to_reviewers.md` to labels in the workspace-root `manuscript.md`.

## Output examples

These are actual Microsoft Word renders of the manuscript and reviewer-reply examples. Equations were built with `mathtype: true` and `mathtypeConversionMethod: mathtype`, using the installed MathType application for conversion.

[![Actual Word output: manuscript title page, tables and equations, and reviewer replies.](docs/assets/docx-examples.png)](docs/assets/docx-examples.png)

Explore the [English manuscript](template/manuscript.md), [Chinese manuscript](template/manuscript-cn.md), and [reviewer-reply starter](template/reply_to_reviewers.md). Click the preview to enlarge it. These examples show supported formatting; journal-specific requirements still need configuration and review.

Download the rendered [manuscript DOCX](docs/assets/examples/manuscript.docx) and [reviewer-reply DOCX](docs/assets/examples/reviewer-reply.docx) to inspect their formatting and editable MathType objects in Word.

## What Papper handles

Papper builds on Pandoc and pandoc-crossref, adding manuscript templates, Word formatting, and a reviewer-reply workflow in one CLI.

| Your task | What Papper provides |
| --- | --- |
| Draft and revise a paper | Markdown sources, reusable project scaffolding, and guidance for AI agents |
| Keep references consistent | CSL citations and cross-references for figures, tables, equations, and sections |
| Prepare a Word manuscript | Author information, table formatting, numbered equations, page layout, and reference DOCX styling |
| Deliver editable equations | Native Word equations or editable MathType-compatible objects |
| Respond to reviewers | DOCX and HTML replies that reuse manuscript item numbers, with optional line-reference lookup |
| Bring an existing paper into Markdown | DOCX import with supported equation, caption, image, and reference recovery |

English and Chinese starters are included. Chinese builds use localized reference labels, chapter-based figure/table/equation numbering, and a bundled GB/T 7714 citation style; explicit citation styles take priority. HTML previews and JSON exports are also available.

## A writing and revision workflow

Initialization creates `manuscript.md`, `style.yml`, `reply_to_reviewers.md`, example images and bibliography files, and agent instructions. Keep manuscript content in Markdown and reusable formatting in `style.yml`.

```bash
papper build docx
papper build html
papper build docx reply_to_reviewers.md
```

The reply starter already declares `reply: manuscript.md` in its YAML header. Use manuscript figure, table, equation, and section labels in your replies to keep their numbers aligned. [Reply syntax and line references →](docs/manuscript-syntax.md#reviewer-replies)

To use a journal-specific style file or Word reference document:

```bash
papper build docx --style-file styles/journal.yml
papper build docx --reference-doc journal-reference.docx
```

Those files must exist. CSL controls citation formatting; `style.yml` and the reference DOCX control document appearance. See [custom reference documents and style precedence](docs/style-configuration.md#one-off-docx-builds) before adapting a journal template.

Already have a Word draft? Import it into a separate directory:

```bash
papper convert existing-paper.docx -o converted
```

This creates `converted/existing-paper.md` and its media. Check the imported content against the original; complex Word layouts may need manual cleanup. [Import examples and recovery limits →](docs/DOCX_IMPORT.md)

## Is Papper a good fit?

Papper suits researchers who want to keep a Markdown source of their paper, use AI or Git during revision, and deliver Word documents. It is especially useful when you repeatedly rebuild a manuscript and its reviewer replies.

Before using it for a submission:

- **Markdown remains the source.** Edits made in a generated DOCX are not automatically merged back into your manuscript. Importing Word content is a separate operation.
- **Journal formatting still needs review.** Configure citation styles and document appearance, then inspect the final DOCX against the journal's requirements. Papper does not guarantee compliance with every publisher template.
- **Line lookup can require an office application.** Resolving reviewer line references from Markdown or Word needs Microsoft Word on Windows or LibreOffice on other platforms. Supplying a suitable PDF avoids that conversion.
- **Check final pagination in Word.** HTML previews help you review content; page breaks and layout can differ from Word.

## Documentation

The installed CLI includes the authoring and configuration guides, so you and your AI agent can consult them inside any manuscript project:

```bash
papper guide
papper guide syntax equations
papper guide style docx-text-styles
```

`papper guide` lists topics with short descriptions. Select a topic or `topic/subtopic` to read it; use `--full` with `syntax` or `style` for the complete guide.

| Looking for | Start here |
| --- | --- |
| Manuscript YAML, citations, figures, tables, and equations | [`papper guide syntax`](docs/manuscript-syntax.md) |
| Page layout, fonts, citation styles, and Word settings | [`papper guide style`](docs/style-configuration.md) |
| DOCX import and optional reference recovery | [Import guide](docs/DOCX_IMPORT.md) |
| Editor integration and the local HTML API | [Runtime and integration](docs/RUNTIME_AND_INTEGRATION.md) |
| Source builds and validation | [Development](DEVELOPMENT.md) |
| Release packaging | [Maintainer guide](docs/RELEASING.md) |
| Rendering implementation | [Rendering internals](docs/MANUSCRIPT_RENDERING.md) |

## Updates and support

Upgrade with `uv tool upgrade papper`. Run `papper --help` or `papper build --help` for commands and options; add `--verbose` when you need diagnostic logs.

For a problem, [open an issue](https://github.com/cesaryuan/papper/issues) with your Papper version, operating system, command, and a minimal example that reproduces it. Remove confidential manuscript content before sharing.

Papper's own code is [MIT licensed](LICENSE). Bundled components retain their respective licenses and notices. Thanks to [Pandoc](https://pandoc.org/) and [pandoc-crossref](https://github.com/lierdakil/pandoc-crossref), which provide the conversion and cross-reference foundations.
