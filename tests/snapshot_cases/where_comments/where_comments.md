# Equation explanations separated by comments

$$
L=\alpha L_{\mathrm{TSDF}}+\beta L_{\mathrm{texture}}
$$ {#eq:total-loss}

<!-- Revision for R2-8: explain the equal loss weights and point to the sensitivity analysis in the experiments. -->
where $\alpha$ and $\beta$ denote the loss-weight coefficients for the TSDF sign-consistency loss and the texture reconstruction loss, respectively.

$$
x = 1
$$

<!-- First revision note. -->

<!--
A multiline revision note.
The explanation still follows the equation.
--> <!-- Another comment in the same block. -->

where $x$ is the input value after several comments.

> $$
> y = 2
> $$
>
> <!-- A revision note inside a block quote. -->
> where $y$ is the nested input value.

$$
z = 3
$$

<!-- This comment does not make intervening prose disappear. -->

A visible paragraph separates the equation from its later discussion.

<!-- Another revision note. -->
where this paragraph remains ordinary prose because it follows visible text.

$$
w = 4
$$

<!-- This raw block also contains visible HTML and must remain a separator. -->
<hr>

where this paragraph remains ordinary prose because visible HTML intervenes.

| Input | Value |
|-------|-------|
| $v$   | 5     |

<!-- A table revision note. -->
The first visible paragraph after the table keeps its post-table style.
