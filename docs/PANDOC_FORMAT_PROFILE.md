# 发布 Worker 的格式裁剪

日期：2026-10-03。此前的精简注册表实验现已接入正式源码构建和 wheel 打包。
本轮只删除不需要的 Reader/Writer 注册入口，不修改保留格式的解析、写出或
扩展行为。所有上游 Pandoc 模块仍编译，链接器可丢弃不再可达的格式实现。

## 保留的格式

输入共 18 个名称（部分名称是别名）：

```text
native json markdown markdown_strict markdown_phpextra markdown_github
markdown_mmd commonmark commonmark_x gfm html latex docx csljson
bibtex biblatex endnotexml ris
```

输出注册共 19 个名称：

```text
native json markdown markdown_strict markdown_phpextra markdown_github
markdown_mmd commonmark commonmark_x gfm html html4 html5 latex docx
plain csljson bibtex biblatex
```

Pandoc 的 `--list-output-formats` 另外报告通过外部程序产生的 `pdf` 目标。
文献 Reader 保留给 citeproc，HTML/LaTeX Reader 和 Markdown/plain Writer
保留给 DOCX 转换、回复与 Lua filters。

Org、EPUB、ODT、PPTX、RST、Typst、各 Wiki/幻灯片等格式不再注册，
请求它们会返回上游 unknown reader/writer 错误。Lua 的 `pandoc.read` 与
`pandoc.write` 也使用这一集合。常规 Papper 的四种构建目标与 DOCX 转换保留。

## 固定源码与发布流程

`scripts/pandoc-server/vendor/pandoc/` 维护两个完整的上游注册表模块及
`SOURCES.json`。上游 Pandoc 3.12 源码 tarball 的固定 SHA-256 是：

```text
ef4475d2b137c72340d36723d6d694c5bd7b823d7a214efc1d5b7634d5bea9b1
```

构建工具优先复用匹配的 Cabal 下载归档，否则下载固定 URL；均验证摘要。
原始源码解包至 `.pmt/pandoc-source/pandoc-3.12`，随后使用项目维护的
Readers/Writers 模块。Cabal 显式引用此本地包，启用 `-split-sections`，
HTTP、模板嵌入、Lua、citeproc 和 crossref 保留。

```powershell
cargo run --locked -p papper-dev -- worker
cargo run --locked -p papper-dev -- wheel --output dist
```

Worker 库/可执行文件的编译输出在 `.pmt/pandoc-worker`。
构建后剥离符号，将运行副本放入 `runtime/<EXE SHA-256>/`，原子更新
`current.json`。源码 CLI 通过这个记录选择 Worker，不会在每次运行时重算
整个 EXE 摘要。运行中的 Windows 服务使用不可变副本，使再次构建可以写入
Cabal 输出而不发生 EXE 被占用错误。已运行的服务继续使用原来的副本。

wheel 打包检查实际 Worker 的全部输入/输出格式，使用 `--prebuilt` 也会检查，
拒绝误用旧的完整格式版本。wheel 保留改动后的注册表、上游来源、格式清单、
各修改模块的 SHA-256 与解析出的 Cabal flags，支持复现此源码配置。

## 验证范围

现有快照独立检查 HTML、DOCX 和 build-reply 输出；不更新黄金快照。
CLI 测试不再要求裁剪后的格式列表与完整上游相同，增加真实进程检查：
已移除的 Org 输入/输出必须报错，且保全目标路径上的已有文件。
该检查覆盖裁剪后失败调用导致既有输出丢失的实际风险，而非重复断言配置清单。

本轮工作区另有 PDF 后端迁移改动，格式裁剪收益应以 Worker 的对照衡量；
最终 wheel 大小包括工作区其余已集成改动，不能全部归因于 Reader/Writer 裁剪。

## 本机验证

- GHC 9.14.1 / Cabal 3.18.1.0 实际编译，输入格式 18 个名称、输出列表 20 个名称
- Worker 未剥离符号为 185,718,784 bytes；发布/源码运行副本为 127,815,680 bytes
- 上版完整 Worker 剥离符号后为 167,417,344 bytes，本轮减少 39,601,664 bytes，约 23.65%
- 全量测试：**331 passed、12 skipped**；快照未刷新，日志 `output/worker-format-trim/pytest-final.log`
- Rust workspace：20 passed；严格 Clippy 与格式检查通过
- 在已发布 Worker 的 HTTP 服务运行期间，Cabal 重新编译并链接成功，原进程继续
  返回相同转换结果；日志 `live-rebuild.log`
- 打包器以真实旧完整 Worker 为输入时拒绝构建，没有产生 wheel；日志 `reject-full-worker.log`
- 原生输出快照覆盖 26 个 HTML/DOCX/build-reply 案例；用户回复 fixture 和黄金快照
  与提交 `910f0b976eb53a532b20c64ad6267489b669265f` 保持原字节

上述运行验证在 Windows x86-64 完成。macOS 的源码 Worker 在剥离符号后重新
进行 ad-hoc 签名，保持源码运行副本可执行；本机没有执行 macOS/manylinux CI。

## 最终发布产物

下表使用十进制 MB（1 MB = 1,000,000 bytes）。Worker 两列均已剥离符号，
可直接衡量格式裁剪的收益。

| 产物 | 完整格式基线 | 裁剪后 | 减少 |
| --- | ---: | ---: | ---: |
| Worker EXE | 167.42 MB | 127.82 MB | 39.60 MB / 23.65% |
| runtime.zip 中的 Worker 压缩条目 | 35.52 MB | 27.01 MB | 8.51 MB / 23.97% |
| runtime.zip 总大小 | 37.56 MB | 29.05 MB | 8.51 MB / 22.65% |

最终 wheel 为 **55,196,933 bytes（55.20 MB）**，其中主 CLI EXE 为
**89,245,184 bytes（89.25 MB）**。主 CLI 内嵌上述 runtime.zip；wheel 中
不再另存一份 Worker。主 CLI/wheel 数字包含同期 PDF 后端与 feature 调整，
不能将其全部变化归因于格式裁剪。

最终产物：`output/worker-format-trim/wheel/papper-0.9.2-py3-none-win_amd64.whl`。
wheel SHA-256：

```text
772c74cf5de7c2ff1b4f55cef7904352571f210bde70cfe3569a65b972a18922
```

通过 `uv tool` 安装最终 wheel 后，真实 CLI 的 HTML（含 Server）、DOCX、
LaTeX、JSON、MathType、SVG 转换像素、DOCX 导入、build-reply TXT/DOCX、
缓存与服务关闭烟雾检查全部通过，日志为 `wheel-smoke-final.log`。
精确字节数、压缩条目及摘要记录在 `artifact-sizes.json`。
