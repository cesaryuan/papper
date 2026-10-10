---
title: Manuscript style override
papperSettings:
  mathtype: false
  tableAutofit: content
  docxShowPageNumbers: false
  docxPageMargins:
    left: 1.5cm
  docxStyle:
    Normal:
      fontSize: 11pt
      color: '336699'
      paragraphSpacing:
        after: 0pt
  pandocMetadata:
    title: This style title must be overridden
    tableTitle: Local Table
---

# Local formatting

This paragraph inherits the style file's spacing before and overrides its
spacing after, font size, and color.

| Item | Value |
| ---- | ----- |
| Sample | 1 |

: A table using the manuscript style. {#tbl:local}

See @tbl:local.

$$
x = 1
$$ {#eq:local}
