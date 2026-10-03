# Rust PDF 文本后端

更新日期：2026-10-03。

`papper-cli` 使用 `pdf_oxide` 0.3.78 直接读取 PDF，没有 MuPDF C 引擎、bindgen、
libclang 或独立 PDF DLL。开发测试中的 Python MuPDF 只生成测试输入或运行冻结参考实现，
不是产品后端。

## 依赖配置

关闭默认 features，不额外开启任何 feature。产品只需要提取非加密 PDF 的文本，
因此不启用 `legacy-crypto`，也不启用渲染、OCR、系统字体、签名或语言绑定。
关闭 `legacy-crypto` 移除可选的 MD5 依赖及旧版安全处理，但上游仍包含其他加密代码，
并不等于完全移除所有密码算法。加密 PDF 不属于产品支持契约。

该版本仍强制依赖 `office_oxide`、图像解码和布局模块，关闭默认 features 并不等于完全
只编译文本代码。`Cargo.lock` 将 `office_oxide` 固定为 0.1.9；解析到 0.1.13 时，
上游 `DocumentIR` 新增 `defined_names` 字段会使 PDF Oxide 编译失败。发布构建使用
`--locked`；更新锁文件时需要同时检查这两个库的兼容性。

## 提取与行号定位

`crates/papper-cli/src/reply/pdf.rs` 通过安全 Rust API 提取文档 Info 元数据、每页文本
和视觉行。`__pdf_extract` 保留 `metadata`、`pages[].text`、`pages[].layout` 接口，
诊断输出继续写入 stderr。

PDF Oxide 会把边栏行号和正文合为一个视觉行。适配层从每行词坐标中分离位于正文左侧的
纯数字，保持行内引用数字与正文在一起。词片段按实际水平间距连接，避免为拆开的引用、
括号和标点无条件添加空格。布局中的矩形使用 `[x0, y0, x1, y1]` 数组，供现有行号
匹配器处理。

Word 和 LibreOffice PDF 继续使用坐标配对。其他来源继续使用文本流中的正文/数字顺序。
行号解析失败或正则匹配不唯一时保留用户占位符；损坏 PDF 不覆盖已有回复文件。

## 发布

发布工作流删除了仅用于 MuPDF 的 Windows/macOS libclang 安装步骤，打包器删除了
MuPDF MSBuild 的 `_CL_=/MT` 覆盖。原有 Rust 静态 CRT 设置继续用于独立 Windows 程序。

wheel 的 `bin/pdf-notices/` 保存 `pdf_oxide` 和 `office_oxide` 的 MIT、Apache 2.0
许可、上游 NOTICE/商标说明，以及锁定版本、features 和 crate 源码下载地址。

## 验证边界

本次 Windows 验证通过：

- `cargo build --locked -p papper-cli -p papper-dev`。
- `uv run --no-sync python -m pytest tests/test_rust_reply_contract.py -q`：迁移时 14 项通过；
  后续按非加密 PDF 的产品范围移除 AES-128 参数案例，13 项重新验证通过。
- `uv run --no-sync python -m pytest tests/test_build_snapshots.py -k build_reply_output_matches_snapshot -q`：两个原有 DOCX/TXT 快照通过，没有刷新快照。
- `cargo fmt --all -- --check` 和 `git diff --check`。
- 复用现有预编译 worker/图像/Office helper，使用 `papper-dev wheel --prebuilt --embed`
  重新构建静态 CRT 的 release CLI 与内嵌资源 wheel；独立 uv 安装 smoke 全部通过，
  包括 PDF 提取、HTML/DOCX/LaTeX/JSON、MathType、SVG、导入、审稿回复及服务退出。

本次生成的 Windows release `papper.exe` 为 97,751,040 bytes，验证 wheel 为
63,604,263 bytes。改动前工作区 release EXE 为 99,646,464 bytes；这些是本地实际
产物记录，不是控制所有其他变量的严格体积基准。主要收益是移除 PDF 的 C 构建链。

现有审稿回复行为测试验证真实 Word/LibreOffice/文本流 PDF 的行号、跨行正则、失败时
数据保全及多页论文锚点；回复 DOCX/TXT 黄金快照保持原预期。
新增用真实非加密 PDF 验证预定义中文 CMap 和 Unicode 元数据，
以防后端更换后出现乱码或丢失元数据。

复杂上标、数学符号和版面可能产生与 MuPDF 不同的文本分组，不能将任何有限样本的
验证视为任意 PDF 提取都与 MuPDF 完全一致。
