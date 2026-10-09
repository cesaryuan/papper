# Comprehensive DOCX import

下面是个公式

$$\sqrt{b^2-4ac}$$

这是一个行内公式：$\sqrt{a^2+b^2}$

这是一个右编号公式：

$$\mathop{\lim }\limits_{x\to \infty }$$

$$\sqrt{b^2-4ac}$$ {#eq:_Ref241620691}

对公式的引用 [@eq:_Ref241620691]

![图表 2 打我的](media/image4.png){#fig:_Ref241621754 width="1.3854in" height="0.46875in"}

对题注的引用[@fig:_Ref241621754]

![打我的d打我的](media/image5.png){width="1.8438in" height="0.59375in"}

对题注的引用[Figure 1](#_Ref241623370)

参考文献：

如\[1\]所见

\[1\] Long F, Bao Y, Hou R. Region-aware neural radiance fields for three-dimensional building reconstruction and facade detail preservation. Dev Built Environ 2026;27:101019.

# Word content

Formatting: **bold**, *italic*, [underlined]{.underline}, ~~deleted~~, H~2~O and x^2^; 中文内容与 English text.

External link: [Papper repository](https://github.com/cesaryuan/papper)

- First bullet

- Second bullet

1.  First numbered item

2.  Second numbered item

> Quoted material.

first line\
second line

Literal syntax: \*asterisk\*, \[brackets\], and \$dollar\$.

Inline code: `result = a + b`

Code block:

    result = a + b
    print(result)

## Native Word equation

OMML remains a formula: $a + b = c$

# Equation layouts and references

Two-column equation with a Word bookmark:

$$\sqrt{b^2-4ac}$$ {#eq:_RefConvertTwoCell}

Three-column equation without a bookmark:

$\sqrt{b^2-4ac}$ (7)

Descriptive label is preserved:

$$\sqrt{b^2-4ac}$$ {#eq:_RefConvertTextLabel} energy balance

A nonempty leading cell is ordinary table content:

+----------------------+-----------------------+----------------------+
| physical quantity    | $$\sqrt{b^2-4ac}$$    | \(8\)                |
+======================+=======================+======================+

A multirow table is not an equation layout:

+-----------------------------------+-----------------------------------+
| $$\sqrt{b^2-4ac}$$                | \(9\)                             |
+===================================+===================================+
| ordinary data row                 | must remain a table               |
+-----------------------------------+-----------------------------------+

References: [@eq:_RefConvertTwoCell] and [@eq:_RefConvertTextLabel]; unknown target stays a link: [unknown](#_RefConvertUnknown)

## Ordinary table

+-------------------------------------------+-----------------------------------+
| Item[]{#_RefConvertOrdinaryTable .anchor} | Value                             |
+===========================================+===================================+
| Alpha                                     | 10                                |
+-------------------------------------------+-----------------------------------+
| 中文                                      | 20                                |
+-------------------------------------------+-----------------------------------+

Ordinary table bookmark stays a link: [Table 1](#_RefConvertOrdinaryTable)

# Figures and references

![Figure 3. Caption bookmark with 中文.](media/image4.png){#fig:_RefConvertCaption width="1in" height="0.33835in"}

![Figure 4. Preceding bookmark.](media/image4.png){#fig:_RefConvertPreceding width="1in" height="0.33835in"}

An ordinary picture has no caption:

![](media/image4.png){width="1in" height="0.33835in"}

Figure references: [@fig:_RefConvertCaption] and [@fig:_RefConvertPreceding]

## Section bookmark

A section link remains a link: [Section bookmark](#section-bookmark)

# Preview fallbacks

Undecodable MathType retains its preview:

![](media/undecodable.wmf)

A preview also used as an ordinary image remains an image in both places:

![](media/shared-preview.wmf)

![](media/shared-preview.wmf)

An unrelated OLE object is not a MathType formula:

![](media/non-equation.wmf)

# Footnote

Text with a formula footnote[^1]

# 仅有WMF的公式

在本节中，$\hat{y}_i$表示预测得到的节点温度$T_i$、应力$\sigma _i$或位移$u_i$，$f_{de}$仍然为3层MLP。为了通过反向传播来训练GNN，均方误差（MSE）被用作损失函数：

$$Loss_{\text{MSE}}=\frac{1}{n}\cdot \sum\limits_{i=1}^n(y_i-\hat{y}_i)^2$$

[^1]: Footnote formula: $\sqrt{a^2+b^2}$
