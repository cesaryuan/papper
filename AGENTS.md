This repository is the `papper` tool, not a manuscript project.

- Native core implementation lives in the Rust workspace under `crates/`.
- Release/development tooling lives in `tools/papper-dev/`; `tests/legacy/` is a frozen historical archive that active tests must not import or execute.
- Template content for generated paper projects lives in `template/`.
- Bundled build defaults live in `defaults/`; project override templates remain in `template/`.

### 用户文档职责与同步更新

- `docs/manuscript-syntax.md` 和 `docs/style-configuration.md` 是提供给 Papper 用户及其 AI Agent 的使用指南，编译时嵌入 CLI，分别通过 `papper guide syntax` 和 `papper guide style` 获取，不随模板复制到生成的稿件项目。
- `manuscript-syntax.md` 说明支持的稿件 YAML 与 Markdown 语法、用途、可直接使用的示例、输出效果及使用限制；`style-configuration.md` 说明 `style.yml` 配置项的用途、写法、默认行为、优先级和适用范围。
- 不要把开发过程记录或内部实现说明写入这两份用户指南，包括滤镜处理顺序、内部 AST/XML 标记、缓存结构、源码位置、依赖实现、内部编号/书签算法和渲染计算细节。用户完成写稿或配置操作所必需的路径、选项和限制可以保留，但应以用户操作和可见结果解释。
- 有维护价值的实现说明放到 `docs/MANUSCRIPT_RENDERING.md`；其他开发说明放到 `docs/` 下职责合适的文档中。
- 新增、修改或删除稿件语法或其用户可见行为时，必须同步更新 `docs/manuscript-syntax.md`，说明写法、作用、示例和支持范围。新增特殊 Markdown 语法还须遵守下方的快照案例要求。
- 新增、修改或删除 style 配置项，或改变其默认行为、优先级、适用范围时，必须同步更新 `docs/style-configuration.md`。语法与配置相互关联的变更必须同时更新两份指南。
- 文档路径、章节或职责变化时，同步更新 `template/AGENTS.md`、`template/CLAUDE.md` 和中英文 README 中受影响的入口与链接，避免留下过时指引。

### 测试规范

- **测试目标**：独立验证稳定、用户可见且有实际回归风险的行为，例如：
	- 实际输出。
	- 配置优先级。
	- 缓存失效。
	- 失败恢复。
	- 数据保全。
- **新增或保留测试的依据**：
	- 除非用户明确要求，不要为每个函数或每次修改机械地新增测试。
	- 新增或保留测试前，应能说明它会捕获什么真实错误，以及已有测试为什么不能覆盖。
- **避免低价值测试**：
	- 不要添加只重复断言生产代码中的常量、默认配置、固定路径、资源存在性、包重导出、CI 配置文本或内部实现细节的测试。
	- 通常不要添加仅检查 mock 是否被调用、参数是否被原样转发、某个函数是否调用另一个函数的接线测试。
- **mock 使用边界**：
	- 允许在外部依赖边界使用 mock。
	- 断言应落在被测逻辑产生的实际行为或结果上，不能只是验证 mock 自己设定的返回值。

- 每当新增一个特殊的 Markdown 语法时，都要添加一个测试快照案例
