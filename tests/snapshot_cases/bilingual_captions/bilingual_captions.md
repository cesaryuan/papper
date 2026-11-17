---
lang: zh-CN
title: 双语图表题注
chapters: true
chapDelim: '-'
---

# 第一章

# 第二章

# 第三章

参见 @fig:architecture、@fig:next、@tbl:comparison 和 @tbl:next。

![MT-MoE ViT 网络架构图](../crossrefs/figure.svg){#fig:architecture width=45% caption-en="Architecture diagram of the *MT-MoE ViT* network with $\\alpha$"}

![后续单语图](../crossrefs/figure.svg){#fig:next width=45%}

| 模型 | 得分 |
| ---- | ---- |
| A | 95 |
| B | 96 |

: 模型性能对比 {#tbl:comparison caption-en="Performance comparison of **different models**" revision_rows="*" cell_margin="0.10cm"}

| 项目 | 值 |
| ---- | -- |
| A | 1 |

: 后续单语表 {#tbl:next}

参见 @fig:panels、@fig:panel-a、@fig:panel-b 和 @fig:panel-c。

:::: {#fig:panels caption-en="Comparison of **models** with $\\alpha$"}

![基线模型](../crossrefs/figure.svg){#fig:panel-a width=49% caption-en="Baseline *model*"}
![改进模型](../crossrefs/figure.svg){#fig:panel-b width=49% caption-en="Proposed model with $\\beta$"}

![独立一行](../crossrefs/figure.svg){#fig:panel-c width=49% caption-en="Panel on a separate row"}

![仅主题注](../crossrefs/figure.svg){#fig:panel-d width=49% caption-en=""}

模型比较。

::::

<div id="fig:child-only">
![仅子图双语](../crossrefs/figure.svg){#fig:child-only-a width=49% caption-en="Translated panel only"}
![单语子图](../crossrefs/figure.svg){#fig:child-only-b width=49%}

只有子图提供英文题注。
</div>

# 第四章

![新章节图](../crossrefs/figure.svg){#fig:chapter width=45% caption-en="Figure in a new chapter"}

<div id="fig:parent-only" caption-en="Translated group only">
![单语面板](../crossrefs/figure.svg){#fig:parent-only-a width=49%}

只有总题注提供英文译文。
</div>

后向引用：@fig:panels、@fig:panel-a、@fig:panel-b、@fig:panel-c 和 @fig:next。

图目录只包含主题注：

\listoffigures

表目录只包含主题注：

\listoftables
