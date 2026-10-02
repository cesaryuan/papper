---
name: word-manuscript-fix
description: 当需要将 Word 稿件转换为或修复为本仓库使用的 Pandoc Markdown 格式时，应使用此技能，尤其适用于需要先转换 `.docx`，或者生成后的 Markdown 仍存在格式问题的情况。
---

# Word 稿件修复

处理从 `.docx` 或 `pandoc` 生成的 Markdown 稿件，将其整理为本仓库要求的 Pandoc Markdown 格式。重点修复转义后的 LaTeX 数学公式、纯文本编号引用、Word 风格 `_Ref...` 交叉引用、基于表格的图片或子图布局、图片尺寸精度过高、不符合项目规范的表格语法，以及缺失的 YAML front matter。

如果输入是 `.docx`，先运行：

`papper convert .\input.docx -o converted`

再继续修复生成后的 Markdown。

## 修复流程

1. 检查目标 Markdown，识别公式、引文、交叉引用、图片/子图布局、表格语法、图片尺寸和 front matter 问题。
2. 运行 `papper repair-math converted/input.md -o manuscript.md` 恢复被转义的数学公式；除这一步外，其余修复都直接编辑 Markdown 和参考文献文件完成，不要再额外编写脚本。输入输出可以指向同一个 Markdown 文件。
3. 将纯文本编号引用和参考文献列表重建为 `[@citekey]` 与 `.bib` 文件，citekey 使用有意义的 `authorYearKeyword` 风格，如 `he2016resnet`，并同步替换文中的数字引用。
4. 将 `[]{#_Ref... .anchor}`、`[表 1](#_Ref...)` 这类 Pandoc-Word 风格交叉引用改写为本仓库使用的 `pandoc-crossref` 语法，如 `[@tbl:*]`、`[@fig:*]`。
5. 将仅用于布局图片和公式的 Word 表格改写为语义化 Markdown，包括普通图片块、子图组和公式表格；图片尺寸收敛到四位有效数字，能只保留 `width` 时优先只保留 `width`。
6. 按 `manuscript.md` 的结构补充或修复 YAML front matter，将 `bibliography` 指向生成的 `.bib` 文件，并收尾检查格式是否完整一致。

## 完成标准

- 数学公式以 Pandoc 数学格式正常渲染，而不是显示为可见的 LaTeX 文本。
- 引文统一使用 `[@citekey]`，参考文献存入 `.bib` 文件，且 citekey 具有可读含义。
- 图片、子图、表格和交叉引用都符合本仓库约定，例如 `![caption](path){#fig:label}`、`[@fig:*]`、`[@tbl:*]` 和 `: Caption {#tbl:label}`。
- 最终稿件中不再保留 `_Ref...` 锚点、纯文本编号参考文献和仅用于布局的 Word 表格。
- 图片尺寸已收敛到四位有效数字，且稿件包含完整、正确的 YAML front matter。

## 备注

- 仅修改格式问题，不修改内容表述；如果发现内容错误或不清晰，应记录下来并反馈给作者，但不在修复过程中直接改动内容。
- 如果某个 Word 表格只是用于布局，应将其替换为语义化 Markdown，而不是继续保留该表格。
