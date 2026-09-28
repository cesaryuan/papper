[English](README.md) | [简体中文](README.zh-CN.md)

# Papper

用 Markdown 写论文，用 Word 交稿。

Papper 是一个面向 AI 时代的、以 DOCX 为核心输出的学术写作工作流。现在 AI 很擅长起草、改写和整理 Markdown，但很多期刊、编辑和合作者最终仍然要 `.docx`。Papper 解决的正是这个错位问题：你继续用清晰、可版本控制、对 AI 友好的 Markdown 写作，在需要提交的时候再稳定地产出接近期刊工作流的 Word 文档。

<!--
README 首图建议，方便你后面自己截图或绘制：
- 用一个横向 3 面板流程图，不要只放 logo。
- 左侧放 Markdown 稿件编辑界面，最好能看到引用、交叉引用，以及一个简短的 AI 提示词或对话片段。
- 中间放终端，显示 `papper build docx` 和 `papper build-reply`。
- 右侧放排版完成的 Word 主稿页面，再加一个 reviewer reply 的 DOCX 页面。
- 图上只保留 3 个短标语最抓眼球，比如：“AI writes Markdown well”, “Papper turns it into DOCX”, “Journal-ready output”。
- 最重要的是让人一眼看懂“同一份内容从 Markdown 流到 Word”的前后对比，而不是抽象图标。
-->

## 为什么会有这个项目

对于很多研究团队来说，Markdown 正在变成一种非常自然的写作格式，尤其是在 AI 已经深度参与起草和修改的情况下。它比 LaTeX 更容易生成、更容易审阅，也更容易做版本比较。LaTeX 当然依然强大，但对很多主要任务是写作和修改的作者来说，它并不总是最友好的选择。Typst 也很有潜力，但目前还不是多数出版社默认接受的主流格式。

现实是，DOCX 仍然是很多出版社、编辑和合作者最喜欢的格式。

Papper 就是围绕这个现实构建的：

- 用 Markdown 写稿
- 保持源文件对人和 AI 都好编辑
- 在交付时生成 Word 优先的投稿文件
- 保留学术写作真正需要的能力：参考文献、公式、表格、图片、交叉引用，以及审稿回复

## Papper 的价值

Papper 不只是一个通用的 Pandoc 封装器。它是一个面向真实投稿流程的 manuscript workflow。

- **DOCX 优先**：主目标是高质量 Word 稿件，而不是把 DOCX 当成顺手导出的副产品。
- **对 AI 友好**：Markdown 更适合 LLM 生成，也更适合人在 Git 里审阅。
- **一条命令初始化项目**：`papper init` 可以直接生成论文目录结构、稿件、样式元数据、参考文献和 agent 指南。
- **面向投稿的后处理**：Pandoc 结束后，Papper 还会做 DOCX 侧的格式整理和增强。
- **支持审稿回复**：`build-reply` 可以生成 DOCX 或 TXT，并自动解析正文中的引用和交叉引用。
- **自管理 Pandoc 工具链**：如果系统里没有 `pandoc` 或 `pandoc-crossref`，Papper 可以把它们下载到当前项目的 `.pmt/tools`。
- **保留其他输出**：虽然以 DOCX 为核心，但仍然支持 LaTeX 和 JSON 输出。

## 你能得到什么

- 用 `papper init` 初始化论文工程
- 用 `papper doctor` 检查环境
- 用 `papper setup` 准备项目本地工具
- 用 `papper build` 构建 DOCX、LaTeX、JSON
- 用 `papper build-reply` 构建审稿回复
- 图、表、公式、章节的交叉引用
- 基于 CSL 的参考文献格式
- 通过 reference DOCX 控制 Word 样式
- 面向 DOCX 的后处理：作者信息、表格行为、样式、行号相关工作流
- 制表符排版的公式段落自动应用 `Para Equation` 样式，继承“正文文本”，段后间距为 0.5 行，使用单倍行距。对已有 DOCX 单独执行此步骤并原地保存：`uv run python -m pandoc_manuscript.docx.postprocess.para_equation_style path/to/file.docx`（加 `--no-save` 可仅检查而不保存）。
  样式的居中、右对齐制表位分别设在 DOCX 第一节正文可用宽度（页面宽度减左右页边距）的一半和末端；移除公式段落上的直接制表位，使其继承样式。修改页边距后需重新执行此步骤。
- Word 不友好图片场景下的 SVG 处理和回退方案
- 需要时支持 MathType 相关的 DOCX 工作流

## 快速开始

### 前置依赖

建议准备以下工具：

1. `uv`
2. `pandoc` 3.0+ 和 `pandoc-crossref`
3. 行号来源工作流在 Windows 上需要安装 Microsoft Word；其他平台可以使用 `soffice`
4. 可选：MathType，用于需要 MathType 公式的 DOCX 输出

如果 `pandoc` 或 `pandoc-crossref` 不在 `PATH` 中，Papper 可以把受管工具下载到项目内的 `.pmt/tools`。

### Python 版本粗检

如果你只是想快速做一次面向语法的 Python 版本检查，可以直接用 Ruff：

```bash
uvx ruff check .
```

这只是粗略检查，能发现不符合当前 Python 目标版本的语法，但不能证明运行时一定兼容。

### 创建第一个项目

```bash
uvx --from papper papper init my-paper
cd my-paper
papper doctor
papper build docx
```

如需中文主稿和审稿回复模板，使用 `papper init my-paper --lang zh-cn`。

生成结果：

```text
output/docx/manuscript.docx
```

如果你更喜欢先全局安装一次工具：

```bash
uv tool install --upgrade papper
papper init my-paper
```

每次 `papper` 命令结束后，Papper 会读取本地缓存的 PyPI 更新状态；发现新版本时会提示升级命令。后台 worker 最多每小时刷新一次缓存，因此命令不会等待网络请求。使用以下命令升级已安装的 Papper：

```bash
uv tool upgrade papper
```

## 典型工作流

```bash
# 初始化一个新的论文项目
papper init my-paper --setup

# 检查依赖和项目文件
papper doctor

# 构建主稿
papper build docx

# 构建中文主稿，交叉引用本地化，图表按章节编号
papper build docx --lang zh-cn

# 显式构建另一个 Markdown 文件
papper build docx paper.md -o build/paper.docx

# 构建一个资源内嵌的独立 HTML 文件
papper build html -o build/paper.html

# 构建审稿回复
papper build-reply reply.md --reply-manuscript manuscript.md -o output/docx/reply.docx
```

HTML 构建使用通用 Pandoc filter 处理中文嵌套编号和
`!<!`/`!^!` 表格单元格合并，再通过 HTML 后处理插入作者信息。DOCX
还使用通用 AST filter 处理独立行内公式的尾部空格。
HTML 构建会强制使用适合独立 HTML 的原生行间公式设置：编号使用
`\tag` 和 `$$i$$`，关闭块公式转行内公式，并关闭公式表格布局。
三线表外观来自 Pandoc 内置的 standalone HTML CSS。直接运行 Pandoc
时需要加 `-s`/`--standalone` 才会把这段 CSS 写入文件；不加时只会输出
HTML 片段。

中文构建（DOCX、HTML、LaTeX 和 JSON）默认使用内置的《GB/T 7714—2015（顺序编码，双语，姓名不大写，无 URL、DOI）》CSL。稿件 metadata 或 `style.yml` 中显式设置的 `csl` 优先。其他构建默认使用内置的 Elsevier Vancouver CSL。

Papper 读取 `style.yml` 和稿件 YAML 头部后，根据构建语言生成 Pandoc 默认元数据，再依次合并 `style.yml:pandocMetadata` 和稿件 YAML。题注、交叉引用前缀、参考文献标题及 `csl` 的显式设置均高于语言默认值。构建 DOCX 时，`--lang zh-cn` 可以为本次构建选择中文默认值，即使稿件声明了其他语言。
随包发布的 `template/style.yml` 和 `template/style-cn.yml` 分别提供英文、中文构建默认值。`papper init` 生成的项目 `style.yml` 只用于覆盖需要调整的值；未填写的值取自当前构建语言对应的随包 YAML。
中文 DOCX 和 HTML 输出的章节标题与章节交叉引用使用点号编号，例如 `3.1`；图、表和公式使用按章节的横杠编号，例如 `3-1`。JSON AST 保留解析后的引用文本。LaTeX 源码保留原生 `\ref`，显示的编号由 TeX 编译阶段决定。

## 这个项目最吸引人的地方

### 1. Markdown 真的适合写和改

Papper 不会逼你放弃纯文本写作。你的 manuscript 仍然易于 diff、重构、喂给 AI、以及协作审阅。

### 2. DOCX 不是附带功能，而是主要目标

很多学术写作工具链把 DOCX 当成“顺便导出一下”。Papper 从一开始就是 Word 导向的，默认设置和后处理都围绕最终交付文档来设计。

### 3. 面向真实投稿细节

Papper 关注的不只是“把 Markdown 转成 Word”，还包括那些常常在投稿前最后几天最容易出问题的环节：

- 审稿回复
- 图表引用
- 公式编号
- 参考文献格式
- Word reference document
- SVG 等 DOCX 图片边界情况

### 4. 适合自动化，但文件仍然透明

整个流程可以脚本化、可复现、可版本控制，但生成出来的项目结构依然是研究者一眼能看懂的论文目录，而不是黑盒。

## 文档导航

- [`template/.agents/manuscript-syntax.md`](template/.agents/manuscript-syntax.md)：稿件语法、引用、交叉引用、伪代码、修订标记、样式元数据
- [`template/manuscript.md`](template/manuscript.md)：示例稿件
- [`AGENTS.md`](AGENTS.md)：仓库级 agent 指南

## 什么时候特别适合用 Papper

如果你符合下面这些情况，Papper 会很合适：

- 你会大量借助 AI 起草和修改论文
- 你希望稿件源文件更适合 Git 管理，而不是直接修改二进制 Word
- 目标期刊仍然要求 DOCX
- 你需要一套可重复的主稿和审稿回复工作流
- 你想要 Pandoc 的能力，但不想强迫所有合作者都进入 LaTeX-first 工作方式

## 命令速览

```bash
papper init my-paper
papper setup
papper doctor
papper build docx
papper build html
papper build latex
papper build json
papper build-reply reply.md -o output/docx/reply.docx
papper clean
papper distclean
```

完整 CLI 请查看 `papper --help`。

如果需要同时为编辑器或其他扩展启动（或复用）本机 Pandoc HTTP 服务，可以执行：

```bash
papper build html --start-server
```

命令会先请求 `http://127.0.0.1:3030/version`；已有可用的 PMT 服务时直接复用，否则
启动项目绑定的 PMT runtime。需要使用其他通用 server 时才设置
`PMT_PANDOC_SERVER_COMMAND`。服务地址会打印到构建日志，支持 `/`、`/batch`、
`/version` 接口。
进程状态保存在 `.pmt/pandoc-server.json`，服务输出保存在 `.pmt/pandoc-server.log`。

这个服务用于高频 Markdown 转换。它会读取当前项目的 PMT defaults 和 metadata，使用
与正常 HTML 构建相同的 `pandoc-crossref` 与 Lua filter 链。Papper 自己的首次 HTML
构建仍使用现有 CLI 流程，因此不会因为启动服务而改变交叉引用或本地资源行为。若
确实需要使用其他通用 server，可以显式指定：

```powershell
$env:PMT_PANDOC_SERVER_COMMAND = 'C:\path\to\pandoc-server.exe'
papper build html --start-server --server-port 3030
```

仓库在 `scripts/pandoc-server` 中提供了最小的可执行入口。安装了 GHC/Cabal 且有
匹配 Pandoc 3.11 的 Haskell 包时，可以将它构建到项目管理的工具目录：

```powershell
Push-Location .\scripts\pandoc-server
cabal install . --installdir ..\..\.pmt\tools\bin --overwrite-policy=always
Pop-Location
```

Papper 包装后的服务不会要求扩展重复传 Pandoc 参数。使用 `GET /version` 检查服务，
使用 `POST /convert` 并提交 `{"path":"manuscript.md"}` 转换单个文件（省略 path 时默认
使用 `manuscript.md`）；也可以向
`POST /batch` 提交多个 `{"path": ...}`。路径只能位于启动服务的项目目录内。返回的
HTML 会继续经过与 `papper build html` 相同的 PMT HTML 后处理。

## 致谢

- [Pandoc](https://pandoc.org/)
- [pandoc-crossref](https://github.com/lierdakil/pandoc-crossref)

## 支持

- 先查看 [`template/.agents/manuscript-syntax.md`](template/.agents/manuscript-syntax.md)
- 提 issue 时尽量附上最小可复现示例
