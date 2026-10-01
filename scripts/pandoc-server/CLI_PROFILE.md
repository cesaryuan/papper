# 连续 HTML CLI 构建的额外耗时

这次只添加诊断脚本和报告，没有修改生产实现。原有暂存树仍为
`9906049393c067873e351aaa5ff9de1a510351a5`。

完整命令比单次 Server 请求多出的主要是固定客户端成本：约 290 ms 的
模块导入与配置模型初始化、150–180 ms 的启动退出，以及约 62 ms 的
默认 TLS 上下文与 Windows 证书加载。正文 YAML、样式准备和参数解析
只占几十毫秒，最终 HTML 写入不到 1 ms。

## 同轮测量

本次完整命令与先前实验一样，计时包含 `uv run --project … papper build
html … --start-server`。三个路由各有自己的预热服务和项目副本，逐轮
收到相同正文，随机交错执行。下表为排除 2 次预热后的 10 次中位数，
没有删除慢样本。

| 输入与操作 | 完整公开 CLI | 直接 HTTP | 配对 CLI 额外时间 |
| --- | ---: | ---: | ---: |
| 模板，修改正文 | 696.9 ms | 97.4 ms | 592.0 ms |
| 模板，内容不变 | 575.5 ms | 2.2 ms | 573.5 ms |
| 真实论文，修改正文 | 877.0 ms | 290.0 ms | 589.9 ms |
| 真实论文，内容不变 | 562.3 ms | 2.9 ms | 558.9 ms |

“配对额外时间”取每轮 CLI 减该轮 HTTP 的差，再求中位数；并非前两列
中位数直接相减。不同服务的转换耗时会抖动，因此定位客户端成本时，
还使用同一次受测 CLI 内部的转换请求时间。真实论文正文修改时，该
方法测得额外时间 558.7 ms；内容不变时为 554.0 ms。

计时探针与公开 CLI 也逐轮比较。配对差的中位数在四种情况中为
−5.8、−1.4、+13.7、−4.4 ms，没有增加数百毫秒的 profiling 开销。
所有 144 个路由输出检查均通过；比较时统一 Windows CRLF 与 HTTP LF。
每个路由的服务启动时间在测量过程中保持不变。

原始报告：

- [全部阶段与启动控制](../../output/benchmarks/server-cli-profile/20261001T093815.245115Z/summary.md)
- [逐次原始事件、HTTP 响应计时与服务 PID](../../output/benchmarks/server-cli-profile/20261001T093815.245115Z/results.json)
- [真实论文 cProfile](../../output/benchmarks/server-cli-profile/20261001T093815.245115Z/1-3d-mesh-manuscript-cprofile.profile.txt)
- [真实论文 importtime](../../output/benchmarks/server-cli-profile/20261001T093815.245115Z/1-3d-mesh-manuscript-importtime.imports.txt)

## 非转换部分分解

以真实论文修改正文后的受测 CLI 为例，嵌套计时扣除子阶段，避免把
同一耗时重复加在 `build_html`、`ensure_server` 和 HTTP 等父子函数上。

| 工作 | 中位数 |
| --- | ---: |
| CLI 模块导入与模型初始化 | 289.8 ms |
| 从父进程启动到脚本入口：uv、系统和 Python 初始化 | 108.0 ms |
| 正常入口前的标准库准备，包含探针的标准库加载 | 13.7 ms |
| 主函数返回后到进程退出 | 44.6 ms |
| 两次默认 TLS 上下文初始化 | 61.6 ms |
| 读取、合并正文与样式元数据 | 7.5 ms |
| HTML 元数据、CSS 等准备 | 3.7 ms |
| 生成 Pandoc metadata YAML | 4.0 ms |
| 命令行参数解析及主函数零散工作 | 6.1 ms |
| 两次 `/version` HTTP 请求 | 3.8 ms |
| 配置 JSON 比较、写入判断 | 1.0 ms |
| 最终 HTML 写入 | 0.7 ms |
| 更新检查 | 0.2 ms |

阶段中位数不必严格相加等于总耗时中位数。其余为路径、日志、HTTP
响应解码、探针安装等零散工作。真实 HTTP 转换请求相对响应中
`Server-Timing: total` 多出的中位数约 1 ms，证明实际传输开销很小。

### 导入成本

以下是按实际 CLI 导入顺序记录的直接导入耗时；依赖计入首次加载它的
模块，不能理解成每个模块独立冷启动的耗时。

| 直接导入 | 中位数 |
| --- | ---: |
| `commands.build` 及其依赖 | 113.6 ms |
| `pydantic_settings` 及其首次加载的依赖 | 61.7 ms |
| `pydantic` 及其首次加载的依赖 | 50.6 ms |
| 包初始化、标准库、安装版本元数据 | 41.8 ms |
| `commands.build_reply` 及其剩余依赖 | 13.7 ms |

HTML 的页边距规范化来自 `docx.page_margins`，该模块顶层导入
`python-docx`，所以 HTML 请求也付出了约 38 ms 的这段导入成本。
其他未使用的子命令同样在 CLI 启动时导入。

### 本地 HTTP 为何加载证书

`_request_version` 与 `build_with_pandoc_server` 各创建一次 urllib opener。
标准库 `build_opener` 自动添加 `HTTPSHandler`，其构造函数即刻创建
默认 TLS context，加载 Windows 证书存储。即便这次 URL 只是
`http://127.0.0.1`，也会支付这笔初始化成本。

每个受测 CLI 中实际观察到两次 TLS 初始化，总计约 62 ms。它们没有
进行网络 TLS 握手。cProfile 的 Windows 证书读取调用链独立确认了
这一原因；cProfile 样本不用于时间表，因为它会拖慢 Python 代码。

### uv 与解释器控制实验

空 Python 程序的中位数为 40.2 ms；通过 `uv run` 启动为空程序时为
113.6 ms，额外约 73 ms。这部分属于测量命令外层的 uv 调用，并非
Papper 本体。直接运行已经安装的 `papper` 时不会包含这个 uv 环节。

另一次启动控制得到空 Python 40.8 ms、Scoop shim 包装的 uv 117.2 ms、
直接 uv 二进制 99.2 ms。Scoop shim 约贡献 18 ms，包含在上述 uv
路由开销中，不能重复相加。原始数据见
[launcher-controls.json](../../output/benchmarks/server-cli-profile/20261001T093815.245115Z/launcher-controls.json)。

## 复现

```powershell
uv run python scripts/profile_html_server_cli.py --runs 10 --warmups 2
```

脚本只读原稿，使用私有项目副本，在结束时停止自己启动的服务。
所有报告写入 `output/benchmarks/server-cli-profile/<时间戳>/`。

## 已实现的前两项优化与实测

本地 HTTP 客户端复用同一个无代理 opener，HTTPS handler 只在实际
HTTPS 请求或重定向时初始化证书上下文，保留标准库的证书校验。
CLI 使用 Pydantic 动态构建当前子命令的配置模型；根帮助加载完整模型，
保留原有选项、命令列表和旧的 Settings/PmtCli 导出。HTML 与 DOCX
共用独立的格式值校验模块，DOCX 后端适配回原生 Length 与对齐枚举，
HTML 不再导入 python-docx。

2026-10-01 对暂存的旧版 Python 客户端和新版进行交错测量，二者使用
同一个优化后的 native worker 二进制，分别维持独立的热 Server。
每个场景 10 次正式样本、2 次预热，保留全部样本；共 192 次 HTML
一致性检查通过。比较基线包含此前已经完成的 Server 优化，仅衡量
本次客户端改动。下表为包含 uv 启动和进程退出的完整命令中位数。

| 输入 | 场景 | 改动前 | 改动后 | 时间缩短 |
| --- | --- | ---: | ---: | ---: |
| 模板 | 修改正文 | 610.976 ms | 505.463 ms | 17.3% |
| 模板 | 内容不变 | 551.663 ms | 425.600 ms | 22.9% |
| 真实论文 | 修改正文 | 759.693 ms | 707.786 ms | 6.8% |
| 真实论文 | 内容不变 | 552.471 ms | 420.343 ms | 23.9% |

独立 Server 的构建耗时也会波动，修改正文的收益以上述完整命令实测
为准。新版带计时探针的 CLI 扣除其自身 HTTP 构建请求后，剩余开销
中位数约 418–423 ms；其中导入及选中模型加载约 225–227 ms，本地
TLS 初始化为 0 次，opener 创建 1 次约 0.15 ms。仍需支付 Python、
Pydantic、uv 及配置准备成本，这两项改动没有达到客户端耗时减半。

完整测试集 235 项通过；与旧版对比的 12 种帮助、版本和错误参数
调用保持相同输出与退出码。新增测试验证系统 TLS 证书无法加载时
本地 HTTP 构建仍成功，另有本地 HTTPS smoke check 验证自签名证书
仍被拒绝。

本轮报告：[summary.md](../../output/benchmarks/server-cli-profile/20261001T103508.985705Z/summary.md)，
[原始数据](../../output/benchmarks/server-cli-profile/20261001T103508.985705Z/results.json)。
暂存树保持为 `9906049393c067873e351aaa5ff9de1a510351a5`，本轮改动
留在工作区，旧客户端快照位于项目旁的
`../papper-cli-client-before-20261001/`。快照额外复制原项目的只读
`pandoc/manuscript-template/reference-doc` 资源，以包含 Git 不跟踪的
解包文件，原资源未改动。

```powershell
uv run python scripts/profile_html_server_cli.py --compare-baseline --runs 10 --warmups 2
```

热 Server 专用轻量客户端尚未实施。
