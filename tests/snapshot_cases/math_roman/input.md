The reported transpose formula:

$$
\mathbf{\Sigma }_i^t=(E-\boldsymbol{\mu }_i^t)(E-\boldsymbol{\mu }_i^t)^{{\rm T}}
$$

Inline transpose $x^{\rm T}$ and scoped text $x+{\rm AB{CD}EF}+y$.

Whole-formula declaration $\rm ABC$ and immediate group $\rm{AB}CD$.

Nested declarations ${\rm A{\rm B}C}$ and repeated declarations ${\rm A\rm B}$.

Escaped braces ${\rm A\{B\}C}$ and percent ${\rm A\%B}$.

Other commands remain $\mathrm{T}+\textrm{T}+\rms+\rmfamily+\\rm$.

Comments keep their braces and commands:

$$
{\rm A% } \rm ignored
B}
$$

Code stays literal: `\rm T`.

```tex
{\rm T}
```
