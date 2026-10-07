在当前时间节点下，网络在不同失效策略下的脆弱性曲线如[@fig:fuzz-1-14]所示。

![不同失效策略下](media/image72.svg){#fig:fuzz-1-14 width="5.7680in" height="3.3344in"}

<!-- original-number: 图1‑14 -->

对该网络的静态拓扑鲁棒性进行了初步评估，如[@tbl:fuzz-1-3]所示。

+----------------------------------+------------+--------------------------+
| 鲁棒性指标                       | 值         | 说明                     |
+================+=================+============+==========================+
| Laplacian      | 代数连通度      | 0.0106     | 网络易被分割             |
|                +-----------------+------------+--------------------------+
|                | 有效阻抗        | 435282.033 | 连通性中等               |
+----------------+-----------------+------------+--------------------------+

: 基于复杂网络理论的鲁棒性度量 {#tbl:fuzz-1-3}

<!-- original-number: 表1‑3 -->

As shown in [@fig:fuzz-1-14], the curves agree. As show in [@tbl:fuzz-1-3], connectivity is moderate.

See [@fig:fuzz-1-14] and refer to [@tbl:fuzz-1-3]. According to [@tbl:fuzz-1-3], this is stable.

As shown in [@fig:fuzz-1-14] and [@tbl:fuzz-1-3], both results are available. 如[@fig:fuzz-1-14]、[@fig:_RefMixed]及[@tbl:fuzz-1-3]所示。

See Fig. 99 and [@tbl:fuzz-1-3]; the first number has no target. Fig. 1-14 and Table 1-3 are ordinary mentions.

普通提及图1-14和表1-3不会改写。如图1-140所示以及如表1-30所示均找不到题注。

See Fig. 1-14-2 and see Fig. 1.14; these are different numbers.

## Mixed bookmark and text references

![**Anchored caption**](media/mixed.png){#fig:_RefMixed}

<!-- original-number: Fig. 2 -->

[@fig:_RefMixed] and see [@fig:_RefMixed]. 如[@fig:_RefMixed]所示。

See [@fig:_RefMixed] and [@tbl:fuzz-1-3]. 如[@fig:_RefMixed]和[@fig:fuzz-1-14]所示。

As shown in [@fig:_RefMixed] and [@tbl:fuzz-1-3], citations also carry the cue.

## References across formatting and Word runs

**说明如**[@fig:fuzz-1-14]所示，保留其余**加粗**内容。如[@fig:fuzz-1-14]所示。

The result is shown in [@fig:fuzz-1-14] and is described in ordinary text.

> Quoted prose: see [@tbl:fuzz-1-3].

- A list refers to Figure 1-14 as shown in [@fig:_RefMixed].

## Caption text and protected syntax

![Caption says see Fig. 2 and 如图1-14所示](media/caption.png){#fig:fuzz-3}

<!-- original-number: Fig. 3 -->

[see Fig. 2](https://example.com/), `see Fig. 2`, $\text{see Fig. 2}$, and [@fig:_RefMixed].

``` text
如图1-14所示。See Tbl. 1-3.
```

### See Fig. 2 in this heading

## Ambiguous captions

![图4 第一个题注](media/duplicate-a.png)

![Figure 4 Duplicate number](media/duplicate-b.png){#fig:duplicate-second}

如图4所示。See Figure 4.

  Name   Value
  ------ -------
  A      1

  : Tables have their own numbering namespace. {#tbl:fuzz-4}

<!-- original-number: Table 4 -->

See [@tbl:fuzz-4].

## ID collisions and arbitrary existing IDs

[]{#fig:fuzz-5}

![Generated identifier must not collide](media/collision.png){#fig:fuzz-5-2}

<!-- original-number: Fig. 5 -->

See [@fig:fuzz-5-2].

![Existing compatible ID](media/existing.png){#fig:authored}

<!-- original-number: Fig. 6 -->

See [@fig:authored].

![Fig. 7 Existing unrelated ID](media/unrelated.png){#custom-picture}

See Fig. 7.

## Numbered targets inside containers

> Nested definition:
>
> ![引用块内的图](media/nested.png){#fig:fuzz-8}
>
> <!-- original-number: 图8 -->

见[@fig:fuzz-8]。See [@fig:fuzz-8].

  Context   Explanation
  --------- ------------------------
  Prose     如[@fig:fuzz-1-14]所示

  : Caption refers to Figure 2 as shown in Fig. 2. {#tbl:fuzz-9}

<!-- original-number: Tbl. 9 -->

See [@tbl:fuzz-9].

## Number parsing and nested targets

![Fig. 10- Invalid caption number](media/invalid.png)

See Fig. 10.

![Spaced Unicode numbering](media/spaces.png){#fig:fuzz-12-3}

<!-- original-number: Fig. 12‑3 -->

如[@fig:fuzz-12-3] 所示。See [@fig:fuzz-12-3].

+-------------------------------------------------------------+
| Content                                                     |
+=============================================================+
| ![Target inside a table cell](media/cell.png){#fig:fuzz-13} |
| <!-- original-number: Fig. 13 -->                           |
+-------------------------------------------------------------+

See [@fig:fuzz-13].

![Fig. 14 Duplicate identifier, first](media/id-a.png){#fig:duplicate}

![Fig. 15 Duplicate identifier, second](media/id-b.png){#fig:duplicate}

See Fig. 14 or Fig. 15.

  Name       Value
  ---------- -------
  Existing   1

  : Existing compatible table ID {#tbl:authored}

<!-- original-number: Tbl. 16 -->

As shown in [@tbl:authored], the table already has an ID.

  Name       Value
  ---------- -------
  Anchored   2

  : Mixed anchored and typed table references {#tbl:_RefTableMixed}

<!-- original-number: Tbl. 17 -->

[@tbl:_RefTableMixed] and see [@tbl:_RefTableMixed].

## Equation references

如[@eq:fuzz-4-43] 所示。参见[@eq:fuzz-4-43]；as in [@eq:fuzz-4-43], the result agrees.

$$x=y$$ {#eq:fuzz-4-43}

<!-- original-number: (4-43) -->

$$a=b$$ {#eq:fuzz-4-44}

<!-- original-number: (4‑44) -->

根据[@eq:fuzz-4-44]和[@eq:fuzz-4-43]，可以得到结果。See [@eq:fuzz-4-44] and [@eq:fuzz-4-43].

## Anchored equations mixed with text references

$$u=v$$ {#eq:_RefEquation}

<!-- original-number: (4-45) -->

[@eq:_RefEquation], 如[@eq:_RefEquation]所示。See [@eq:_RefEquation] and [@eq:fuzz-4-43].

$$h=k$$ {#eq:_RefUnicodeEquation}

<!-- original-number: （4‑47） -->

参见[@eq:_RefUnicodeEquation]和[@eq:fuzz-4-43]；[@eq:_RefUnicodeEquation]。

$$p=q$$ {#eq:authored}

<!-- original-number: (4-46) -->

As shown in [@eq:authored], an existing ID is reused.

## Single numbers, formatting and Unicode parentheses

$$r=s$$ {#eq:fuzz-7}

<!-- original-number: （7） -->

如[@eq:fuzz-7]所示，见[@eq:fuzz-7]。Using [@eq:fuzz-7], the estimate is derived.

$$m=n$$ {#eq:fuzz-8}

<!-- original-number: (8) -->

**如**[@eq:fuzz-8]所示，保留**其他加粗**内容。Refer to [@eq:fuzz-8].

## Equations in containers

> Numbered formula:
>
> $$c=d$$ {#eq:fuzz-9}
>
> <!-- original-number: (9) -->

见[@eq:fuzz-9]。See [@eq:fuzz-9].

+--------------------------------+
| Content                        |
+================================+
| $$e=f$$ {#eq:fuzz-10}          |
| <!-- original-number: (10) --> |
+--------------------------------+

参见[@eq:fuzz-10]。

## Ambiguity and collisions

$g=h$ (11)

$$i=j$$ (11)

如式11所示。See Eq. 11.

[]{#eq:fuzz-12}

$$k=l$$ {#eq:fuzz-12-2}

<!-- original-number: (12) -->

See [@eq:fuzz-12-2].

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

## Preserve spaces inside original equation numbers

$$j=k$$ {#eq:fuzz-19}

<!-- original-number: ( 19 ) -->

See [@eq:fuzz-19].
