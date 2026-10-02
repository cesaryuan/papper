# Rust 迁移实施报告

日期：2026-10-02

本次实施基于 `55f0d9c`，位于 `rust-migration` 分支。目标是替换 Python 产品运行时，保留 Lua filters、Haskell worker 和 C# MathType helper。原设计见 [RUST_MIGRATION_PLAN.md](RUST_MIGRATION_PLAN.md)。

## 已实现的产品路径

| 模块 | 实现与交付 |
| --- | --- |
| CLI 与项目命令 | 原生 `papper` / `pmt`，支持 init、setup、doctor、build、convert、build-reply、clean、distclean；另将模板 Python 公式修复脚本替换为 `repair-math` |
| 配置与元数据 | Rust YAML 配置、字段默认值、别名、显式覆盖、语言与环境变量优先级、参考 DOCX 样式解析 |
| HTML CLI 与 Server | Rust HTTP 服务、完整命令客户端、项目身份与进程状态、后台启动、持久 Haskell worker、配置重载、失败恢复及退出 |
| HTML 缓存 | 不可变元数据文件、依赖图、远程 CSL 条件验证、文件字节/摘要、有限 HTML 结果缓存；每请求验证依赖与缺失的高优先候选 |
| HTML 后处理 | 作者区、表格边距、样式与参考 DOCX 继承，兼容已有快照 |
| DOCX 后处理 | OOXML 包修改、作者信息、表格、修订与公式排版、样式、页边距、页码、行号与原生交叉引用；保留未知部件和二进制对象 |
| 原 Python filters | 六个 JSON filters 改为原生 Rust，实现 SVG、图片嵌入、公式字体、LaTeX EMF/资源/表格处理 |
| MathType 与导入 | Rust 调用已有公式 C ABI，处理 OLE/MTEF/WMF、字体/偏好、缓存与转换后发布；DOCX 导入由保留 Lua filters 配合原生解码完成 |
| 审稿回复 | TXT/DOCX、正文编号和引用、行号映射、真实 PDF 几何提取、Office 行号来源转换、MathType 公式 |
| wheel 与发布 | Rust wheel builder，原生入口、内嵌资源、按摘要解包、依赖修复和许可证/来源；Windows/macOS/manylinux 发布矩阵及安装 smoke |

主要代码位于 `crates/`，原生开发与打包工具位于 `tools/papper-dev/`。产品源码和资源目录 `src/`、`pandoc/`、`template/` 中已无 Python 产品脚本。

保留组件继续承担既有职责：Haskell worker 运行 Pandoc、citeproc 和内嵌 crossref；Lua 执行文档变换；C# helper 负责 Windows MathType SDK 桥接。Rust 管理它们的资源、配置、协议和生命周期。

## Python 与安装边界

旧实现迁至 `tests/legacy/`，供既有测试和输出差分对照使用，不进入正式 wheel，不被产品 CLI 或 Server 调用。对照实现仅调整定位资源的路径；初始化测试的一项过时脚本断言改为合并输出字节契约。HTML/DOCX 快照未改写。移除测试对照的条件记录于 [tests/legacy/README.md](tests/legacy/README.md)。

产品 `dependencies` 为空。Python 依赖仅在 `dev` 组中；`tools/papper_build.py` 是源码安装所需的 PEP 517/660 构建桥接，委托 Rust 打包，不安装到产品运行路径。原来的 Python 导入 API 和 `python -m` 工具从原生 wheel 移除，集成使用 CLI 或 Rust 库。

运行 HTML 服务的项目在升级前执行 `papper clean` 停止自己的后台进程，保留可复用缓存，升级后再构建并启动新程序。当前没有实现在线替换已经运行的 Rust 服务进程；不能让旧进程自动获得新二进制的业务代码。

安装方式仍为 `uv tool install papper`。wheel 的 scripts 中放置原生可执行文件；Windows 上 uv 将其复制到工具入口目录。引擎、动态库和模板内嵌后按归档摘要解包到 `PAPPER_HOME/runtime/<digest>`，首次成功发布完成标记后复用。普通产品命令直接运行 Rust，不因 uv 的安装环境而启动 Python。

源码开发使用 `uv sync`、`uv run pytest`。变更 native CLI 后使用 `uv sync --reinstall-package papper` 刷新开发入口。发布构建与验证：

```powershell
cargo run --locked -p papper-dev -- wheel --output dist
cargo run --locked -p papper-dev -- smoke --wheel <wheel-path>
```

本地复用已编译组件可加 `--prebuilt --embed`。正式 CI 从相同的固定源码构建 Haskell worker、公式库和 C# helper，并在内嵌前审计依赖。

## 测试与数据保全

迁移前基线为 **259 passed、14 skipped**，日志位于 `output/rust-migration/baseline/`。
最终完整运行：**326 passed、14 skipped、0 failures/errors**，168.52 秒，命令为：

```powershell
uv run --offline pytest tests -q --basetemp=output/rust-migration/full-suite-verified --junitxml=output/rust-migration/full-suite-verified-junit.xml
```

见 [完整日志](output/rust-migration/full-suite-verified.log) 和 [JUnit](output/rust-migration/full-suite-verified-junit.xml)。
14 个跳过项沿用基线：Windows 无法执行 POSIX 信号契约 1 项，以及依赖严格上游版本对照环境的 13 项。
没有新增 skip，没有更新任何 HTML/DOCX 快照。两项 warnings 是原有 tarfile 解包弃用提示。

初始化合并测试原本只断言两个资源文件存在，其中 Python 公式修复脚本已被原生命令替换。
测试现改为保留用户文件原始字节，并比较其余初始化模板的完整文件树与字节内容；没有删掉
初始化合并契约。原生 `repair-math` 另以实际 CLI 验证公式修复、源数据保全与深 Unicode 路径。

Windows 原生 wheel 已通过隔离 `uv tool install` 与完整安装 smoke：PDF 原生库、所有输出目标、MathType 导出/导入、引用缓存、未保存/磁盘编辑、回复及清理退出均通过。安装后的 `papper`/`pmt` 与 wheel 内原生 PE 字节一致，没有 Python shim。

最终制品为 [papper-0.9.2-py3-none-win_amd64.whl](output/rust-migration/native-wheel-final/papper-0.9.2-py3-none-win_amd64.whl)，82,130,648 字节，SHA256：
`a40ca52992db4a9d9bc0faac11cf64c891c53114fd08175c45fdf3ad2588f9a3`。
原生 `papper.exe` SHA256 为 `aa9db7f4e7bf00c0cc51eed10adc7db38389ff0f8c12356d91764018aece06d8`。
wheel 没有 `.py`、`.pyc`、`.pyo` 或 `.pth`，`Requires-Dist=[]`；`Requires-Python>=3.11` 是保留的安装前端兼容声明，不表示原生构建命令启动 Python。
库存和入口核验见 [wheel-report-final.json](output/rust-migration/native-wheel-final/wheel-report-final.json)，
最后一次安装验证日志见 [smoke-metadata-final.log](output/rust-migration/native-wheel-final/smoke-metadata-final.log)。
性能实验使用的上一 wheel 摘要为 `1ff9325acc4334d40127c86fca7e5cccf8360a82eedbd92a828604cc7ad4e446`，保留在 `prior-metadata/`。
最后一次更新仅改变 wheel 的 `METADATA` 和 `RECORD`，使包内 README 与项目一致；两个原生入口、内嵌资源和 worker 字节均不变，安装 smoke 再次通过，因此下述性能结果仍对应最终交付程序。

Rust 工作区的 **23 个测试**已通过，验证配置优先级、YAML 合并、样式/尺寸、输出转换、下载校验/恢复等真实行为。见 [Cargo 日志](output/rust-migration/cargo-test-final.log)。
`cargo clippy --workspace --all-targets --offline -- -D warnings` 已通过，见 [Clippy 日志](output/rust-migration/clippy-final.log)；格式和 `git diff --check` 通过。

新增原生契约直接执行 Rust CLI、真实 Haskell 引擎、公式库和 HTTP 服务，覆盖输出快照、编辑失效、远程 CSL、进程失败恢复、文档导入、审稿回复、未知 DOCX 部件保全与清理边界。另覆盖以下本次发现的真实回归：

- A → B → A 头信息切换后仍读取正确的不可变元数据文件。
- 次要源文件使用自己的资源路径；后来创建的优先图片和恢复 mtime 后的字节编辑会失效。
- 同时首次启动两次 CLI 时保存真实服务 PID，失败子进程不会提前中止等待；clean 退出 server 和 worker 并保留项目缓存。
- Windows 原子发布在瞬时共享锁和超出 MAX_PATH 的 Unicode 路径下保持完整输出。
- 嵌入 CSS 的本地 `@import`、图片资源变化会失效；无法可靠验证的 CSS 依赖关闭整份 HTML 复用。
- 启用资源内嵌后，正文中的远程图片变化不能被整份 HTML 复用遮住；普通非内嵌 URL 继续允许缓存。
- Word 行号来源转换使用私有副本，仅关闭自己打开的文档，没有调用 `Application.Quit()`。

## 性能测量边界

性能结论使用完整原生 CLI，区分热 Server 正文编辑、内容不变、冷启动和直接 HTTP。源码 release 可执行文件与安装 wheel 的实际可执行文件分别测量，不将较小的源码构建结果冒充内嵌 wheel 的交付性能。

测量中定位并修复了依赖图反复展开，以及真实论文图片超过字节预算导致整个缓存反复清空的问题。大图片只保留可靠文件签名与摘要；小型模板/配置保留字节缓存。Windows 签名包含文件 identity 和 ChangeTime，Unix 包含 inode、ctime、mtime 与尺寸，避免单纯 mtime/size 快捷检查遗漏编辑。

完整 CLI 仍有 Windows 创建进程及装载可执行映像的成本。独立探针测得完整 `--version` 与 `--help` 约 39 ms，HTTP `/version` 约 0.7 ms；不能将这段差值归因于 HTTP 或断言一定来自杀毒扫描。安装制品也要实测它的映像大小影响。

源码 release 的正式对照已完成，报告位于
[summary.md](output/benchmarks/rust-migration-digestcache-final/20261002T161134.345033Z/summary.md)，
原始样本位于同目录 `results.json`。冻结可执行文件 SHA256 为
`e5161a22c197eb78639ba53720e15287f87af2528d01d3f226fa77cb321dd136`。

| 完整命令场景 | 原 Python CLI 中位 | Rust 源码 release 中位 | 时间减少 |
| --- | ---: | ---: | ---: |
| 模板：正文编辑 | 436.23 ms | 113.05 ms | 74.1% |
| 模板：内容不变 | 364.36 ms | 48.92 ms | 86.6% |
| 真实论文：正文编辑 | 567.76 ms | 242.74 ms | 57.2% |
| 真实论文：内容不变 | 366.96 ms | 51.74 ms | 85.9% |

每场景 15 组配对样本，合计 120 次正式完整 CLI 调用、68 组完整 HTML 对照全部一致，仅统一文件换行与末尾换行。
正文和资源源文件摘要保持不变。两版本复用同一基线 Haskell worker；原生客户端传递
defaults/resource-path，不通过特殊 server command 或预写 metadata 来绕过完整配置。
更新检查按报告记录的本地缓存/禁用边界处理，避免无关网络干扰。真实论文编辑 p95
为 Python 621.98 ms、Rust 268.24 ms。单次冷启动也记录在报告中，但不从单样本推断稳定提速。

最终安装制品在隔离 `uv tool` 安装后，以原生入口及其实际内嵌 runtime/worker 测量，
没有 `PAPPER_RESOURCE_ROOT` 开发资源覆盖。正式 15 组配对结果：

| 实际安装 wheel：真实论文 | 原 Python CLI 中位 | 安装后的 Rust 中位 | 时间减少 |
| --- | ---: | ---: | ---: |
| 正文编辑 | 560.11 ms | 241.20 ms | **56.9%** |
| 内容不变 | 360.06 ms | 47.36 ms | **86.8%** |

真实论文正文编辑 p95 为 Python 629.02 ms、Rust 275.53 ms；不变内容 p95 为
429.67 ms、74.70 ms。合计 60 次正式完整命令调用、34 组完整 HTML 对照全部一致（仅统一换行），
源文件摘要保持不变。测量用原生入口 SHA256 与前述最终 wheel 原生入口一致；包内 worker
SHA256 为 `4adbda67d17fc525a92cf9cd493f40ca0b202bd63ca7bd12803cf52c7eecca60`，
与源码正式对照的基线 worker 一致。实际制品达到连续热构建减少 50% 的目标。

结果见 [正式安装制品报告](output/benchmarks/rust-migration-wheel-final/20261002T165438.260445Z/summary.md)，
方法、摘要与边界也记录于受版本控制的 [RUST_MIGRATION_BENCHMARK.md](docs/RUST_MIGRATION_BENCHMARK.md)。
初始 5 组配对（55.2% / 87.2%）也保留；最终采用独立完整 15 组实验，不混合轮次或删除较慢样本。

这些结果针对已经启动的 Server。新鲜冷启动和首次资源展开另行记录；本次没有证明冷启动
更快，不能将热构建百分比套用到所有命令。源码 release、安装制品与直接 HTTP 测量分别报告。
失败或不完整的中间轮次保留原始状态，不作为最终性能结论。

## 平台与发布状态

Windows 使用真实 worker、公式库、MuPDF 和 C# helper 审计 PE 依赖，暂存官方 Visual Studio CRT，原组件 SHA256 前后不变。共享 DLL loader 使用 canonical 绝对路径及受控搜索目录；原生 CLI 静态链接 CRT。发布包含依赖来源/许可证和 Haskell 262 个依赖的固定来源 manifest。

macOS 和 Linux 的暂存、递归依赖修复与安装验证已接入发布流程，YAML 与脚本语法检查通过。本机没有执行这些平台的实际构建和动态加载，不能将它们标为通过；由相应平台 CI 执行同一套原生安装 smoke 后才能正式发布。当前未推送、创建 release tag 或上传 PyPI。

MathType 默认 Rust 路径使用真实公式库完成导出/导入，Word 行号来源使用真实 Office 转换验证。
可选 `rust-sdk` / `set-data` / `both` 的 C# helper 调用层与保全逻辑已经迁移，但本次没有
在真实 MathType SDK 环境中完成全部方法的验收，不能把打包成功等同于这项可选集成的真机通过。
