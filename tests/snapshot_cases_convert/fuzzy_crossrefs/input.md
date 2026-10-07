在当前时间节点下，网络在不同失效策略下的脆弱性曲线如图1-14所示。

![图1‑14 不同失效策略下](media/image72.svg){width="5.7680in" height="3.3344in"}

对该网络的静态拓扑鲁棒性进行了初步评估，如表1-3所示。

+---------------+--------------------+------------+-------------------------------+
| 鲁棒性指标                         | 值         | 说明                          |
+===============+====================+============+===============================+
| Laplacian     | 代数连通度         | 0.0106     | 网络易被分割                  |
|               +--------------------+------------+-------------------------------+
|               | 有效阻抗           | 435282.033 | 连通性中等                    |
+---------------+--------------------+------------+-------------------------------+

: 表1‑3 基于复杂网络理论的鲁棒性度量

As shown in FIG. 1‑14, the curves agree. As show in Tbl. 1-3, connectivity is moderate.

See Figure 1-14 and refer to Table 1‑3. According to Table 1-3, this is stable.

As shown in Figure 1-14 and Table 1-3, both results are available. 如图1-14、图2及表1-3所示。

See Fig. 99 and Table 1-3; the first number has no target. Fig. 1-14 and Table 1-3 are ordinary mentions.

普通提及图1-14和表1-3不会改写。如图1-140所示以及如表1-30所示均找不到题注。

See Fig. 1-14-2 and see Fig. 1.14; these are different numbers.

## Mixed bookmark and text references

![[]{#_RefMixed .anchor}Fig. 2 **Anchored caption**](media/mixed.png)

[Fig. 2](#_RefMixed) and see Fig. 2. 如图2所示。

See [Fig. 2](#_RefMixed) and Table 1-3. 如[Fig. 2](#_RefMixed)和图1-14所示。

As shown in [@fig:_RefMixed] and Tbl. 1-3, citations also carry the cue.

## References across formatting and Word runs

**说明如图**1-14所示，保留其余**加粗**内容。如图1**‑14**所示。

The result is shown in **Fig.** *1-14* and is described in ordinary text.

> Quoted prose: see Tbl. 1-3.

- A list refers to Figure 1-14 as shown in Fig. 2.

## Caption text and protected syntax

![Fig. 3 Caption says see Fig. 2 and 如图1-14所示](media/caption.png)

[see Fig. 2](https://example.com/), `see Fig. 2`, $\text{see Fig. 2}$, and [@fig:_RefMixed].

```text
如图1-14所示。See Tbl. 1-3.
```

### See Fig. 2 in this heading

## Ambiguous captions

![图4 第一个题注](media/duplicate-a.png)

![Figure 4 Duplicate number](media/duplicate-b.png){#fig:duplicate-second}

如图4所示。See Figure 4.

| Name | Value |
|------|-------|
| A    | 1     |

: Table 4 Tables have their own numbering namespace.

See Table 4.

## ID collisions and arbitrary existing IDs

[]{#fig:fuzz-5}

![Fig. 5 Generated identifier must not collide](media/collision.png)

See Fig. 5.

![Fig. 6 Existing compatible ID](media/existing.png){#fig:authored}

See Fig. 6.

![Fig. 7 Existing unrelated ID](media/unrelated.png){#custom-picture}

See Fig. 7.

## Numbered targets inside containers

> Nested definition:
>
> ![图8 引用块内的图](media/nested.png)

见图8。See FIG. 8.

| Context | Explanation |
|---------|-------------|
| Prose   | 如图1-14所示 |

: Tbl. 9 Caption refers to Figure 2 as shown in Fig. 2.

See Tbl. 9.

## Number parsing and nested targets

![Fig. 10- Invalid caption number](media/invalid.png)

See Fig. 10.

![Fig. 12‑3 Spaced Unicode numbering](media/spaces.png)

如图 12 ‑ 3 所示。See Fig. 12 - 3.

| Content |
|---------|
| ![Fig. 13 Target inside a table cell](media/cell.png) |

See Fig. 13.

![Fig. 14 Duplicate identifier, first](media/id-a.png){#fig:duplicate}

![Fig. 15 Duplicate identifier, second](media/id-b.png){#fig:duplicate}

See Fig. 14 or Fig. 15.

| Name | Value |
|------|-------|
| Existing | 1 |

: Tbl. 16 Existing compatible table ID {#tbl:authored}

As shown in Tbl. 16, the table already has an ID.

| Name | Value |
|------|-------|
| Anchored | 2 |

: []{#_RefTableMixed .anchor}Tbl. 17 Mixed anchored and typed table references

[Tbl. 17](#_RefTableMixed) and see Tbl. 17.


## Equation references

如式 4-43 所示。参见公式4‑43；as in Eq. (4-43), the result agrees.

$x=y$ (4-43)

$$a=b$$ (4‑44)

根据式（4-44）和式4-43，可以得到结果。See Equation 4-44 and Eq. 4-43.

## Anchored equations mixed with text references

$u=v$ []{#_RefEquation .anchor}(4-45)

[Equation 4-45](#_RefEquation), 如式4-45所示。See [Equation 4-45](#_RefEquation) and Eq. 4-43.

$h=k$ []{#_RefUnicodeEquation .anchor}（4‑47）

参见式4-47和公式4-43；[Equation 4-47](#_RefUnicodeEquation)。

$$p=q$$ (4-46) {#eq:authored}

As shown in Equation (4-46), an existing ID is reused.

## Single numbers, formatting and Unicode parentheses

$r=s$ （7）

如式7所示，见公式（7）。Using Eq. 7, the estimate is derived.

$m=n$ **(8)**

**如式**8所示，保留**其他加粗**内容。Refer to **Eq.** *8*.

## Equations in containers

> Numbered formula:
>
> $c=d$ (9)

见式9。See Equation 9.

| Content |
|---------|
| $e=f$ (10) |

参见式10。

## Ambiguity and collisions

$g=h$ (11)

$$i=j$$ (11)

如式11所示。See Eq. 11.

[]{#eq:fuzz-12}

$k=l$ (12)

See Eq. 12.

## Preserve nondefinitions and protected content

普通正文中的$x=y$ (13) 不应变成公式定义。

$x$ and $y$ (14)

$z$ (15) descriptive label

$w$ (16-)

$v$ (17.2)

See Eq. 13 or Eq. 14. 如式15所示。As in Equation 16, nothing is defined.

普通提及式4-43和Equation 4-44不改写。如式4-430所示不匹配短编号。

`如式4-43所示` and $\text{如式4-43所示}$ remain unchanged.

[see Eq. 4-43](https://example.com/) remains a link.

## Bare existing ID without an original number

$$a+b=c$$ {#eq:unknown-number}

See Eq. 18.

## Recognize spaces inside equation numbers

$j=k$ ( 19 )

See Eq. 19.
