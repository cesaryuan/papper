# 常驻 DOCX 与编辑预览接入记录

日期：2026-10-31。对应 Papper 和相邻 VS Code 扩展的本地实验构建，尚未发布。
此前常驻原型的历史配对数据见 [DOCX_RESIDENT_BENCHMARK.md](DOCX_RESIDENT_BENCHMARK.md)。

## 当前行为

扩展的独立 DOCX 分页预览已使用当前编辑缓冲区，不保存或覆盖源文件。
默认停止输入 700 ms 后构建，保存和手动刷新立即提交。每个面板只执行一个构建，
等待中的编辑合并为最新版本；编辑会立即使旧结果失效，包括 PDF.js 正在解析或
绘制的候选 PDF。最近成功显示的 PDF 保留到下一份可显示的结果就绪。

`docxPreview.refreshOnEdit`、`docxPreview.editDelay` 和 `docxPreview.refreshOnSave`
控制自动刷新。可见页绘制、缩放和页内位置保留继续使用既有 PDF.js 查看器。
关闭面板会停止其服务及子进程，等待退出后清理面板目录。

预览强制关闭 `docxNativeCrossref`：Lua 原生字段生成和 DOCX 最终处理都跳过，
使用构建时解析的静态编号。正式导出仍遵循项目设置，配置文件和作者内容不被改写。
常驻构建器只供预览服务内部使用，不提供通用 DOCX worker 命令、任意构建参数
或调用方选择的 DOCX 输出路径。

SVG filter 仍遵循项目配置，Pandoc SVG PNG 备用图保留。上一轮显式关闭备用图
会导致当前渲染链路中部分图片空白，该实验开关及其 Pandoc 应用模块覆盖现已移除。

## 服务与分发

`papper-server/src/docx_preview.rs` 提供本机 HTTP 服务。扩展启动内部入口
`papper __docx_preview_server --token <hex> --work-dir <owned-root>`，通过 stdout
取得带版本的 ready 消息及随机端口。`POST /preview` 使用 Bearer token，接收
`source` 与 `source_text`，返回 PDF 字节和 DOCX/PDF 耗时、Pandoc PID 响应头。

CLI 提供现有完整 DOCX 构建回调，服务内部直接调用 `docxide-pdf` 0.20.1 的
字节转换接口。该依赖关闭默认功能，不编译其 CLI。无需另外安装 Docxide，
也不调用其可执行文件。常驻 Pandoc、引用依赖缓存和现有公式／图片缓存继续复用。
PDF 仍全量生成，浏览器随后只绘制附近页；没有实现 DOCX 或 PDF 的增量分页。

服务按预览面板独立拥有，与共享 HTML 服务分开。构建错误返回 422，服务继续
接受请求；无效认证返回 401。服务崩溃或请求超时后，下一次刷新重启服务。
扩展宿主消失时 stdin EOF 结束服务。DOCX 中间文件位于编辑器拥有的临时根下，
强制终止时也能在子进程退出后清理。

打包流程同时收录 Docxide Apache-2.0 LICENSE 及锁定版本／功能记录。
实际 wheel 已安装到隔离环境并成功通过内置 PDF 预览，不依赖源码树中的运行资源。
另已确认 wheel 不含 `docxide-pdf.exe`，许可证记录中的 Docxide features 为空。

## 实测与验证

37 页真实论文的原稿只读，以此前验证过的图片／公式缓存预热私有状态目录。
两轮预热后提交五次不同的、可见的正文编辑。每次生成的 PDF 均含对应编辑文本，
页数均为 37，七次请求的 Pandoc PID 相同。测量期间没有并行编译或测试。

| 阶段 | 五次热编辑中位数 |
| --- | ---: |
| DOCX 构建 | 3.917 秒 |
| 服务内 PDF 转换 | 0.768 秒 |
| 提交到完整 HTTP PDF 响应 | 4.722 秒 |

各列分别取中位数，不直接相加。HTTP 耗时包含快照传输、服务处理及 PDF 返回，
不包含编辑防抖、PDF.js 绘制和屏幕刷新。本场是接入后的延迟检查，没有同时运行
旧路径对照，不能从跨场数字得出加速比例。复杂论文仍需数秒，不能称为逐键即时显示。
首轮预热为约 11.39 秒，第二轮约 4.83 秒，不能把热编辑结果用作首次启动承诺。

本地 Papper 可执行文件从接入前的 63,629,312 字节变为 70,104,064 字节，
增加 6,474,752 字节，约 6.18 MiB。当前外置资源 wheel 为 66,533,566 字节，
相比此前常驻原型存档的 64,145,096 字节增加约 2.28 MiB；这个存档间比较还包含
前面的 SVG/helper/fallback 小幅改动，不能完全归因于本次 PDF 库。VSIX 约 6.85 MB。

接入时的验证结果（以下测试数量记录清理前版本，当前回归见文末）：

- 扩展 `npm run check`：245 项测试、类型检查、bundle 和产物语法检查通过。
- DOCX worker 和原生交叉引用回归：44 通过，两个需显式运行的 Word 视觉用例跳过。
- DOCX worker 和既有 HTML 服务回归：26 通过，其中实际 PDF 像素验证图片可见。
- 新的真实进程／HTTP 客户端用例验证进程复用、未保存文本和崩溃后重启。
- 同一原生 worker 验证“原生字段导出 → 静态预览 → 原生字段导出”，并验证错误恢复、
  源文件保护、认证拒绝、EOF 清理及 SVG 图片显示。
- Playwright 操作生产查看器：200 页仅绘制附近 canvas，第 100 页约 35% 的位置在
  刷新／缩放后保留；坏 PDF 保留原文档，短文档收缩页码；网络等待中的候选 PDF
  收到编辑失效通知后不发布，后续有效 PDF 可正常显示。
- 配套本地 wheel 在隔离安装后生成了含当前文本和可见蓝色 SVG 图片的一页 PDF，
  正常退出后服务工作目录为空。

本机产物在 `output/benchmarks/docx-live-preview-20261031/`：`results.json` 保存原始
耗时、输入与二进制哈希，`package-size.json` 保存体积观测，`installed-smoke.json`
保存已安装 wheel 的验证结果，`package/` 中是本地 wheel。相关构建与测试日志位于
`output/benchmarks/docx-live-*.log`。查看器日志及 VSIX 在扩展的忽略目录
`experiments/docx-preview-smoke/` 中。上述验证不是完整 VS Code 扩展宿主的端到端
按键延迟测量，也不等于 Word 排版一致性验收。

## 清理实验入口

独立的 JSONL DOCX worker、任意 options/output 构建协议及依赖它的三个 benchmark
脚本已删除；隐藏的静态引用 CLI 参数和无 SVG 备用图参数一并移除。预览静态引用
直接由内部常驻构建上下文决定。Pandoc 应用模块恢复上游实现，保留正常备用图生成。
扩展同时移除已无产品调用方的逐次 CLI 转换函数及其专属测试。

预览回归已迁移至实际 HTTP → DOCX → 内置 PDF 服务，验证未保存文本、页眉页脚
清除、同路径文献更新、样式重新读取、正式导出隔离、SVG 显示、错误恢复和 EOF 清理。
原有实验报告仅作历史测量记录，不再提供当前代码可运行的通用 worker 实验入口。

清理后的验证：扩展全套检查 243 项测试通过；预览服务、原生交叉引用和 HTML
服务回归共 61 项通过，两个 Word 视觉用例按默认策略跳过。Rust CLI 与恢复上游
应用模块后的 Pandoc worker 均重新编译。服务回归直接检查当前 PDF 内容与图片
像素，不再通过已删除的 JSONL 协议验证预览。
