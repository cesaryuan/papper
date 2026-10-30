# DOCXIDE 后端与已知差异

DOCX 视觉回归测试默认运行 Word 和 DOCXIDE，分别维护环境记录和逐页像素基线。
安装、运行、选择单个后端及更新基线的方法统一维护在
[视觉测试说明](../tests/visual/README.md)。

## 当前版本与验证范围

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

## 当前已确认的修复

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

## 当前剩余差异

| 类别 / 案例 | Word 与 DOCXIDE 0.20.1 的差异 | 本地对比图 |
| --- | --- | --- |
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

## 本次评估材料

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
