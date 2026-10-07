## Consecutive three-column equations with a header row

$a_i=\mathop{\arg \max }\limits_{A\left(s_i\right)}\left(Q\left(s_i,a\right)+U\left(s_i,a\right)\right)$ (3-30)

$U\left(s_i,a_i\right)=c_{puct}P\left(s_i,a_i\right)\frac{\sqrt{\sum\nolimits_bN\left(s_i,b\right)}}{1+N\left(s_i,a_i\right)}$ (3-31)

式中，$a_i$为所选的节点；$U\left(s_i,a_i\right)$为节点的收益信号。

当一次模拟完成时，每个边中的统计信息将按下列公式更新：

$N\left(s_i,a_i\right)=N\left(s_i,a_i\right)+1$ (3-32)

$W\left(s_i,a_i\right)=W\left(s_i,a_i\right)+v$ (3-33)

$v=\left\{\begin{matrix}\text{score}(c=a_i\left|t\right.),\text{ 未结束}\\1,\text{ 获胜}             \\-1,\text{ 失败                       }\end{matrix}\right.$ (3-34)

$S\left(s_i,a_i\right)=\frac{W\left(s_i,a_i\right)}{N\left(s_i,a_i\right)}$ (3-35)

## Two-column equations without a header, with bookmarks and descriptive labels

$E=mc^2$ []{#_RefEnergy .anchor}(1)

$F=ma$ []{#_RefForce .anchor}force balance

## A single-row layout remains supported

$u=v$ (9-1)

## A mixed table stays intact even after valid equation rows

  -------------------------------------
            $$x=1$$           (1-1)
  --------- ----------------- ---------
            $$y=2$$           (1-2)

  Data      measured value    3
  -------------------------------------

## Captioned equation tables stay intact

  ------------------------
  $$x=1$$        (1-1)
  -------------- ---------
  $$y=2$$        (1-2)

  ------------------------

  : Authored table caption

## Merged cells stay intact

+---------+-----------------+---------------+
|         | $$x=1$$         | (1-1)         |
+=========+=================+===============+
|         | $$y=2$$                         |
+---------+---------------------------------+

## Multiple formulas in one cell stay intact

  -------------------------------
  $$x=1$$               (1-1)
  --------------------- ---------
  $$y=2$$ $$z=3$$       (1-2)

  -------------------------------
