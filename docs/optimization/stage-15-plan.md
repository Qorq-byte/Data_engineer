# 阶段15计划：DuckDB解析环境与缓存来源

2026-10-22；基准024dd6c及成功CI；独立工作树codex/mvp-hardening，origin为Qorq-byte/Data_engineer。原工作区及既有未跟踪文件不动，计划先于实现。

## 范围与步骤

1. 新增纯dataclass DuckDBSourceState，记录当前catalog/schema名及原生OID、search_path、用户临时对象名集合；Snapshot追加可选状态，旧快照为None，裁剪保留。不把OID当作表列DDL版本。
2. 用原生只读元数据查询读取状态，内省前后核对，防止环境混合；cache在lease/native worker中读取并比较状态，同时保留TTL/句柄/深拷贝规则。切换catalog/schema/search_path、同名catalog重新挂载都使旧缓存失效。
3. DuckDB绑定执行与EXPLAIN在原生worker里核对请求状态；变化输出schema_changed，恢复后用户显式新请求可执行。主schema不是main或存在用户临时表/视图时保守拒绝，输出source_scope_unsupported；不按SQL前缀猜测视图内部解析，不重写SQL/SET/USE/自动重试。无绑定状态的旧快照/直接SQL维持既有只读兼容。
4. 先写真实DuckDB固定回归记录旧行为，再覆盖USE、schema/search_path变化、detach/attach同名catalog、temp表/视图、状态传播和三个协议；验证正常main/CTE/聚合与清理后恢复。不得削弱Harness固定计划、节点隔离、只读、外部访问关闭、预算、超时或取消/lease。
5. 运行定向/全量pytest、Ruff、前端、扩展strict；全仓mypy与197项基线对比，无新增诊断且保留存量失败。记录环境、命令、实测与限制，不给未测性能/检索质量结论。
6. 更新协议/运行说明/报告/进度/证据，审查暂存与凭据后commit、普通push、核对远程SHA/Linux CI，再补记远程证据；不强推、不合并main。

## 验收与限制

TTL未过时解析环境变化也能获得新来源Schema；进行中请求不悄悄迁移环境，EXPLAIN同样受限；预存临时对象和非main解析环境不能成为绑定请求的数据来源。保守限制包括有无关temp对象或完整限定SQL时也拒绝，避免临时对象/视图解析的未证明路径；清理环境后显式新请求恢复。

当前仍内省单catalog/main，未支持跨schema同名对象集合。状态核对不是数据库事务/数据快照，也不是所有表列DDL版本检测；持久视图的跨库血缘/对象权限、应用外部并发直接操作原生句柄、PG/MySQL漂移及生产性能均未覆盖。RAG仍需已有refresh替换索引，不新增catalog索引字段。

## 进度

- 计划已写入；基线Windows3163通过/uvloop1跳过，Linux3164通过，strict32源文件、全仓197项存量类型错误。

- 步骤1–5本地完成：固定13项原全部失败，首轮20项通过，最终新增35项；全量3198通过/Windows uvloop1跳过（58.88秒），定向188通过/1跳过，扩展strict33源文件、Ruff/前端通过，全仓197项且新增诊断0。补充原EXPLAIN cursor独立会话问题并以原生探针验证，属于步骤3来源绑定的必要调整。步骤6文档/证据已整理，待提交和远程验收。
