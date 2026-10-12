---
title: Footer number formats
papperSettings:
  docxFooter:
    content: |
      Decimal []{t="pageNum" format="decimal"} · Roman []{t="pageNum" format="upperRoman"} / []{t="pageNum" format="lowerRoman"}

      Letters []{t="pageNum" format="upperLetter"} / []{t="pageNum" format="lowerLetter"} · Dash []{t="pageNum" format="decimalEnclosedDash"}

      Ordinal []{t="pageNum" format="ordinal"} · Cardinal []{t="pageNum" format="cardinalText"} · Ordinal text []{t="pageNum" format="ordinalText"} · Hex []{t="pageNum" format="hex"}
    alignment: center
---

# First page

Each placeholder applies its own numbering format to the same current page.

```{=openxml}
<w:p><w:r><w:br w:type="page"/></w:r></w:p>
```

# Second page

Formatting switches remain dynamic on subsequent pages.
