This repository is the `papper` tool, not a manuscript project.

- Core implementation lives in `src/pandoc_manuscript/`.
- Template content for generated paper projects lives in `template/`.
- Keep changes scoped to the file being edited. Do not rewrite generated output or cache directories unless the user explicitly asks.
- When changing CLI behavior, template copying, or packaging, update the corresponding docs and run a focused syntax check or smoke test.

测试应独立验证稳定、用户可见且有实际回归风险的行为，例如实际输出、配置优先级、缓存失效、失败恢复和数据保全。除非用户明确要求，不要为每个函数或每次修改机械地新增测试；新增或保留测试前，应能说明它会捕获什么真实错误，以及已有测试为什么不能覆盖。删除只重复断言生产代码中的常量、默认配置、固定路径、资源存在性、包重导出、CI 配置文本或内部实现细节的测试；仅检查 mock 是否被调用、参数是否被原样转发、某个函数是否调用另一个函数的接线测试，通常也应删除。允许在外部依赖边界使用 mock，但断言应落在被测逻辑产生的实际行为或结果上，不能只是验证 mock 自己设定的返回值。

精简测试以行为覆盖为依据，不以测试数量或文件大小为依据。已有集成测试或输出测试充分覆盖的辅助函数测试应删除；参数化只保留会触发不同逻辑、失败模式或有明确回归价值的边界场景，避免重复枚举等价输入和无必要的笛卡尔积。断言必须能区分正确实现与错误实现，删除未接入执行流程的空列表断言、输入恰好等于默认值等无法证明测试意图的检查；优先验证最终内容或可观察结果，避免锁定内部元数据和调用顺序。删除后运行保留的相关测试，较大范围精简时运行完整测试集，确认关键行为覆盖仍然有效。
