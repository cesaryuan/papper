---
title: Equation terms
---

# Special thesis layout {#sec:terms}

$$
y = kx + \sigma
$$ {#eq:terms}

::: {#symbol-explanations .equation-terms}
$M_{\mathrm{soft}}$
: 软掩码连续权重矩阵，$M_{\mathrm{soft}}\in[0,1]^{1\times H_1\times W_1}$；

$\mathrm{GaussianBlur}(\cdot)$
: 二维高斯滤波算子；

$k$
: **Kernel size**, as used in @eq:terms and @sec:terms；

$\sigma$
: Standard deviation, controlling the smoothing range. This deliberately longer explanation should wrap in its own column without shifting the symbols or the separators.

    A second paragraph retains *emphasis* and a [link](https://example.org).

    - A description can contain a list.
    - Its mathematical content $x^{\rm T}$ stays editable.
:::

Ordinary prose: 式中，$k$ 为核大小，$\sigma$ 为标准差。

Ordinary term
: An unmarked definition list stays an ordinary definition list.

::: {.equation-terms}
$z$
: A single-entry block also works without a preceding equation.
:::

| Item | Value |
|------|-------|
| Sample | 1 |

: Ordinary numbered table. {#tbl:ordinary}

See @tbl:ordinary.
