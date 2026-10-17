---
papperSettings:
  mathtype: false
  docxStyle:
    Revision Char:
      fontColor: "#FF0000"
    Quote:
      fontSize: 16pt
      bold: true
      paragraphSpacing: {before: 12pt, after: 12pt}
    Block Text:
      borders:
        all: "0.75pt solid #005588"
        between: none
        space: 4pt
      firstLineIndentChars: 0
      paragraphSpacing: {before: 3pt, after: 3pt}
    Heading 3:
      borders: {bottom: "0.75pt double #666666", space: 2pt}
---

# Local paragraph formatting

Control before the formatted block.

::::: {#local-format .example somekey="preserved" custom-style="Quote" fontSize="10pt" fontColor="#005588" bold="false" alignment="center" firstLineIndentChars="0" lineSpacing="18pt" paragraphSpacing.before="3pt" paragraphSpacing.after="6pt" indentation.left="0.5cm" fontFamily="Times New Roman"}
Outer paragraph with **bold words**, *emphasis*, [revision words]{custom-style="Revision Char"}, and [a link](https://example.org).

Another outer paragraph with a note.[^scope]

::: {alignment="right" paragraphSpacing.after="0pt" indentation.hanging="6pt" fontFamily.chinese="SimSun"}
Inner paragraph inherits size, color, font and exact line spacing.
:::

Outer paragraph after the nested block.

- First list paragraph.
- Second list paragraph.

> Quoted paragraph.

## Heading keeps its own format

```text
Code keeps its own format.
```

| Item | Value |
|------|-------|
| Cell | Text  |

: Table keeps its own format.

$$z = x + y$$
:::::

Control after the formatted block with a [jump](#local-format).

::: {fontSize="小五" lineSpacing="1.5" indentation.firstLine="12pt"}
Chinese Word size and proportional line spacing.
:::

::: {fontSize="9pt"}
Only the font size changes; ordinary paragraph spacing and indentation remain.
:::

::: {firstLineIndentChars="1" fontSize="10pt"}
One-character local first-line indent.
:::

::: {indentation.left="18pt" indentation.hanging="6pt" fontSize="10pt"}
Local physical hanging indent.
:::

# Paragraph borders

Control paragraph without a border.

::::: {#named-border custom-style="Block Text"}
First paragraph of a connected named-style box.

Second paragraph of the same box, with **bold** text.

::: {borders.bottom="1.5pt wave #BB5500"}
Local wave bottom overrides the named style only on this paragraph.
:::

::: {borders="none"}
Local reset removes every inherited border.
:::
:::::

### Heading with a double bottom border

::: {#local-box borders="0.75pt solid #808080" borders.space="4pt" borders.between="0.5pt dotted #005588" firstLineIndentChars="0"}
First local box paragraph.

Second local box paragraph, separated by a dotted border.
:::

::: {#left-only borders.left="2pt solid rgb(0, 85, 136)" borders.space="6pt" firstLineIndentChars="0"}
A left-side rule using the CSS-like shorthand.
:::

::: {#special-lines borders.top="1pt dotDash #666666" borders.bottom="1.5pt thinThickMediumGap #666666" borders.space="4pt"}
Word retains the compound line styles; HTML approximates them.
:::

::::: {#nested borders="0.5pt solid #999999" borders.space="3pt"}
Outer border before nesting.

::: {borders.all="1pt dashed #005588" borders.left="none"}
Inner border changes the outer edges and removes the left edge.
:::

Outer border resumes after nesting.
:::::

Control paragraph after all blocks.

[^scope]: A note keeps its own formatting.

    ::: {fontSize="8pt" alignment="left"}
    Explicitly formatted note paragraph.
    :::
