[English](README.md) | [简体中文](README.zh-CN.md)

# Papper

用 Markdown 写论文，用 Word 交稿。

Papper 是一个面向 AI 时代的、以 DOCX 为核心输出的学术写作工作流。现在 AI 很擅长起草、改写和整理 Markdown，但很多期刊、编辑和合作者最终仍然要 `.docx`。Papper 解决的正是这个错位问题：你继续用清晰、可版本控制、对 AI 友好的 Markdown 写作，在需要提交的时候再稳定地产出接近期刊工作流的 Word 文档。

CLI、配置、HTML Server、文档后处理和集成层由 Rust 实现。Lua filters、Haskell
Pandoc worker 和 Windows C# MathType helper 保留原实现。通过 `uv tool` 安装
PyPI wheel 后，`papper` 和 `pmt` 直接运行原生可执行文件，构建时不启动 Python。

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
- **内置 Pandoc 工具链**：平台 wheel 内置 Pandoc 3.12、crossref 和持久 HTML worker，无需另外下载；源码开发环境也支持受管工具。
- **保留其他输出**：虽然以 DOCX 为核心，但仍然支持 HTML、LaTeX 和 JSON 输出。

## 你能得到什么

- 用 `papper init` 初始化论文工程
- 用 `papper doctor` 检查环境
- 用 `papper setup` 准备项目本地工具
- 用 `papper build` 构建 DOCX、LaTeX、JSON
- 用 `papper convert` 将已有 DOCX 论文导入 Markdown
- 用 `papper build-reply` 构建审稿回复
- 图、表、公式、章节的交叉引用
- 基于 CSL 的参考文献格式
- 通过 reference DOCX 控制 Word 样式
- 面向 DOCX 的后处理：作者信息、表格行为、样式、行号相关工作流
- `papper build docx` 中，制表符排版的公式段落自动应用 `Para Equation` 样式，继承“正文文本”，段后间距为 0.5 行，使用单倍行距。
  样式的居中、右对齐制表位分别设在 DOCX 第一节正文可用宽度（页面宽度减左右页边距）的一半和末端；移除公式段落上的直接制表位，使其继承样式。修改页边距后需重新执行此步骤。
- Word 不友好图片场景下的 SVG 处理和回退方案
- 需要时支持 MathType 相关的 DOCX 工作流

## 将 Word 论文转换为 Markdown

使用 `papper convert` 导入 DOCX，生成 Markdown 和相邻的 `media` 图片目录：

```powershell
uv run papper convert "测试文档.docx" -o converted
```

命令会生成 `converted/测试文档.md`，并只把 Markdown 仍需要的图片放进
`converted/media`。三个内置 Lua 过滤器依次将 MathType OLE 公式转换为 LaTeX、
展平单行的公式排版表格、把可确认的 Word 书签链接转换为 `@fig:_Ref241620557`
或 `@eq:_Ref241620691` 等 pandoc-crossref 引用。普通表格和无法确认的链接沿用
Pandoc 的结果；公式无法解码时会保留预览图片。

MathType 反向解码复用 LaTeX 转 MTEF 时直接链接的同一份 Rust 公式库。
`papper convert` 在当前 Rust 进程中完成解码，再把结果交给 Lua；wheel
无需独立 MathType DLL 或公式转换可执行文件。

在源码仓库中也可以直接运行 MathType Lua 过滤器，将原生 `papper` 放到 Pandoc 的 PATH：

```powershell
pandoc input.docx -f docx -t markdown -L pandoc/filters/convert/mtef_parser.lua -o converted.md
```

此时 Lua 一次调用原生批量解码入口，使用直接链接的公式库。若 `papper` 不在 Pandoc 的
PATH 中，可将 `PAPPER_EXECUTABLE` 设置为原生可执行文件路径。DOCX 公式转换为
Pandoc Math 节点后，Markdown writer 直接生成数学分隔符，不需要再次反转义。

## 快速开始

### 前置依赖

建议准备以下工具：

1. `uv`，用于安装平台 wheel
2. Papper 平台 wheel，已内置 Pandoc 3.12 和 crossref
3. 行号来源工作流在 Windows 上需要安装 Microsoft Word；其他平台可以使用 `soffice`
4. 可选：Windows MathType，仅 `rust-sdk`、`set-data`、`auto` 或 `both` 方式需要；默认 `rust` 方式可独立工作

平台 wheel 的 `papper setup` 和 `papper init --setup` 在本地验证内置引擎，包含
`--force` 时也不另外下载。源码开发环境可以使用本地编译的 worker、用户目录中的
worker，或 Pandoc 3.11+ 配合独立 crossref；需要时下载受管工具到 `~/.papper/tools`。
可复用缓存和持久构建状态分别保存在 `~/.papper/projects/<project-id>/cache` 与
`~/.papper/projects/<project-id>/work`。项目 ID 由项目绝对路径计算，不同项目不会互相覆盖。
MathType OLE/WMF 预览图、行号来源转换文件等单次构建中间产物存放在系统临时目录，Papper
进程退出时清理。`papper clean` 删除生成结果，并清理当前项目的 work 和缓存，
包括 MathType 公式缓存及旧版 `.pandoc-cache`。
原有项目内的 `.pmt` 和 `.papper` 目录不会自动迁移或删除。

六个原 Python filters 现在由 Pandoc 进程内的 Lua 执行，共享资源查找和 SVG
处理模块。仅 SVGZ 解压与 PNG 像素渲染按需调用独立的原生 `papper-svg` helper；
它不内嵌整份运行时，也不复制或启动完整 `papper` 来执行 AST JSON filter。
DOCX 构建还会在转换子进程的 PATH 中提供内置的 `rsvg-convert` PNG 适配入口，
将 Pandoc 的备用图转换交给 `papper-svg`，同时保留原始 SVG 与 PNG 兼容图片。
无需单独安装 librsvg，也不修改系统 PATH。设置 `docxConvertSvgToPng: true`
时仍按原有行为用 PNG 替代 SVG。

### 源码开发与验证

安装固定版本的 Rust 工具链后运行：

```bash
cargo check --workspace --locked
cargo test --workspace --locked
cargo run -p papper-dev -- worker
uv sync
uv run pytest
```

`uv sync` 安装原生开发 CLI。Python 只用于驱动集成测试并检查 Rust 和现用
Haskell/Lua 组件生成的实际产物。Pytest 每次会话都会编译当前 Rust CLI，测试
不依赖已安装的开发程序是否最新。`tests/legacy/` 仅作为历史归档，不参与测试，
也不会加入开发安装的 Python 搜索路径。修改 CLI 后，运行
`uv sync --reinstall-package papper` 刷新开发
可执行文件。发布 wheel 不包含旧 Python 实现，也不依赖 Python 运行库。
源码集成测试需要 GHC 9.14.1 和 Cabal 3.18.1.0，先用上面的 worker 命令构建
当前锁定的格式配置，避免测试误用以前保留全部格式的开发程序。
完整打包和安装验证分别使用 `cargo run -p papper-dev -- wheel --output dist` 和
`cargo run -p papper-dev -- smoke --wheel <wheel-path>`。

Papper 自有配置使用真实 Rust 字段和受控更新，保留历史 YAML 别名与显式
false/null 的优先级；任意 Pandoc 元数据单独处理。MathType OLE/MTEF 编解码和
LaTeX→WMF 通过安全 Rust API 直接链接，不再使用公式 DLL 或二进制 hex/JSON
C ABI。公式缓存采用 `native-v2` 和构建期引擎指纹，仍验证用户偏好、字体与可选
helper 输入。独立图像 helper 不依赖 `papper-core`，不内嵌运行时资源归档。
PDF 几何提取通过 `mupdf-sys` 0.8.0 直接链接 MuPDF 1.27.2，仅开启 PDF 和
Base14 字体。C 异常包装器将原生错误返回独立 Rust PDF helper，不再动态加载
MuPDF DLL。源码构建需要 C/C++ 编译器与 libclang，见 [DEVELOPMENT.md](DEVELOPMENT.md)。
Windows C# helper 保留 SDK 桥接职责。
打包器仅按原生组件实际 PE imports 和目录分发 Windows CRT，保留跨平台依赖审计。

内置 Worker 保留 Markdown 系列、HTML、LaTeX、DOCX、JSON/native 和文献格式，
不再注册 Org、EPUB、ODT、PPTX、RST 等其他 Pandoc 格式；Lua Reader/Writer
调用也使用这一格式集。源码配置与构建方法见
[scripts/pandoc-server/README.md](scripts/pandoc-server/README.md)。

原来的 `pandoc_manuscript` Python 导入 API 和 `python -m` 工具不随原生 wheel
发布。集成使用原生 CLI 或 Rust workspace 库；旧实现仅供仓库测试对照。
已有稿件和样式配置继续使用，原生服务状态单独保存在 `work/rust-v1`。
后台 HTML 服务从按程序内容哈希保存的独立副本运行，因此服务运行期间也可以执行
`uv tool upgrade papper`，不会锁住 Windows CLI 入口。下一次执行
`papper build html --start-server` 时，会校验服务的实际版本和程序身份：同一项目的
旧版原生服务先正常停止，再在原端口启动已安装的新版，保留项目输出和磁盘缓存；
程序未变化时复用现有服务，不会停止其他项目的服务。本修复之前的版本启动的服务，
在 Windows 上首次升级前仍需停止一次。

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

# 为本次构建指定样式文件
papper build docx paper.md --style-file styles/journal.yml

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
`papper build` 的 DOCX、LaTeX、HTML 和 JSON 目标均支持 `--style-file PATH`。指定后，本次构建只读取该文件作为项目样式，不再自动查找和合并稿件目录及当前目录的 `style.yml`。相对路径以当前工作目录为基准，也支持绝对路径；文件不存在或路径指向目录时会报错。内置语言默认值仍然生效，稿件 YAML 仍优先于所选文件的 `pandocMetadata`。不传此参数时保留原有的自动查找和合并行为。
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
```

完整 CLI 请查看 `papper --help`。

`papper build docx` 的原生交叉引用默认关闭。在 `style.yml` 顶层设置：

```yaml
docxNativeCrossref: true
```

默认值 `false` 完整保留原有 Pandoc 编号和超链接引用行为。开启后，图、表、公式
使用原生 `SEQ` 编号和 `REF` 引用；章节标题使用关联标题样式的 Word 多级列表，
章节编号引用使用 `REF ... \r \h`。章节编号仍遵循 `numberSections`、
`sectionsDepth` 以及不编号标题的设置。常规一级章节前缀使用 `STYLEREF`，项目
序列通过 `SEQ \s 1` 随章节重启。图、表的 `SEQ` 序列名分别取去除首尾空白后的
`figureTitle`、`tableTitle`；标题为空时回退到 `Figure`、`Table`，公式序列名为
`Equation`。
数字制 CSL 参考文献的条目编号使用 `SEQ PapperBibliography`，正文引用编号使用
指向对应编号书签的 `REF ... \h \* MERGEFORMAT`。保留 CSL 的方括号、上标、
页码定位符和连续引用范围分隔符，范围的两端分别使用 REF 域。作者—年份制和无法
识别的文献编号格式保留 citeproc 输出；显式设置 `link-citations: false` 时正文
文献引用仍为纯文本。Word 可以重编号文献条目并更新 REF，但引用分组、排序或
CSL 格式改变后仍需重新构建。
原生引用书签名使用 `PapperRef-` 加 9 位随机小写字母或数字，总长
19 个字符，并在本篇文档内排重；书签起止位置的数字
ID 成对随机化，覆盖正文、脚注和页眉页脚，以降低合并文档时的冲突。
修改文档后在 Word 中更新域；前向引用可能
需要更新两遍。无法识别的自定义编号或模板保留 Pandoc 结果并给出提示。
该选项仅用于主稿 DOCX 构建，其他输出目标和审稿回复继续使用原有流程。

如果需要同时为编辑器或其他扩展启动（或复用）本机 Pandoc HTTP 服务，可以执行：

```bash
papper build html --start-server
```

命令会先请求 `http://127.0.0.1:3030/version`；已有可用的 PMT 服务时直接复用，否则
启动项目绑定的 PMT runtime。需要使用其他通用 server 时才设置
`PMT_PANDOC_SERVER_COMMAND`。服务地址会打印到构建日志，支持 `/`、`/batch`、
`/version` 接口。
Rust 服务的进程状态保存在 `~/.papper/projects/<project-id>/work/rust-v1/server-state.json`，配置与日志分别保存在同目录的 `server-config.json` 和 `server.log`。

这个服务用于高频 Markdown 转换。它会读取当前项目的 PMT defaults 和 metadata，使用
与正常 HTML 构建相同的 `pandoc-crossref` 与 Lua filter 链。带 `--start-server` 的首次
HTML 构建也通过 `/convert/raw` 完成，后续构建复用同一个 worker。Markdown 可以位于
工作目录项目之外；服务仍绑定工作目录项目，样式与资源查找保留源文件所在目录的上下文。若
确实需要使用其他通用 server，可以显式指定：

```powershell
$env:PMT_PANDOC_SERVER_COMMAND = 'C:\path\to\pandoc-server.exe'
papper build html --start-server --server-port 3030
```

仓库在 `scripts/pandoc-server` 中提供了最小的可执行入口。安装了 GHC/Cabal 且有
匹配 Pandoc 3.11 的 Haskell 包时，可以将它构建到项目管理的工具目录：

```powershell
Push-Location .\scripts\pandoc-server
cabal install . --installdir "$HOME\.papper\tools\bin" --overwrite-policy=always
Pop-Location
```

Papper 包装后的服务不会要求扩展重复传 Pandoc 参数。使用 `GET /version` 检查服务，
使用 `POST /convert` 并提交 `{"path":"manuscript.md"}` 转换单个文件（省略 path 时默认
使用 `manuscript.md`）；也可以向
`POST /batch` 提交多个 `{"path": ...}`。相对路径基于启动服务的项目目录解析，也接受
指向项目外 Markdown 的绝对路径或相对路径。编辑器可以向 `/convert/raw` 提交
`{"path":"<源文件路径>","text":"<当前编辑器文本>"}`，无需保存源文件。返回的
HTML 会继续经过与 `papper build html` 相同的 PMT HTML 后处理。

## 维护者发布与构建缓存

发布 wheel 时，Typst CLI 必须与 `Cargo.lock` 中 `typst-library` 的版本一致。
CI 会从官方发布包安装对应版本。本地打包前，先运行
`uv run --script tools/ci/prepare-typst.py`，再把仓库的 `.pmt/typst/bin`
目录加入 `PATH`。打包器会拒绝缺失或版本不匹配的 CLI，清除 MiTeX 规格的 release
构建缓存，并显式启用 `papper-platform/generate-mitex-spec`，避免新编译的 wheel
使用上游旧的预生成规格。安装包检查要求带圆圈的数学运算符全部转换为 MathType
对象，不能退回 Word 原生公式。

提交修改后，使用 `uvx bump-my-version bump patch` 更新版本，再运行
`git push origin main --tags`。**Publish to PyPI** 的 `main` 运行会构建并验证
Windows、macOS 和 Linux wheel，保留七天的构建产物；版本 tag 运行等待同一提交的
`main` 构建完成后，直接复用这些 wheel 发布，不再重复编译三个平台。
找不到对应构建、构建失败或产物已过期时，tag 运行会重新构建；等待超时则可以在
`main` 构建完成后重跑 tag 工作流。手动选择 `main` 运行工作流也可以预热缓存。

Rust 缓存键包含源码和嵌入资源，避免只修改源码时一直恢复旧编译产物。Haskell 缓存
同时保存依赖、包索引、`.pmt/pandoc-worker` 和 `.pmt/pandoc-source`，其中 Linux
也会保留本地 Pandoc 编译产物。macOS 缓存固定版本的 GHC/Cabal；Linux 缓存容器内
固定版本的 Rust 和 GHC/Cabal 工具链。三个平台构建成功后，工作流只保留每个平台、
每类发布缓存的最新快照，清理旧发布缓存；其他工作流和 tag 下的缓存不受影响。

## 致谢

- [Pandoc](https://pandoc.org/)
- [pandoc-crossref](https://github.com/lierdakil/pandoc-crossref)

## 支持

- 先查看 [`template/.agents/manuscript-syntax.md`](template/.agents/manuscript-syntax.md)
- 提 issue 时尽量附上最小可复现示例
