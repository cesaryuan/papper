---
title: Markdown footer
papperSettings:
  docxFooter:
    content: |
      **Confidential** · *Draft* · [Project website](https://example.org/footer)

      1. Review copy
      2. Please retain this footer

      | Logo | Formula | Page |
      | :--- | :---: | ---: |
      | ![Logo](logo.png){width=0.25in} | $x^2$ | **[]{t="pageNum" format="upperRoman"}** |
    alignment: right
---

# First page

The footer contains Markdown typography, links, a numbered list, an image, a
native Word equation, and a table with a Roman page number.

```{=openxml}
<w:p><w:r><w:br w:type="page"/></w:r></w:p>
```

# Second page

All footer content repeats while the dynamic page number changes.
