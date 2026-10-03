# Lua filters 与原生 Rust API 改造后的 HTML 热构建基准

2026-10-03，在 Windows x86_64 上，对本轮最终 wheel 进行隔离 `uv tool` 安装后，真实论文的完整 HTML 热构建 CLI 中位耗时如下：修改正文后，Python 基线 **558.44ms → 当前 Rust 243.70ms，减少 56.4%**；内容不变时，**354.96ms → 47.18ms，减少 86.7%**。两个场景分别采集 15 对正式样本，并保留全部较慢样本。

本报告记录本轮最终制品的新测量。历史 Rust 迁移实验保留在 [RUST_MIGRATION_BENCHMARK.md](RUST_MIGRATION_BENCHMARK.md)，其制品、源码身份及样本未混入本轮统计。这里的减少比例相对于冻结的完整 Python CLI；这次没有逐项消融，不能把全部提速归因于 Lua filter 或公式接口改造中的某一项。

本轮对 Python 额外隔离了 `HOME` / `USERPROFILE`；历史脚本设置的 `PAPPER_HOME` 对旧 Python 无效，当时通过不同项目标识隔离其服务与缓存。新的隔离更严格，是两轮实验的环境差异。因此不合并两轮样本，也不根据两个 Rust 中位数的细小差异宣称额外收益。

| 真实论文场景 | 每种实现样本数 | Python 完整 CLI 中位数 | 当前安装制品完整 CLI 中位数 | 中位耗时减少 | Python p95 | 当前 Rust p95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 修改正文后构建 | 15 | 558.44ms | 243.70ms | 56.4% | 597.62ms | 274.08ms |
| 内容相同，仍写入 HTML | 15 | 354.96ms | 47.18ms | 86.7% | 378.98ms | 51.29ms |

共计 **60 条正式 CLI 调用观测**，每个场景还有 2 对预热。包括预热在内的 **34 对完整 HTML 输出全部一致**，比较仅统一换行与文件末尾空白。正文编辑场景均未命中整个 HTML 缓存；内容相同场景的正式样本均命中该缓存。每次构建仍然发布最终 HTML 文件，计时包含这项开销。

计时和对照采用以下边界：

1. 先把最终 wheel 安装到独立的 `UV_TOOL_DIR`、`UV_TOOL_BIN_DIR` 和 `UV_CACHE_DIR`。核对安装后的 `papper.exe` 与 wheel 内对应入口的完整字节一致，再复制冻结入口供计时。安装、复制和摘要计算均在计时之前。
2. Python 通过开发环境的 `python.exe -m pandoc_manuscript.cli` 调用 `tests/legacy/` 的冻结实现；Rust 直接运行安装制品的原生入口。双方计时均创建新的 CLI 进程，不包含 `uv` 前端或旧 console shim。
3. 两种实现拥有独立的稿件、样式副本、输出、端口、项目状态以及 `HOME` / `USERPROFILE`。这也隔离了不读取 `PAPPER_HOME` 的旧 Python 实现。原稿和原有资源只读，每一对处理相同正文。
4. 双方执行 `build html -m <副本> -o <输出> --start-server --server-port <独立端口> --resource-path <资源根>`。计时覆盖进程启动、配置与准备、服务校验、构建请求及 HTML 发布，到成功退出为止；写入副本与读取诊断计数在计时区间之外。
5. 每个场景先做 2 对预热，然后按固定随机种子 `20261002` 交错执行 15 对正式样本。每次检查请求计数增加 1，以及预期的 HTML 缓存命中状态；完整 HTML 在每一对结束后对照。
6. 原生进程移除 `PAPPER_RESOURCE_ROOT`，使用实际安装制品解包的内嵌运行时；双方清除 worker 命令覆盖。服务配置记录的内嵌 Worker 与 Python 基线 Worker SHA-256 完全相同，避免比较不同引擎。
7. 设置 `PAPPER_DISABLE_UPDATE_CHECK=1`。旧 Python 未实现该开关，因此为它准备独立且新鲜的更新缓存，阻止后台更新请求，保留完整 CLI 及正常缓存读取。
8. 完整测试、Cargo 检查、最终 wheel 构建和安装 smoke 全部完成后才开始计时，没有并行测试或编译。所有正式样本参加统计；减少比例为 `1 - Rust中位数 / Python中位数`。p95 取排序后的零起点下标 `min(n-1, floor(0.95*n))`，本轮 15 个样本对应最高样本。

原稿实验前后 SHA-256 保持相同，全部服务由实验记录的所属 PID 停止。正式测量完成后没有再次采样或筛除观测。

冷启动另记录了双方各 1 次观察：Python **2924.33ms**，Rust **5273.80ms**。这包含各自新服务与 Worker 初始化；Rust 的独立新状态还包含首次运行时解包。这两个单次值不能构成稳定的冷启动性能比较，本轮结论限定于热服务连续构建。

本轮制品及源码身份如下，均使用 SHA-256：

| 对象 | SHA-256 |
| --- | --- |
| 本轮最终 wheel | `b0ec1e2e616f09ef75c0a8d18e493e2b70887c2029ba030187c6d4607b0bb021` |
| wheel 内、实际安装及冻结的原生 CLI | `170e3de8d53cd5975f222d37ec86f2f9396b9145f3df0e331acb7717cd0123ca` |
| 内嵌运行时 / 实际解包 runtime ID | `a0717f352578625ccf36b066fda66d26f98f16e231d2163d4c19d97c8ff2d2b6` |
| 双方共用的 Haskell Worker | `4adbda67d17fc525a92cf9cd493f40ca0b202bd63ca7bd12803cf52c7eecca60` |
| 当前 Rust、Lua 与发布工具源码树 | `8463506cd5c41fa4464f2418d0583ba875c80b4a4504121432fa7e844dcc2966` |
| 已冻结的 Python 基线源码树 | `2b88fe8679a2b6ffe27ffa16633f0cd6926b9f8f560a827486875e5281b78eab` |
| 真实论文原稿，实验前后相同 | `0866be4130fc1dcbaf17aff8e674a8e0404c6ad30cc28e392aca14a0104727f8` |

公式依赖的仓库版本也记录在源码身份文件中：`mathtype-rust` 为 `a4ac2700a24467a779d51215ad830818d559d3f5`，`latex2wmf` 为 `ed2fb6a7b8073c1537a1ee30f38364921f86623a`。源码树身份覆盖根构建清单、`crates/`、`pandoc/filters/` 及 `tools/papper-dev/src/` 下文件。每个文件先计算 SHA-256，再按仓库相对路径排序，连接 `UTF8路径 + NUL + ASCII文件摘要 + 换行` 后计算总摘要。该范围包含本轮新的 Lua 实现，与历史实验的 Rust 源码树范围分别记录。

编译器为 `rustc 1.98.0 (88d9e12ae 2026-08-18)`，Python 为 3.13.12，Worker 报告 Pandoc 3.12 / Lua 5.4。计时记录的基准 Git HEAD 是 `9f4dabcbcd18a2052f2675a6e3cf9a1b0f3d3add`；本轮修改当时尚未提交，以上源码树摘要和子模块版本用于区分实际编译的改动。

原始样本、私有输入路径、配置和冻结入口保留在 ignored 的 `output/` 中：

- `output/benchmarks/lua-native-refactor-final/20261003T024923.175555Z/results.json`：全部样本、逐次计数、HTML 摘要、源码与 Worker 身份及冷启动观察。
- `output/benchmarks/lua-native-refactor-final/20261003T024923.175555Z/summary.md`：本轮独立汇总。
- `output/benchmarks/lua-native-refactor-final/source-identities.json`：逐文件源码摘要及公式依赖版本。
- `output/lua-native-refactor/benchmark-installed/`：独立 `uv tool` 安装与缓存目录。
- `output/lua-native-refactor/benchmark_wheel.py`、`save_benchmark_identities.py`：本轮开发测量脚本。
- `output/lua-native-refactor/wheel/papper-0.9.2-py3-none-win_amd64.whl`：实际安装与测量的最终 wheel。

性能实验开始前，完整测试为 **326 passed、14 个既有跳过、2 个既有警告**；Cargo 测试、Clippy、格式检查及最终安装 smoke 通过。安装 smoke 包含真实 SVG → PNG 像素、MathType 导出与导入、各构建目标、审稿回复、缓存及服务清理。本报告没有测量其他平台、DOCX/LaTeX 构建性能或各个机械迁移修复的单独收益。
