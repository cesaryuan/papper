This repository is the `papper` tool, not a manuscript project.

- Native core implementation lives in the Rust workspace under `crates/`.
- Release/development tooling lives in `tools/papper-dev/`; `tests/legacy/` is a frozen historical archive that active tests must not import or execute.
- Template content for generated paper projects lives in `template/`.
- Bundled build defaults live in `defaults/`; project override templates remain in `template/`.

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
