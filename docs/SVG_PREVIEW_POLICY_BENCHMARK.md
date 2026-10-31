# 预览时关闭 SVG → PNG 的实验

> 历史实验记录：文中 JSONL worker、benchmark 脚本及关闭 SVG 备用图的实验开关
> 已在预览接入后的清理中删除。以下命令与接口仅描述当时的归档版本，不适用于当前源码。
> 当前实现见 [DOCX 实时预览](DOCX_LIVE_PREVIEW.md)。

日期：2026-10-31。目的：判断关闭主动 SVG 栅格化后，完整 DOCX → Docxide PDF
是否更快，以及是否只是将工作移到后续转换。仅在实验运行时和项目副本上切换策略，
没有修改论文源文件、产品默认值或扩展的预览策略。

## 结论和适用范围

在本机、这篇 37 页真实论文及 Docxide CLI 0.20.1 上，直接关闭转换能减少约
0.42 秒完整等待，但组合图丢失了内部图片，不能作为正确输出下的性能收益。
Docxide 本身从约 0.65 秒降至 0.38–0.41 秒，没有出现“关掉后 PDF 更慢”的结果；
部分原因是缺失的图像内容减少了后续工作。

保持 SVG 并嵌入子图片的补充策略也不能替代当前路径：DOCX 显著增大，构建更慢，
部分图仍空白。当前不能为了预览速度而一律关闭主动栅格化。
本次没有独立验证 Word 的显示，也没有将本机结果推广到其他 Docxide 版本。

## 实验方法

沿用上一轮 Release 常驻 CLI、同一原生 Pandoc worker、优化后的 SVG helper／
扫描器、相同 MathType 配置和同一份 Docxide。论文原始配置是
`docxConvertSvgToPng: true`、`docxEmbedSvgImages: false`，另有一张图带
`to-png=true`。因此分别测量：

| 策略 | 全局 SVG 转 PNG | 单图 `to-png=true` | 嵌入 SVG 子图片 |
| --- | --- | --- | --- |
| `png`：当前基线 | 开 | 保留 | 关，与原论文一致 |
| `global-off`：只关全局开关 | 关 | 保留，仍会转换该图 | 关 |
| `filter-off`：完全跳过该 filter | 不执行 | 同时跳过 | 关 |
| `embed-on`：保留 SVG 并嵌入子图片 | 关 | 保留 | 开 |

`filter-off` 仅在独立运行时副本里将该 Lua filter 替换为无操作入口。
它不会禁用 Pandoc DOCX writer 自己的 PNG fallback 生成。
其余策略通过项目副本中的独立 style 文件指定，不覆盖真实项目配置。

每组先预热 2 轮，再测量 12 轮。每轮追加同一个不同的可见正文编辑，随机交错
执行顺序，各策略持有独立常驻会话、图片缓存和公式缓存。复制上一轮已验证的
公式／图片缓存作为起点，避免把 MathType 首次转换成本混入策略比较。

分别测量 JSONL 请求至 DOCX 完成、同一 Docxide CLI 至完整 PDF 完成，并保留
相加后的实际总时间。未计入编辑器防抖及 PDF.js 绘制。测量期间不编译或运行测试。
正文 XML 文本逐轮精确校验相同；页数及首尾配对的每页像素差异单独记录。
策略改变了图片格式，未要求整个 DOCX／PDF 逐字节相同。

## 直接关闭转换的结果

单位秒，均为 12 轮热构建中位数。

| 策略 | Papper DOCX | Docxide PDF | 完整等待 | 页数 | DOCX 大小 | 显示检查 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 当前 `png` | 3.497 | 0.647 | 4.156 | 37 | 27.2 MiB | 对照 |
| `global-off` | 3.329 | 0.408 | 3.734 | 37 | 24.4 MiB | 组合图内部图片缺失 |
| `filter-off` | 3.362 | 0.377 | 3.740 | 37 | 24.0 MiB | 组合图内部图片缺失 |

全局关闭后，filter 本身中位数约为 76 ms；完全跳过时约为 2 ms；开启时约为
767 ms。但完整 DOCX 并没有相应缩短 0.69–0.77 秒：Pandoc writer 仍需生成 SVG
的 PNG 备用图，相关工作转移到了 filter 之外。

这些数值不能用于宣称“相同预览快了 10%”。第 24 页原本的网格／纹理比较图，
在关闭转换后只剩虚线边框和标签，内部图片消失。其他变化还出现在第 22、26 页；
完全跳过单图标记时第 20 页也改变。页数仍为 37、正文仍一致，都不足以证明图片正确。

检查 DOCX 内容可见，保留的 SVG 含有指向外部 PNG 或其他 SVG 的相对 href。
原始图片没有作为这些 SVG 的内嵌子资源一起写入包。进一步取出 Pandoc 生成的
第 24 页 PNG fallback，其本身已经只有边框和标签，因此这一处缺图发生在
Docxide 之前。不能仅将问题归因于 Docxide 对 SVG 的支持。

## 嵌入子图片的补测

该补测与自己的 `png` 基线同场配对，结果及原始逐轮数据见下方第二个产物目录。
12 轮热构建中位数如下，单位秒：

| 策略 | Papper DOCX | Docxide PDF | 完整等待 |
| --- | ---: | ---: | ---: |
| 同场 `png` 对照 | 3.808 | 0.649 | 4.462 |
| `embed-on` | 15.724 | 0.897 | 16.630 |

嵌入后 DOCX 约为 85.7 MiB，而 PNG 基线约为 27.2 MiB。对多个 raster 子图片
做 base64 嵌入并保留在 SVG 中，没有得到更轻量的 DOCX。

第 24 页的组合图在这一策略下整体空白。包内的 SVG 确实含有内嵌 data URI 子图片，
但对应 drawing 没有可用的主 PNG fallback 引用；最终 Docxide PDF 中该区域空白。
这比简单关闭转换的输出更差，不能作为预览替代路径。

这里记录的是当前输出链路的实际行为，不据此断言所有 SVG 都需要 PNG，或所有
SVG 都不能由 Docxide 渲染。直接 SVG、外部子资源和内嵌子资源需要分别验证。

## 关闭 Pandoc 自身备用图的后续实验

新增隐藏 DOCX 参数 `--preview-no-svg-fallback`。它通过当前请求的 writer variable
控制适配后的 `Text.Pandoc.App`，在 `createPngFallbacks` 调用之前跳过 SVG PNG
生成。它不是构建后删除图片，也没有使用全局进程开关。普通导出不传该参数时，
继续执行原有生成流程。参数可用于单次 `build docx`，也可放在常驻请求的
`options` 数组中。它只控制 Pandoc 的备用图阶段，前面的主动栅格化仍按独立配置执行。

同一常驻 worker 的回归测试验证了“普通导出 → 关闭备用图 → 普通导出”：
三次都保留相同 SVG 字节，只有中间一次没有 PNG；进程 PID 不变。
worker 的 GPL 适配源码、构建覆盖清单和安装包源码清单一并更新。

重新编译后，使用同一新 CLI、同一新 worker、同一优化后的 SVG helper，重复
12 轮热编辑配对（另有 2 轮预热），只比较同场数据。真实论文结果如下，单位秒：

| 策略 | Papper DOCX | Docxide PDF | 完整等待 | SVG PNG 备用图数 |
| --- | ---: | ---: | ---: | ---: |
| 当前主动转 PNG | 3.991 | 0.657 | 4.646 | 不适用，图片已为 PNG |
| 关主动 filter，保留 Pandoc 备用图 | 3.725 | 0.399 | 4.121 | 5 |
| 关主动 filter，也关闭 Pandoc 备用图 | 3.119 | 0.343 | 3.469 | 0 |

相对只关 filter，进一步关闭备用图减少约 0.606 秒 DOCX 构建和 0.652 秒总等待。
原始 DOCX 中保留 5 张 SVG，PNG 从 6 个变为 1 个（保留原本就是 PNG 的图片）；
逐轮验证没有 SVG fallback relationship，正文文本一致，页数仍为 37。
没有并行运行编译／测试来干扰计时。各场次存在绝对耗时波动，不跨场比较百分比。

**显示结果不满足预览要求：关闭备用图后，这 5 张 SVG 在当前 Docxide 0.20.1
输出中都未显示。** 它没有把省下的 PNG 转换工作接过来完成，PDF 更快包含了
未绘制这些图片的影响。单独用一个蓝色矩形及文字的自包含 SVG 验证，同样变成空白，
因而不能把这个结果只解释为复杂组合图的外部资源问题。

还在独立 DOCX 副本上试了将 SVG 本身设为 `a:blip` 主引用，继续保留 SVG 扩展引用；
当前 Docxide 仍未绘出简单 SVG。因此本次没有把这个诊断性 XML 改法加入生产 writer，
也没有把无备用图模式设为扩展默认策略。

关闭机制已可显式测试；若要将它作为可用的预览默认行为，下一步需要先让下游
直接绘制 SVG，或提供能保留图片的替代路径。空白图片下的速度不作为可用提速。

本场原始数据：`output/benchmarks/svg-preview/20261031T060032.170497Z/results.json`。
简单 SVG 的 DOCX／PDF／对照图保存在
`output/benchmarks/svg-no-fallback-20261031/probe/`。构建、回归及 benchmark 日志
保存在其父目录。相关回归共 72 项通过（首轮 70 项，另外单独复查的两个中文模板
快照用例也通过）；排除原先已知失败的两个用户模板快照用例。Rust Release 和
Haskell worker 编译通过。

```powershell
# 仅跳过 Pandoc SVG 备用图，保留项目原有主动图片转换配置
papper build docx manuscript.md -o output/preview.docx --preview-no-svg-fallback

# 完整的三策略实验还会在私有运行时中跳过主动 filter
# AFTER_EXE 与 WORKER_EXE 指本场存档 CLI、重编译 worker 的实际路径
uv run --no-sync python scripts/benchmark_svg_preview.py --policies png,filter-off,no-fallback --after AFTER_EXE --before-worker WORKER_EXE
```

参数只由本次重编译的 Papper 和 worker 支持，旧版本没有该功能。

## 决策

当前保持已优化的 PNG 路径更合适。若继续探索预览专用策略，应按资源类型选择：
自包含、经验证可正确显示的简单 SVG 可以单独评估；含外部资源、嵌套 SVG 或
不受支持内容的组合图仍需生成可靠 PNG。还须确保预览与最终导出的一致性。

更直接的提速方向是复用正确的 PNG 及其依赖检查结果、减少 DOCX 的完整重写，
而不是让一次策略切换丢掉图片内容。本次只完成实验，没有实施自动选择策略。

## 产物和复现

入口为 `scripts/benchmark_svg_preview.py`，使用 pydantic-settings 配置。

```powershell
# 三种直接开关对照
uv run --no-sync python scripts/benchmark_svg_preview.py --policies png,global-off,filter-off
# 子图片嵌入方案与自己的同场基线比较
uv run --no-sync python scripts/benchmark_svg_preview.py --policies png,embed-on
```

本机产物：

- `output/benchmarks/svg-preview/20261031T052936.412941Z/results.json`：直接开关的完整对照。
- `output/benchmarks/svg-preview/20261031T053304.940738Z/results.json`：嵌入策略补测。
- 同目录 `projects/paper/`：各策略 style、日志、DOCX、PDF 和独立状态。
- `output/benchmarks/svg-filter-20261031/preview-page24.jpg`：开启／关闭转换的第 24 页对照。
- `output/benchmarks/svg-filter-20261031/fallback-page24.jpg`：从关闭转换的 DOCX 中提取的缺图 PNG fallback。
- `output/benchmarks/svg-filter-20261031/preview-embed-page24.jpg`：开启转换／嵌入子图策略的第 24 页对照。

默认输入和缓存路径依赖上一轮留存的基线，具体位置及工具 SHA-256 保存在原始 JSON。
