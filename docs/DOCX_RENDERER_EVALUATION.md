# DOCXIDE 后端与已知差异

DOCX 视觉回归测试默认运行 Word 和 DOCXIDE，分别维护环境记录和逐页像素基线。
安装、运行、选择单个后端及更新基线的方法统一维护在
[视觉测试说明](../tests/visual/README.md)。

初始 DOCXIDE 基线使用 **0.19.1**，固定提交
[`123aae5755ef77e51608b069221d53b526ffd162`](https://github.com/sverrejb/docxide-pdf/commit/123aae5755ef77e51608b069221d53b526ffd162)，
覆盖 24 个案例、64 页。每份输入经两次独立导出验证像素一致。
这些基线用于发现回归；Word 的字段更新、目录及布局语义检查继续保留。

## 已知渲染差异

以下差异使用 Microsoft Word **16.0.20527.20088** 验证，已提交
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

## 补充确认：参考文献悬挂缩进

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
和三张已验证加载的对比图。现有 DOCXIDE 像素基线包含这一差异，不能用其通过来证明与 Word 一致。
