# DOCXIDE 后端与已知差异

DOCX 视觉回归测试默认运行 Word 和 DOCXIDE，分别维护环境记录和逐页像素基线。
安装、运行、选择单个后端及更新基线的方法统一维护在
[视觉测试说明](../tests/visual/README.md)。

## 当前版本与验证范围

2026-11-15 将内置预览库和 visual CLI 从 `520230ce4c14` 同步更新到上游
main 的 **0.21.3 / `3936b809c2cdbf50b81cf283a02b79ff7ce784d5`**。
`Cargo.lock`、预览 renderer 身份、安装说明和 DOCXIDE 环境记录已同步。
本轮重新检查了已关闭的 [issue #164](https://github.com/sverrejb/docxide-pdf/issues/164)
中的全部原始输入，并运行完整视觉回归。

- DOCXIDE 基线采集：**26 passed**；Word 基线未更新。
- 关闭基线更新后的完整 visual：**72 passed, 496 deselected**。
- 内置预览 HTTP 回归：**6 passed**。
- `cargo build --locked -p papper-cli`、`cargo fmt --all -- --check` 和
  `git diff --check` 均通过。
- 25 个案例的旧 DOCXIDE、新 DOCXIDE 和 Word 均为 **70 页**，逐案例页数相同。
  相较更新前保存的 `520230c` 工作区基线，**23 个案例的 56 页**发生像素变化，
  已查看全部变化页面的新旧及 Word 对照。主要可见变化包括行号字体、公式编号位置、
  目录页码和脚注分隔线；没有观察到新的大面积内容缺失或重叠。
  两种后端分别维护基线，测试通过不表示它们与 Word 逐页等价。

### Issue #164 原始输入复核

7 份原始 DOCX 未重新生成、未通过 Word 保存，在 Word 和新 DOCXIDE 中
各独立导出两次，共 **28 次导出**。输入哈希与公开报告一致，重复页面像素稳定。

| 问题 | 当前实测结果 |
| --- | --- |
| 公式右侧编号偏低 | 原始居中输入的编号基线为 Word 91.820 pt / DOCXIDE 92.081 pt，差约 **0.26 pt**，此前约 11.96 pt 的偏低消失。普通 PNG 对照同样恢复，baseline 对照差约 0.08 pt |
| 行号遵循“行号”样式字体 | 原始输入指定 Courier New 16 pt，DOCXIDE 现使用 Courier New 16.0 pt，Word PDF 测得 Courier New 15.96 pt；不再使用此前的 Times New Roman 12 pt |
| 高表格后标题的目录页码 | 原始主输入及 `pageBreakBefore` 对照均显示 **2**，与标题实际落页、PAGE 页脚和目录跳转一致 |

另外对原始 `rust` / `both` MathType 输入分别独立导出两次。
`rust` 两条公式右侧编号相对 Word 的基线差降至约 **0.33 / 0.32 pt**，
公式主体仍完整，原先明显偏低的问题恢复。`both` 的编号位置改善，
但公式主体仍只有零散片段，不能据此认为 `both` 的公式内容渲染已修复。

### 完整样例仍有子图目录错误

`docx_directives` 的 **70 个目录条目中，64 个页码与 Word 一致**。
此前 Results、Caption sources、主图和表格的错误页码均恢复；
但表格内的两个子图题注仍有问题：

- `(a) 中文子图`、`(b) English panel` 在 Word 和 DOCXIDE 中均实际位于
  **物理第 7 页，即正文第 5 页**。
- Word 目录显示 **5**，新 DOCXIDE 仍显示 **4**。两个题注在三处目录中重复，
  共形成 **6 个错误条目**。
- Word 的这 6 个目录链接均跳到物理第 7 页；DOCXIDE 均跳到物理第 6 页。
  因此残留问题同时影响打印页码和跳转目标，不能将目录问题整体标记为已修复。

完整英文模板仍有与 Word 不同的换行和内容落页，稿件行号的计数也仍有差异；
这些与本轮已修复的行号字体不是同一个问题。

本轮材料位于忽略目录 `tests/visual/results/docxide-3936b80-20261115/`：
`docx-update.log`、`visual-verify.log`、`preview-verify.log` 保存正式测试结果；
`measurements.json`、`exports/`、`fix-summary.json` 保存原始输入验证、测量及
完整目录的页码和跳转目标；`detail-equation.png`、`detail-line-font.png`、
`detail-toc-repro.png` 为三项原始复现的新旧/Word 对照；
`detail-remaining-panel-directory.png` 和 `detail-remaining-panel-captions.png`
记录残留子图目录与实际题注；`original-math.json` 及
`detail-original-math-{rust,both}.png` 保存原始 MathType 对照。
`review.json`、`comparisons/` 和 `overview-01.png` 至 `overview-07.png`
保存全部变化页面；`previous-baselines/` 与 `previous-docxide-pdf.exe`
保留更新前的工作区版本。本轮没有发布新的上游 issue 或评论。

## 2026-11-14 中文重叠修复与验证范围

2026-11-14 在上一轮更新后，再将内置预览库和 visual CLI 同步锁定到
**0.21.3 / `520230ce4c14464aaf4995eccbbe9980f07c6fd2`**。
本次只更新一个上游提交，修复
[issue #163](https://github.com/sverrejb/docxide-pdf/issues/163)
报告的带制表符中文参考文献重叠；`Cargo.lock`、预览 renderer 身份、安装说明及
DOCXIDE 环境记录同步更新。

- DOCXIDE 基线采集：**26 passed**。
- 关闭基线更新后的完整 visual：**72 passed, 496 deselected**，覆盖 Word 26 项、
  DOCXIDE 26 项、HTML 18 项和原生目录语义 2 项。
- 内置预览 HTTP 回归：**6 passed**。
- 25 个稿件案例的旧 DOCXIDE、新 DOCXIDE、Word 均为 **70 页**，逐案例页数相同。
  相较本次更新前保存的 `d1f22ac` 工作区基线，只有 **2 个案例的 3 页**发生变化：
  `chinese_crossrefs` 第 1 页、`template_cn` 第 8–9 页，均涉及参考文献。
  已查看全部变化页面及参考文献放大对照，未发现新的重叠。Word 基线未更新。
- `cargo build --locked -p papper-cli`、`cargo fmt --all -- --check`、
  `git diff --check` 均通过。

### 中文重叠原始最小输入复核

使用 #163 公开报告的同一批 5 份 DOCX，没有重新生成或经 Word 保存。
每份输入在 Word、新 DOCXIDE 中各独立导出两次，共 **20 次导出**；
页面像素稳定，源 DOCX 哈希未变。与公开报告中的修复前 PDF 对比：

| 主输入字符边界 | 修复前 DOCXIDE 间距 | 修复后 DOCXIDE 间距 | Word 间距 |
| --- | --- | --- | --- |
| 标题“桥梁”之前 | −25.529 pt | +1.481 pt | +1.920 pt |
| 期刊“土木”之前 | −25.527 pt | +1.483 pt | +1.920 pt |

主输入的两处重叠消失，换行位置与 Word 一致；标题及期刊起始横坐标分别为
182.067 / 372.272 pt，Word 为 182.42 / 372.43 pt。去掉悬挂缩进的对照
也不再重叠，另外三份对照未出现这两处负间距。
原始 `chinese_crossrefs` 页底两条参考文献均恢复可读；
`template_cn` 原先部分英文参考文献中的单词挤压也随本次修复改善。

这不代表与 Word 完全等价：完整中文案例的第二条参考文献，以及无悬挂缩进的
最小对照仍有换行差异，部分中西文间距也不同。此前记录的 MathType 纵向位置、
`both` 公式内容及普通字段 PDF 跳转限制，本轮未重新专项评估。

本轮本地材料位于忽略目录 `tests/visual/results/docxide-520230c-20261114/`：
`docx-update.log`、`visual-verify.log`、`preview-verify.log` 保存正式测试结果；
`repro-measurements.json`、`repros/` 记录原始输入双次导出及坐标；
`chinese_crossrefs-references-comparison.png` 为用户指出区域的修复前/Word/修复后对照；
`review.json`、`comparisons/` 和 `template_cn-page-*-comparison.png` 保存全部变化页面的检查材料。
`previous-baselines/` 和 `previous-docxide-pdf.exe` 保留本次更新前的工作区版本，
因此这里的“3 页变化”没有混入上一轮尚未提交的基线变化。

### 520230c 与 Word 对照：当时仍然明显的差异

随后针对 **0.21.3 / `520230ce4c14`** 重新检查全部 25 个案例的 70 页
Word/DOCXIDE 同输入页面、文本、几何及 PDF 链接；另外将 #146/#147 的
9 份原始输入在两种后端各独立导出两次。以下结论来自当前产物，
没有把历史报告中的问题直接当作仍然存在的问题。

| 优先级 / 类别 | 当前实际差异 |
| --- | --- |
| 高：完整案例目录页码 | `docx_directives` 中，Word 的 Results / Caption sources 为正文第 2 / 3 页，DOCXIDE 目录显示 1 / 2；两者标题实际均落在物理第 4 / 5 页。Figure 3 实际均在物理第 7 页，Word 目录为 5，DOCXIDE 为 4；Table 3 实际均在物理第 8 页，目录分别为 6 / 4。该案例页眉显示正文从物理第 3 页以 1 起算，目录跳转的目标物理页也正确，错误集中在打印的页码。简单 #146 分节页码最小输入恢复，不能据此判定完整目录已全部修复 |
| 高：`both` MathType 内容 | 原始两公式输入中 Word 完整绘制，DOCXIDE 仍只画出零散线条或符号片段，无法正常阅读。这个限制涉及 SDK WMF，普通使用 OMML 的稿件视觉测试不会覆盖它 |
| 中高：Rust MathType 垂直位置 | 公式主体完整、横向居中已恢复，但右侧编号偏低；同一输入 `(1)` / `(2)` 编号基线在 Word 为 182.780 / 243.050 pt，DOCXIDE 为 194.808 / 259.327 pt，相对页面低约 12.03 / 16.28 pt。这是纵向排版差异，不是之前的横向居中问题 |
| 中高：换行累计导致内容落页不同 | 英文 `template` 第 2 页 Introduction 首段为 Word 2 行 / DOCXIDE 3 行。第 2 页末红色说明在 Word 留在本页，在 DOCXIDE 移到第 3 页；Table 4 在 Word 第 6 页完整，在 DOCXIDE 最后一行及重复表头移到第 7 页。中文模板的“3.2 统计显著性”则为 Word 第 7 页 / DOCXIDE 第 6 页。两套模板总页数都为 9，不代表每页内容一致 |
| 中：PDF 交叉引用跳转 | `native_crossrefs` Word 29 个链接区域 / DOCXIDE 2 个；`references` 4 / 0；英文模板 42 / 13。普通 REF 和文献引用的文字可以存在，点击区域仍缺失。目录虽均有 70 个链接，Word 可点击整条目录，DOCXIDE 当前区域通常只有右侧页码，约 6 pt 宽 |
| 中：稿件行号 | `docx_footer` 第 10 页相同标题“6 Independent copies”在 Word 为第 22 行，DOCXIDE 为第 17 行；第 14 页最后正文为 33 / 26。该案例实际分页一致，存在额外的计数差异，具体触发条件尚未进一步缩减。编辑预览行号仍不能作为 Word 审稿行号的替代 |

中文重叠、按章节重置编号、原始简单分节目录页码、表格内子图目录页码、
图表目录样式、原始 `keepNext` 标题和图片同页、Rust 公式横向居中，
本轮未在各自原始复现中再次观察到旧问题。正文图片裁剪的当前主要可见区域
也基本一致；部分脚注和边框细节不在上述主要问题结论中。

当前对照材料位于忽略目录 `tests/visual/results/docxide-word-current-20261114/`：
`pages/` 和 `overview-01.png` 至 `overview-12.png` 覆盖全部 70 页；
`corpus.json` 保存逐页文本、位置、图片和链接；
`detail-directory-page-numbers.png`、`detail-math-{both,rust}.png`、
`detail-template-introduction.png`、`detail-template-table-continuation.png`、
`detail-line-numbering.png` 为放大证据；
`repro-measurements.json`、`repro-environments.json`、`repros/` 保存原始
9 份输入的 36 次导出及源文件哈希验证。没有更新基线、修改输入或发布新的上游报告。

### 上游 issue #164：公式编号纵向位置、行号字体及目录页码

按用户要求，将其中三项分别缩减为独立输入，并合并提交到
[issue #164](https://github.com/sverrejb/docxide-pdf/issues/164)。
测试版本仍为 **0.21.3 / `520230ce4c14`**，Word 为 **16.0.20527.20196**。

| 最小输入 | 独立触发条件及实测结果 |
| --- | --- |
| `01-equation-center.docx`，6,175 字节 | 只有一个段落、一条 Rust MathType 公式、居中/右对齐制表位及 `(1)`。设置 `textAlignment="center"` 时，Word 编号基线为 91.820 pt，DOCXIDE 为 103.783 pt，低 11.96 pt；公式主体位置基本一致 |
| `02-line-number-style.docx`，1,986 字节 | 三个短段落，启用自动行号，文档默认 Times New Roman 12 pt；内置 `LineNumber` 字符样式（`line number`，中文别名“行号”）指定 Courier New 16 pt。Word 行号采用 Courier New，PDF 测得约 15.96 pt；DOCXIDE 仍为 Times New Roman 12 pt。这次报告的是字体，不是上一节的行号计数差异 |
| `03-directory-table-height.docx`，2,562 字节 | 一个目录条目、一个 600 pt 高的单行表格、一个段前 48 pt 的 Results 一级标题和 PAGE 页脚。两种后端的标题实际都在第 2 页，PAGE 页脚均为 2，目录链接均正确跳到第 2 页；Word 目录显示 2，DOCXIDE 却显示 1。无需分节、页码重置、图片、题注或交叉引用缓存 |

另附四份单项对照：

- 只将公式段落 `textAlignment` 改成 `baseline`，Word 编号降到 103.700 pt，
  DOCXIDE 仍为 103.783 pt。
- 将公式换成相同 123 × 31.65 pt 的普通 PNG，两个后端图片矩形完全相同，
  编号仍相差 11.96 pt。因此纵向问题无需 MathType、WMF 或 OLE 也能触发。
- 只移除 `LineNumber` 样式，两种后端的行号均使用 Times New Roman 12 pt。
- 只给 Results 加 `pageBreakBefore`，两边仍为两页、标题仍在第 2 页，
  DOCXIDE 目录页码恢复为 2。

7 份原始输入在两种后端各独立导出两次，共 **28 次导出**，重复页面像素一致，
源 DOCX 哈希未变。仅使用标准库的生成器及合成公式资源在另一目录中逐字节
重建全部输入；Word 没有保存这些原始输入，DOCXIDE 收到的目录字段缓存
只是 `Update field.` 占位文本。没有将正确答案注入缓存，也没有改变产品代码或基线。

[公开复现材料](https://gist.github.com/cesaryuan/f16c4d08087ad308deb357f3b9ddd276)
提供完整 ZIP、原始输入、14 份 PDF、10 张对比图、测量及环境记录。
32 个公开二进制文件已下载并逐一核对本地 SHA-256；
发布后的 issue 正文与本地一致，四张嵌入图均通过 GitHub 渲染后的图片地址验证加载。
本地材料位于忽略目录 `tests/visual/results/docxide-three-layout-issues-20261114/`，
其中 `generate_inputs.py` 为独立生成器，`public/` 为附件内容，
`publication.json` 保存下载校验与发布结果。

## 2026-11-14 首次更新与验证范围

2026-11-14 将内置预览库和 visual CLI 从 **0.20.1 / `0d97e9b0468d`**
同步更新到 **0.21.3**，固定上游 main 提交
`d1f22acbca61d9de27e644a802c4447c2d89e29a`（上游提交时间为 2026-11-13）。
`Cargo.lock`、预览服务报告的 renderer 身份及 CLI 安装说明均同步更新。

- DOCX 基线重新采集：**52 passed**，Word 与 DOCXIDE 各 26 项。
- 关闭基线更新后的完整 visual：**72 passed, 496 deselected**，覆盖 Word 26 项、
  DOCXIDE 26 项、HTML 18 项，以及原生目录语义 2 项。
- 内置预览 HTTP 回归：**6 passed**，覆盖未保存内容、公式、样式依赖更新及失败恢复。
- 25 个稿件案例的 Word、新 DOCXIDE、旧 DOCXIDE 各为 **70 页**，逐案例页数均相同。
  本次 25 个案例共 **65 页** DOCXIDE 像素发生变化，已保留差异图及新旧/Word 对照。
  两种后端分别维护基线，通过测试不代表它们逐页等价。
- 本机 Word 从 **16.0.20527.20088** 更新为 **16.0.20527.20196**。
  重新采集后，**所有 Word 基线页像素均与 HEAD 相同**，仅环境记录更新；
  字体、打印机、PyMuPDF/MuPDF 及 Chromium 环境未变。

### 本次复核上游 issue #146 / #147 的原始输入

保留的 9 份原始 DOCX 在当前 Word 和新 DOCXIDE 中各独立导出两次，
共 36 次导出，重复页面像素稳定，源 DOCX 哈希未变。没有重新生成或用 Word
保存输入，因此可与上次报告直接比较。

| 问题 | 0.21.3 实测结果 |
| --- | --- |
| SEQ 按章节重置 | 已恢复。第 2 章的图、表、公式均为 `2-1`，与正文 REF 缓存一致 |
| 分节后的目录页码 | 已恢复。正文逻辑页码重新从 1 开始，普通目录和图目录也显示 1 |
| 表格中的子图目录页码 | 已恢复。两个单元格子图条目与表格外主图条目均显示页码 1 |
| 图表目录样式 | 已恢复。普通目录保留红色粗体，图目录和表目录使用 `Table of Figures` 的绿色斜体 |
| 标题与后继图片同页 | 已恢复。直接设置或继承 `keepNext` 的两份输入中，标题、图片、图注一起位于第 2 页 |
| Rust MathType 横向居中 | 已恢复。宽 123 pt 的普通 PNG 与 Word 具有完全相同的 `[244.5, 367.5]` 横向边界，中心为 306 pt。两个真实公式可见轮廓中心相对正文中线的误差分别约为 −0.40 / −0.38 pt；与 Word 中心的差值约为 −0.75 / −0.01 pt |

### 当时记录的限制

Rust MathType 公式编号仍低于公式主体；横向居中修复不解决这个纵向问题。
`both` 输入在 Word 中显示完整，而新 DOCXIDE 仍只绘制少量公式片段，
没有恢复完整公式。普通稿件 visual 多数使用原生 OMML，这两项由上述独立
MathType 输入额外检查，不能从普通 visual 通过推断其已修复。

完整案例中，多数普通章节、公式及文献字段仍缺少 PDF 跳转区域：
`references` 为 Word **4** / DOCXIDE **0**，`native_crossrefs` 为 **29 / 2**。
目录案例 `docx_directives` 则为 **70 / 70**。英文模板的换行及后续内容落页、
中文模板的局部排版与 Word 仍有差异。

用户指出 `chinese_crossrefs` 页底的中文参考文献存在同一行文字重叠后，
已进一步缩减为 **1,658 字节、一个段落、一个 run** 的独立 DOCX，并提交
[issue #163](https://github.com/sverrejb/docxide-pdf/issues/163)。
该输入使用制表符、两端对齐及 Times New Roman/SimSun 混排；DOCXIDE 将标题、
期刊两个片段分别向前画入前文，连续字符边界的水平前进量约为 **−25.53 pt**，
Word 对应为 **+1.92 pt**。去掉悬挂缩进仍重叠；去掉制表符、改为左对齐或
统一为 SimSun 时，没有出现同样的重叠，左对齐对照仍有越过右边距的换行差异。
旧 **0.20.1 / `0d97e9b0468d`** 对同一最小输入产生完全相同的重叠坐标，
因此不将它称为本次升级引入的回归。之前缩略图复核漏掉了这个局部问题。
五份输入在当前 Word、DOCXIDE 中各导出两次，主输入另经旧 DOCXIDE 双次导出，
共 **22 次**；重复像素稳定、源文件哈希未变，独立生成器能逐字节重建输入。
[公开材料](https://gist.github.com/cesaryuan/31b2091301826601f29e971c981dd5ba)
包含 5 份 DOCX、11 份 PDF、6 张对比图和测量，公开二进制附件与本地哈希一致，
issue 中三张图片已验证加载。本地材料位于忽略目录
`tests/visual/results/chinese-reference-overlap-20261114/`。

本轮材料位于忽略目录 `tests/visual/results/docxide-d1f22ac-20261114/`：
`docx-update.log`、`visual-verify.log`、`preview-verify.log` 为正式运行记录；
`review.json` 记录 25 个案例的文本、坐标、链接及页面变化；
`overview-01.png` 至 `overview-09.png` 和 `comparisons/` 为旧 DOCXIDE、
新 DOCXIDE、Word 的三列对照，覆盖全部 65 张变化页面；
`repro-measurements.json`、`repros/`、`repro-*.png` 为 9 份原始输入的双次
导出结果及对照。这里的本地诊断脚本不属于长期测试入口。

## 2026-10-31 版本更新与验证范围

2026-10-31 将内置预览库和 visual CLI 同步锁定到最新上游提交
[`0d97e9b0468d43bf23aacbb46b2619557d701c6e`](https://github.com/sverrejb/docxide-pdf/commit/0d97e9b0468d43bf23aacbb46b2619557d701c6e)。
包版本仍为 **0.20.1**，这些修改尚未发布到 crates.io，因此使用固定 Git 提交。
Word、字体及 PDF 栅格化环境保持不变；仅更新 DOCXIDE 的环境记录和基线。

- DOCXIDE 基线更新：**26 passed**，每份输入两次独立导出的页面像素一致，源 DOCX 未改写。
- 完整 visual 验证：**72 passed, 496 deselected**，覆盖 Word 26 项、DOCXIDE 26 项、
  HTML 18 项和原生目录语义 2 项；验证时未启用基线更新。
- 内置预览 HTTP 回归：**6 passed**，覆盖公式、未保存内容、依赖变更和失败恢复。
- 25 个稿件案例中，15 个案例的 33 张已有页面图发生变化，目录另新增 3 页。
  `docx_directives` 从 5 页增至 8 页；DOCXIDE 合计 **70 页**，与 Word 总页数一致。
  相同页数不表示各页内容、字段语义或像素一致。

复核了全部 36 张变化或新增页面与旧 DOCXIDE、Word 的并排图。确认目录不再显示
`Update field in Word.` 占位；`word_fields` 的中文 SEQ 结果从“一”变为“1”；
`native_crossrefs` 题注不再将“第…章”混入章节号；`docx_footer` 第 6 页小表格恢复居中。
段落边框线型、列表标记及部分表格布局也发生变化，已保留逐页差异图。
随后按下节重新检查全部 25 个案例的 PDF 文本、位置及链接，并将历史九份最小输入
在两个后端各独立导出两次。主要残留问题为章节序号重置、目录页码、字段链接及正文排版。
下文的旧坐标测量和上游报告继续作为历史记录。

本地运行日志及并排复核图保存在忽略目录
`tests/visual/results/docxide-0d97e9b0468d/`：`docxide-update.log`、`visual-verify.log`、
`preview-verify.log`、`review-index.json`、`comparison-*.png` 和 `overview-*.png`。
并排图从左至右为旧 DOCXIDE、新 DOCXIDE、Word；基线旁的 `page-*-diff.png`
继续以 Git HEAD 为参照保留变化。

## 2026-10-31 最新提交与 Word 的主要差异

以下仅列本轮实际输出中再次确认的问题。25 个案例分别比较，Word 和 DOCXIDE
每个案例的页数都相同，总计均为 70 页，但段落落页、编号和 PDF 跳转仍有差异。
本轮稿件 visual 使用原生 OMML，不覆盖旧报告中的 SDK MathType WMF 缺失问题。

| 类别 | 当前观察与影响 | 复核材料 |
| --- | --- | --- |
| 章节内序号未重置 | `native_crossrefs` 第 2 章 Word 为图 2-1、表 2-1、式 (2-1)，DOCXIDE 为图 2-3、表 2-2、式 (2-2)。正文引用仍为缓存的 2-1，与实际题注不一致；输入 SEQ 指令包含按一级标题重置的 `\s 1` | `remaining-full-05.png`、`current-texts.json` |
| 目录页码与样式 | `docx_directives` 首个 Methods 条目 Word 页码为 1、DOCXIDE 为 3；Results 为 2 / 3。子图 (a)/(b) 条目在 Word 有页码，DOCXIDE 只显示引导线且没有对应跳转；图目录主条目粗体也与 Word 不同。目录能生成尚不代表分节页码和子图目录正确 | `remaining-full-03.png`、`remaining-links.json` |
| PDF 引用跳转缺失 | `references` 的 Word PDF 有 4 个内部链接区域，DOCXIDE 为 0；`native_crossrefs` 为 29 / 2，缺少章节、图表、公式和文献字段的多数跳转。DOCXIDE 仍保留普通显式内部链接及脚注链接，不能概括为完全不支持链接 | `remaining-links.json` |
| 英文换行与布局累积偏移 | `template` 第 2 页 Introduction 首段 Word 2 行、DOCXIDE 3 行；Methods 标题顶部 y 为约 467.10 / 487.78 pt。第 3 页 Mathematical Formulation 标题为约 619.65 / 689.79 pt，已相差约 70 pt，后续公式和表格位置随之不同 | `remaining-full-01.png`、`remaining-full-02.png` |
| 标题随正文分页 | `bilingual_captions` 的“4 第四章”在 DOCXIDE 第 1 页末独自出现，Word 将其与后面的图放在第 2 页。历史独立标题最小输入本次已恢复两者均在第 2 页，因此不能认定所有 keep-with-next 场景都已修复 | `remaining-full-06.png`、`remaining-minimal.json` |
| 自动表格宽度与列间距 | 历史 `05-header-table-center` 的表格已居中，但 DOCXIDE 表格仍比 Word 宽，列间距也更大；两个后端的 First 首词位置约为 275.45 / 270.02 pt。定位问题已修复，不代表自动宽度一致 | `remaining-05-header-table-center.png`、`remaining-minimal.json` |
| 段落边框细节 | dotted、dashed、double 等已能区分，但 wave 在 Word 中为细斜纹状，在 DOCXIDE 为明显的连续正弦波；`paragraph_format` 嵌套连接框的接边位置与间隔仍不同 | `remaining-07-paragraph-border-styles.png`、`remaining-full-08.png`、`remaining-full-09.png` |

需要更正前一轮概览中的两项判断：`native_crossrefs` 的两个子图本次都已完整放到
第 2 页，原先拆到两页的问题没有复现；窄栏公式编号折成两行在 Word 中同样存在，
不应单独归为 DOCXIDE 差异。当前公式编号的实质差异是第 2 章序号没有重置。

九份历史最小输入确认了简单目录生成、STYLEREF 编号提取、ASCII/中文 SEQ、居中
列表标记、表格居中继承、合并单元格垂直居中，以及嵌套表格与标题的两个简单分页
场景均已恢复对应行为。上述完整稿件中的残留不能直接用这些简单案例全部通过来排除。

本节材料仍在 `tests/visual/results/docxide-0d97e9b0468d/`，所有 `remaining-*.png`
均为左 Word、右 DOCXIDE；`remaining-minimal/` 保留本轮双次导出的 PDF，
`inspect_remaining.py` 为本地诊断入口，不是长期测试或基线更新入口。

## 2026-10-31 补充：Rust MathType 公式居中偏右

对同一 Markdown 仅切换 `mathtypeConversionMethod: rust/both`，生成两份 DOCX，
再分别由 Word 与当前固定提交的 DOCXIDE 各导出两次。Rust 输入中的两个公式
在 Word 中居中，在 DOCXIDE 中分别向右偏约 **61.10 pt** 和 **80.37 pt**；
对应公式对象宽度为 **123 pt** 和 **161.5 pt**，偏移接近对象宽度的一半。
公式编号在 DOCXIDE 中也落在公式主体的下方。

该输入正文中线为 **297.65 pt**。按公式可见轮廓测量，Word 的两个中心分别为
**297.99/297.27 pt**，DOCXIDE 为 **358.75/378.02 pt**。同一份 Rust DOCX
在 Word 中正确，不能将此归因为 Rust 公式转换没有设置居中。
`both` 在 Word 中显示并居中，但本次 DOCXIDE 中公式主体仍为空，不能作为
“both 居中正常”的有效对照。

独立的一段式 PNG 对照进一步排除了公式字体、WMF 和 OLE：在宽 **468 pt**
的正文中设置中心制表位和右对齐编号，放置宽 **123 pt** 的普通 PNG。
Word 图像中心为 **306 pt**；DOCXIDE 将图像左边缘放到 **306 pt**，
中心变为 **367.5 pt**，恰好偏右 **61.5 pt**。
固定提交的 `src/pdf/layout.rs` 中，`resolve_tab_aligned_start` 调用的
`segment_width` 只测量文字；实际排版随后才累加 `inline_image` 的宽度。
因此中心制表位预估遗漏了内嵌图像，影响不限于 MathType。

之前普通稿件的 visual 构建使用 `--no-mathtype`，未验证这条 Rust OLE 路径；
两种后端各自基线通过也不能独立证明跨后端居中一致。
本次未更新基线或修改公式排版实现。两种公式输入和 PNG 对照在每个后端
分别双次导出，页面像素稳定，源 DOCX 哈希未变。
本地最小输入、PDF、对比图、测量及诊断脚本位于忽略目录
`tests/visual/results/docxide-0d97e9b-followup/math-rust-vs-both/`。

已提交独立上游 [issue #147](https://github.com/sverrejb/docxide-pdf/issues/147)，
附[公开最小复现材料](https://gist.github.com/cesaryuan/b872d303313a66827e2a8a64b20457e5)：
三份原始 DOCX、六份 PDF、三张对比图、测量 JSON 及可独立生成 PNG 对照的脚本。
公开二进制附件重新下载后的 SHA-256 均与本地一致，issue 中三张图片均已验证加载。
编号纵向偏移仅记录为观察，未将其原因归结为本次已定位的横向宽度计算。

## 2026-10-30 发布版评估记录

2026-10-30 更新到 **0.20.1**，固定当前上游提交
[`c2fa4fa461d987653dc717d7327096e57afb4686`](https://github.com/sverrejb/docxide-pdf/commit/c2fa4fa461d987653dc717d7327096e57afb4686)。
对照 Word 为 **16.0.20527.20088**，字体不变，PDF 按 144 DPI RGB 栅格化。
本次输入来自当前工作区，包含目录及双语题注案例的最新修改。

- Word 正式视觉测试：**26 passed**，保留现有 Word 基线。
- DOCXIDE 正式基线更新：**26 passed**；随后独立验证：**26 passed**。
- 25 个完整案例合计 Word **70 页**、DOCXIDE **67 页**；页数差异全部来自 `docx_directives`。
  另外一项测试是颜色及分页变化的检测装置，不计入稿件页数。
- 每个后端对每份输入独立导出两次，检查像素稳定及原 DOCX 未改变。
- 当前 Word 测试的 DOCX 另复制到评估目录，直接供 DOCXIDE 渲染，保证跨后端对照使用相同输入。
  Word 按正式测试要求更新 `docx_directives`、`word_fields` 字段；DOCXIDE 自行处理支持的字段。

两套基线各自通过，仅说明输出稳定且符合各自审阅结果，不表示两种后端等价。
Word 的字段更新、目录及布局语义检查继续保留；没有放宽像素容差。

### 当时已确认的修复

| 问题 | 0.20.1 实测结果 |
| --- | --- |
| 继承的字符单位首行缩进丢失，#144 | 五个最小对照均恢复 24 pt 缩进，首行约 96.03 pt、续行约 72.03 pt；Word 为 96.024/72.024 pt |
| 完整案例的同类首行缩进 | 保守匹配 200 个正文段落，包括之前出错的 83 个继承字符缩进段落；未发现原来的缩进丢失，所有起始横坐标差异不超过 0.975 pt |
| 参考文献悬挂缩进，#143 | 字符值优先的修复仍有效，五组最小对照及当前中英文模板均已复核 |
| 混排脚注英文词内字距，#142 第 1 项 | 最小输入中 Correspondence 首末字母起点距离为 61.66 pt，Word 为 61.73 pt；此前为 89.11 pt |
| 嵌套表格图片裁剪，#142 第 2 项 | 最小输入图片恢复为 166 × 83 pt，未再裁成约 0.4 pt 竖条 |
| 简单 SVG，#142 第 3 项 | 该最小输入导出为矢量路径，PDF 为 0 个位图、2 个绘图对象；此结论仅覆盖该简单 SVG |
| 自动宽度页眉表格，#142 第 5 项 | 原最小输入宽度恢复正常；复杂页眉中的横向位置仍有问题 |
| 正文 STYLEREF，#142 第 6 项 | 原最小输入正文与页眉均输出当前标题；编号提取开关仍有差异 |
| 段落边框继承 | 当前 `paragraph_format` 能绘制继承的连接框及单侧覆盖；复杂线型仍有差异 |

首行缩进及边框继承的上游修复说明见
[issue #144 回复](https://github.com/sverrejb/docxide-pdf/issues/144#issuecomment-6522589456)。
正文匹配忽略空白，要求唯一的 28 字符段首，并跳过歧义及无法匹配的段落；
这能复核此前的回归，不能证明所有段落、列表及表格单元格均等价。

### 当时剩余差异

| 类别 / 案例 | Word 与 DOCXIDE 0.20.1 的差异 | 本地对比图 |
| --- | --- | --- |
| SDK MathType WMF 公式：真实论文与 12 公式最小输入 | 193 个公式 WMF 均含当前未绘制的 `META_EXTTEXTOUT`；最小输入中 Word 显示前 12 个公式，DOCXIDE 全部空白。原生 OMML、Rust 轮廓 WMF 的对照能显示，详见 [预览内容复核](DOCX_PREVIEW_RENDERING_CHECK.md) | `output/benchmarks/docx-render-investigation-20261031/math-minimal-comparison.png` |
| 英文两端对齐换行：`template` 第 2 页及 `04-introduction-wrap.docx` | Introduction 首段仍是 Word 2 行、DOCXIDE 3 行；完整模板后续表格和公式也向后移动 | `detail-introduction.png` |
| 目录字段：`docx_directives` | Word 生成正文、图、表及子图等目录和页码；DOCXIDE 保留 `Update field in Word.` 占位缓存，整份文档为 8 页 / 5 页。这是字段支持差异，不能直接归因于正文排版变紧 | `docx_directives-01.png`、`docx_directives-04.png` |
| 章节编号字段：`native_crossrefs` | Word 题注为 `图 1-1`、`表 1-1`，DOCXIDE 为 `图 第 1 章-1`、`表 第 1 章-1`；公式编号也带入“第…章”并在窄栏中折成多行。正文引用仍为原缓存的 `图 1-1`，与题注不一致 | `detail-chapter-fields.png` |
| SEQ 字段缓存：`word_fields` | `Chinese instruction` 一行 Word 更新后输出 `1.`，DOCXIDE 保留缓存 `一.`；后续最小对照确认 ASCII 名称也保留缓存，是当前文档声明的 SEQ 计算支持范围限制 | `word_fields-01.png` |
| 分页与内容成组：`native_crossrefs`、`bilingual_captions` | Word 把两个子图都放在第 2 页；DOCXIDE 将 (a) 留在第 1 页、(b) 放在第 2 页。双语题注案例的“4 第四章”在 DOCXIDE 第 1 页末单独出现，Word 放在下一页 | `native_crossrefs-01.png`、`native_crossrefs-02.png`、`bilingual_captions-01.png` |
| 居中列表标记：`paragraph_format` 第 1 页 | 文字居中，但圆点标记留在左侧。第一项圆点 x 坐标约 254.81 / 104.00 pt，偏差约 151 pt；不是正文首行缩进回归 | `detail-list-markers.png` |
| 页眉表格位置：`docx_footer` 第 6 页 | Word 小表格与标题一起居中；DOCXIDE 标题居中、表格靠左。表格首词 x 坐标约 270.17 / 94.03 pt，偏差约 176 pt | `detail-header-table.png` |
| 合并单元格垂直对齐：`reply` 第 1 页 | 跨三行的 `Vertical group` 在 Word 中居中，在 DOCXIDE 中靠顶；文字上沿约 699.77 / 682.66 pt，相差约 17 pt。完整模板的部分表格行高也不同 | `detail-table-alignment.png`、`template-02.png` |
| 段落边框线型：`paragraph_format` | 虚线、点线、波浪线及部分复合线型仍被绘成实线或不同外观；继承与单侧覆盖恢复不代表线型完全一致 | `detail-border-styles.png`、`paragraph_format-01.png` |

以上是当前版本与 Word 的直接差异。本次没有逐版本追溯所有现象的引入提交，
因此不将它们全部称为 0.20.1 新回归。优先继续处理目录字段、章节编号字段及分页成组行为。

### 当时评估材料

本地材料位于忽略目录 `tests/visual/results/docxide-0.20.1-review-20261030/`，不随 Git 检出分发：

- `corpus/<case>/input.docx`、`word.pdf`、`docxide/export-{1,2}/rendered.pdf`：25 个案例的同输入对照。
- `comparisons/`：每页左右对照图，左 Word、右 DOCXIDE；上表图片均在此目录，`detail-*.png` 为局部。
- `corpus.json`：段落坐标及页数；`minimal.json`：最小输入逐行坐标；`details.json`：局部文字位置；`texts.json`：逐页文本。
- `word-test.log`、`docxide-update.log`、`docxide-verify.log`：正式测试结果。
- `review.py`：复用正式双次导出逻辑的本地诊断入口，不作为长期测试依赖。

完整测试输入保留在本地。日常验证继续使用正式 pytest 入口。

## 后续上游报告：八类差异的九份最小输入

已通过 `gh issue create --body-file` 提交
[issue #145](https://github.com/sverrejb/docxide-pdf/issues/145)。
按用户要求排除英文两端对齐换行，其他八类差异分别缩减为独立合成输入；
分页问题提供嵌套表格和标题两份输入，共九份 DOCX，每份约 1.8–2.4 KB。

[公开复现包](https://github.com/user-attachments/files/34188028/docxide-0.20.1-eight-categories-minimal-repros.zip)
包含九份 DOCX、18 份 PDF、九张对比图、输入哈希、测量及环境记录和仅需 Python 标准库的生成器。
每个输入在 Word 与 DOCXIDE 中各独立导出两次，页像素稳定且源 DOCX 哈希未变。
九张图片均已单独嵌入 issue，并验证发布后加载正常；压缩包重新下载后的 SHA-256 与本地一致。
这些合成输入无需 Papper、Pandoc、原稿件或嵌入字体，可独立复现。

最小化进一步确认了两个问题的范围：表格居中和合并单元格垂直对齐均涉及表格样式属性，
直接设置对应属性的对照能正常工作；SEQ 则不局限于中文名称。
报告明确将 TOC 生成与 SEQ 计算列为当前文档所述缓存行为之外的支持请求，未称为新回归。

本地材料：`tests/visual/results/docxide-0.20.1-eight-issues/`，
其中 `generate_inputs.py` 为独立生成器，`public/` 为公开附件内容，
`issue-body.md` 为发布正文，`publication.json` 记录上游及附件链接。

## 历史报告：0.19.1 的六项差异

以下为历史结果，当前状态以上文为准。它们使用 Microsoft Word **16.0.20527.20088** 验证，已提交
[上游 issue #142](https://github.com/sverrejb/docxide-pdf/issues/142)。
完整触发条件、对照输入和六张对比图集中保存在 issue 中。

| 最小输入 | 差异 |
| --- | --- |
| `01-footnote-mixed-justify.docx` | 中英文混排脚注两端对齐时，英文词内字距被拉大；Correspondence 首末字母距离从 Word 的 61.73 pt 变为 89.11 pt |
| `02-nested-table-figure.docx` | 嵌套表格中的 166 × 83 pt PNG 被约 0.4 pt 宽的窗口裁成竖条 |
| `03-svg-fallback.docx` | SVG 使用低分辨率 PNG 后备图，显示尺寸下约 69.4 DPI |
| `04-introduction-wrap.docx` | Introduction 首段 Word 为 2 行，DOCXIDE 为 3 行 |
| `05-header-autofit-table.docx` | 自动宽度页眉表格保留 396 pt 网格，Word 收缩到约 64 pt |
| `06-body-styleref.docx` | 正文 STYLEREF 保留旧缓存，页眉能解析当前标题 |

保留的[复现压缩包](https://github.com/user-attachments/files/34027419/docxide-0.19.1-six-minimal-repros.zip)
包含六份问题 DOCX、八份对照 DOCX、两种后端的 PDF/图片、测量结果、输入哈希及独立生成器
`generate_inputs.py`，可脱离 Papper 使用。图片为合成内容，不包含完整稿件或字体。
本地备份位于
`tests/visual/results/docxide-0.19.1-repros/docxide-0.19.1-six-minimal-repros.zip`；
该目录为忽略的本地材料，干净检出可从上述链接下载。

一次性评估、重跑和问题打包脚本已移除；日常回归验证使用正式 pytest 入口。

## 历史报告：参考文献悬挂缩进

DOCXIDE **0.19.1** 与上述 Word 版本对 `corpus/template`、`corpus/template_cn`
参考文献续行的缩进不同：Word 为约 **24 pt**，DOCXIDE 为 **10 pt**。
英文模板第 3 条参考文献的换行位置也因此改变。

两份 DOCX 的参考文献样式均包含
`<w:ind w:hanging="200" w:hangingChars="200" w:left="200"/>`。
最小对照确认：仅使用物理单位的 10 pt 或 24 pt 悬挂缩进时，两者基本一致；
同时存在物理值和字符值时，Word 使用字符值，而 DOCXIDE 优先采用物理值。
因此这不是完全不支持悬挂缩进，而是字符单位及其优先级与 Word 不兼容。
对应逻辑见固定版本的
[`extract_indents`](https://github.com/sverrejb/docxide-pdf/blob/123aae5755ef77e51608b069221d53b526ffd162/src/docx/mod.rs#L750-L777)。

本次五组最小对照均经每个后端两次独立导出验证像素一致。
最小 DOCX、两种后端的 PDF、环境记录、测量 JSON 和中英文局部对比图保存在忽略的本地目录
`tests/visual/results/hanging-indent-check-20261028-001851/`。
该问题已单独提交为[上游 issue #143](https://github.com/sverrejb/docxide-pdf/issues/143)，
包含[最小复现包](https://github.com/user-attachments/files/34031036/docxide-0.19.1-hanging-indent-repro.zip)
和三张已验证加载的对比图。0.20.1 已不再复现这一问题。

首行缩进回归另见 [issue #144](https://github.com/sverrejb/docxide-pdf/issues/144)及
[五组最小输入](https://github.com/user-attachments/files/34165899/docxide-0.20.0-inherited-first-line-repro.zip)。
0.20.1 的复核结果见上文。
