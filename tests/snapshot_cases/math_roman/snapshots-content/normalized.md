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
