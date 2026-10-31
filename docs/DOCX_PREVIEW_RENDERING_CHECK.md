# DOCX 预览 SVG 与 MathType 公式缺失复核

日期：2026-10-31。针对当前内置 Docxide 0.20.1 的 PDF 预览链路复核，
不修改论文、项目样式或产品默认策略。测试输入为同一篇真实论文，原稿前后
SHA-256 均为 `0866be4130fc1dcbaf17aff8e674a8e0404c6ad30cc28e392aca14a0104727f8`。

后续已按用户要求修复预览策略：启用 MathType 时，预览内部使用 Rust 轮廓 WMF；
原生公式 opt-out 保留，正式导出仍使用作者配置。下文的 SDK 空白及三策略数据
记录修复前的诊断，不表示修复后的正常预览仍采用 SDK WMF。

## 结论

- 关闭主动 SVG filter 和 Pandoc PNG 备用图后，那篇论文的五张 SVG 确实全部未绘出。
  这不代表 Docxide 完全不支持 SVG：当前支持的是一个有限子集。
- 公式缺失与 SVG 开关无关。当前实际预览输入没有 SVG，193 个公式均已转换为
  MathType OLE 对象，其 SDK WMF 预览使用当前 Docxide 没有实现的文字绘制记录。
- 取前 12 个原始对象组成最小输入，Word 全部正常显示；相同 DOCX 经内置字节接口
  转 PDF 后只有标签，公式为空白，PDF 没有任何图片绘制或路径绘制。
- 原生 OMML 和 Rust 后端的轮廓 WMF 都能在对照中恢复公式显示，但与 SDK 排版有差异。
  本次不自动改变作者的公式配置，也不将页数相同当作视觉一致。

## 渲染器与复测方式

当前预览使用 crates.io `docxide-pdf = 0.20.1`，禁用默认 features。
其 `.cargo_vcs_info.json` 对应提交 `c2fa4fa461d987653dc717d7327096e57afb4686`，
与历史实验使用的 Docxide CLI 提交一致。

归档 DOCX 通过与服务相同的 `convert_docx_bytes_to_pdf_bytes` 接口重新转换。
简单含文字 SVG、真实论文关闭备用图版本、真实论文主动转 PNG 版本的 PDF，
均与各自历史 CLI 产物逐字节一致。因此本次空白不是 CLI 参数或输出命名差异。
局部诊断程序仅保存在忽略的 `output/benchmarks/`，不增加产品命令或分发 CLI。

完整论文另通过当前 `__docx_preview_server` 提交未保存的缓冲区做三种策略对照，
保存服务实际送给 Docxide 的 DOCX。三次请求使用同一 Pandoc PID，关闭服务后
其私有工作目录为空，原稿哈希不变。

Word 对照使用 16.0.20527.20088，通过已有 `export_word_pdf.ps1` 只打开测试副本，
不更新字段、不退出 Word。两次导出均确认原有用户文档仍打开。

## SVG：支持形状不等于支持任意组合图

当前上游 [`docx/svg.rs`](https://github.com/sverrejb/docxide-pdf/blob/c2fa4fa461d987653dc717d7327096e57afb4686/src/docx/svg.rs#L149-L163)
遇到 `text`、`tspan`、`image`、`use` 等元素时返回不支持，随后尝试 PNG 备用图；
不会只丢掉该元素而继续绘制整个 SVG 的其他部分。

用相同无备用图 DOCX，仅替换其中 SVG 字节的最小对照：

| SVG 内容 | 矢量绘制数 | 实际绘制位图数 | 蓝色像素数 |
| --- | ---: | ---: | ---: |
| 纯蓝色矩形 | 1 | 0 | 44,551 |
| 蓝色矩形加文字 | 0 | 0 | 0 |
| 通过 `use` 引用矩形 | 0 | 0 | 0 |

这也解释了旧报告的“简单 SVG 也空白”：旧样例包含 `text`，不能将它作为
纯形状 SVG 不受支持的证据。

五张真实 SVG 各自隔离为仅包含该 drawing 的 DOCX 后，仍均为 0 路径、0 位图：

| DOCX part | 论文题注 | 当前不支持的内容 |
| --- | --- | --- |
| `rId81.svg` | Figure 10：训练损失曲线 | 两个子图片和文字 |
| `rId90.svg` | Figure 12：COLMAP 对比 | 四个子图片和文字 |
| `rId99.svg` | Figure 14：网格对比 | 十个子图片和文字 |
| `rId103.svg` | Figure 15：纹理对比 | 十八个子图片和文字 |
| `rId113.svg` | Figure 17：消融对比 | 图片、61 个 `use`，以及蒙版／滤镜等 |

部分组合图还有外部子图片引用，没有在 SVG 中内嵌。它们即使保留 Pandoc
备用图，也可能在备用图生成时丢失子图片，这与直接 SVG 渲染失败需分别处理。
最后一张含 data URI 图片，因此也不能将五张空白统一归因于外部资源路径。

当前项目 `docxConvertSvgToPng: true`，正常预览的 DOCX 为 0 个 SVG，
图片已由主动 filter 转成 PNG。上述五张是关闭主动 filter 的对照输入，
不能描述成“当前默认预览有五张 SVG 空白”。

## 公式：SDK WMF 的文字绘制尚未实现

论文配置为 `mathtype: true`、`mathtypeConversionMethod: both`。
`both` 会比较两种后端的公式数据，并在 SDK 成功时采用 SDK 的 OLE 和 WMF。

实际 DOCX 保留 193 个 OLE 对象、193 个 WMF，0 个 OMML。193 个 WMF 均含
`META_EXTTEXTOUT`（`0x0a32`），使用文字绘制字母和符号；只有第 188 个还含
一个多边形记录。当前 Docxide 的
[`wmf_to_emf`](https://github.com/sverrejb/docxide-pdf/blob/c2fa4fa461d987653dc717d7327096e57afb4686/src/docx/wmf.rs#L329-L332)
明确不绘制 WMF 文字，未使用 OLE 内部的 MTEF 来代替绘制公式。

因此构建日志的 `converted=193, retained=0` 只能证明 MathType 转换成功，
不能证明 PDF 已完整绘制这些公式。公式编号来自普通正文，可能正常显示而公式主体为空。

同一常驻服务的完整论文对照：

| 仅在未保存快照中使用的策略 | DOCX 中的公式 | Docxide 页数 | 前 12 个公式最小对照 |
| --- | --- | ---: | --- |
| 原项目 `both` | 193 个 SDK WMF／OLE，0 个 OMML | 37 | 公式主体全部空白 |
| `mathtype: false` | 193 个 OMML，0 个 OLE | 39 | 原生公式恢复显示 |
| `mathtypeConversionMethod: rust` | 193 个轮廓 WMF／OLE，0 个 OMML | 39 | 公式恢复显示 |

Rust 输出的 193 个 WMF 都包含多边形轮廓，不含 `META_EXTTEXTOUT`，可由当前
Docxide 绘制。完整 PDF 的路径绘制数从 544 增至 1,871；最小样例中所有
前 12 个公式均已目视复核。没有逐个核验所有复杂公式的数学和视觉等价性。

Word 导出原项目同一 DOCX 为 39 页，公式正常显示；这不证明两种 39 页的
Docxide 对照已经与 Word 逐页等价。37 页结果包含公式缺失，不能当作更紧凑
排版或完整预览成功的证据。

## 后续实现与剩余范围

已在预览内部选择 Rust 公式后端，使预览保留 MathType 形式并绕开 SDK WMF
文字支持缺口；正式导出继续使用作者配置，`mathtype: false` 仍保留原生公式。
覆盖发生在依赖检查和缓存选择前，不要求本机 MathType SDK，继续使用现有 Rust
公式缓存及配置的公式渲染器／字体。字体、公式尺寸和分页可能与 SDK 输出不同。
原生 OMML 也是可用对照，但同样改变公式形式和排版。若要求 SDK WMF 的原始
外观，则需要补齐渲染器的 WMF 字体／文字绘制，或增加带缓存的可靠图片转换。

SVG 不宜统一关闭备用图。支持的纯形状可以直接绘制；本论文这五张复杂 SVG
仍需要完整的栅格化路径，不能以删掉图片后的 PDF 耗时作为有效提速。

## 本地证据

材料位于忽略目录 `output/benchmarks/docx-render-investigation-20261031/`：

- `current-results.json`、`current-service.log`：实际 HTTP 请求、构建／转换耗时和公式类型计数。
  本次用于内容诊断，未做多轮配对计时，不据此给出新的性能提升百分比。
- `current-{mathtype,omml,rust-wmf}.{docx,pdf,txt}`：三种完整论文输出。
- `math-wmf-minimal.{docx,embedded.pdf,word.pdf}`：同一份 12 公式 DOCX 的两后端对照。
- `math-minimal-comparison.png`：左 Word、右当前 Docxide，显示空白问题。
- `math-recovery-{rust,omml}.png`：Word 与两个可显示方案的局部对照。
- `svg-shape-{only,text,use}.*`、`minimal-results.json`：SVG 支持范围最小对照。
- `paper-rId*.{docx,embedded.pdf}`、`isolated-svg-results.json`、`svg-parts.json`：五张 SVG 的隔离结果。
- `wmf-records.json`：原项目 193 个 WMF 的绘制记录分布。

这些本地输入含论文相关资源，仅用于本地诊断，没有上传或提交上游。
