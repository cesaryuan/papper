# 原生 PDF 接入与 Pandoc Worker 体积实验

日期：2026-10-03，Windows x86-64，`rust-migration` 分支，代码基线 `964a180`。
本文中 MB 为十进制字节数 / 1,000,000。实验与日志位于
`output/native-pdf-worker-review/`，该目录不参与发布。

## MuPDF 接入

`papper-cli` 固定使用 `mupdf-sys = 0.8.0`，实际内嵌 MuPDF 1.27.2。
关闭默认 features，只开启 `base14-fonts`；PDF 支持由该 crate 默认编译，
不启用 XPS、EPUB、SVG 文档读取、JavaScript、OCR 或额外 Noto/CJK 字体。
PDF 中嵌入的字体仍由 MuPDF 读取。

`crates/papper-cli/src/reply/pdf.rs` 直接链接 crate 的原生库，用具有明确所有权的
Rust 对象管理 context、document、page、structured text、buffer 和 output。
可能抛出 MuPDF 异常的操作走 crate 的 C 包装器，在 C 内捕获 `longjmp`，
随后转为 Rust `Result`。保持原有几何提取 flags、元数据和 JSON 输出结构。
产品不再有运行时 `libloading` 或独立 MuPDF DLL；bindgen 的构建依赖仍可能包含
`libloading`，这用于构建时加载 libclang，不能与产品运行时依赖混淆。

接入过程中发现该版本 `mupdf_drop_base_context` 的 Windows 析构顺序错误：
先销毁全局 `CRITICAL_SECTION`，再销毁需要这些锁的 MuPDF context。
真实 PDF 读取成功后，进程在 context 析构时发生 `0xc0000005`，GDB 定位到
`RtlEnterCriticalSection`。当前直接调用 `fz_drop_context` 释放 context，
让全局锁随独立 `__pdf_extract` 进程结束而回收，不调用有问题的 base 析构器。
每个独立进程只创建一个 context；该处理不适用于在同一常驻进程中反复初始化 base context。

Windows wheel 构建同时为 Rust 和 MuPDF C 工程选用静态 CRT。Rust 使用
`-C target-feature=+crt-static`；上游 MSBuild 工程默认 `/MD`，因此打包器通过
MSVC 最后应用的 `_CL_=/MT` 统一 C 编译选项。源码构建需要 C/C++ 编译器和
libclang；安装 wheel 后不需要这些构建工具。

MuPDF 及启用的第三方组件的许可、源码来源和实际 crate/engine 版本随 wheel 保留。
开发环境的 Python MuPDF 仍用于冻结参考测试，不能作为产品 PDF 后端。

## build-reply 快照

从用户指定的提交 `910f0b976eb53a532b20c64ad6267489b669265f` 导入
`tests/snapshot_cases/reply/` 和 `tests/snapshots/reply/`，保留原输入 PDF 与
DOCX/TXT 黄金快照，不刷新预期内容。`tests/test_build_snapshots.py` 通过公开
`papper build-reply` 命令验证它们。

指定回复快照与现有 PDF/回复契约测试：**14 passed**。这些测试覆盖真实 Word PDF
行号、不同 PDF producer 的几何、损坏 PDF 时保全已有输出、Word 缓存和 MathType OLE。

## Worker 为什么大

默认 Worker 仍使用完整 Pandoc 3.12 库，包含所有格式注册、citeproc 0.14、
pandoc-crossref 0.3.25、Lua、HTTP/server、GHC RTS 和静态 Haskell 依赖。
Pandoc 的完整格式注册表将各 Reader/Writer 函数挂到可达入口中，链接器无法仅根据
Papper 常用的输出目标将它们全部剔除。Worker 的 `convertWithOpts` 和 Lua
Reader/Writer 查找都会用到上游注册表，只限制 Papper 的参数不能得到同样的裁剪效果。

原始 Cabal EXE 为 **236,627,968 bytes**。PE 分析显示：

| 部分 | 约占字节数 | 含义 |
| --- | ---: | --- |
| `.text` | 129.57 MB | Pandoc、过滤器、依赖、RTS 等实际机器码 |
| 两个 `.rdata` | 19.73 MB | 常量和其他只读数据 |
| `.data` | 15.47 MB | 静态数据 |
| `.reloc` | 2.61 MB | 重定位记录 |
| DWARF debug sections | 0.35 MB | 调试信息 |
| COFF 符号与字符串表等可剥离内容 | 约 69 MB | 很多内容位于 section 数据之外 |

因此仅 `--strip-debug` 几乎没有作用；主要可直接删除的是 COFF 符号与字符串表。
这是文件体积分析，不能把它解释为这些符号都占用同样多的运行时驻留内存。
构建 plan 中出现的两个 Pandoc 包条目分别是主库与内部 `xml-light` 子库，
不能据此认为可执行文件链接了两份完整 Pandoc。

## 已实测的裁剪结果

编译器为 GHC 9.14.1。完整/精简控制组使用同一个本地 Pandoc 源码包、
相同依赖、`-split-sections` 和 Cabal 配置，只恢复或移除格式注册表条目。
以下 ZIP 数据使用 DEFLATE 默认设置压缩单个 EXE；实际 wheel 还包含入口、
其他组件和嵌套压缩，不能将单个 EXE 的下降比例直接套用于完整 wheel。

| 构建 | EXE bytes | 单 EXE DEFLATE bytes |
| --- | ---: | ---: |
| 原始完整 Worker | 236,627,968 | 40,935,152 |
| 原始完整 Worker，仅 strip debug | 236,277,426 | 40,795,931 |
| 原始完整 Worker，strip all | 167,417,344 | 35,599,456 |
| 同配置完整注册表控制组，strip all | 167,382,528 | 35,697,425 |
| 同配置精简注册表实验组，strip all | 127,815,680 | 27,060,090 |

直接剥离符号减少 **69,210,624 bytes（29.25%）**，单 EXE 压缩后减少约
**5.34 MB（13.03%）**。打包器已经把这一操作用于发布的 staging 副本；
原始 Cabal 产物保留，所有格式保留。

控制组到精简组减少 **39,566,848 bytes（23.64%）**，单 EXE 压缩后减少
**8.64 MB（24.20%）**。精简组相对原始未剥离 EXE 总共减少 **45.99%**。
完整控制组与原始 strip-all 的大小接近，未观察到仅改变 split-sections 就能获得
同量级收益。

## 只保留需要的 Reader/Writer 是否可行

可行，已实际编译并运行，不是仅根据源码推断。实验保留的 Reader 名称为：

```text
native json markdown markdown_strict markdown_phpextra markdown_github
markdown_mmd commonmark commonmark_x gfm html latex docx csljson
bibtex biblatex endnotexml ris
```

Writer 保留上述列表中适合输出的格式，去掉 `endnotexml`、`ris`，加入
`plain`、`html4`、`html5`。Pandoc 的格式列表另外报告特殊目标 `pdf`。
完整默认构建报告 51 个输入名、76 个输出名；实验报告 18 个输入名、20 个输出名。
名字包含别名，不等于独立实现的数量。

保留文献 Reader 很重要：Markdown 转换的 citeproc 也要读取 BibTeX/BibLaTeX、
CSL JSON 等文献输入。保留 HTML/LaTeX Reader 也不能简单按公开目标删除，
它们可能被 DOCX 读取、数学和内部转换用到。

实验版通过所有 **26 个**现有 HTML/DOCX/build-reply 输出快照，未改黄金快照。
初次采用单独资源目录的验证在 DOCX 中产生 CSL 路径差异，恢复原资源路径后
26 个全部通过；这一结果验证输出，不通过修改快照掩盖差异。

需要区分“少链接一些格式”和“完全不编译这些模块”：本次仅改变注册表，
Pandoc Cabal 的 exposed modules 未删，所有模块仍会编译，最终 EXE 少链接
39.6 MB。上游 `pandoc.cabal` 没有按 Reader/Writer 提供逐格式开关。
若要连编译过程也裁剪，需要维护 Pandoc fork，继续清理模块声明、依赖和传递引用，
同时保留 Lua、自定义过滤器、文献与 DOCX 等路径必需的内部转换能力。

**本轮发布默认值保持全格式**，只应用剥离符号。精简组放在独立实验目录，
避免未经限定就删除用户通过自定义 Pandoc 参数或 Lua 脚本访问其他格式的能力。
如果后续决定只支持明确的格式集合，应将它作为固定 revision 的 Pandoc fork
维护，而不是在每次构建中临时改写下载缓存里的源码。

上游源码依据：[Readers 注册表](https://github.com/jgm/pandoc/blob/3.12/src/Text/Pandoc/Readers.hs)、
[Writers 注册表](https://github.com/jgm/pandoc/blob/3.12/src/Text/Pandoc/Writers.hs)、
[Cabal 模块与 flags](https://github.com/jgm/pandoc/blob/3.12/pandoc.cabal)、
[mupdf-rs / mupdf-sys](https://github.com/messense/mupdf-rs)。

## 验证记录

- `cargo fmt --all --check` 通过
- `cargo clippy --workspace --all-targets --locked -- -D warnings` 通过
- `cargo test --workspace --locked`：20 passed
- 默认完整 Worker、当前静态链接 PDF 实现：`uv run --offline --no-sync pytest -q`，
  **328 passed、14 skipped**；没有新增跳过项，两个 warnings 来自冻结 Python 参考的
  tar 解压弃用提示
- 独立精简 Worker：`tests/test_build_snapshots.py`，**26 passed**
- 用户提交的输入与黄金快照保持原字节，不使用 `--snapshot-update`
- 最终 release CLI 配合发布用 stripped 完整 Worker，再次构建 DOCX/TXT 回复：
  两项均与用户原始快照一致，日志为 `release-reply-snapshots.log`

开发 debug 链接仍会显示上游 MSBuild 的动态 CRT 与其他库混用提示；
发布路径显式统一静态 CRT，不通过隐藏 linker warnings 解决问题。
以上编译和运行验证仅覆盖本机 Windows；macOS/manylinux 的工具链配置已更新，
没有把未执行的 CI 当成验证通过。

## 最终 Windows wheel

发布包保留完整格式集；本表比较本轮实际 wheel 与上轮
`output/lua-native-refactor/wheel/` 的实际 wheel，没有把实验精简组混入发布结果。

| 文件 | 上轮 bytes | 本轮 bytes | 变化 |
| --- | ---: | ---: | ---: |
| Windows wheel | 80,808,757 | 65,599,661 | −18.82% |
| `papper.exe` | 112,708,096 | 99,646,464 | −11.59% |
| 内嵌 `runtime.zip` | 57,427,419 | 37,575,750 | −34.57% |
| 完整 Worker EXE | 236,627,968 | 167,417,344 | −29.25% |
| `mupdfcpp64.dll` | 25,651,200 | 不再单独分发 | 引擎链接进 CLI |

本轮 runtime.zip 中 Worker 的压缩 entry 为 35,517,667 bytes，独立 SVG helper
仍为 3,649,536 bytes（压缩 1,640,566 bytes）。其余空间为 Lua/模板/CSL、
源码记录、许可和 C# helper 等。上轮 MuPDF DLL entry 压缩后为 14,336,101 bytes；
删除它的收益不能全部算成净 wheel 收益，因为链接的 MuPDF 代码现在计入主 EXE。
`pmt.exe` 仍为约 321 KB 的小入口，没有额外复制一份主程序。

`papper-dev smoke` 已通过 uv tool 的隔离安装，验证复制后的两种入口、原生 PDF
提取、HTML/DOCX/LaTeX/JSON、MathType 导出与导入、SVG 像素、build-reply、
热 HTML 缓存、编辑失效及服务退出。最终 `papper.exe` 的 PE imports 只有
Windows 系统 DLL，没有 MuPDF、`vcruntime140` 或 `msvcp140` DLL 依赖。
release 链接没有 CRT 冲突告警；保留的上游告警为 LTCG 链接重启和 C4702 不可达代码。

产物：`output/native-pdf-worker-review/wheel/papper-0.9.2-py3-none-win_amd64.whl`。

```text
wheel SHA-256: c78528c69da1252810b5b1d15a516965cd11bc58e9324e9d344ff6024db39e7c
```

精确 entry 清单、各产物哈希和对照数据见实验目录的 `wheel-sizes.json`；
安装验证日志为 `wheel-smoke.log`，全量测试日志为 `pytest-full.log`。
