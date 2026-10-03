# Rust 迁移架构检查与修正

更新日期：2026-10-03。检查基线为 `rust-migration` 分支的 `9f4dabc`。

2026-10-02 的检查确认了第一轮迁移中沿用 Python 接口的成本，并删除冗余 `repair-math`。
本轮按用户的新要求落实 Rust 公式直连、Lua filters 和类型化配置。下面记录当前实现；
`9f4dabc` 的 wheel 身份与性能数据保留为历史记录，不能用于证明本轮新程序的安装或性能结果。

## 1. MathType / LaTeX→WMF 已改为 Rust 库直连

第一轮迁移沿用了 Python 使用的 C ABI：公式字节转 hex、JSON 编解码、`libloading` 加载
`mathtype_rust.dll`，还在运行时计算 DLL 和公式源码摘要。这些边界不适合两个自有 Rust 库。

当前路径为：

```text
papper Rust
  → papper-platform 的安全 Rust 公式门面
  → mathtype-rust 的 owned OLE/MTEF 编解码 API
  → 同一固定 Git revision 的 latex2wmf 渲染 API
  → owned 字节、预览和 Result 返回值
```

`mathtype-rust` 已公开 `EquationPayload`、`encode_latex`、`mtef_from_ole` 和
`decode_ole`，保留自动模式的源 TeX / 结构化回退。Papper 和公式库使用同一来源及
revision 的 `latex2wmf`，不通过引用私有源码文件或构建时改写源码接入。

`crates/papper-platform/src/native.rs` 直接调用这些 Rust API。公式二进制不再通过
JSON/hex C ABI 传输，不再解析动态符号、管理跨库裸指针或重复加载公式 DLL。普通公式
错误继续以 `Result` 进入逐公式恢复；上游渲染器 panic 仍在调用边界隔离，避免改变整篇
构建的失败恢复策略。Lua 解码映射使用的对象摘要不属于已经删除的公式二进制传输协议。

打包器删除独立 `cdylib` 构建、`--formula-library` 参数和公式 DLL 资源，继续交付 `.eqp`、
字体/第三方许可及 C# helper。公式缓存使用 `native-v2` 和构建期内容指纹，包含实际编译
的公式实现、渲染器及资源、门面、Cargo.lock 和目标平台；运行时继续验证用户偏好、字体
及 helper 等可变输入，不再扫描公式库文件和整份源码。旧 DLL 缓存不会被新实现误用。

PDF 后端已进一步改为 `pdf_oxide` 0.3.78，删除 MuPDF C FFI、`mupdf-sys`、bindgen
和 libclang 构建要求。独立 PDF helper 保留页面文本、元数据和行坐标接口；接入说明见
[Rust PDF 文本后端](PDF_BACKEND.md)。此前的 MuPDF 与 Reader/Writer 体积数据保留于
[原生 PDF 与 Worker 体积检查](NATIVE_PDF_WORKER_SIZE_REVIEW.md)，属于历史实验。
Windows 文件身份和 Office COM 仍需要系统接口。

## 2. 六个旧 Python filters 已迁移到 Lua

第一轮为每个旧 Python filter 创建完整 `papper.exe` 别名。跨盘 hardlink 失败时，
DOCX 两个、LaTeX 三个别名会复制约 180 MB / 270 MB 的历史 90 MB 主程序，并分别启动
进程和完整读写 AST JSON。本轮已经删除该别名机制、隐藏 JSON filter 入口及对应 Rust
AST filter 实现。

六个 filters 现在是 Pandoc 进程内的 Lua：

- `docx/svg_embed_images.lua`、`docx/svg_to_png.lua`、`docx/to_mathbfit.lua`
- `latex/emf_to_pdf.lua`、`latex/resource_move.lua`、`latex/table_convert.lua`

两个新共享模块 `shared/filter_resources.lua` 和 `shared/svg_resources.lua` 负责资源
查找、SVG 资源处理及缓存等复用逻辑。它们与既有 Lua filters 按原有管线顺序执行。
LaTeX 宽图直接使用 `pandoc.write`，不再为每个 Figure 启动一次 Haskell/Pandoc writer。
用户自定义的外部 JSON filters 继续按 Pandoc 的标准协议执行。

Lua 处理 AST 和 SVG 资源规则；仅 SVGZ 解压与 PNG 像素渲染交给独立 `papper-svg`。
这个 Rust helper 不依赖 `papper-core`，不内嵌 runtime.zip，也不承载 AST JSON filter，
避免调用图像处理时装载或复制整份产品。它按需要执行，图像输出继续保留 DPI、scale、
width、字体、per-image 控制及缓存失效行为。

默认 renderer 使用构建期 ID；显式替换 `PAPPER_SVG_RENDERER` 时，按该程序实际字节生成
请求级摘要，避免更换程序后复用旧 PNG。中文路径与环境变量通过 Pandoc 的 Haskell API
读写，绕过 Windows Lua C API 的 ANSI 路径限制。根外绝对资源改用内建 SHA1 的短摘要
生成内部缓存名称，首次会新建缓存，不影响源文件或最终图片内容。

开发安装与 release 都单独编译图像 helper。整工作区一起构建会因 Typst 的 Cargo feature
合并切换 `flate2` 压缩后端，改变 PNG 的 IDAT 字节；13 项解压后的完整像素数据仍一致。
独立 Release helper 的 13 项 PNG 完整字节差分也已重新通过，未修改像素渲染算法。

## 3. Papper 配置已改为真实 Rust 字段

`PmtSettings.values: Map<String, Value>` 已删除。16 个自有设置使用 `bool`、`Option`、
`ConversionMethod`、`SvgBackend`、`LineNumberMode` 和 `LengthInput` 等类型。
`fields()`/`provided()` 只读；CLI 和派生配置通过受控更新 API 修改设置，不能直接写入
拼错的键或绕过字段校验。reply 覆盖同样使用 typed fields 与显式提供集合。

历史 camelCase/kebab-case/snake_case 别名、false/null、YAML anchors/merge、语言和
项目/reply 优先级保留在配置边界。effective metadata 的旧 JSON 结构仍可读写，但
反序列化必须通过配置校验，不能直接把非法字段值放进内部状态。命名 Word 样式保持
用户配置顺序；任意 Pandoc 元数据和可扩展样式属性保留映射。

DOCX 样式、页边距、公式 tab、行号/页码，以及 HTML 参考样式直接消费 typed settings。
产品 CSS 路径不再把已经验证的整份样式字典序列化后重新解析。原始 JSON 接口只保留在
外部文档格式、配置序列化和对照工具等实际边界。

审稿回复仍保留三个独立 Pandoc probe。它们的独立性有 citeproc 编号/排序理由，本轮
没有将其合成一份文档；持久 worker 复用可以另行测量后实施。

## 4. 冗余 repair-math 保持删除

它来自旧文件 `template/.agents/word-manuscript-fix/scripts/unescape_latex.py`。旧 CLI
没有该命令，第一轮迁移把过时模板脚本提升为产品功能没有必要。上一轮已删除命令、
参数、dispatch、`repair_math.rs` 和两项专用测试；本轮没有重新引入。

`convert` 先解码 MathType，把映射传给 `mtef_parser.lua`，直接构造 `pandoc.Math`；
DOCX reader 的 OMML 同样生成数学节点。Markdown writer 已输出 `$...$` / `$$...$$`，
不需要之后做正则反转义。历史脚本仅留在冻结的 `tests/legacy/`。

现有真实 MathType 往返合同验证 `$x^2+y^2$`、媒体及用户文件保全、与 Python 导入器
输出一致，以及独立 Lua 解码。手工 Markdown 的字面 `\$...\$` 不等同于 DOCX 数学
对象，不能用旧脚本修改代码块或合法 `\%` / `\&`。

## 5. portability 保留为发布模块并缩小公式相关职责

`tools/papper-dev/src/main.rs` 的 `mod portability` 引入仓库自己的发布模块。它在
组装 wheel / runtime.zip 前执行，文档构建命令不会运行它。它先复制原始二进制到
staging，再只修暂存副本：

| 平台 | 发布处理 |
| --- | --- |
| Windows | 检查 PE 普通/延迟导入，按实际 imports 和使用目录分发官方 VC release CRT，附带来源/许可 |
| Linux | 检查 ELF 依赖，补非系统共享库，修正 DT_NEEDED 和 `$ORIGIN` RUNPATH |
| macOS | 补 dylib，改为 `@loader_path`，去掉构建机 rpath，重新签名及验签 |

公式 DLL 的 staging 已删除。Windows 不再无条件预置三项 CRT 或重复复制到
`mathtype/bin`，而是沿实际依赖图定位需要的文件和组件目录。Haskell worker、
保留 C# helper 及图像 helper 的分发仍需审计；PDF 后端现已使用直连 Rust PDF Oxide。
模块继续生成
`bin/native-notices/dependencies.json` 和许可记录，不假设开发机 PATH/Homebrew
目录也存在于用户机器。

## 6. 验证记录与边界

本轮已经独立完成：

| 验证 | 已确认结果 |
| --- | --- |
| Lua 与冻结 Python 的 AST 差分 | 27 项一致 |
| PNG 像素与已有渲染结果 | 13 项一致 |
| typed settings 与冻结 Python 的配置差分 | 新建 28 项全部一致，包含有效值、显式集合、reply 和非法输入拒绝 |
| `papper-core` 行为测试 | 13 项通过 |
| 整个 Rust 工作区行为测试 | 20 项通过 |
| 工作区 Clippy、fmt 与 diff 检查 | 最终修改后通过，Clippy `--all-targets -D warnings` |
| `papper-document` 编译检查 | 通过 |
| 无公式 DLL 的 MathType DOCX/cache 契约 | 原有定向测试通过，OLE/WMF 字节、失败保全及损坏缓存恢复均一致 |
| MathType 上游默认偏好对照 | 3 项既有测试通过 |
| Lua 图片缓存与恢复契约 | 2 项新测试通过，包含中文目录及恢复原时间戳的内容编辑 |
| 本轮完整 pytest | 326 passed、14 skipped、2 个既有 warnings，193.13 秒；未修改快照或新增 skip |

配置差分记录位于 `output/rust-review/typed-metadata-differential/summary.json`。它是
本轮新验证，不冒称重跑了初次迁移的旧脚本。差分工具仅在 ignored output 中，不进入
产品制品。完整回归日志为 `output/lua-native-refactor/full-suite-verified.log`，JUnit 为
同目录 `full-suite-verified-junit.xml`。新增的两个 Lua 行为测试替代已删除的两个
`repair-math` 测试，使当前 pytest 总数回到 326；该结果来自本轮实际运行。
最终 Windows wheel 已通过隔离 `uv tool` 安装验收，移除了所有开发 loader/resource 覆盖，
并限制 PATH。实际运行 init/setup/doctor、HTML 普通及 Server 构建、MathType DOCX 与
导入、Lua SVG→PNG、LaTeX/JSON、审稿回复、PDF 引擎、缓存失效和 clean/退出。新图片
案例验证 40×20 SVG 按 scale=2 生成包含完整像素数据的 80×40 PNG，三个输入文件保全。
记录为 `output/lua-native-refactor/wheel-smoke-final.log`。

交付 wheel 为 `output/lua-native-refactor/wheel/papper-0.9.2-py3-none-win_amd64.whl`，
80,808,757 字节，SHA-256 为
`b0ec1e2e616f09ef75c0a8d18e493e2b70887c2029ba030187c6d4607b0bb021`。
原生主程序 SHA-256 为 `170e3de8d53cd5975f222d37ec86f2f9396b9145f3df0e331acb7717cd0123ca`，
runtime ID 为 `a0717f352578625ccf36b066fda66d26f98f16e231d2163d4c19d97c8ff2d2b6`。
实际归档 CRC 和组件清单见 `wheel-report-final.json`，没有 Python 产品脚本或公式 DLL。

MathType 子模块的安全 API 与默认偏好嵌入以本地 `papper-safe-api` 分支提交
`a4ac2700a24467a779d51215ad830818d559d3f5` 固定；子仓库干净。源补丁和发布顺序见
`patches/README.md`，远程发布父仓库前应先使该子模块 commit 可获取。当前没有推送。

本轮最终制品对真实论文执行完整 `build html --start-server` 配对重测。每个场景
15 对正式样本及 2 对预热：正文编辑中位从 Python **558.44 ms** 到 Rust **243.70 ms**，
减少 **56.4%**；内容不变仍发布 HTML 从 **354.96 ms** 到 **47.18 ms**，减少 **86.7%**。
34 对完整输出一致，原稿保持不变。该结果只适用于已预热 Server 的这份输入，详见
[新制品基准](RUST_LUA_REFACTOR_BENCHMARK.md)；不沿用 `9f4dabc` 的旧程序百分比。

上一轮仅删除 `repair-math` 后的历史完整回归为 **324 passed、14 skipped、2 个既有
warnings**，170.18 秒，日志在 `output/rust-review/full-suite-verified.log`，JUnit 在
同目录 `full-suite-verified-junit.xml`。初次迁移 `9f4dabc` 则为 326 passed；差的两项
是删除的冗余命令专用测试。两轮均未更新 HTML/DOCX 快照或新增 skip。这些历史结果
不替代当前更改后的完整测试和安装验证。

macOS/Linux 实际构建、加载和安装仍由对应平台 CI 验收；可选真实 MathType SDK
方法仍需完整真机验证。本轮热构建性能有上述配对数据；其他路径及冷启动的稳定
提速幅度没有据此推断。
