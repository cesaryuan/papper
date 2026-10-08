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

Inline hats $\hat{\mathbf{C}}+\hat{\mathcal{C}}$ and nested fonts $\hat{\mathbf{\mathcal{C}}}$.

Display math, nested hats, and scripts inside the font wrapper:

$$
\hat{\mathbf{C}_{i}} + \hat{\mathcal{\hat{\mathbf{D}}}}
$$

Spaced command and group syntax $\hat { \mathbf { C } }$.

Already corrected $\mathbf{\hat{C}}+\mathcal{\hat{D}}$ and plain $\hat{C}$.

Extra terms and scripts outside the wrapper keep their scope: $\hat{\mathbf{x}+y}+\hat{\mathbf{x}_i}+\hat{\mathbf{x}}_i$.

Custom commands and similarly named controls stay unchanged: $\hat{\myfont{C}}+\hatname{\mathbf{C}}$.

Escaped braces and percent $\hat{\mathbf{C\{D\}\%}}$.

Comments inside the operand keep their braces and commands:

$$
\hat{\mathbf{C% } \hat{\mathcal{ignored}}
D}}
$$

Comments outside the font wrapper keep the operand intact:

$$
\hat{% \hat{\mathbf{ignored}}
\mathbf{C}}
$$

Literal control symbols $\\hat{\mathbf{C}}$ and unmatched groups $\hat{\mathbf{C}$.

Code stays literal: `\hat{\mathbf{C}}`.

```tex
\hat{\mathbf{C}}
```
