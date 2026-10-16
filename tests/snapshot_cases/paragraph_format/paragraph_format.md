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

[^scope]: A note keeps its own formatting.

    ::: {fontSize="8pt" alignment="left"}
    Explicitly formatted note paragraph.
    :::
