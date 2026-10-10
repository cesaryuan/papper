# SVG CPU 渲染后端对照

2026-10-10，在 Windows x86_64 MSVC 环境中，对报告中的两张原始 SVG
完成了 `skia-safe` CPU 渲染试验。Skia 在原始分辨率下可以快速完成这两张图，
浏览器对照与局部检查没有发现缺字、漏图或结构变化。随后已将生产渲染器
替换为 Skia，删除 resvg 依赖及实验基线代码。以下独立 DOM 实验数值保留
为迁移依据，生产集成结果另列于“生产接入验证”。

## 输入和方法

- `critical_rerouting.svg`：2877 × 1542，158 处滤镜引用
- `critical_sp_gnn.svg`：3826 × 2114，182 处滤镜引用
- 保留原 SVG 的文字、内嵌 PNG、渐变、裁剪、模糊阴影和透明背景，未缩小输入
- Skia：`skia-safe 0.153.3`，release 编译，CPU raster surface
- resvg：迁移前版本 `0.47.0` 的历史独立 release 基线，所有依赖均完全优化
- Chromium：145.0.7632.6，Playwright 1.58.0，禁用 GPU，软件 canvas
- Skia 与 Chromium 各重复三次，每次读取原始内容建立新 DOM/图像，完整编码 PNG
- 时间包含解析、分配画布、渲染和编码，不包含浏览器启动、文件读取和文件写入
- Skia 字体管理器初始化单独计时，约 2 ms；resvg 字体初始化包含在基线中
- resvg 每张限时 15 秒，超时后只终止并回收实验自己创建的基线进程

Skia 的 Windows 官方预编译包需要启用 `gl` 构建功能，程序没有创建 GPU
context 或 surface，实际渲染始终使用 `surfaces::raster_n32_premul`。
这是 CPU 测量，不依赖显卡性能。

## 性能结果

| 原始 SVG | Skia 完整转换中位数 | Chromium 完整转换中位数 | resvg release |
| --- | ---: | ---: | --- |
| critical_rerouting.svg | 148.3 ms | 58.6 ms | 超过 15 秒，停在 rasterization |
| critical_sp_gnn.svg | 281.8 ms | 112.9 ms | 超过 15 秒，停在 rasterization |

Skia 三次平均阶段时间如下：

| 原始 SVG | 解析 | CPU 像素渲染（含画布分配） | PNG 编码 |
| --- | ---: | ---: | ---: |
| critical_rerouting.svg | 4.4 ms | 41.1 ms | 102.9 ms |
| critical_sp_gnn.svg | 5.6 ms | 81.8 ms | 193.8 ms |

resvg 完成字体初始化和解析分别只需约 0.051 秒、0.082 秒，随后均在
像素渲染阶段达到 15 秒上限。相对于该下限，Skia 完整转换时间分别至少
快约 101 倍、53 倍。没有等待 resvg 最终完成，不能报告它的精确耗时。
Chromium 与 Skia 的 PNG 编码参数不同，编码耗时和压缩后文件大小不宜直接
当作渲染质量指标。

## 输出核对

对完整原始像素进行了 RGBA 和白底显示像素比较，并查看了缩略图及原尺寸
的公式、节点、箭头和阴影局部。两张图未发现文字缺失、内嵌图片遗漏、
布局改变或裁剪错误。

| 原始 SVG | 白底完全相同像素 | 任一 RGB 通道差值 > 10/255 的像素 | RGB 平均绝对差值（0–255） |
| --- | ---: | ---: | --- |
| critical_rerouting.svg | 98.989% | 0.709% | 约 0.463 |
| critical_sp_gnn.svg | 98.624% | 0.660% | 约 0.646–0.670 |

残余差异主要出现在文字/线条边缘和模糊阴影区域。完全相同像素比例包含
大量背景像素，不能单独作为完整 SVG 兼容性的证明；这里同时做了局部
视觉核对。两种输出并非逐像素完全一致。

额外将两张图的 `filter` 引用仅在诊断副本中删除，再用同一 Skia 程序
渲染。原输出与无滤镜输出在白底上分别有约 0.461%、0.496% 的像素产生
超过 10/255 的变化，渲染时间也明显减少。结合可见的模糊阴影，确认本次
快速输出确实执行了滤镜处理，并非简单丢弃所有滤镜。

## 复现与产物

实验入口为
[tools/papper-dev/benchmarks/svg-skia](../tools/papper-dev/benchmarks/svg-skia/README.md)。
按照该目录的 README 编译后，执行：

```powershell
uv run --script tools/papper-dev/benchmarks/svg-skia/compare.py
```

结果写入 `target/svg-skia-benchmark/results/`：

- `comparison.json`：历史三个渲染器的原始计时和完整像素对照；当前脚本只比较 Skia 与 Chromium
- `critical_rerouting-skia.png`、`critical_sp_gnn-skia.png`：原始分辨率 Skia 输出
- `*-chromium.png`：原始分辨率浏览器参考输出
- `*-no-filter.svg`、`*-no-filter.png`：滤镜诊断副本，原始输入未修改
- `formula-comparison.png`：本次手动检查的原尺寸局部，上为 Chromium，下为 Skia

Rust 工具完成 release 构建、rustfmt 检查和 Clippy `-D warnings` 检查；
对照脚本完整运行成功，两个限时 resvg 进程均被回收。

## 生产接入验证

生产 helper 使用 usvg 做 CSS、DPI、字体轮廓和外部相对资源的预处理，
再用 Skia CPU surface 渲染。嵌套 SVG 递归转为子图，WebP 使用单独的
解码器补足官方 Windows Skia 二进制缺失的 codec。两种 PNG 缓存均更换
渲染命名空间，避免读回旧后端像素；字体身份仍使用实际安装字体的内容。

对原始 SVG 各启动三个独立 release 进程，禁用 fallback 缓存、96 DPI，
计时包含字体扫描、usvg 预处理、Skia 渲染、PNG 编码及文件写入：

| 原始 SVG | 生产 release 总耗时中位数 | Skia 像素渲染耗时 | 生产 debug 单次总耗时 |
| --- | ---: | ---: | ---: |
| critical_rerouting.svg | 235 ms | 42 ms | 322 ms |
| critical_sp_gnn.svg | 382 ms | 79–80 ms | 572 ms |

原始计时位于 `target/svg-skia-benchmark/results/production-timings.json`。
26 项 SVG fallback 与 Lua 图片集成测试通过，包括 DOCX 同时保留 SVG 与
PNG、物理尺寸/DPI、外部 PNG、嵌套 SVG、SVGZ、DOCTYPE、字体变化、缓存
失效/损坏恢复、并发写入和失败时保全旧文件。新增的两个回归检查独立验证
WebP 颜色/透明度，以及 200 个视口范围模糊滤镜在 15 秒内输出可见阴影。
Clippy `-D warnings` 通过。Linux/macOS 按官方可用 archive 补齐构建特性，
当前机器只完成了 Windows 构建及运行验证。

另将两张原始图用相对资源路径放入同一份 Markdown，通过公开 `build docx`
命令、关闭 MathType、禁用 fallback 缓存，1.70 秒完成构建。检查 DOCX ZIP
确认其中包含两份原始 SVG 和两份 PNG fallback。该验证没有打开 Word，
不代表已经完成 Word 视觉快照检查。

Skia 的 SVG DOM 与 Chromium 的 SVG 前端不同。实验结果说明 Skia 在这些
复杂滤镜图上具有可用的速度与输出效果，不能据此推断全部浏览器 SVG 特性
都受到支持。

## Windows wheel 体积估算

另以 Papper 的 `lto = "thin"`、`codegen-units = 1`、`strip = true`
重新编译实验工具，再使用产品打包器相同的 `zip 2.4.2`、`deflate` feature
及默认 DEFLATE 选项压缩单个 wheel 条目。当前产品 `papper-svg` 按打包代码
此前要求启用 `+crt-static` 并单独 release 编译。Windows 官方 Skia 预编译包
没有匹配的静态 CRT 变体，因此此次 Skia 数值来自动态 CRT 构建，并额外
计算其微软 C++ Runtime DLL 依赖。

| 组件 | 安装后文件字节数 | wheel 压缩条目字节数 |
| --- | ---: | ---: |
| 迁移前完整 papper-svg（resvg，release，静态 CRT） | 3,771,392 | 1,689,735 |
| 生产 Skia papper-svg（release，动态 CRT） | 18,933,760 | 8,243,712 |
| MSVCP140.dll | 557,728 | 185,647 |
| VCRUNTIME140.dll | 124,544 | 60,591 |
| VCRUNTIME140_1.dll（MSVCP140 的传递依赖） | 49,792 | 26,654 |

由此，替换原渲染器并新增全部上述 Runtime DLL 时，wheel 预计增加
6,826,869 字节，约 **6.83 MB / 6.51 MiB**；安装后约增加
15,894,432 字节，即 **15.89 MB / 15.16 MiB**。若同目录已经打包了某些
Runtime DLL，增量会相应减少。wheel ZIP 条目元数据和许可文件只带来小量
额外开销。`rsvg-convert` 是独立的小启动器。正式 wheel 将运行时嵌入
`papper.exe`；`pmt.exe` 是约 321 KB 的转发入口，未再次嵌入运行时。
因此上述渲染器增量无需翻倍。外层 ZIP 再压缩、许可文件以及其他 CLI
变化会影响完整 wheel 的最终差值。

Skia exe 已包含 `icudtl.dat`：10,876,560 字节，单独按同选项压缩为
4,674,309 字节。其内容用于文本/Unicode 处理，是本次体积增长的主要来源；
这是 exe 内嵌内容，不能在总增量中再次相加，也不能未经字体兼容验证直接删除。

组件数值来自实际生成的 Windows wheel ZIP 条目，包含生产 helper 的
参数兼容、缓存及图像解码逻辑。抽取 helper、adapter 和三份 CRT DLL 后，
在清空 PATH 的环境中成功转换原始 `critical_rerouting.svg`。数据只适用于
当前 Windows x86_64、Skia 0.153.3，其他平台需重新测量。总增量仍是相对
历史 helper 的组件估算，完整 wheel 还会受 CLI 和其他依赖的变化影响。

带嵌入运行时的完整 wheel 已构建，并将其中的公开 `papper.exe` 复制至
隔离目录，在清空 PATH、不指定源码资源根的环境中构建两图 DOCX，1.58 秒
完成。检查两份 PNG fallback 的尺寸分别为 2877 × 1542 和 3826 × 2114。
最终含独立可读许可文件的 wheel 为 63,313,499 字节（约 63.31 MB / 60.38 MiB）；
重新抽取公开 CLI 后，1.60 秒完成同一构建，四份媒体内容分别与原始 SVG
及独立 Skia helper 输出逐字节一致。

压缩探针和原始测量保存在 `target/svg-skia-wheel-size/`；当前产品体积对照
二进制位于 `target/svg-resvg-wheel-size/release/papper-svg.exe`，未覆盖正常构建。
