# 阶段14计划：SQLite Schema版本与实际读取范围

基准5212be2及成功CI，继续codex/mvp-hardening独立工作树。原工作区和既有临时文件不动，先规划后实现。

## 范围与顺序

1. SchemaSnapshot末尾追加可选source_schema_version，表示实际SQLite main.schema_version，其他方言/旧快照为None。内省前后检查版本，变化时拒绝生成混合快照；裁剪保留来源版本。
2. cached_extract在现有lease内读取SQLite main版本，缓存仍受TTL/句柄身份约束，版本不符重新内省。不同外部连接DDL也应触发，无sleep/关闭缓存。保留深拷贝与关闭失效。
3. 执行SQLite来源快照时，原生worker在EXPLAIN/结果执行之前检查版本，不符返回schema_changed，固定提示新请求；不自动刷新并用新Schema执行旧SQL。使用共享authorizer约束带来源版本的查询只读main，拒绝temp/attached读取，原SQL不改写。无来源版本的直接SQL/旧快照保留既有只读行为。
4. 先固定实库回归并记录修复前失败，再验证同句柄/外部文件连接create/alter/drop、缓存深拷贝、来源裁剪、旧快照拒绝、新请求恢复、预存临时同名表/聚合/EXPLAIN及JSON/SSE/MCP。Harness、权限、只读、超时、取消/连接lease不削弱。
5. 定向和全量pytest、Ruff、前端、扩展核心strict通过；全仓mypy与197项基线比对，无新增诊断且如实保留失败。记录环境/命令/结果/功能对照，未压测不推断性能。
6. 更新查询契约/运行说明/阶段报告/进度及证据，审查暂存与凭据，commit、普通push，核对远程SHA/Linux CI后补记；不强推、不合并main。

## 验收与边界

DDL后TTL尚未到期也能获得新main Schema；已生成请求不悄悄接受新版本；临时同名表不能提供带main来源的查询结果；main限定、CTE/聚合与正常新请求可执行，直接SQL原有接口兼容。

本阶段只为SQLite建立版本和原生读范围保护，不把schema_version当作数据快照/业务正确性/权限体系。版本检查与执行之间外部连接的DDL竞态仍由SQLite锁/重新编译语义约束，不承诺分布式DDL事务快照；应用不得手工重置schema_version。DuckDB/PG/MySQL的catalog/search_path/DDL漂移需独立阶段，不套用SQLite机制或无依据自动SET/USE。RAG索引仍需既有refresh入口更新，本阶段不声称自动重建索引。

## 进度

- 计划已写入；基线Windows3131通过/uvloop1跳过，Linux3132通过，核心strict31源文件，全仓类型197项。

- 步骤1–5本地完成：旧固定14例12失败/2通过，首轮19项通过后补充优化回调边界；新增32项，全量3163通过/Windows uvloop1跳过（92.23秒），定向153通过/1跳过。扩展strict32源文件、Ruff/前端通过，全仓197项且新增诊断0。SQLite database=None兼容调整及旧缓存断言更新见报告，未降低来源约束。步骤6文档/证据已整理，待提交和远程验收。
