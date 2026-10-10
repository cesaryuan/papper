# MathType OLE helper 并发测量

测量日期：2026-10-09。本报告针对现有 `src/pandoc_manuscript/mathtype/ole_helper` 实现及其预构建 EXE，未修改转换代码。结论是：生成 WMF/metadata 的两条 MathType 路线不能安全并发调用；只有不使用 MathType server 的纯 MTEF→OLE 包装路线在本次测量中能够正确并发并获得加速。单进程批量转换是完整转换路线目前可用的加速方式。

## 调用语义与代码依据

helper 是 EXE，不是提供线程安全 API 的库。调用方的多个线程分别启动 helper，实际上是多个单线程 helper 进程同时访问 MathType。本报告实测的就是这一调用方式，不是改造 C# 后在一个进程里并发调用私有函数。

- `Program.Main` 标记 `[STAThread]`，调用 `OleInitialize`；批量入口 `CreateOleBins` 使用顺序 `foreach`。STA 本身并不意味着任何多进程或多 STA 程序都不能并行，但该实现没有内部并行调度。
- `CloseStaleBackgroundMathTypeProcesses` 启动时按进程名清理无主窗口的 MathType/MathTypeLib，可能终止其他调用正在使用的 server。
- `CloseMathTypeProcessesOpenedByHelper` 用“启动前 PID 集合中不存在”判断应清理的进程；多个并发 helper 会把同一个后启动 server 视为自己的进程。批量模式每条公式后也调用此清理。
- `set-data` 的 `MTGetLastDimension` 读取最近一条公式的尺寸；在并发转换时，最近一条不一定属于当前 helper。实测出现成功退出但 metadata 取到另一条公式尺寸。
- SDK transform 涉及连接/断开、reset、偏好设置和状态读取，没有会话隔离或同步。实测能够确认现有调用组合不可靠，但不能仅据此确定每次故障究竟由进程清理、SDK server 或其他共享状态导致。
- 同进程直接并发调用还有未经保护的 `mathTypeApiDllResolved`/`mathTypeApiDllPath`、进程级 `SetDllDirectory` 等共享状态。该模式未实测，不应把本报告理解为对 MathType SDK 所有使用方式的线程安全判定。
- `sdk-xform-ole --binary` 不传 `--preview-output` 时，只调用 `MathTypeSDK.getOLEBase64` 读取输入、构造字节并写文件；不连接 MathType SDK/server。不过 EXE 入口仍有全局进程清理，因此该模式与正在运行的其他 MathType 工作混用仍然不安全。

Windows 对 `OleInitialize` 的 STA 要求见 [Microsoft 官方说明](https://learn.microsoft.com/en-us/windows/win32/api/ole2/nf-ole2-oleinitialize)。不能直接把当前 OLE 路线放到默认 MTA 线程池里解决这些问题。

## 环境与测量方法

- Windows 11 Pro，版本 `10.0.26300`。
- Intel Core i7-13700KF，16 核、24 逻辑处理器。
- 本机 MathType 产品版本 `7.8.0.0`，安装路径 `C:\Program Files (x86)\MathType`。
- EXE：`src/pandoc_manuscript/mathtype/ole_helper/bin/Release/net48/MathTypeOleHelper.exe`，70144 字节，文件时间 `2026-10-09 22:37:32`。
- EXE SHA-256：`0BBEC68BBB270286F8160C32822E1E59AEC58BDD4A3C999F4B0444DE5EA3A28B`。
- 输入取自 `scripts/mathtype-rust/samples/generated/eq_*.tex`，按文件大小排序后等间隔选取，覆盖简短与复杂公式；32 条样本的 LaTeX 长度为 5～957 字符。每条保存独立输入输出目录。
- `set-data` 使用 `--encoding utf16le --pre-verb 2 --no-verb`；完整转换同时写 OLE、WMF、metadata。SDK 输入从同一批串行 `set-data` 输出的 `Equation Native` stream 中去掉 28 字节 native header 提取。
- 不传 `--prefs-file`，因此结论没有覆盖并发应用不同 EQP 的情况。测量直接调用 helper，不读取 Papper 公式缓存。
- 每种并发配置重复 3 轮，报告每轮总墙钟时间的中位数；并发数轮换顺序。计时包含 EXE 启动、转换、文件写入及 helper 自身清理，不包含输入准备、结果验证或基准的故障清理。
- 验证要求为退出码 0、MTEF 一致、WMF 在 Windows GDI 192 dpi 渲染后的 RGB 像素一致、metadata 一致。纯 OLE 模式仅验证实际 MTEF。原始 OLE 文件头和 WMF 的无意义填充/绘图记录排序可能变化，不能直接用整个文件哈希判定可见结果。
- 开始时没有运行中的 Word/MathType。场景后只清理本轮新出现的 `MathType.exe -Embedding` 及其 MathTypeLib 子进程。现有 helper 的原始清理行为保持不变。

## 完整转换：多线程调用没有可用的加速

以下每轮使用相同 8 条公式，3 轮累计 24 次转换。单个 helper 超时上限为 8 秒。表中“成功退出”和“输出一致”不同：尺寸串到另一条公式时，helper 仍可能返回成功。

| 方法 | 并发 helper 数 | 每轮耗时中位数 | 成功退出 | 输出一致 |
| --- | ---: | ---: | ---: | ---: |
| set-data + WMF + metadata | 1 | 2.884 s | 24/24 | 24/24 |
| set-data + WMF + metadata | 2 | 2.825 s | 12/24 | 5/24 |
| set-data + WMF + metadata | 4 | 8.434 s | 5/24 | 2/24 |
| set-data + WMF + metadata | 8 | 1.994 s | 7/24 | 2/24 |
| SDK + WMF + metadata | 1 | 2.124 s | 24/24 | 24/24 |
| SDK + WMF + metadata | 2 | 0.913 s | 6/24 | 6/24 |
| SDK + WMF + metadata | 4 | 0.566 s | 15/24 | 15/24 |
| SDK + WMF + metadata | 8 | 0.397 s | 9/24 | 9/24 |

这些并发耗时不能作为完成同一工作量的加速倍数：部分工作失败、超时或输出错误。`set-data` 的 2 并发有一轮耗时 24.036 秒；SDK 并发多次以 `0xC0000005` 访问冲突退出。`set-data` 也复现 `0x800706BE` RPC 调用失败。

metadata 串公式实例：8 条样本的索引 6，WMF 宽 `228.9827 pt`、高 `63.978 pt`，却读到索引 7 的 MathType 尺寸 `274 pt × 276 pt`，baseline `135 pt`。这种输出可能影响 DOCX 公式排版，不能只检查输出文件存在。

一次 32 条扩展测量进一步复现退化：串行 10.570 秒、32/32 输出一致；2 并发 7.589 秒、7/32 一致；4 并发 48.860 秒、2/32 一致；8 并发 32.096 秒、0/32 一致。高并发还使下一场景的批量转换卡住，8 条复测中的两轮后续批量任务超时。32 条扩展测量没有完成三轮，单独列为故障证据，不用于统计正常性能。

## 不渲染 WMF：纯 OLE 包装获得加速

以下每轮 32 条公式、3 轮；所有配置 96/96 成功并且 MTEF 一致。该模式输入是已经生成的裸 MTEF，计时不包含 LaTeX→MTEF 转换，也不产生 Word 需要的 WMF/metadata。

| 调用方式 | 每轮耗时中位数 | 相对逐条串行倍数 | 耗时减少 |
| --- | ---: | ---: | ---: |
| 逐条串行，1 个 helper | 3.194 s | 1.00× | 0% |
| 2 个并发 helper | 1.678 s | 1.90× | 47.5% |
| 4 个并发 helper | 0.976 s | 3.27× | 69.4% |
| 8 个并发 helper | 0.578 s | 5.52× | 81.9% |
| 单进程串行批量 manifest | 0.746 s | 4.28× | 76.7% |

这个结果主要适用于进程启动和文件包装的端到端吞吐，不能解释为公式渲染计算提速 5.52 倍。纯包装很轻量，避免每条启动一个 EXE 已能显著改善性能；若还需要 WMF，必须另外计算渲染成本。8 条复测也全部通过，8 并发约为串行的 5.39 倍。

## 完整转换：串行批量可提速约 1.5 倍

在恢复后的环境单独运行 32 条 × 3 轮，不夹杂失败的 MathType 并发转换：

| 方法 | 逐条启动 helper | 单进程批量 manifest | 耗时减少 | 输出一致 |
| --- | ---: | ---: | ---: | ---: |
| set-data + WMF + metadata | 10.590 s | 7.221 s | 31.8%，约 1.47× | 两者均 96/96 |
| SDK + WMF + metadata | 8.479 s | 5.545 s | 34.6%，约 1.53× | 逐条 95/96；批量 96/96 |

SDK 逐条模式有一轮第一条公式达到 8 秒上限，整轮耗时 16.404 秒；另两轮为 8.384、8.479 秒。因此 SDK 表中的 1.53× 是中位耗时比较，不是三轮均成功的保证。SDK 批量三轮为 5.512～5.656 秒。set-data 逐条三轮为 10.580～10.674 秒，批量为 7.190～7.308 秒。

现有 Rust 调用路径 `crates/papper-document/src/docx/mathtype/backend.rs` 的 `sdk` 每条公式单独启动 helper，并始终要求 WMF/metadata；本报告的纯 OLE 并发收益不能直接套到该调用路径。优先复用已经支持的串行 batch manifest 更符合本次实测结果。若要实现真正的 MathType 并发，必须先解决进程所有权、共享 server 状态、尺寸来源和失败恢复，再重新测量；仅去掉 `[STAThread]` 或添加线程池不足以证明安全。

## 复现与原始数据

基准入口：[scripts/benchmark_ole_helper.py](../scripts/benchmark_ole_helper.py)。使用 PEP 723 依赖声明和 `pydantic-settings`，不会修改项目依赖。先关闭 Word/MathType 会话，从仓库根目录运行：

```powershell
# 3 轮并发稳定性试验，每轮 8 条
uv run --script scripts/benchmark_ole_helper.py --output tmp/ole-helper-concurrent --count 8 --repeats 3 --timeout 8

# 3 轮串行/批量对照，每轮 32 条；纯 OLE 模式同时测 1/2/4/8 并发
uv run --script scripts/benchmark_ole_helper.py --output tmp/ole-helper-controls --mode controls --count 32 --repeats 3 --timeout 8
```

本次本地原始数据包括所有命令输出、exit code、逐条耗时和生成文件：

- `tmp/ole-concurrency-20261009/repeated/results_validated.json`：8 条 × 3 轮并发结果，重新按最终 GDI 像素验证标准检查。
- `tmp/ole-concurrency-20261009/controls/results_validated.json`：32 条 × 3 轮稳定对照与纯 OLE 并发结果。
- `tmp/ole-concurrency-20261009/formal/results_validated.json`：未完成的 32 条高并发扩展测量。

测量脚本和报告作为可复现材料保留；大型转换产物位于被忽略的 `tmp/`，没有加入版本控制。这次没有改动 helper 的生产实现，也没有新增自动化测试。
