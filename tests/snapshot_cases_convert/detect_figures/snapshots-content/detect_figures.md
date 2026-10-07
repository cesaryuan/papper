# Table layout figures

![[]{#_Ref181215092 .anchor}图3‑17 车辆荷载示意图](media/image762.png){width="4.986899606299213in" height="2.7142858705161856in"}

![Figure 2-4 **English** caption](media/second.png){width="3in"}

  Actual                                     Data
  ------------------------------------------ -------
  ![](media/not-a-figure.png){width="5in"}   42
  图3-18 不能合并                            Other

  ------------------------------------------------------
  ![Already captioned](media/already.png){width="5in"}
  图3-19 已有题注不能合并
  ------------------------------------------------------

  -----------------------------------
  ![](media/three.png){width="5in"}
  图3-20 第一行
  图3-21 第二行
  -----------------------------------

  ---------------------------------------------
  ![](media/captioned-table.png){width="5in"}
  图3-22 原有表题注
  ---------------------------------------------

  : A real table caption

  ------------------------------------------------
  Notes beside ![](media/prose.png){width="5in"}
  图3-23 原有正文不能丢失
  ------------------------------------------------

# Tables with their own figure captions

![[[]{#_Toc241698015 .anchor}]{#_Ref202346883 .anchor}图4‑1 方法流程图](media/image1163.png){width="5.654166666666667in" height="3.3472222222222223in"}

![[]{#_RefHeader .anchor}Figure 4-2 **Method** workflow](media/header-image.png "Image title"){width="4in" height="2in"}

![[]{#_RefBody .anchor}图4‑3 表体中的图片](media/body-image.png){width="3in" height="2in"}

  ----------------------------------------------
  ![](media/ordinary-caption.png){width="3in"}
  ----------------------------------------------

  : An ordinary table caption

  -----------------------------------------
  ![](media/table-title.png){width="3in"}
  -----------------------------------------

  : 表4‑1 真实表格题注

  Image                                      Data
  ------------------------------------------ ------
  ![](media/multi-column.png){width="3in"}   42

  : 图4‑4 多列表格

  ---------------------------------------------------------------
  ![Existing caption](media/already-captioned.png){width="3in"}
  ---------------------------------------------------------------

  : 图4‑5 已有图片题注

  ------------------------------------------------
  Notes beside ![](media/prose.png){width="3in"}
  ------------------------------------------------

  : 图4‑6 图片旁边有正文

  ---------------------------------------
  ![](media/extra-row.png){width="3in"}
  Data must survive
  ---------------------------------------

  : 图4‑7 额外行

  -----------------------------------------------
  ![](media/competing-caption.png){width="3in"}
  图4‑8 内部图注
  -----------------------------------------------

  : 图4‑9 外部图注

  -----------------------------------------------------
  ![](media/subfigure.png){#fig:existing width="3in"}
  -----------------------------------------------------

  : 图4‑10 已标识的子图

# Indented figures

![[]{#_Ref202795466 .anchor}图2‑10 "智慧桥梁"车辆监测站点分布图](media/image224.png){width="4.09375in" height="2.231709317585302in"}

![Figure 2--11 **English** caption.](media/english.png "Picture title"){width="1in"}

- Nested list:

  ![图2-12 列表内的题注](media/list.png){width="3in"}

> ![](media/kept.png)
>
> 这不是编号题注

> ![](media/multiple.png)
>
> 第二段仍在引用块中

[]{#_RefNotPaired .anchor}图2-13 多段引用不能移出

> 说明文字 ![](media/prose.png){width="4in"}

图2-14 图片旁还有正文

> ![Existing caption](media/existing.png){width="4in"}

图2-15 已有题注

> ![](media/left.png) ![](media/right.png)

图2-16 多图引用不能配对

> ![](media/separated.png)

中间隔着普通正文

图2-17 不相邻的题注

> ![](media/numberless.png)

普通段落不是题注

> ![](media/trailing.png)

# Figure numbering and prefixes

![[]{#_RefSingle .anchor}图2 单级编号](media/chinese.png){width="3in"}

![Figure 3 **Single English number**](media/full.png)

![Fig. 4-5 Abbreviated prefix.](media/abbr.png)

![FIG.6 大写且没有空格](media/upper.png)

![fig 7‑8 Undotted prefix.](media/undotted.png)

![Figure. 9 Full prefix with a period.](media/dotted-full.png)

![Fig. 10 Indented picture.](media/indented.png){width="4in"}

![图11 表格排版的单级图题注](media/layout.png){width="3in"}

![fig.12-13 **Abbreviated table-layout caption**](media/layout-abbr.png){width="3in"}

## Invalid numbers and prefixes remain separate

![](media/invalid.png)

Fig. 14- Missing second number.

![](media/numberless.png)

Fig. Caption without a number.

![](media/unknown-prefix.png)

Figment 15 Not an allowed prefix.

![](media/table-prefix.png)

Tbl. 16 Not a figure caption.

![](media/prose.png)

正文提到了图17

# Separate captions

![[[]{#_Toc241697898 .anchor}]{#_Ref181174213 .anchor}图1‑11 区域桥隧网络脆弱节点识别结果](media/image24.png){width="4.25in" height="4.40625in"}

![Figure 2-35 **English caption** with $x^2$.](media/english.png "Picture title"){width="2in"}

![FIGURE3--7 En dash numbering.](media/dash.png)

> ![图 4－2 引用块中的题注](media/quote.png)

## Existing captions stay intact

![Existing caption](media/existing.png)

Figure 1-2 A separate paragraph.

## Multi-image paragraphs are ambiguous

![](media/left.png) ![](media/right.png)

图1-3 不应合并

## Intervening text prevents pairing

![](media/separated.png)

An intervening explanation.

Figure 1-4 A separate paragraph.

## Ordinary prose stays prose

![](media/prose.png)

This paragraph refers to Figure 1-5.

![](media/numberless.png)

Figure caption without a number.

![](media/table-caption.png)

表1-6 普通表题注

![](media/trailing.png)

# Single-cell image tables

![[]{#_Ref181222315 .anchor}基于AAE-SDR神经网络的可靠度主动学习方法流程](media/image947.jpeg){#fig:fuzz-3-27 width="3.3858in" height="3.2043in" original-number="图3‑27"}

![Existing **caption**](media/single-cell-body.png "Picture title"){width="3in" height="2in"}

Figure 6-1 This separate caption must remain outside the existing image caption.

![[]{#_RefUnwrapped .anchor}图6‑2 表格外的图注](media/single-cell-bare.png){width="3in"}

![](media/single-cell-uncaptioned.png){width="2in"}

## Single-cell prose and multiple images remain tables

  ------------------------------------------------------------
  Notes beside ![](media/single-cell-prose.png){width="3in"}
  ------------------------------------------------------------

  ----------------------------------------------------------------------------
  ![](media/single-cell-prose-border.png){width="3in"} Notes must survive \|

  ----------------------------------------------------------------------------

  ------------------------------------------------------------------
  ![](media/single-cell-left.png) ![](media/single-cell-right.png)
  ------------------------------------------------------------------
