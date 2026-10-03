# PDF 与公式渲染依赖的 feature 检查

检查日期：2026-10-03。版本以当前 `Cargo.lock` 为准。

检查当前 Cargo 解析结果、锁定版本的 `Cargo.toml` 和实际调用代码，区分已关闭的
feature、可配置但当前行为需要的 feature，以及上游没有提供的模块开关。

## PDF 文本提取

`pdf_oxide` 0.3.78 使用 `default-features = false`，不额外开启 feature。
按产品只需要非加密 PDF 的范围关闭 `legacy-crypto`，锁文件移除 `md-5` 0.11.0。
普通中文 CMap、Unicode 元数据、视觉行和审稿回复行号的验证继续保留。

`legacy-crypto` 对应可选 MD5 依赖及旧版 PDF 安全处理。AES、SHA-2 等仍是上游
非可选依赖，因此这次修改不代表移除整个加密模块。旧版 release 的 PDB 链接记录中
`md5` 对象仅约 2.4 KB；关闭该 feature 的总收益还包含调用方代码，不能把整个
`pdf_oxide` 的 4.61 MB 都归因于加密支持。

## Typst

当前使用 Typst 0.15.1、`typst-as-lib` 0.16.0。

| 库 / feature | 当前解析结果 | 可否直接关闭 |
|---|---|---|
| `typst`、`typst-library`、`typst-layout`、`typst-svg` | 没有 Cargo feature 列表 | 没有 `math-only`、禁用参考文献、插件、PDF 图形等现成开关 |
| `typst-as-lib/typst-kit-embed-fonts` | 开启 | 可以，但需要替代默认字体，并移除受此 feature 控制的 API 调用 |
| `typst-as-lib/typst-kit-fonts` | 开启，启用 `typst-kit/scan-fonts` | 可以，但当前依赖它发现系统字体，包括中文回退和用户指定字体 |
| `typst-as-lib/typst-html` | 关闭 | 已关闭；它不控制 `typst` 对 `typst-html` 的非可选依赖 |
| `typst-as-lib/packages`、`reqwest`、`ureq` | 关闭 | 已关闭，没有可继续删除的下载功能 |
| `typst-kit` 的默认 features | 空 | 设置 `default-features = false` 不会再削减核心库 |
| `hayagriva/archive`、`biblatex` | 由 Typst 非可选依赖开启 | 不能从 Papper 顶层直接减掉，需修改上游依赖和功能注册 |
| `wasmi/simd` | 由 `typst-library` 显式开启 | 不能从 Papper 顶层直接减掉 |

Cargo features 会取各依赖路径的并集。Papper 顶层额外声明相同库的
`default-features = false`，不会关闭 `latex2wmf` 或 Typst 已经开启的 features。
当前链接的是固定 Git 修订的 `latex2wmf`，不是 `scripts/latex2wmf` 中的参考副本；
只修改参考副本的 manifest 不会改变产品二进制。

### 字体裁剪边界

旧版 release 中实际嵌入了 Typst 的 17 个 TTF/OTF 默认字体，共 9,683,068 bytes；
另有始终内嵌的 XITS Math 548,096 bytes。字体已用源文件字节与 exe 精确匹配，
合计约 10.23 MB，不包含 PDF 的 Foxit 字体。

Papper 当前默认数学字体是 `New Computer Modern Math`。关闭整套 Typst 字体嵌入
后仅保留 XITS，会改变默认输出或依赖目标机器恰好安装 New Computer Modern。
不能把这种变化当作无行为影响的 feature 清理。

可行的后续方案是让 `latex2wmf` 支持一个显式的精简字体集：保留默认数学字体需要的
字重、必要的文本字体和 XITS，继续扫描系统字体处理中文和自定义字体。
这需要调整上游封装的字体加载代码，并对普通、粗体、文本、中文、上下标和自定义
字体公式验证实际 SVG/WMF 输出。只切换 `include_embedded_fonts(false)` 的运行时选项
不能可靠保证字体资源从二进制中移除，应该关闭对应 Cargo feature。

## RaTeX

当前使用 `ratex-parser`、`ratex-layout`、`ratex-svg` 0.1.14。

| 库 / feature | 当前解析结果 | 可否直接关闭 |
|---|---|---|
| `ratex-parser`、`ratex-layout` | 没有 Cargo features | 已是公式解析 / 排版库，没有额外的文档模块开关 |
| `ratex-svg/cli` | 关闭 | 已关闭，不编译其命令行功能 |
| `ratex-svg/standalone` | 由 `embed-fonts` 开启 | 当前 WMF 转换需要字形轮廓路径，不能直接去掉 |
| `ratex-svg/embed-fonts` | 开启 | 可以改成外部字体目录，但必须保留 `standalone` 并配置 `font_dir` |
| `ratex-svg` 的默认 features | 空 | 设置 `default-features = false` 不会进一步减小当前功能集 |

当前封装设置 `embed_glyphs = true`、`font_dir = ""`。不启用 `embed-fonts` 时，
需提供 KaTeX TTF 字体目录，否则字形可能回退到 SVG `<text>`，不能维持现有 WMF
轮廓转换。实际嵌入的 KaTeX 字体共 513,664 bytes，相比 Typst 字体收益较小。
把字体移到内嵌 runtime ZIP 可以压缩这部分数据，但仍需分发字体和修改加载路径。

## 结论

本次直接关闭 PDF 的 `legacy-crypto`；其余不需要的可选下载 / CLI 功能已经关闭。
Typst 没有现成的数学专用编译开关，较大的可行裁剪点是精简字体集，其次才是维护
上游模块裁剪或移除某个渲染后端。当前保留两个可配置的公式后端和默认字体行为。

参考：

- [Typst 0.15.1 源码 manifest](https://github.com/typst/typst/blob/v0.15.1/crates/typst/Cargo.toml)
- [typst-as-lib 0.16.0 源码 manifest](https://github.com/Relacibo/typst-as-lib/blob/v0.16.0/Cargo.toml)
- [RaTeX SVG 源码与构建说明](https://github.com/erweixin/ratex/tree/main/crates/ratex-svg)
- [Cargo feature 合并规则](https://doc.rust-lang.org/cargo/reference/features.html#feature-unification)

## 验证

- `cargo check --offline -p papper-cli -p papper-dev` 通过。
- `uv run --no-sync python -m pytest tests/test_rust_reply_contract.py -q`：13 项通过。
- `cargo metadata --locked --offline` 中 `pdf_oxide` 的 features 为 `[]`，锁文件不再有 `md-5`。
- `cargo fmt --all -- --check` 与 `git diff --check` 通过。
- 静态 CRT release 构建通过；直接调用 release 的 `__pdf_extract`，中文文本与 Unicode
  标题保持正确。构建使用隔离的 runtime ZIP，保留当前已裁剪的 Pandoc worker，仅更新
  PDF feature 的来源记录；其他条目的解压内容与原归档一致。
- 并行打包流程随后刷新了工作区 release，feature 来源记录同样为 `[]`。对最终本地
  快照再次验证中文提取：EXE 从 89,256,960 bytes 变为 89,245,184 bytes，减少
  11,776 bytes。两个内嵌归档的内容只在 PDF feature 记录上不同，归档本身减少 15 bytes。
  这些是本地构建快照，不应把此前 Pandoc 裁剪的 MB 级收益归到本次 PDF feature 上。
