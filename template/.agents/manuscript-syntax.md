# Manuscript Syntax

This document separates manuscript content syntax from style metadata. Use the
`Manuscript Syntax` section for content written in `manuscript.md`, and the
`Style Metadata` section for style-related defaults in `style.yml`.

## Author Metadata

The DOCX post-processing step reads author information from the YAML header in `manuscript.md` and inserts formatted author names, affiliations, and the corresponding-author footnote after the title. Use `authors` as the preferred field name. The singular alias `author` is also accepted by the post-processor for compatibility.

Each author must be written as a YAML mapping with at least a `name` field. Optional fields are:

- `affiliation`: A single affiliation key or a single inline affiliation string.
- `affiliations`: One affiliation key/string or a list of affiliation keys/strings.
- `email`: Used in the generated corresponding-author footnote.
- `title`: Added in parentheses in the generated corresponding-author footnote.
- `corresponding`: Use `true` to generate the default correspondence footnote, or provide a string to use as the complete footnote text.

Affiliations can be written inline under each author, or defined once in a top-level `affiliations` map and then referenced by key. The singular top-level alias `affiliation` is also accepted when using keyed affiliations.

### Format 1: Inline Single Affiliation

This is the simplest format and matches the default `manuscript.md` template:

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

Use `affiliations` as a list when an author has more than one affiliation. Repeated affiliation text is automatically assigned the same superscript label.

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

Avoid Pandoc's compact string-only author syntax, such as `author: [First Author, Second Author]`, when you need this template's DOCX author formatting. The post-processing script expects each author to be a mapping so it can read affiliations and correspondence metadata.

## Optional LaTeX Source Configuration

The primary workflow is DOCX generation. If you also generate LaTeX source, you can edit the YAML header in `manuscript.md` for document-class-specific output:

### Example 1: Elsevier Journal

Uncomment and customize this section in `manuscript.md`:

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

## Cross-References

The template uses [pandoc-crossref](https://github.com/lierdakil/pandoc-crossref) for automatic numbering:

- **Figures**: `![Caption](image.png){#fig:label}` -> Reference with `@fig:label`
- **Tables**: `Table: Caption {#tbl:label}` -> Reference with `@tbl:label`
- **Equations**: `$$ equation $$ {#eq:label}` -> Reference with `@eq:label`
- **Sections**: `# Section {#sec:label}` -> Reference with `@sec:label`

Example:
```markdown
See @fig:results for details. As shown in @tbl:comparison and @eq:model...
```

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
`fig:`/`tbl:` identifier is required. Bilingual captions currently support ordinary
figures and tables; grouped subfigures and LaTeX output are outside this extension.
Custom caption templates must retain exactly one `$$i$$` placeholder.

Both caption languages share the same styles: `Image Caption` for figures and
`Table Caption` for tables. Default typography is owned by
`pandoc/manuscript-template/reference-doc/word/styles.xml` and the matching
`reference-doc.docx`; HTML reads the same XML styles. Adjust these shared styles
in a custom reference DOCX or under `docxStyle` in `style.yml`. Project settings
apply to both caption lines in DOCX and HTML:

```yaml
docxStyle:
  Image Caption:
    fontSize: 10pt
  Table Caption:
    fontSize: 10pt
```

With `docxNativeCrossref: true`, the primary number uses the existing Word `SEQ`
field and the English number uses a `REF` to the same number bookmark. Update
fields in Word after inserting or moving figures/tables; forward references may
require two updates. With native fields disabled, both numbers are resolved at
build time and require rebuilding after source changes.

When a formula needs both `\hat{...}` and a style macro such as `\mathbf{...}`,
write the hat inside the style macro, for example `\mathbf{\hat{C}}` rather
than `\hat{\mathbf{C}}`. When a DOCX build actually starts MathType conversion,
`papper build` and `papper build-reply` warn about the latter form because
MathType-exported PDFs may hide the hat.

Set `mathtypeConversionMethod` in `style.yml` to choose the MathType backend:
`rust` is cross-platform and combines LaTeX -> `mathtype-rust` -> OLE/MTEF
with LaTeX -> SVG -> `latex2wmf` -> WMF/JSON. `set-data` uses MathType's
Windows-only TeX input OLE path. `rust-sdk` preserves the older two-stage path:
`mathtype-rust` generates OLE/MTEF, then the prebuilt MathType helper runs
`sdk-xform-ole` to generate WMF/JSON. On Windows, when MathType is available,
`auto` tries `set-data`, then `rust-sdk`, then `rust`; if MathType is unavailable,
it uses `rust` directly. On non-Windows systems, `auto` always uses `rust`. The
`both` mode is Windows-only: it generates the `rust` and `set-data` backends,
compares only the MTEF formula data streams extracted from OLE and warns if they differ.
WMF previews and JSON metadata are excluded from comparison. The DOCX uses the
`set-data` output. Runtime
conversion never builds the .NET helper; Windows wheels contain its executable.

Set `mathtypeSvgBackend` to choose how the cross-platform `rust` path produces
formula SVG. `ratex` (the default) parses LaTeX directly, embeds glyph outlines,
and reports its exact layout depth for Word baseline placement. `typst` converts
LaTeX math with the pinned MiTeX 0.2.7 Rust converter and evaluates it against
the matching complete official MiTeX Typst scope embedded in the executable.
`mathtypeTypstMathFont` accepts a string for one font used throughout, or an object
with required `font` and `calligraphicFont` fields. The default object selects
XITS Math for `font` and New Computer Modern Math for `calligraphicFont`.
The latter controls `\mathcal`, `\mathscr`, and `\cal`. Both fields accept installed
family names or font file paths relative to their owning style file. XITS Math,
New Computer Modern Math, and STIX Two Math are bundled and require no system
installation. For example, `mathtypeTypstMathFont: STIX Two Math` selects STIX
for both roles; an object can instead select STIX for either role independently.
It renders the result as vector outlines,
reads the labelled formula frame's actual descent before page composition drops
child baselines, and expands the transparent canvas to include glyph ink that
overhangs that frame. Both backends reject SVG features outside the formula vector
subset instead of silently rasterizing them.
`pmt` also preserves whether Pandoc marked each formula as inline or display:
RaTeX uses text style for inline formulas and display style for display formulas,
so fractions, large operators, and limits keep the layout expected in prose.
The RaTeX path treats `0.02em` as the minimum safety margin for glyph overshoot.
It expands only transparent canvas space until the width and both baseline-side
extents land on Word's half-point grid; the Typst path starts with the same rule.
After Typst's adaptive WMF clipping protection, inline previews receive any
remaining bottom whitespace needed to put the final baseline depth back on that
grid. Formula paths are not moved, rescaled, or trimmed by this final step, so
Word's run position needs no rounding compensation. Display previews skip it.

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
`model-comparison.svg` under `figures/`. This is important for reproducible DOCX
builds because the SVG child-image embedding filter resolves local resources
from the SVG file location.

For DOCX builds that use linked child images inside SVG files, keep child-image
embedding enabled in `style.yml`:

```yaml
docxEmbedSvgImages: true
```

With this option, `papper build docx` converts local Markdown image references such
as `figures/model-comparison.svg` to cached self-contained SVG files under
`.pmt/cache/svg-embedded/`. The linked child panels are embedded into the cached
SVG as data URIs, while SVG text and vector elements remain SVG. The source
Markdown and SVG files are not rewritten. If `docxConvertSvgToPng: true` is
enabled, `docxEmbedSvgImages` is automatically disabled because the full SVG is
rasterized instead. If a linked child image is itself an SVG, the DOCX pipeline
automatically converts the composed parent SVG to PNG even when
`docxConvertSvgToPng` is false, because Word cannot render an SVG data URI nested
inside another SVG.

If one SVG must be rasterized for a specific submission target, add
`to-png=true` to that image. Add `to-png-scale=2` on the same image when it
needs a higher PNG scale than the global default. To rasterize every SVG image
in the DOCX build, enable the global option in `style.yml`. Use at most one
global sizing control: `docxSvgToPngWidth`, `docxSvgToPngDpi`, or
`docxSvgToPngScale`.

```yaml
docxConvertSvgToPng: true
docxSvgToPngWidth: 1600
```

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

When `subfigGrid` is used, the HTML build marks its nested layout table and
removes the regular table borders and cell padding from that table. Ordinary
manuscript tables keep their normal HTML table styling.

## Marking Revisions in Red

Use Pandoc custom styles to mark substantive manuscript revisions in generated DOCX and HTML files. The default reference DOCX includes a character style named `Revision Char`, and the HTML template maps the same marker to red text, so revised inline text can be written as a bracketed span:

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

For native Word display equations, add `revision=true` in the equation attribute list. `papper build docx` strips that custom attribute before `pandoc-crossref` runs, keeps labels such as `#eq:model`, and then colors the generated Word equation red during DOCX post-processing.

```markdown
$$
\mathbf{y} = \mathbf{A}\mathbf{x} + \mathbf{b}
$$ {#eq:linear-model revision=true}
```

This revision coloring currently targets native Word equations only. If the DOCX build later converts equations to MathType OLE objects, this equation-level red coloring is not preserved.

The shared Lua filter wraps a paragraph that begins with the standalone word
`where` immediately after a display equation with `custom-style="Para Where"`.
The reference DOCX provides that paragraph style, while the HTML template uses
the marker to remove the body first-line indent. The filter also recognizes
equation-layout tables when a project enables them.

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

This pseudocode style is currently implemented as a normal table, not as a dedicated `algorithm` float. As a result, the template does not currently support cross-references.

## Citations

Use standard Pandoc citation syntax:

- Single citation: `[@smith2023]`
- Multiple citations: `[@smith2023; @jones2024]`
- Narrative citation: `@smith2023 showed that...`
- With page numbers: `[@smith2023, p. 42]`

## Advanced Table Formatting (DOCX Post-Processing)

When generating DOCX output, three post-processing scripts automatically enhance table formatting:

### 1. Table Attributes

Add standard Pandoc attributes to table captions to control DOCX table properties. Pandoc does not preserve arbitrary table attributes in the generated DOCX, so `papper build docx` runs a Lua filter that embeds a hidden WordprocessingML marker before conversion. The DOCX post-processor reads the marker, applies the settings, and removes it before saving the final document.

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

Use special markers to merge table cells in the generated DOCX:

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

**Important notes**:
- Markers are processed and removed during DOCX generation
- Left merges (`!<!`) are processed first, then up merges (`!^!`)
- The marker cell must be empty except for the marker itself

### 3. Auto-fit Tables

All tables are automatically fitted to window width and centered. This can be overridden using the `autofit` or `alignment` table attributes.

**Post-processing modules location**: `src/pandoc_manuscript/docx/postprocess/`
- `process_table_metadata.py` - Applies metadata collected from Pandoc table attributes
- `autofit_tables.py` - Auto-fits tables to window

The shared Pandoc AST filter for the `!<!` and `!^!` markers is
`pandoc/filters/shared/merge_table_cells.lua`; it runs before DOCX, HTML, and
LaTeX writers.

These modules run automatically during `papper build docx` and `papper build-reply` when DOCX post-processing is enabled.

# Style Metadata

This section describes style-related defaults in `style.yml`. Keep paper
content and manuscript-specific metadata in `manuscript.md`; keep reusable
formatting, citation, cross-reference, and DOCX style defaults here.

## Output Style Metadata

`style.yml` separates two configuration domains. Its top-level Papper settings control
build behavior such as MathType conversion, SVG handling, page margins, line
numbers, and DOCX styles. Metadata consumed by Pandoc, citeproc, or
pandoc-crossref belongs under `pandocMetadata`, including CSL, reference titles,
cross-reference labels and prefixes, numbering, and subfigure layout.

The YAML header in `manuscript.md` is manuscript/Pandoc metadata. It recursively
overrides `style.yml:pandocMetadata`, but it does not override Papper-owned top-level
settings. The optional `reply:` section can override both Papper settings and its own
`reply.pandocMetadata` for `papper build-reply`.

Native Word cross-references are opt-in for manuscript DOCX builds. Configure
this at the top level of `style.yml`, outside `pandocMetadata`:

```yaml
docxNativeCrossref: true
```

The default `false` leaves the original Pandoc numbering and hyperlink
references unchanged. With `true`, Arabic figure, table, and equation item
numbers become `SEQ` fields and their references become `REF` fields. Numbered
headings use a Word multilevel list linked to Heading styles; their references
use `REF ... \r \h`. `numberSections: false`, `sectionsDepth`, and `{-}` headings
remain respected. Title text keeps its inline formatting, with numbering stored
in the list rather than typed into the title. Subfigure references combine a
`REF` to the parent's number with a `REF` to the panel letter; panels do not
increment the figure sequence.

Figure and table `SEQ` identifiers use `figureTitle` and `tableTitle` with leading
and trailing whitespace removed; empty labels fall back to `Figure` and `Table`.
Equation sequences retain `Equation`. Native reference bookmark names use
`PapperRef-` plus nine random lowercase letters/digits, with collisions checked
within each build. Bookmark start/end numeric IDs are randomized
in matching pairs across the document XML parts to reduce merge collisions.

For standard level-1 chapter prefixes, `STYLEREF` reads the native heading number
and `SEQ \s 1` restarts item numbering after each chapter. Other custom chapter
prefixes retain Pandoc text and explicit SEQ resets. Non-Arabic heading/item
numbers and unsupported templates retain their Pandoc result with a warning.
Field results are cached during the build. After editing, update fields in Word;
forward references may need two updates. `pandocMetadata.linkReferences: false`
still produces plain reference text instead of `REF` fields. Manuscript YAML
cannot override the Papper-owned `docxNativeCrossref` setting. HTML, LaTeX, JSON,
and reviewer reply builds do not use this DOCX workflow.

The bundled English and Chinese style YAML files provide build defaults.
A new project's `style.yml` starts with only `pandocMetadata: {}` so it can
override either language without pinning values from the other language.

Papper chooses the built-in Pandoc defaults from the effective language first,
then overlays `style.yml:pandocMetadata` and manuscript YAML. This lets explicit
caption labels, cross-reference prefixes, bibliography titles, and CSL paths
override either language's defaults. DOCX `--lang zh-cn` selects Chinese defaults
for one build while preserving explicit non-language metadata.

Older projects may still keep Pandoc keys at the top level of `style.yml`. Papper
continues to load those keys and prints a deprecation warning, but new and updated
projects should move them under `pandocMetadata`. Papper never rewrites the source
`style.yml`; generated Pandoc-only metadata is written under `.pmt/work/`.

In reviewer replies, `papper build-reply` resolves `@fig:...`, `@tbl:...`, `@sec:...`, and `@eq:...` references from the manuscript before writing the reply output. When a copied figure or table keeps its manuscript label, such as `![Caption](image.png){#fig:model}` or `: Caption {#tbl:results}`, the reply build automatically prefixes its caption with the matching manuscript number. DOCX output is selected with an `.docx` output path. A labeled display equation copied into the reply, for example `$$ ... $$ {#eq:model}`, is assigned the matching manuscript equation number and rewritten to the DOCX tab-stop equation layout. If a figure, table, or equation label cannot be resolved from the manuscript, the original Markdown is left unchanged so the missing mapping remains visible.

TXT reply output is selected with an `.txt` output path, for example `papper build-reply reply.md -o output/txt/reply.txt`. It keeps Markdown syntax for `**bold**`, `_emphasis_`, tables, and formulas, replaces images with `[Image: ...]` placeholders, resolves ``(Line `regex`)`` placeholders and manuscript cross-references, removes trailing `{#eq:...}`, `{#fig:...}`, and `{#tbl:...}` label attributes from formulas, images, and tables, strips reply-only `::: {custom-style="Reply to Reviewers"}` wrappers plus `<br>` tags, collapses the resulting extra blank lines to at most one blank line, and restores escaped ordered-list markers such as `1\.` to `1.`.

Collapsed numeric citation ranges can use a journal-specific delimiter after Pandoc citeproc renders them. This is a Papper filter setting rather than Pandoc metadata, so configure it at the top level of `style.yml`:

```yaml
citationNumberRangeDelimiter: "-"  # [1-3]
```

Papper passes this value to its Lua filter through the
`PMT_CITATION_NUMBER_RANGE_DELIMITER` environment variable; it is not written
into generated Pandoc metadata and cannot be overridden from manuscript YAML.

To add a space after commas between non-consecutive numeric citations, edit the active CSL file's citation layout delimiter. For example, in `pandoc/csl/elsevier-vancouver.csl`, change:

```xml
<layout prefix="[" suffix="]" delimiter=",">
```

to:

```xml
<layout prefix="[" suffix="]" delimiter=", ">
```

This changes citations such as `[1,3]` to `[1, 3]`. It does not control collapsed ranges such as `[1-3]`, which are handled by `citationNumberRangeDelimiter`.

When SVG files reference local child images with paths, enable DOCX-only
child-image embedding in `style.yml`. This writes cached self-contained SVG
files for DOCX output while keeping SVG text and vector elements sharp:

```yaml
docxEmbedSvgImages: true
```

For journal submission systems that reject SVG image files entirely, enable
DOCX-only SVG rasterization instead. If only one SVG needs rasterization, add
`to-png=true` to that Markdown image instead of enabling the global option. Use
`to-png-scale=2` on the same image to override the global PNG scale:

```yaml
docxConvertSvgToPng: true
# Optional rasterization control; enable at most one:
docxSvgToPngWidth: 1600
# docxSvgToPngDpi: 300
# docxSvgToPngScale: 1
```

During `papper build docx`, self-contained SVG cache files are written under
`.pmt/cache/svg-embedded/`, and PNG rasterization outputs are written under
`.pmt/cache/svg-png/`. The original Markdown and SVG files are not rewritten.
The PNG converter uses the Python `resvg-py` dependency.

The DOCX post-processing step can update paragraph styles from Papper settings. Add style names under the top-level `docxStyle`; each key is matched against an existing DOCX style name, and missing styles are reported as warnings without stopping the build. The default template uses a two-character first-line indent and no spacing before or after body paragraphs:

Set DOCX page margins under `docxPageMargins`. The values are written into the
reference DOCX before Pandoc conversion, so Pandoc calculates image widths from
the same writable page width that Word will use. Omit a side to keep the
reference DOCX value for that side unchanged:

```yaml
docxPageMargins:
  top: 2.54cm
  bottom: 2.54cm
  left: 3.17cm
  right: 3.17cm
```

To control automatic page numbers in DOCX footers, set `docxShowPageNumbers`.
`true` adds a Word `PAGE` field to each defined footer using the reference
DOCX's `page number` character style; `false` removes `PAGE` fields while
retaining any other footer text. Papper does not request a document-wide field
update when Word opens the file. If the setting is omitted, Papper leaves the
reference DOCX footer unchanged:

```yaml
docxShowPageNumbers: true
```

For DOCX builds, `pmt` derives the pandoc-crossref equation layout automatically;
do not add `tableEqns`, `eqnBlockTemplate`, or `eqnBlockInlineMath` to
`pandocMetadata`. Papper uses a three-column table template with a separate text
number, so both Word math and MathType equations stay centered while native
numbering fields remain right-aligned.

For a one-off DOCX build, the command line can override the top-level
`mathtype` setting without editing `style.yml`:

```powershell
papper build docx --mathtype
papper build docx --no-mathtype
papper build docx --lang zh-cn
```

The command-line value has higher priority than `style.yml`. These flags apply
only to `papper build docx`.

`--lang zh-cn` marks this DOCX build as Chinese-primary. It enables chapter
numbering for cross-referenced items (`图 3-1`, `表 3-1`) and sets the `标题`,
`副标题`, and `标题 1`–`标题 3` DOCX styles to 黑体 for Chinese text and Times New
Roman for Western text, without bold. Headings keep dotted numbers such as
`3.1`; section and equation references use `节` and `式`. The DOCX post-processing
step changes nested heading numbers and `节 3-1` references to dotted notation
without changing the figure/table numbering. `标题 1` uses 小三 (15 pt), and
`标题 2` uses 四号 (14 pt). Chinese DOCX builds hide line numbers by default;
set `docxShowLineNumbers: true` in `style.yml` to show continuous line numbers.
Explicit `docxStyle` entries in `style.yml` override the corresponding Chinese
DOCX style defaults.
If the command
line option is omitted, `pandocMetadata.lang` from the manuscript or style file
selects the language mode. Papper removes `lang` from the metadata passed to
Pandoc and, when it appears in the manuscript header, uses a temporary cleaned
Markdown copy to avoid Pandoc localization warnings; the original Markdown is
unchanged. The `build docx` `--lang` option currently accepts `zh-cn` or `zhcn`.
Chinese DOCX builds use the bundled GB/T 7714—2015 bilingual numeric CSL by
default. An explicit `csl` in manuscript metadata or `style.yml` takes priority.
The bibliography heading is `参考文献`.

To initialize a project with the translated Chinese manuscript and reviewer
reply starters, use:

```powershell
papper init my-paper --lang zh-cn
```

Common Chinese built-in names such as `标题 1`, `正文文本`, and `正文` are automatically mapped to the corresponding Word built-in style names like `Heading 1`, `Body Text`, and `Normal`. Custom styles still need to use their exact DOCX style names.

```yaml
docxStyle:
  正文文本:
    firstLineIndentChars: 2
    paragraphSpacing:
      before: 0pt
      after: 0pt
```

Use point values for paragraph spacing, such as `6pt`. The first-line indent is written as a Word character-based indent, so `2` means two characters rather than a fixed centimeter or inch value. Fields that are omitted from a style block are left unchanged in the DOCX style.

Common style fields under `docxStyle` include:

- `fontFamily`: a font name string applies to both Western and Chinese text; a mapping sets either or both independently, such as `{western: "Times New Roman", chinese: "黑体"}`
- `bold`: `true` or `false`
- `fontSize`: font size such as `10.5pt` or Chinese Word sizes like `小五` and `四号`
- `fontColor`: font color such as `#000000` or `rgb(0, 0, 0)`
- `lineSpacing`: paragraph line spacing such as `1.5` or `18pt`
- `alignment`: `left`, `center`, `right`, or `justify`
- `firstLineIndentChars`: Word character-based first-line indent
- `indentation`: length-based `left`, `right`, `firstLine`, or `hanging` indent values such as `0.5cm`
- `paragraphSpacing`: `before` and `after` spacing values such as `6pt`

## Changing Citation Styles

1. **Browse styles**: Visit [Zotero Style Repository](https://www.zotero.org/styles)
2. **Download CSL file**: Save to `pandoc/` directory
3. **Update `style.yml`**:
   ```yaml
   pandocMetadata:
     csl: pandoc/your-style.csl
   ```

Common styles included:
- `elsevier-vancouver.csl`: Numeric citations (Vancouver style)
- `engineering-applications-of-artificial-intelligence.csl`: EA-AI journal style
