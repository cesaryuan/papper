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

: Whole-table revision selected through columns. {#tbl:all-columns revision_columns="*"}
