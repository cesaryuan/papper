The reported transpose formula:

$$
\mathbf{\Sigma }_i^t=(E-\boldsymbol{\mu }_i^t)(E-\boldsymbol{\mu }_i^t)^{{\textrm{T}}}
$$

Inline transpose $x^{\textrm{T}}$ and scoped text $x+{\textrm{AB{CD}EF}}+y$.

Whole-formula declaration $\textrm{ABC}$ and immediate group $\textrm{{AB}CD}$.

Nested declarations ${\textrm{A{\textrm{B}}C}}$ and repeated declarations ${\textrm{A\textrm{B}}}$.

Escaped braces ${\textrm{A\{B\}C}}$ and percent ${\textrm{A\%B}}$.

Other commands remain $\mathrm{T}+\textrm{T}+\rms+\rmfamily+\\rm$.

Comments keep their braces and commands:

$$
{\textrm{A% } \rm ignored
B}}
$$

Code stays literal: `\rm T`.

``` tex
{\rm T}
```

Inline hats $\mathbf{\hat{C}}+\mathcal{\hat{C}}$ and nested fonts $\mathbf{\mathcal{\hat{C}}}$.

Display math, nested hats, and scripts inside the font wrapper:

$$
\hat{\mathbf{C}_{i}} + \mathcal{\mathbf{\hat{\hat{D}}}}
$$

Spaced command and group syntax $\mathbf{\hat{ C }}$.

Already corrected $\mathbf{\hat{C}}+\mathcal{\hat{D}}$ and plain $\hat{C}$.

Extra terms and scripts outside the wrapper keep their scope: $\hat{\mathbf{x}+y}+\hat{\mathbf{x}_i}+\mathbf{\hat{x}}_i$.

Custom commands and similarly named controls stay unchanged: $\hat{\myfont{C}}+\hatname{\mathbf{C}}$.

Escaped braces and percent $\mathbf{\hat{C\{D\}\%}}$.

Comments inside the operand keep their braces and commands:

$$
\mathbf{\hat{C% } \hat{\mathcal{ignored}}
D}}
$$

Comments outside the font wrapper keep the operand intact:

$$
\hat{% \hat{\mathbf{ignored}}
\mathbf{C}}
$$

Literal control symbols $\\hat{\mathbf{C}}$ and unmatched groups $\hat{\mathbf{C}$.

Code stays literal: `\hat{\mathbf{C}}`.

``` tex
\hat{\mathbf{C}}
```
