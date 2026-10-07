在当前时间节点下，网络在不同失效策略下的脆弱性曲线如[@fig:fuzz-1-14]所示。

![图1‑14 不同失效策略下](media/image72.svg){#fig:fuzz-1-14 width="5.7680in" height="3.3344in"}

对该网络的静态拓扑鲁棒性进行了初步评估，如[@tbl:fuzz-1-3]所示。

+----------------------------------+------------+--------------------------+
| 鲁棒性指标                       | 值         | 说明                     |
+================+=================+============+==========================+
| Laplacian      | 代数连通度      | 0.0106     | 网络易被分割             |
|                +-----------------+------------+--------------------------+
|                | 有效阻抗        | 435282.033 | 连通性中等               |
+----------------+-----------------+------------+--------------------------+

: 表1‑3 基于复杂网络理论的鲁棒性度量 {#tbl:fuzz-1-3}

As shown in [@fig:fuzz-1-14], the curves agree. As show in [@tbl:fuzz-1-3], connectivity is moderate.

See [@fig:fuzz-1-14] and refer to [@tbl:fuzz-1-3]. According to [@tbl:fuzz-1-3], this is stable.

As shown in [@fig:fuzz-1-14] and [@tbl:fuzz-1-3], both results are available. 如[@fig:fuzz-1-14]、[@fig:_RefMixed]及[@tbl:fuzz-1-3]所示。

See Fig. 99 and [@tbl:fuzz-1-3]; the first number has no target. Fig. 1-14 and Table 1-3 are ordinary mentions.

普通提及图1-14和表1-3不会改写。如图1-140所示以及如表1-30所示均找不到题注。

See Fig. 1-14-2 and see Fig. 1.14; these are different numbers.

## Mixed bookmark and text references

![Fig. 2 **Anchored caption**](media/mixed.png){#fig:_RefMixed}

[@fig:_RefMixed] and see [@fig:_RefMixed]. 如[@fig:_RefMixed]所示。

See [@fig:_RefMixed] and [@tbl:fuzz-1-3]. 如[@fig:_RefMixed]和[@fig:fuzz-1-14]所示。

As shown in [@fig:_RefMixed] and [@tbl:fuzz-1-3], citations also carry the cue.

## References across formatting and Word runs

**说明如**[@fig:fuzz-1-14]所示，保留其余**加粗**内容。如[@fig:fuzz-1-14]所示。

The result is shown in [@fig:fuzz-1-14] and is described in ordinary text.

> Quoted prose: see [@tbl:fuzz-1-3].

- A list refers to Figure 1-14 as shown in [@fig:_RefMixed].

## Caption text and protected syntax

![Fig. 3 Caption says see Fig. 2 and 如图1-14所示](media/caption.png){#fig:fuzz-3}

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

  : Table 4 Tables have their own numbering namespace. {#tbl:fuzz-4}

See [@tbl:fuzz-4].

## ID collisions and arbitrary existing IDs

[]{#fig:fuzz-5}

![Fig. 5 Generated identifier must not collide](media/collision.png){#fig:fuzz-5-2}

See [@fig:fuzz-5-2].

![Fig. 6 Existing compatible ID](media/existing.png){#fig:authored}

See [@fig:authored].

![Fig. 7 Existing unrelated ID](media/unrelated.png){#custom-picture}

See Fig. 7.

## Numbered targets inside containers

> Nested definition:
>
> ![图8 引用块内的图](media/nested.png){#fig:fuzz-8}

见[@fig:fuzz-8]。See [@fig:fuzz-8].

  Context   Explanation
  --------- ------------------------
  Prose     如[@fig:fuzz-1-14]所示

  : Tbl. 9 Caption refers to Figure 2 as shown in Fig. 2. {#tbl:fuzz-9}

See [@tbl:fuzz-9].

## Number parsing and nested targets

![Fig. 10- Invalid caption number](media/invalid.png)

See Fig. 10.

![Fig. 12‑3 Spaced Unicode numbering](media/spaces.png){#fig:fuzz-12-3}

如[@fig:fuzz-12-3] 所示。See [@fig:fuzz-12-3].

  Content
  ---------------------------------------------------------------------
  ![Fig. 13 Target inside a table cell](media/cell.png){#fig:fuzz-13}

See [@fig:fuzz-13].

![Fig. 14 Duplicate identifier, first](media/id-a.png){#fig:duplicate}

![Fig. 15 Duplicate identifier, second](media/id-b.png){#fig:duplicate}

See Fig. 14 or Fig. 15.

  Name       Value
  ---------- -------
  Existing   1

  : Tbl. 16 Existing compatible table ID {#tbl:authored}

As shown in [@tbl:authored], the table already has an ID.

  Name       Value
  ---------- -------
  Anchored   2

  : Tbl. 17 Mixed anchored and typed table references {#tbl:_RefTableMixed}

[@tbl:_RefTableMixed] and see [@tbl:_RefTableMixed].
