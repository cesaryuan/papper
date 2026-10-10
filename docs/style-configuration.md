# Style Configuration

Use `style.yml` to configure reusable formatting and build behavior for a
Papper project. For manuscript Markdown and YAML syntax, see
[`manuscript-syntax.md`](manuscript-syntax.md).

## Configuration Placement and Precedence

Keep Papper settings at the top level and Pandoc settings under
`pandocMetadata`. Use `papper-style` in manuscript YAML for formatting overrides
that apply only to that Markdown file.

`style.yml` separates two configuration domains. Its top-level Papper settings control
build behavior such as MathType conversion, SVG handling, page margins, line
numbers, and DOCX styles. Metadata consumed by Pandoc, citeproc, or
pandoc-crossref belongs under `pandocMetadata`, including CSL, reference titles,
cross-reference labels and prefixes, numbering, and subfigure layout.

Ordinary fields in the YAML header in `manuscript.md` are manuscript/Pandoc
metadata. They recursively override `style.yml:pandocMetadata`. Papper-owned
settings in that header belong inside a `papper-style` mapping, which accepts
the same settings, values, and aliases as `style.yml`, including
`pandocMetadata` and `reply`. The optional style `reply:` section can override both Papper settings and its own
`reply.pandocMetadata` when `papper build html/docx` detects a `reply` path in
the input Markdown header. The Markdown `reply` field is a manuscript path;
the style `reply` section contains formatting overrides.

Without an explicit style file, Papper loads the project-root `style.yml`, then
overlays a `style.yml` beside the input Markdown if it is in a different
directory. Values beside the manuscript take priority. Bundled language
defaults fill in settings that the project does not specify. To select one
file instead of this discovery, use:

```powershell
papper build docx manuscript.md --style-file journal-style.yml
```

A relative `--style-file` path is resolved from the project directory.
`PMT_STYLE_FILE` provides the same selection when the command-line option is
absent. The examples below use the preferred camelCase Papper setting names;
their kebab-case and snake_case aliases are also accepted.

For example:

```yaml
mathtype: true
docxNativeCrossref: false
pandocMetadata:
  csl: pandoc/csl/elsevier-vancouver.csl
  subfigGrid: true
reply:
  tableAutofit: content
  docxStyle:
    Body Text:
      firstLineIndentChars: 0
      paragraphSpacing: {before: 6pt, after: 6pt}
  pandocMetadata:
    reference-section-title: References cited in this reply
```

Reply overrides affect reply builds only. Ordinary fields in the reply Markdown's
YAML header still have the highest priority for Pandoc metadata. Keep manuscript-specific
title, author, abstract, and bibliography values in the manuscript YAML.

HTML and DOCX reply formatting sets table text and figure/table captions to
blue italic text, and `Para Where` explanations to blue. These reply effects
take priority over configured colors for those elements; ordinary manuscript
builds keep their configured colors.

When both the project root and the reply Markdown directory contain `style.yml`,
their `reply` sections are merged recursively too. Reply-directory values override
project-root values, while unspecified reply settings remain inherited. Within
the merged style, `reply.pandocMetadata` overrides top-level `pandocMetadata`;
the reply Markdown's ordinary YAML fields can override both.

### Per-Manuscript Style Overrides

Put a `papper-style` mapping in the YAML header of the Markdown file passed to
`papper build` to override project styles for that file, without editing
`style.yml`. It also works when no project style file exists.

```yaml
---
title: A manuscript with its own formatting
papper-style:
  mathtype: false
  docxShowPageNumbers: false
  docxPageMargins:
    left: 2cm
  docxStyle:
    Normal:
      fontSize: 11pt
  pandocMetadata:
    tableTitle: Table
---
```

This manuscript uses native Word equations, hides DOCX page numbers, sets its
left margin to `2cm`, and changes the `Normal` font size to `11pt`. Other
margins and style properties remain inherited. `pandocMetadata.tableTitle`
sets its table caption prefix in supported outputs.

Mappings merge recursively; scalars and lists replace inherited values.
Explicit `false`, zero, and `null` are preserved where the corresponding
setting accepts them. `papper-style` must be a mapping and uses the same
validation rules and output-specific limits as `style.yml`. Relative font
file paths inside it resolve beside the Markdown file.

For Papper settings, precedence from lower to higher is bundled language
defaults, discovered or explicitly selected style files, file-based reply
overrides when applicable, and `papper-style`. In a reply build,
`papper-style.reply` overrides the other values in `papper-style` and inherits
unspecified reply settings from the files. Explicit build command-line
overrides still take priority.
Set `papper-style.reply: null` to clear inherited reply configuration for
this file; reply recognition and its automatic blue styling still apply.

Pandoc metadata follows the same style layers, then ordinary manuscript YAML
fields take highest priority. For example, a top-level `title` overrides
`papper-style.pandocMetadata.title`. A `lang` in
`papper-style.pandocMetadata` can select language defaults when the ordinary
header does not supply one. The `papper-style` mapping itself is configuration
and does not appear as document metadata.

## MathType Equations

Choose native Word or editable MathType equations for DOCX, with conversion
methods, preview renderers, and math fonts.

Set `mathtype: true` at the top level of `style.yml` to generate editable
MathType equations in DOCX. Set it to `false` to keep native Word equations.
If the conversion environment is unavailable, the build keeps native Word
equations and reports the fallback.

Choose `mathtypeConversionMethod` according to your environment:

| Value | When to use it |
| --- | --- |
| `rust` | The default, available across platforms without a MathType installation. |
| `set-data` | Use installed MathType on Windows for conversion. |
| `rust-sdk` | Use the alternative Windows conversion path that also requires MathType. |
| `auto` | On Windows, try the installed MathType paths and then fall back to `rust`; elsewhere, use `rust`. |
| `both` | Diagnose conversion differences on Windows with MathType installed; the document uses the `set-data` result. |

For the `rust` method, `mathtypeSvgBackend` selects the equation preview
renderer: `typst` (the default) or `ratex`. With `typst`, use
`mathtypeTypstMathFont` to select fonts:

```yaml
mathtype: true
mathtypeConversionMethod: rust
mathtypeSvgBackend: typst
mathtypeTypstMathFont:
  font: XITS Math
  calligraphicFont: New Computer Modern Math
```

The font object requires both fields. `calligraphicFont` controls `\mathcal`,
`\mathscr`, and `\cal`; `font` controls the other symbols. A string such as
`mathtypeTypstMathFont: STIX Two Math` selects one font for both roles.
XITS Math, New Computer Modern Math, and STIX Two Math are bundled. You can also
use an installed family name or a font file path relative to the style file.
These settings change the `rust` method's Typst previews; they do not change
the previews produced by installed MathType or the `ratex` renderer.

## Native Word Cross-References

Enable `docxNativeCrossref` for updateable Word numbering and references in
manuscript DOCX, including numeric citations.

This setting is opt-in. Set it at the top level of `style.yml`:

```yaml
docxNativeCrossref: true
```

With `false` (the default), numbers and references are resolved when you build
and require rebuilding after source changes. With `true`, Word can update
figure, table, equation, and heading numbers and their references after edits.
After inserting or moving items in Word, update its fields; forward references
may need two updates. Both lines of a bilingual caption use the same number.

Heading numbering respects `numberSections`, `sectionsDepth`, and unnumbered
`{-}` headings. Subfigure panels share the parent's number and add panel letters.
Non-Arabic numbering and unsupported custom templates keep their build-time
numbers with a warning. Custom bilingual caption templates require exactly one
`$$i$$` number placeholder.

With a numeric CSL, bibliography entry numbers and numeric citation links also
become updateable Word numbers and references. Author-date citations and
unsupported citation forms keep their ordinary CSL-formatted output.

Set `pandocMetadata.linkReferences: false` to use plain reference text. Native
Word cross-references apply only to manuscript DOCX builds, not reviewer replies
or other output formats.

## Language Defaults

Select English or Chinese build defaults; explicit formatting settings
override bundled values, and DOCX `--lang` selects language for one build.

The bundled English and Chinese style YAML files provide build defaults.
`papper init` creates a project `style.yml` with explicit settings for MathType,
SVG handling, line numbers, page numbers, and table autofit, plus an empty
`pandocMetadata` mapping. These explicit project values override the bundled
defaults for either language. Remove a setting from the project file when you
want it to follow the selected language's bundled default.

Papper chooses the built-in Pandoc defaults from the effective language first,
then overlays `style.yml:pandocMetadata` and manuscript YAML. This lets explicit
caption labels, cross-reference prefixes, bibliography titles, and CSL paths
override either language's defaults. DOCX `--lang zh-cn` selects Chinese defaults
for one build while preserving explicit non-language metadata.

Older projects may still keep Pandoc keys at the top level of `style.yml`. Papper
continues to load those keys and prints a deprecation warning, but new and updated
projects should move them under `pandocMetadata`. Your source `style.yml` is not
rewritten by the build.

## Pandoc Metadata and Numbering

Use `pandocMetadata` for caption labels, reference prefixes and links, section
and item numbering, subfigure layouts, and CSL citation settings.

For example:

```yaml
pandocMetadata:
  figureTitle: Figure
  tableTitle: Table
  titleDelim: ""
  figPrefix: Figure
  tblPrefix: Table
  secPrefix: Section
  eqnPrefix: Equation
  linkReferences: true
  autoSectionLabels: true
  autoEqnLabels: true
  numberSections: true
  sectionsDepth: 3
  subfigGrid: true
  subfigureChildTemplate: "($$i$$) $$t$$"
  subfigureTemplate: "$$figureTitle$$ $$i$$$$titleDelim$$ $$t$$"
  reference-section-title: References
  link-citations: true
  csl: pandoc/csl/elsevier-vancouver.csl
```

| Keys | Purpose |
| --- | --- |
| `figureTitle`, `tableTitle`, `titleDelim` | Caption labels and the separator between the number and caption text. |
| `figPrefix`, `tblPrefix`, `secPrefix`, `eqnPrefix` | The words used when referencing figures, tables, sections, and equations. |
| `linkReferences` | Make cross-references clickable; `false` keeps plain reference text. |
| `autoSectionLabels`, `autoEqnLabels` | Supply section labels automatically and number display equations automatically. Explicit labels are preferable for stable references. |
| `numberSections`, `sectionsDepth` | Enable heading numbering and choose how many heading levels are numbered. |
| `chapters`, `chaptersDepth`, `chapDelim` | Use chapter-based item numbers, choose the chapter heading depth, and set the separator; Chinese defaults use numbers such as `3-1`. |
| `subfigGrid` | Enable the grouped Markdown subfigure layout described in the syntax guide. |
| `subfigureChildTemplate`, `subfigureTemplate` | Format panel captions and the shared parent caption. `$$i$$` is the number or panel letter, and `$$t$$` is caption text. |
| `reference-section-title` | Bibliography heading text. |
| `link-citations` | Link citations to bibliography entries in supported outputs. |
| `csl` | The citation style file used for DOCX and HTML. |
| `lang` | Document language and selection of English or Chinese defaults. |

The bundled defaults enable reference links, automatic section labels,
automatic equation numbering, heading numbering through level 3, and subfigure
grids. Manuscript YAML can override these values for one manuscript. For
document-class options and bibliography input syntax, see
[`manuscript-syntax.md`](manuscript-syntax.md).

## Numeric Citation Formatting

Configure DOCX numeric citation range delimiters in `style.yml`; edit the CSL
layout delimiter for commas between separate numeric citations.

Set the delimiter for collapsed numeric citation ranges at the top level of
`style.yml`:

```yaml
citationNumberRangeDelimiter: "-"  # [1-3]
```

Manuscript YAML cannot override this top-level setting. This control applies
to numeric citation ranges in DOCX; omit it to retain the CSL's range delimiter.

To add a space after commas between non-consecutive numeric citations, edit the active CSL file's citation layout delimiter. For example, in `pandoc/csl/elsevier-vancouver.csl`, change:

```xml
<layout prefix="[" suffix="]" delimiter=",">
```

to:

```xml
<layout prefix="[" suffix="]" delimiter=", ">
```

This changes citations such as `[1,3]` to `[1, 3]`. It does not control collapsed ranges such as `[1-3]`, which are handled by `citationNumberRangeDelimiter`.

## Table Layout Defaults

Set `tableAutofit` for ordinary DOCX/HTML tables using `window`, `content`,
`fixed`, or `none`; per-table attributes take priority.

```yaml
tableAutofit: none
```

| Value | Behavior |
| --- | --- |
| `window` | Fit the table to the available page width. |
| `content` | Size the table to its contents. |
| `fixed` | Preserve authored column widths and disable automatic resizing. |
| `none` | Leave tables without an explicit autofit attribute at their own settings; this is the default. |

A table caption's `autofit` attribute takes priority. This default does not
change generated equation or subfigure layouts. Set `reply.tableAutofit` to
choose a different default for reviewer replies. Table styles, cell margins,
cell merging, and revision attributes are documented in
[`manuscript-syntax.md`](manuscript-syntax.md#advanced-table-formatting).

## SVG Images in DOCX

Embed SVG child images or rasterize SVGs to PNG in DOCX; configure PNG
resolution globally or enable conversion on individual images.

When SVG files link to local child images, enable embedding so the generated
DOCX includes those images while SVG text and vector elements stay sharp:

```yaml
docxEmbedSvgImages: true
```

For journal submission systems that reject SVG image files entirely, enable
DOCX-only SVG rasterization instead. If only one SVG needs rasterization, add
`to-png=true` to that Markdown image instead of enabling the global option. Use
`to-png-scale=2` on an image without a width attribute to override the global
PNG scale:

```yaml
docxConvertSvgToPng: true
# Optional rasterization control; enable at most one:
docxSvgToPngWidth: 1600
# docxSvgToPngDpi: 300
# docxSvgToPngScale: 1
```

`docxSvgToPngWidth` is a positive integer pixel width. `docxSvgToPngDpi` and
`docxSvgToPngScale` are positive numbers; their fallback values are 300 DPI and
scale 1. Set at most one of these three global controls. A global pixel width,
or a width derived from the Markdown image's `width` attribute, takes priority
over scaling. A per-image `to-png-scale` is ignored when such a width is used.
Increasing PNG resolution does not change the image's authored display width.

The per-image aliases `to_png` / `toPng` and `to_png_scale` / `toPngScale` are
also accepted. These settings affect SVG images in DOCX, not other image
formats or the HTML/LaTeX outputs. New projects enable child-image embedding
and leave global SVG-to-PNG conversion disabled.

Global SVG rasterization takes priority over child-image embedding. If a
linked child image is itself an SVG, the composed parent is converted to PNG
for Word compatibility. Source Markdown and SVG files are not rewritten.

SVG selection follows the effective resource search order: the input Markdown's
directory, then the project directory by default. `--resource-path` replaces this
order for both SVG processing and other image formats. A same-named project image
therefore does not override an image beside the manuscript.

## DOCX Line Numbers

Use `docxShowLineNumbers` for continuous, per-page, or per-section DOCX line
numbers; `false` preserves the reference DOCX's settings.

Set it at the top level of `style.yml`:

```yaml
docxShowLineNumbers: continuous
```

| Value | Behavior |
| --- | --- |
| `true` or `continuous` | Number lines continuously through the document. |
| `restart-page` | Restart numbering on each page. |
| `restart-section` | Restart numbering in each Word section. |
| `false` | Do not enable or alter line numbering; retain the reference DOCX's settings. |

The Chinese values `连续`, `每页`, `每节`, and `关闭` are accepted equivalents.
The English bundled default is continuous numbering; the Chinese bundled
default is `false`. The generated project `style.yml` explicitly uses
`continuous`, so set it to `false` or remove that entry if you want to follow
the Chinese default. A reference document that already has line numbers needs
those numbers disabled in Word when you want `false` to produce no line numbers.

## DOCX Page Layout

Configure DOCX page margins and footer page numbers, including how to retain
reference DOCX settings; margins also determine image sizing width.

Set DOCX page margins under `docxPageMargins`. Image sizing uses the resulting
writable page width. Omitted sides preserve the corresponding reference DOCX
margin when no bundled or project value supplies that side:

```yaml
docxPageMargins:
  top: 2.54cm
  bottom: 2.54cm
  left: 3.17cm
  right: 3.17cm
```

Lengths accept `pt`, `cm`, `mm`, and `in`; numbers without a unit mean points.
`inside` and `outside` are aliases for the left and right margins, respectively.
The bundled margins are 2.54 cm at the top/bottom and 3.17 cm at the left/right.
Project settings merge with these defaults. To preserve all margins from a
custom reference DOCX, use `docxPageMargins: null`.
An explicit `null` in the selected project style or manuscript `papper-style`
overrides inherited margins, including the bundled defaults.

To control automatic page numbers in DOCX footers, set `docxShowPageNumbers`.
`true` shows automatic page numbers using the reference DOCX's `page number`
character style; `false` hides automatic page numbers and retains other footer
text. The bundled default is `true`. Set `null` to retain the reference DOCX's
footer settings:

```yaml
docxShowPageNumbers: true
```

Papper centers display equations and right-aligns their numbers automatically.
Leave `tableEqns`, `eqnBlockTemplate`, and `eqnBlockInlineMath` out of
`pandocMetadata` for DOCX builds.

## One-Off DOCX Builds

Use DOCX command-line flags to override MathType conversion, select Chinese
language defaults, or use and export a Word reference document for one build.

```powershell
papper build docx --mathtype
papper build docx --no-mathtype
papper build docx --lang zh-cn
```

The command-line value has higher priority than `style.yml`. These flags apply
only to `papper build docx`.

Use `--reference-doc PATH` to select a custom Word reference DOCX, for example
one containing a journal's paragraph styles. Without this option, Papper uses
`PMT_REFERENCE_DOC` when set, otherwise its bundled reference. Relative paths
are resolved from the current project directory; missing files are errors.
Papper applies effective `docxStyle` settings and page margins to a temporary
reference copy before generating the DOCX; the selected source file is left
unchanged. To keep the custom reference's margins, set `docxPageMargins: null`
in `style.yml`. To keep all its text styles too, set `docxStyle: null`.

Use `--export-reference-doc [PATH]` to save the effective reference DOCX and
skip the actual manuscript build. Omitting `PATH` writes `reference-doc.docx`
in the current project directory. The exported reference includes the page
margins and `docxStyle` settings selected for the build. Line numbers and footer
page numbers apply to the generated document; the export does not capture
those output formatting changes or the manuscript content.
Export does not run Pandoc, resolve manuscript references, process images or
convert MathType equations. Existing manuscript outputs are left unchanged.
If no Markdown file is selected and `manuscript.md` is absent, export uses the
project style and bundled defaults. An existing or explicitly selected Markdown
file supplies YAML overrides and language/reply settings; its body is not rendered.
Explicitly selected missing Markdown files are errors. `--output-file` does not
select the export destination; use the optional `PATH` argument instead.
Export creates missing destination directories and replaces an existing export
file only after reference preparation succeeds. Its path must differ from the manuscript,
selected reference input and final DOCX output. Both options also work when
`papper build docx` detects a reviewer reply, and neither applies to other targets.

```powershell
# Export the effective reference for editing in Word, without building the manuscript
papper build docx --export-reference-doc

# Build with the edited reference
papper build docx manuscript.md --reference-doc journal-reference.docx

# Export the custom reference with this build's configured margins and text styles
papper build docx manuscript.md --reference-doc journal-reference.docx --export-reference-doc exports/active-reference.docx
```

`--lang zh-cn` marks this DOCX build as Chinese-primary. It enables chapter
numbering for cross-referenced items (`图 3-1`, `表 3-1`) and sets the `标题`,
`副标题`, and `标题 1`–`标题 3` DOCX styles to 黑体 for Chinese text and Times New
Roman for Western text, without bold. Headings and section references use dotted
numbers such as `3.1`; section and equation references use `节` and `式`.
`标题 1` uses 小三 (15 pt), and
`标题 2` uses 小四 (12 pt). The Chinese bundled style does not enable line
numbers; an explicit project `docxShowLineNumbers` value takes priority, as
explained under DOCX Line Numbers.
Explicit `docxStyle` entries in `style.yml` override the corresponding Chinese
DOCX style defaults.

If the command-line option is omitted, `lang` in the manuscript YAML or
`style.yml:pandocMetadata` selects the language mode. The `build docx --lang`
option accepts `zh-cn` or `zhcn`.

Chinese DOCX builds use the bundled GB/T 7714—2015 bilingual numeric CSL by
default. An explicit `csl` in manuscript metadata or `style.yml` takes priority.
The bibliography heading is `参考文献`.

To initialize a project with the translated Chinese manuscript and reviewer
reply starters, use:

```powershell
papper init my-paper --lang zh-cn
```

## DOCX Text Styles

Use `docxStyle` to configure fonts and paragraph formatting for existing
reference DOCX styles; HTML uses the supported text style settings too.

Set style names under the top-level `docxStyle` key. Styles must exist in the
reference DOCX; missing styles produce warnings. Omitted properties retain
their existing values. DOCX builds apply these settings to a temporary
reference copy before generating the document, and `--export-reference-doc`
includes the configured styles. The source reference is left unchanged, and
text style settings still apply when page margins are retained with
`docxPageMargins: null`. Set `docxStyle: null` to retain all reference text styles.
The bundled reference DOCX explicitly sets `Normal`
to `12pt` (小四). The default body style has a two-character first-line
indent and no spacing before or after paragraphs.

HTML uses the same supported text style settings for its corresponding
paragraphs and spans. Page margins, footer numbers, and Word line numbers are
DOCX-specific. Style settings do not change LaTeX document-class typography.

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
- `fontColor`: font color such as `"#000000"`, `"rgb(0, 0, 0)"`, or `[0, 0, 0]`; quote a hexadecimal value so YAML does not treat it as a comment
- `lineSpacing`: a positive line multiple such as `1.5`, the named values `single`, `one-half`, or `double`, or an exact point value such as `18pt`
- `alignment`: `left`, `center`, `right`, `justify`, or `distribute`; `centre` is an alias for `center`
- `firstLineIndentChars`: Word character-based first-line indent
- `indentation`: length-based `left`, `right`, `firstLine`, or `hanging` indent values such as `0.5cm`
- `paragraphSpacing`: `before` and `after` spacing values such as `6pt`

Do not combine `firstLineIndentChars` with length-based `firstLine` or
`hanging` in the same style. Length-based `firstLine` and `hanging` are also
mutually exclusive. Left and right indents may be used with either approach.
Font sizes must be positive; point sizes and the Chinese Word size names are
accepted. Use the canonical fields above for new configurations; existing
`fontName` and nested `font.family` spellings remain accepted.

For example, configure body text and a revision character style:

```yaml
docxStyle:
  Body Text:
    fontFamily: {western: Times New Roman, chinese: 宋体}
    fontSize: 10.5pt
    lineSpacing: 1.5
    alignment: justify
    firstLineIndentChars: 2
    paragraphSpacing: {before: 0pt, after: 0pt}
  Revision Char:
    fontColor: "#FF0000"
```

Apply named paragraph or character styles in Markdown using `custom-style`.
To change a table's cell paragraph style, use `custom-text-style`; table
appearance is a separate `custom-style`. See the syntax guide for examples.

## Changing Citation Styles

Choose or download a CSL file and set `pandocMetadata.csl` to its path for
DOCX/HTML citations and bibliography formatting.

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
