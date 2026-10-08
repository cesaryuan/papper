# Manuscript Syntax

This guide describes the Markdown and YAML syntax available when writing a
Papper manuscript. For reusable project formatting and build settings, see
[`style-configuration.md`](style-configuration.md).

## Basic Markdown and Manuscript Metadata

Papper reads Pandoc Markdown. Ordinary headings, paragraphs, emphasis, bold,
links, block quotes, numbered and bulleted lists, fenced code blocks, pipe
tables, footnotes, and inline/display math are available. Keep a blank line
between blocks. The sections below explain the manuscript patterns and Papper
extensions you will use most often.

```markdown
# Methods {#sec:methods}

An *emphasized* phrase, **bold** text, and a [link](https://example.com).
Water is H~2~O; a squared quantity is x^2^.

Text with a footnote.[^note]

[^note]: The footnote text.

> A quoted paragraph.
```

Start the manuscript with a YAML header between `---` lines. Keep
manuscript-specific information here:

```yaml
---
title: "A Study of Adaptive Sampling"
authors:
  - name: First Author
    affiliation: Department of Example, University Name
abstract: |
  This study evaluates an adaptive sampling method.
keywords: [sampling, evaluation, reproducibility]
bibliography: references.bib
lang: en-US
---
```

`title`, `abstract`, and `keywords` describe the manuscript; `bibliography`
selects its reference database. `lang` selects language defaults, including
Chinese defaults for `zh-CN`. Use the author mappings described below for
affiliations and correspondence details. Reusable formatting belongs in
`style.yml`; see [`style-configuration.md`](style-configuration.md).

### Bracketed Numbered Lists

Write at least two consecutive numbered items with a space after each marker:

```markdown
[1] Collect the samples.
[2] **Validate** the measurements.
[3] Report the results.
```

DOCX and HTML display an ordered list with square-bracket markers. The first
number can be any starting value, such as `[4]` followed by `[5]`. Each item
occupies its own source line; blank lines between items are also accepted.
A single item, non-consecutive numbers, or a bracketed number within prose
remains ordinary text. LaTeX receives an ordered list with its normal marker
format rather than the DOCX/HTML square-bracket styling.

### Custom Text Styles

For DOCX and HTML, apply a character style to a bracketed span, or a paragraph
style to a fenced block:

```markdown
Text with [revised wording]{custom-style="Revision Char"}.

::: {custom-style="Quote"}
A paragraph using the Quote style.
:::
```

Use a style defined in the reference document. `custom-style` selects a style;
it does not define a new one. Configure its supported properties through
`docxStyle` in `style.yml`. Explicit paragraph styles take priority over the
automatic equation-explanation and post-table paragraph styles. For table
appearance and cell text, use the separate attributes described under
Advanced Table Formatting.

### Ordinary Tables

Write pipe tables with a separator row, and put a caption on a separate line
after the table when needed:

```markdown
| Method | Accuracy | Notes |
| :----- | -------: | :---: |
| Baseline | 78.3% | Reference |
| Proposed | 92.4% | Improved |

: Prediction accuracy. {#tbl:accuracy}
```

Colons in the separator choose left, right, or centered cell text. Escape a
literal pipe inside a cell as `\|`. A `tbl:` label enables numbered table
references; an attribute-only caption applies formatting without visible
caption text. For merged cells and custom appearance, see Advanced Table
Formatting below.

## Author Metadata

Add author information to the YAML header in `manuscript.md`. Papper formats
author names, affiliations, and corresponding-author notes below the title in
DOCX and HTML output.
Use `authors`; `author` is also accepted as an alias.

Each author must be written as a YAML mapping with at least a `name` field. Optional fields are:

- `affiliation`: One affiliation key/string or a list of affiliation keys/strings.
- `affiliations`: One affiliation key/string or a list of affiliation keys/strings.
- `email`: Used in the generated corresponding-author footnote.
- `title`: Added in parentheses in the generated corresponding-author footnote.
- `corresponding`: Use `true` to generate the default correspondence footnote, or provide a string to use as the complete footnote text.

Affiliations can be written inline under each author, or defined once in a top-level `affiliations` map and then referenced by key. The singular top-level alias `affiliation` is also accepted when using keyed affiliations.

### Format 1: Inline Single Affiliation

Use a single string when each author has one affiliation:

```yaml
authors:
  - name: First Author
    email: first.author@university.edu
    affiliation: Department of Example, University Name, City, Country

  - name: Second Author
    email: second.author@university.edu
    affiliation: Department of Example, University Name, City, Country
    corresponding: true
```

### Format 2: Inline Multiple Affiliations

Use `affiliations` as a list when an author has more than one affiliation.
`affiliation` also accepts a list, as in the starter manuscript. Repeated
affiliation text is automatically assigned the same superscript label.

```yaml
authors:
  - name: First Author
    affiliations:
      - Department of Example, University Name, City, Country
      - Research Center, Institute Name, City, Country

  - name: Second Author
    affiliations:
      - Department of Example, University Name, City, Country
      - Research Center, Institute Name, City, Country
    corresponding: true
    email: second.author@university.edu
```

### Format 3: Keyed Affiliations

Use a top-level `affiliations` map when several authors share the same institutions. Author entries can reference one key with `affiliation`, or several keys with `affiliations`.

```yaml
authors:
  - name: First Author
    affiliations: [a, b]

  - name: Second Author
    affiliation: a
    corresponding: true
    email: second.author@university.edu
    title: Professor

affiliations:
  a: Department of Example, University Name, City, Country
  b: Research Center, Institute Name, City, Country
```

### Format 4: Custom Corresponding-Author Footnote

Set `corresponding` to a string when the journal requires custom wording. The string is used as the complete footnote text.

```yaml
authors:
  - name: First Author
    affiliation: a

  - name: Second Author
    affiliation: a
    corresponding: "Correspondence concerning this article should be addressed to Second Author, Department of Example, University Name. Email: second.author@university.edu."

affiliations:
  a: Department of Example, University Name, City, Country
```

Write each author as a mapping when you want to include affiliations,
correspondence details, or titles. A string-only list such as
`author: [First Author, Second Author]` does not provide Papper's DOCX author
formatting.

The generated correspondence note uses the first corresponding author. For
wording that names several contacts, use one custom `corresponding` string
containing the complete note.

## Optional LaTeX Source Configuration

The primary workflow is DOCX generation. If you also generate LaTeX source, you can edit the YAML header in `manuscript.md` for document-class-specific output:

### Example 1: Elsevier Journal

Add this section to the manuscript YAML header:

````yaml
documentclass: elsarticle
classoption: [preprint, 3p, authoryear]
header-includes:
- |
  ```{=latex}
  \journal{Journal of Example Research}
  ```
````

### Example 2: Springer Journal

```yaml
documentclass: svjour3
classoption: [smallextended]
```

### Example 3: Wiley Journal (e.g., Computer-Aided Civil Engineering)

````yaml
documentclass: WileyNJDv5
classoption: [HARVARD, Times2COL]
header-includes:
- |
  ```{=latex}
  \journal{Comput Aided Civ Inf.}
  \volume{00}
  \copyyear{2025}
  \startpage{1}
  \articletype{RESEARCH ARTICLE}
  ```
````

### Example 4: IEEE Journal

```yaml
documentclass: IEEEtran
classoption: [journal]
```

`papper build latex` generates LaTeX source. Compiling it requires a TeX
environment containing the selected document class and any packages it needs.

### Figures and Tables Across Two Columns

In LaTeX output, add `twocol=true` to a figure to make it span both columns:

```markdown
![Full-width comparison.](images/comparison.png){#fig:wide twocol=true}
```

Add `twocol=true` or `twocol=false` to a table's caption attributes to choose
a table spanning both columns or a single-column table. Without this attribute,
tables with more than five columns or image cells use the full-width layout.
The difference is useful with a two-column document class; it does not change
DOCX or HTML layout.

## Cross-References

The template uses [pandoc-crossref](https://github.com/lierdakil/pandoc-crossref) for automatic numbering:

- **Figures**: `![Caption](image.png){#fig:label}` -> Reference with `@fig:label`
- **Tables**: `: Caption {#tbl:label}` -> Reference with `@tbl:label`
- **Equations**: `$$ equation $$ {#eq:label}` -> Reference with `@eq:label`
- **Sections**: `# Section {#sec:label}` -> Reference with `@sec:label`

Example:

```markdown
See @fig:results for details. As shown in @tbl:comparison and @eq:model...
```

Use stable, unique labels. Bracketed references such as `[@fig:results]` are
also accepted. Add `{-}` to a heading to leave it unnumbered:

```markdown
# Acknowledgments {-}
```

Reference prefixes, automatic equation labels, chapter numbering, and heading
numbering depth are project settings described in
[`style-configuration.md`](style-configuration.md).

### Bilingual Figure and Table Captions

For HTML and DOCX, add `caption-en` to an ordinary numbered figure or table:

```markdown
![MT-MoE ViT 网络架构图](images/architecture.svg){#fig:architecture caption-en="Architecture diagram of the *MT-MoE ViT* network"}

| 模型 | 准确率 |
| ---- | ------ |
| A | 95.2% |

: 模型性能对比 {#tbl:comparison caption-en="Performance comparison of different models"}
```

Write only the translated title in the attribute. Papper adds `Fig.` or `Table`
and reuses the primary caption's number, including chapter prefixes. The primary
caption appears first and the English caption on its own line. Normal references
such as `@fig:architecture` and `@tbl:comparison` keep the same IDs. Crossref's
figure/table lists contain the primary title once, without the translation.
An empty `caption-en` keeps the ordinary single-caption output.

The attribute accepts one paragraph of inline Markdown, including emphasis and
math. Escape double quotes and backslashes as required by Markdown attributes;
for example, use `caption-en="Error for $\\alpha$"` for a TeX command. A numbered
`fig:` or `tbl:` identifier is required. Bilingual captions are supported for
ordinary figures and tables in HTML and DOCX.
Grouped subfigures and LaTeX output do not support `caption-en`.

Both caption lines share `Image Caption` for figures or `Table Caption` for
tables. For caption fonts and Word numbering options, see
[`style-configuration.md`](style-configuration.md).

## Equations

Write inline math with `$...$` and display equations with `$$...$$`. Add an
`eq:` label to a display equation when you need a numbered cross-reference:

```markdown
$$
\mathbf{y} = \mathbf{A}\mathbf{x} + \mathbf{b}
$$ {#eq:linear-model}

See @eq:linear-model for the prediction model.
```

A paragraph beginning with the standalone word `where` immediately after a
display equation uses the `Para Where` style in DOCX and HTML. In HTML, it
also omits the normal body first-line indent:

```markdown
$$
y = ax + b
$$ {#eq:line}

where $a$ is the slope and $b$ is the intercept.
```

`Where` is also recognized. Body paragraphs immediately after ordinary tables
use `Para After Table` in DOCX
and HTML. Customize these styles when different paragraph spacing is needed.

Legacy TeX roman declarations such as `$x^{\rm T}$` are accepted in inline and
display math. Papper renders the declaration's remaining brace-group contents
as roman text. Prefer explicit `\mathrm{T}` for mathematical symbols or
`\textrm{...}` for text when writing new formulas.

Papper automatically moves `\hat` inside mathematical font commands when the
whole hat operand is styled, including nested font wrappers:

```tex
\hat{\mathbf{C}}           -> \mathbf{\hat{C}}
\hat{\mathcal{C}}          -> \mathcal{\hat{C}}
\hat{\mathbf{\mathcal{C}}} -> \mathbf{\mathcal{\hat{C}}}
```

This avoids a MathType preview issue that can hide the hat. The correction applies
to inline and display math in DOCX, HTML, and LaTeX builds, while leaving source
Markdown unchanged. Supported wrappers are `\mathbf`, `\mathcal`, `\mathbb`,
`\mathfrak`, `\mathit`, `\mathrm`, `\mathsf`, `\mathscr`, `\mathtt`, `\mathbfit`,
`\boldsymbol`, and `\bm`. Writing the corrected form directly is also supported.
Operands with extra terms or scripts outside the style wrapper, such as
`\hat{\mathbf{x}+y}` or `\hat{\mathbf{x}_i}`, keep their original scope.

For MathType conversion settings and math font choices, see
[`style-configuration.md`](style-configuration.md#mathtype-equations).

## Images

Put a numbered figure on its own line, separated from surrounding text by
blank lines. Add a `fig:` label for references and a width for sizing:

```markdown
![Prediction accuracy across sampling rates.](images/accuracy.svg){#fig:accuracy width=90%}
```

Widths can use percentages or physical units, for example `width=12cm`.
Subfigure grids require percentage widths. Keep local image paths relative to
the manuscript and make the image files available when building.

For a standalone image without a visible caption, use empty caption text:

```markdown
![](images/logo.png){width=3cm}
```

In DOCX, a standalone captionless image uses the `Figure` paragraph style.
Use a caption and `fig:` label when you need an ordinary numbered figure.
For LaTeX, the figure layout may choose its own width; see the two-column
figure guidance above.

## Subfigure Layouts

For most multi-panel figures, prefer creating one SVG layout file that
references the child image files. The Markdown manuscript then inserts that SVG
as a normal figure. This approach makes the final layout explicit: panel
positions, labels such as `(a)` and `(b)`, and shared spacing are controlled in
one editable source file rather than inferred from a Word table layout.

Recommended file structure:

```text
manuscript.md
figures/
  model-comparison.svg
  model-comparison-a.png
  model-comparison-b.png
```

Example SVG layout (`figures/model-comparison.svg`):

```xml
<svg xmlns="http://www.w3.org/2000/svg"
     xmlns:xlink="http://www.w3.org/1999/xlink"
     width="160mm"
     height="78mm"
     viewBox="0 0 1600 780">
  <style>
    text {
      font-family: Times New Roman, Times, serif;
      font-size: 42px;
      fill: #000000;
    }
    .panel-label {
      font-weight: bold;
    }
  </style>

  <rect x="0" y="0" width="1600" height="780" fill="#ffffff"/>

  <image href="model-comparison-a.png"
         xlink:href="model-comparison-a.png"
         x="40" y="40" width="720" height="560"
         preserveAspectRatio="xMidYMid meet"/>
  <image href="model-comparison-b.png"
         xlink:href="model-comparison-b.png"
         x="840" y="40" width="720" height="560"
         preserveAspectRatio="xMidYMid meet"/>

  <text x="400" y="710" text-anchor="middle">(a) Baseline setting</text>
  <text x="1200" y="710" text-anchor="middle">(b) Proposed setting</text>
</svg>
```

Reference the composed layout from `manuscript.md` as one figure:

```markdown
@fig:model-comparison compares the baseline setting with the proposed setting.

![Comparison of baseline and proposed model behavior across two experimental settings.](figures/model-comparison.svg){#fig:model-comparison width=90%}
```

Keep the child image paths in the SVG relative to the SVG file itself. In the
example above, `model-comparison-a.png` and `model-comparison-b.png` sit beside
`model-comparison.svg` under `figures/`.

For DOCX builds with linked child images, keep `docxEmbedSvgImages: true` in
`style.yml` so the composed figure includes its panels. SVG text and vector
elements stay sharp. If a child image is itself an SVG, Papper converts the
composed parent figure to PNG for Word compatibility.

To convert just one SVG to PNG for submission, add `to-png=true` to the image.
Use `to-png-scale=2` to increase its PNG scale:

```markdown
![Model comparison.](figures/model-comparison.svg){#fig:model-comparison to-png=true to-png-scale=2}
```

`to-png-scale` applies when no output pixel width has been selected. A global
`docxSvgToPngWidth` or a convertible image `width` (percentage, `px`, `cm`, `mm`,
or `in`) takes priority over the scale. The underscore and camelCase aliases
`to_png`/`toPng` and `to_png_scale`/`toPngScale` are also accepted.

For project-wide SVG settings, see
[`style-configuration.md`](style-configuration.md#svg-images-in-docx).

This SVG-based pattern gives the composed figure one cross-reference label,
`@fig:model-comparison`. The panel markers `(a)` and `(b)` are visual labels
inside the SVG, not separate Pandoc figure labels. If the manuscript must cite
individual child panels with separate references such as `@fig:model-a` and
`@fig:model-b`, use the built-in `subfigGrid` form instead:

```markdown
<div id="fig:model-comparison-grid">
![Baseline setting.](figures/model-comparison-a.png){#fig:model-a width=49%}
![Proposed setting.](figures/model-comparison-b.png){#fig:model-b width=49%}

Comparison of baseline and proposed model behavior.
</div>
```

Enable this layout with `pandocMetadata.subfigGrid: true` in `style.yml`, or
`subfigGrid: true` in the manuscript YAML header. Use percentages for child
image widths, and separate rows with a blank line. Customize the
`TableSubfigure` table style in your reference DOCX to change the group's
appearance; ordinary manuscript tables keep their own style.

## Marking Revisions in Red

Use the `Revision Char` custom style to mark substantive manuscript revisions
in red in generated DOCX and HTML files:

```markdown
The proposed workflow improves [the adaptive sampling stage]{custom-style="Revision Char"} while keeping the original preprocessing steps unchanged.
```

Recommended revision-marking rules:

- Use `Revision Char` only for modified or newly added words, phrases, sentences, or paragraphs that need to appear in red.
- Do not mark very small edits within one sentence, such as changes under three words.
- When old text is replaced, omit the deleted wording and mark only the new or modified surviving text.
- For heavily revised existing paragraphs, mark only the changed parts. Mark a whole paragraph only when the entire paragraph is newly added.
- Keep cross-reference tokens such as `@fig:result` or `@tbl:comparison` outside the styled span unless the reference text itself is substantively changed.

For a modified or newly added figure, mark the caption rather than the image path. Size-only figure changes do not need revision markup.

```markdown
![[Updated model comparison under the same evaluation protocol.]{custom-style="Revision Char"}](figures/model-comparison.png){#fig:model-comparison}
```

For tables, use revision attributes on the table caption. Use `revision-rows="*"` for a newly added table. For a modified table, list changed or added 1-based row or column numbers with `revision-rows="..."` and `revision-columns="..."`; row numbers include the table header row. DOCX and HTML builds apply these attributes to the corresponding changed cells, while `*` also colors the HTML table caption.

```markdown
| **Method** | **Accuracy (%)** | **Runtime (s)** |
|:----------:|:----------------:|:---------------:|
| Baseline   | 78.3             | 12.4            |
| Proposed   | 92.4             | 10.1            |

: Performance comparison. {#tbl:performance revision-columns="2,3" revision-rows="3"}
```

The underscore forms `revision_rows` and `revision_columns` are equivalent and are documented with the other DOCX table attributes below.

For native Word display equations, add `revision=true` to the equation
attributes to make the equation red in DOCX output:

```markdown
$$
\mathbf{y} = \mathbf{A}\mathbf{x} + \mathbf{b}
$$ {#eq:linear-model revision=true}
```

This revision coloring currently targets native Word equations only. If the DOCX build later converts equations to MathType OLE objects, this equation-level red coloring is not preserved.

## Writing Pseudocode

For method or workflow descriptions, the recommended pattern is to write pseudocode as a one-column pipe table. This format is easy to edit in Markdown and stays visually stable after DOCX conversion.

**Recommended conventions**:

- Use a bold first row for the algorithm title, for example `| **Algorithm: ...** |`.
- Use bold label rows such as `**Input:**`, `**Output:**`, and `**Step 1 ...:**` to separate major blocks.
- Put one operation in each table row so the procedure stays readable in both Markdown and DOCX.
- Write control keywords in bold, such as `**for**`, `**if**`, `**else**`, `**end for**`, and `**end if**`.
- Use inline math with `$...$` for symbols and variables, and use `@eq:label` when the pseudocode refers to numbered equations in the manuscript.
- Inside table cells, use escaped spaces such as `\ \ ` to show nesting. This is useful because ordinary leading spaces in Markdown tables may collapse during rendering.
- If line numbers are needed, prefix each operation row with `1.\ \`, `2.\ \`, and so on. For nested operations, add more escaped spaces after the line number, for example `4.\ \ \ \ Train ...`.

Example:

```markdown
| **Algorithm: Library book borrowing workflow** |
|---|
| **Input:** |
| Borrow request list $R=\{r_i \mid i=1,\cdots,N\}$ and catalog records $C$ |
| **Output:** |
| Updated borrowing log $L$ |
| **Step 1 Validation:** |
| Read the next request $r_i$ and extract the member ID and book ID |
| Check whether the member account is active |
| **Step 2 Availability check:** |
| **for** each request $r_i$ in $R$ **do** |
| \ \ Search the catalog record for the requested book |
| \ \ **if** a copy is available **then** |
| \ \ \ \ Mark the copy as borrowed |
| \ \ \ \ Set the due date according to the lending policy |
| \ \ **else** |
| \ \ \ \ Add the request to the waiting list |
| \ \ **end if** |
| **end for** |
| **Step 3 Logging:** |
| Write the transaction result to the borrowing log $L$ |
| **if** an overdue fine is triggered **then** |
| \ \ Notify the member and update the account balance |
| **end if** |
```

Numbered pseudocode rows use the same one-column table format:

```markdown
| **Algorithm: Dataset preparation and model evaluation workflow** |
|---|
| **Input:** Raw dataset $D$, model family $M$, evaluation metric $s$ |
| **Output:** Trained model $\hat{m}$ and evaluation score $\hat{s}$ |
| 1.\ \ Clean and normalize all records in $D$ |
| 2.\ \ Split $D$ into training, validation, and test subsets |
| 3.\ \ **for** each candidate model $m \in M$ **do** |
| 4.\ \ \ \ Train $m$ on the training subset |
| 5.\ \ \ \ Tune hyperparameters using the validation subset |
| 6.\ \ **end for** |
| 7.\ \ Select the best model $\hat{m}$ according to validation performance |
| 8.\ \ Compute $\hat{s}$ for $\hat{m}$ on the test subset |
| 9.\ \ **return** $\hat{m}$ and $\hat{s}$ |
```

Pseudocode uses normal table numbering if you add a caption with a `tbl:` label.
There is no separate algorithm numbering or algorithm cross-reference syntax.

## Citations

Set `bibliography: references.bib` in the manuscript YAML header and use keys
from that database. A list selects several bibliography files. For example,
an entry with the key `smith2023` can be cited as follows:

Use standard Pandoc citation syntax:

- Single citation: `[@smith2023]`
- Multiple citations: `[@smith2023; @jones2024]`
- Narrative citation: `@smith2023 showed that...`
- With page numbers: `[@smith2023, p. 42]`
- Suppress the author's name: `[-@smith2023]`

The active CSL determines how citations and the bibliography appear in DOCX
and HTML. To include uncited entries, add `nocite: '@*'` to the manuscript YAML,
or list selected keys in `nocite`. LaTeX uses its bibliography workflow and
document-class citation settings. See
[`style-configuration.md`](style-configuration.md) for citation styles.

## Advanced Table Formatting

Use table caption attributes to choose Word table styles, control layout,
merge cells, and mark revisions. Table styles, cell text styles, cell margins,
revision attributes, and autofit are supported in DOCX and HTML. Cell spacing,
row height, and table alignment below are DOCX layout controls. Cell merging
also runs for LaTeX output; final layout follows that output's table renderer.

### Custom Table Styles and Borderless Layouts

Use the bundled `TableNoBorder` table style for tables with no visible
borders, including aligned blocks of text. Add `custom-style="TableNoBorder"`
to the table caption attributes. This styles the table itself; `Table Text`
is the paragraph style for text inside cells.

For a table with a visible caption:

```markdown
| Left item | Right item |
|:---------:|:----------:|
| First     | Second     |

: Aligned items. {#tbl:aligned-items custom-style="TableNoBorder"}
```

For aligned text without a visible table caption:

```markdown
| | |
|:---|:---|
| Experiment | Baseline |
| Dataset | Sample dataset |

: {custom-style="TableNoBorder" cell_margin="0.05cm" alignment="center" autofit="window"}
```

A completely empty header adds no blank header row to the output. An
attribute-only caption line applies formatting without a visible caption.

If you use a custom reference DOCX, include a table style with the ID
`TableNoBorder` to use this example. This style controls DOCX appearance;
HTML also applies the supported table appearance from the reference styles.
LaTeX uses its own table formatting.

### Table Cell Text Styles

Use `custom-text-style` to select the paragraph style inside table cells,
independently of the table's `custom-style`. For example:

```markdown
| Item | Description |
| ---- | ----------- |
| A | A paragraph using Body Text. |

: Styled cells. {#tbl:styled-cells custom-style="TableNoBorder" custom-text-style="Body Text"}
```

The alias `custom_text_style` is also accepted. The selected style applies to
cell paragraphs, including paragraphs in lists; captions retain their own
style. Independently styled nested tables retain their own cell styles. Without
this attribute, cells use the normal table text style. DOCX and HTML support
this setting.

### 1. Table Attributes

Add attributes to table captions to control DOCX table layout.

**Syntax**: Add attributes at the end of the Pandoc table caption.
For tables that should not have a visible caption, use an attribute-only caption line such as `: {revision_rows="*"}`.

**Available attribute keys**:

- `cell_margin="0.10cm"` - Set all cell margins (supports cm, mm, in, pt)
- `cell_margin_top="0.10cm"`, `cell_margin_bottom="0.10cm"`, `cell_margin_left="0.10cm"`, `cell_margin_right="0.10cm"` - Individual margins
- `cell_spacing="0pt"` - Spacing between cells
- `row_height="0.5cm"` - Set row height for all rows
- `revision_rows="1,2,3"` - Mark changed or added 1-based rows in red text
- `revision_columns="6,7"` - Mark changed or added 1-based columns in red text
- `revision_rows="*"` or `revision_columns="*"` - Mark the entire table and its caption in red text
- `alignment="center"` - Table alignment (left, center, right)
- `autofit="window"` - Autofit behavior (fixed, content, window)

Revision row and column numbers are 1-based and include the table header row.

Hyphenated aliases such as `cell-margin="0.10cm"` and `revision-columns="6,7"` are also accepted.

**Example**:

```markdown
| **Method** | **Accuracy (%)** |
|:----------:|:----------------:|
| Baseline   | 78.3             |
| Proposed   | 92.4             |

: Performance comparison. {#tbl:results cell_margin="0.10cm" autofit="window" alignment="center" revision_rows="*"}
```

The attributes are applied to the DOCX table without appearing in the final caption.

### 2. Cell Merging

Use special markers to merge table cells:

- `!<!` - Merge with the cell to the left
- `!^!` - Merge with the cell above

**Example**:

```markdown
| **Category** | **Subcategory** | **Value** |
|:------------:|:---------------:|:---------:|
| Group A      | Item 1          | 10        |
| !^!          | Item 2          | 20        |
| Group B      | Item 3          | 30        |

: Table with merged cells. {#tbl:merged}
```

In this example, "Group A" will span two rows (merging with the cell below containing `!^!`).

The marker cell must contain only the marker and have an existing cell to
merge with in the indicated direction. The markers are replaced by merged
cells in the output.

### 3. Auto-fit Tables

Set the top-level string option `tableAutofit` in `style.yml` to control authored
tables without an explicit `autofit` attribute in both DOCX and HTML:

```yaml
tableAutofit: window
```

The default `window` fits tables to the available page width. `content` sizes
them to their contents; `fixed` preserves authored widths; `none` keeps each
table's own settings. This default applies to tables written in Markdown and
does not affect generated equation or subfigure layouts.

Per-table `autofit="window"`, `autofit="content"`, or `autofit="fixed"` takes
priority over the global switch. `fixed` preserves the authored widths and
disables automatic resizing. Set `alignment` separately to control a table's
alignment. The optional top-level `reply.tableAutofit` setting overrides the
manuscript setting for reviewer replies. This is a Papper setting outside
`pandocMetadata`; manuscript YAML does not override it.

## Reviewer Replies

Keep reviewer comments as ordinary text and wrap each response in the
`Reply to Reviewers` paragraph style. Other available reply styles include
`Reply Header` for the opening block. For example:

```markdown
**Reviewer comment:** Please clarify the sampling procedure.

::: {custom-style="Reply to Reviewers"}
We clarified the procedure in @sec:methods
(Line `The sampling procedure selects\s+representative observations`).
:::
```

`papper build-reply` can reuse manuscript references in a reviewer reply.
Write `@fig:...`, `@tbl:...`, `@sec:...`, or `@eq:...` to refer to the
manuscript's existing numbered items. When copying a figure, table, or equation
into the reply, keep its manuscript label to reuse its manuscript number.
Unresolved labels remain unchanged so you can identify missing references.

### Manuscript Line References

Write a location as ``(Line `regex`)``. The regular expression is matched
case-insensitively against the rendered manuscript's PDF text. Exactly one
match replaces the placeholder with its starting line number, such as
`(Line 128)`. Invalid expressions, zero matches, or multiple matches produce
warnings and leave the placeholder unchanged.

Match distinctive visible prose and use `\s+` for flexible whitespace. Avoid
Markdown labels, fixed line numbers, and formula glyphs as anchors. Choose the
manuscript used for cross-references and the source used for line lookup:

```powershell
papper build-reply reply.md --reply-manuscript manuscript.md --manuscript-line-source output/pdf/manuscript.pdf
```

Both options default to `manuscript.md`. The line source accepts Markdown,
DOCX/DOCM, or PDF; Markdown and Word sources require conversion to PDF for
lookup. A PDF should include the line numbers whose locations you want to cite.
Converting Word or Markdown line sources requires Microsoft Word on Windows,
or LibreOffice on other platforms. Providing a PDF avoids that conversion.

### Reply Output

Choose a `.docx` output path for Word, or a `.txt` path for plain text:

```powershell
papper build-reply reply.md -o output/txt/reply.txt
```

TXT output keeps Markdown bold, emphasis, tables, and formulas. Images become
`[Image: ...]` placeholders; manuscript cross-references and ``(Line `regex`)``
placeholders are resolved. Item label attributes and reply-only formatting
wrappers are omitted from the text output.

DOCX replies use the reply paragraph styles and format copied table text and
captions in blue italic text. Reusable reply overrides belong in `style.yml`
under `reply`; see [`style-configuration.md`](style-configuration.md).
