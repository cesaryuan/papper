# Table attribute combinations {#sec:tables}

@tbl:directional selects one row and one column while leaving other cells unchanged.

| Item | Value | Notes |
| ---- | ----- | ----- |
| Baseline | 10 | Original |
| Candidate | 12 | Updated |

: Directional margins and selective revisions. {#tbl:directional cell_margin_top="1mm" cell-margin-bottom="2mm" cell_margin_left="3mm" cell-margin-right="4mm" row-height="0.6cm" alignment="left" autofit="content" revision_rows="2" revision-columns="3"}

The paragraph after the table should retain its post-table style.

| Method | Score |
| ------ | ----- |
| Added method | 15 |

: A newly added table with a revised caption. {#tbl:all-rows revision-rows="*"}

| Method | Score |
| ------ | ----- |
| Another method | 16 |

: Whole-table revision selected through columns. {#tbl:all-columns revision_columns="*" custom-style="TableNoBorder"}

| Item | Description |
| ---- | ----------- |
| Styled cell | **Bold** and *italic* text |
| Merged cell | !<! |

: Custom cell paragraphs with an independent table style. {#tbl:text-style custom-style="TableNoBorder" custom-text-style="Body Text" revision_rows="2"}

This paragraph keeps its post-table style.

| Item | Value |
| ---- | ----- |
| Alias style | 18 |

: The underscore alias also selects cell paragraph styles. {#tbl:alias-text-style custom_text_style="Quote"}

| Item | Value |
| ---- | ----- |
| Default style | 20 |

: The following table retains its default cell style. {#tbl:default-text-style}

| Uncaptioned styled cell |
| ---------------------- |
| A cell without a visible caption |

: {custom-text-style="Quote"}

::: {custom-style="Body Text"}

+---------------------------+-----------------------+
| Nested content            | Value                 |
+===========================+=======================+
| First cell paragraph.     | - First list item     |
|                           | - Second list item    |
| Second cell paragraph.    |                       |
+---------------------------+-----------------------+

: A table inside a styled block with multiple cell paragraphs and a list. {#tbl:block-text-style custom-text-style="Quote"}

:::
