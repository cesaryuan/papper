---
subfigGrid: true
papperSettings:
  docxHeader:
    content: '![](colors.png "Header"){width="1in" crop-bottom="25"}'
    alignment: left
---

# Editable rectangular crops

An inline detail ![](colors.png "Inline"){width="0.5in" crop-left="25" crop-right="25"} keeps the original embedded.

![Top detail.](colors.png "Standalone"){#fig:detail width="2in" crop-top="20" crop-bottom="10"}

:::: {#fig:panels}
![Left half.](colors.png "Left"){#fig:left width="50%" crop-right="50"}
![Right half.](colors.png "Right"){#fig:right width="50%" crop-left="50"}

Independent crops of a shared image.
::::

The note also keeps editable cropping.[^detail]

[^detail]: ![](colors.png "Note"){width="1in" crop-left="12.5" crop-top="10"}
