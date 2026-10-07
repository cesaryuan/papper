---
subfigGrid: true
---

See [@fig:_RefGrid] and [@fig:_RefPanel]. As shown in [@fig:_RefGrid], the fields agree.

::: {#fig:_RefGrid}
![**Field A**](a.png){#fig:_RefGrid-a width="50%" label="a" original-width="3in" original-height="2in"} ![Field B](b.png){#fig:_RefPanel width="50%" label="b" original-width="3in" original-height="2in"}

![Field C](c.png){#fig:_RefGrid-c width="50%" label="c" original-width="3in"} ![Field D](d.png){#fig:_RefGrid-d width="50%" label="d" original-width="3in"}

Figure 1 Fields
:::

[@fig:_RefGrid] shows the reconstructed fields. This paragraph must survive.

::: {#fig:subfig-2}
![Upper panel](e.png){#fig:subfig-2-c width="50%" label="c" original-width="6cm"} ![Lower panel](f.png){#fig:subfig-2-d width="50%" label="d" original-width="6cm"}

Fig. 2 Inline child captions
:::

::: {#fig:subfig-3}
![Top](g.png){#fig:subfig-3-a width="100%" label="a" original-width="4in"}

![Bottom](h.png){#fig:subfig-3-b width="100%" label="b" original-width="4in"}

图3 纵向子图
:::

::: {#fig:subfig-4}
![](i.png){#fig:subfig-4-1 width="50%" original-width="2in"} ![](j.png){#fig:subfig-4-2 width="50%" original-width="2in"}

图4 无子题注
:::

::: {#fig:subfig-5}
![Upper paragraph](k.png){#fig:subfig-5-a width="100%" label="a" original-width="4in"}

![Lower paragraph](l.png){#fig:subfig-5-b width="100%" label="b" original-width="4in"}

图5 段落形式
:::

| ![](m.png)                   | ![](n.png)                    |
|------------------------------|-------------------------------|
| 图6 First independent figure | 图7 Second independent figure |

| ![](o.png) | ![](p.png) |
|------------|------------|
| \(a\) One  | \(b\) Two  |

Unnumbered caption: leave the table alone.

| ![](q.png) | ![](r.png) |
|------------|------------|

: 图8 Internal title

图9 Conflicting title

| ![](s.png) | 123 |
|------------|-----|
| ![](t.png) | 456 |

: Table 1 Actual data {#tbl:fuzz-1}

::: {#fig:subfig-10}
![Existing A](existing-a.png){#fig:existing-a width="50%"} ![Existing B](existing-b.png){#fig:existing-b width="50%"}

Already authored caption.
:::

::: {#fig:subfig-10-2}
![](u.png){#fig:subfig-10-2-1 width="50%"} ![](v.png){#fig:subfig-10-2-2 width="50%"}

Fig. 10 ID collision
:::

::: {#fig:existing}
![](w.png){#fig:existing-1 width="50%"} ![](x.png){#fig:existing-2 width="50%"}

Figure 11 Existing table reference
:::

As shown in [@fig:existing], both panels agree.

::: {#fig:subfig-12}
![Left](aa.png){#fig:custom-panel width="50%" label="a" original-width="2in"} ![Right](ab.png){#fig:subfig-12-b width="50%" label="b" original-width="2in"}

Fig. 12 Cell separators left behind by an old export
:::

See [custom panel](#fig:custom-panel).

| ![](ac.png) | ![](ad.png) |
|-------------|-------------|

图13中所示的图片是正文引用，这一段不能被吞作题注。
