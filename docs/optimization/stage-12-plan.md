# 阶段12计划：有来源依据的表命名空间

基准81b01de，继续codex/mvp-hardening。先规划后实施；原工作区及未跟踪临时文件不动。

## 范围及顺序

1. TableSchema末尾追加可选schema_name/catalog_name，兼容既有构造。PG只声明实际内省的public；SQLite只声明main；MySQL使用SELECT DATABASE结果；DuckDB读取current_database并仅内省该catalog的main，不合并挂载库。未知字段不猜测。
2. 校验私有AST的限定表名与源元数据匹配后再解除限定，原SQL不改写；列的命名空间同样验证。旧快照保留SQLite main/MySQL同库兼容，未知PG/DuckDB限定仍拒绝。不得关闭只读/作用域检查。
3. SQLite内省明确main并正确转义标识符；DuckDB元数据条件参数化，计数使用完整限定标识符。Schema裁剪保留字段，提示词展示来源，RAG已有schema_name优先实际元数据，旧数据保留既有fallback。
4. 真实SQLite/DuckDB验证同名对象、挂载库、特殊标识符、限定查询执行；离线PG/MySQL解析验证正确/错误限定、引用大小写、CTE/别名。补传播回归。未连接PG/MySQL不声称在线兼容。
5. 运行定向及全量pytest、Ruff、前端编译、既有strict门禁；全仓mypy与197项基线比较，保留既有失败。Harness计划/Runner/节点职责不变。
6. 更新实现文档/报告/结果/进度，审查差异与敏感信息，commit和普通push，核对远程SHA/CI后补记。禁止强推和合并main。

## 验收与限制

有证据的schema/catalog限定名通过，错误限定不能借同名表通过；不同挂载库元数据不混合，生成/执行SQL不被验证器修改；所有现有门禁通过且无新增类型诊断。表字典仍以简单表名为键，仅支持内省既有单namespace，不扩展多schema、跨库FK、租户权限或RAG catalog索引结构。schema_name变化可能改变SQLite/DuckDB索引文档ID，既有索引需重建，不代表召回质量提升。

## 进度

- 计划先于代码修改写入；步骤1–5已完成。新增43项，本地全量3118通过/Windows uvloop1跳过；strict30源文件、Ruff/前端通过，全仓197项存量且新增诊断0。
- 步骤3补充同库schema命中拒绝，原因及旧索引迁移见报告。步骤6文档/证据已整理，等待提交推送与远程验收。
