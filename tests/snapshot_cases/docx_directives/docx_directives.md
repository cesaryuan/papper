---
title: Native DOCX insertions
papperSettings:
  mathtype: false
  docxHeader:
    content: '**Global header**'
  docxFooter:
    content: 'Page []{t="pageNum"}'
---

[]{t="toc" title="Research contents" depth="2"}

[]{t="listofFigures" title="Main figures"}

[]{t="listofFigures" title="Figures and panels" includeSubfigures="true"}

[]{t="listofTables" title="Data tables"}

Date: **[]{t="date"}**. Page: *[]{t="pageNum" format="lowerRoman"}*.

[]{t="sectionBreak" pageStart="1" header="**Methods header**" footer='Section page []{t="pageNum"}' headerAlignment="left" footerAlignment="right"}

# Methods {#sec:methods}

![Primary figure](../crossrefs/figure.svg){#fig:primary width=30% caption-en="Translated primary figure"}

:::: {#fig:panels}

![First panel](../crossrefs/figure.svg){#fig:first width=30%}

![Second panel](../crossrefs/figure.svg){#fig:second width=30%}

Combined panels

::::

| Sample | Value |
| ------ | ----: |
| A      | 3     |

: Measurements {#tbl:measurements caption-en="Translated measurements"}

## Details {#sec:details}

Before the page break. []{t="pageBreak"} After the page break.

[]{t="sectionBreak" kind="continuous" headerLink="false" footerLink="true"}

# Results {#sec:results}

Independent copy of the previous header; inherited footer and continued page numbering.

[]{t="sectionBreak" kind="oddPage" header="" footer=""}

Final section with cleared page stories.
