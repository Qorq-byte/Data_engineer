# 阶段15：DuckDB解析环境与缓存来源

2026-10-22；依据[先行计划](stage-15-plan.md)，基准024dd6c及成功CI，在codex/mvp-hardening独立工作树实施。原工作区、既有临时文件及固定计划/WorkflowRunner/节点生命周期不改。

## 实证与实现

原DuckDB缓存只检查TTL/句柄身份；同句柄USE other后仍能拿到原catalog的表列，detach/attach同名catalog也无法区分。执行无请求来源状态检查，生成期间切库/search_path或临时对象变化可以继续使用旧Schema。DuckDB临时表还会遮蔽main.table，不能移植SQLite的authorizer策略。

- `app/models/schema.py:DuckDBSourceState`是冻结dataclass，含catalog/schema名及原生OID、search_path和用户临时对象tuple。Snapshot末尾追加可选duckdb_source_state，旧快照None，两条Schema裁剪路径保留。
- `app/db/duckdb_source.py:source_state`使用实际原生元数据函数，不从显示名猜身份。catalog OID区分同名重新挂载，schema OID区分同名namespace重建，临时对象只统计非internal对象，内部信息Schema视图不误报。`SchemaExtractor`内省前后比较，缓存命中前在lease/native worker检查，TTL/深拷贝及注册身份规则保留。
- `ExecuteSQLNode`为绑定DuckDB结果和EXPLAIN传递状态；worker内变化返回schema_changed，固定提示显式新请求。状态相同但当前schema不是main或存在用户临时对象时返回source_scope_unsupported。两者均不获取新目标、自动USE/SET、删除对象或改写SQL。
- MVP保守拒绝所有用户临时表/视图环境，包括无关对象或完整限定SQL；这避免根据SQL外层前缀猜测持久视图内部解析。清理环境后新请求可恢复，另一个catalog的main可由新请求正常使用。
- 发现DuckDB cursor()会建立独立会话，原EXPLAIN路径不适合证明原连接来源（[原生探针](results/stage-15-cursor-probe.json)：原句柄other.main、cursor为memory/main且search_path为空）；绑定EXPLAIN改为原句柄execute，最多1000计划行，不回退到无守卫会话。使用现有驱动协议cast明确原生分派边界，未放宽strict。外部访问关闭、只读策略、计时中断、取消等待、lease和Harness预算仍沿用。

## 验证与结果

Windows/Python3.13.9、Node24、DuckDB1.5.5、SQLGlot30.18.0、Ruff0.17.1、mypy2.4.0。沿用D:/Users/anaconda/python.exe与原site-packages；类型检查附加既有type-deps/driver-types，默认导入追踪不关闭。运行设置LITELLM_LOCAL_MODEL_COST_MAP=True、RAG_EMBEDDING_PROVIDER=mock、DE_TRACE_DIR为忽略目录；DuckDB/SQLite等本地实现真实，LLM/embedding mock。

- 写回归后、改实现前：`pytest tests/test_db/test_duckdb_source.py -k 'cache_tracks or running_request or unsupported_scope' -q` **13 failed、7 deselected，6.66秒**，[基线](results/stage-15-before.txt)。首轮实现同文件**20 passed，2.68秒**，[记录](results/stage-15-initial-after.txt)；后补3项EXPLAIN原会话、缓存/搜索路径及schema OID回归。
- 最终`pytest tests/test_db tests/test_api/test_query_contract.py tests/test_nodes/test_execute_sql_node.py tests/test_nodes/test_execute_sql_explain.py -q`：**188 passed、1 skipped、3 warnings，21.84秒**，[记录](results/stage-15-target.txt)。新增35项：23项实库状态/缓存/传播/兼容，12项JSON/SSE/MCP的USE/search_path/temp/预存临时对象失败与显式恢复；失败无execution事件/结果，MCP沿用WORKFLOW_FAILED，切库后的新请求实际返回新库999，其他恢复返回原库1。没有真实模型质量结论。
- 最终`python -m pytest -q`：**3198 passed、1 skipped、4 warnings，58.88秒**，[记录](results/stage-15-pytest.txt)。Windows uvloop跳过由LinuxCI补足，既有SQLite版本/main守卫回归仍通过。
- 扩展核心`mypy app/harness app/models app/nodes/execute_sql.py app/nodes/generate_sql.py app/llm/budget.py app/workflow/contracts.py app/knowledge/schema_graph.py app/core/schema_validation.py app/db/query_targets.py app/db/sqlite_source.py app/db/duckdb_source.py`：**33源文件零错误**，[记录](results/stage-15-core.txt)。全仓`mypy app --output json`仍退出1，197项/42文件，与阶段14按文件/错误码/消息多重集比较新增0，[诊断](results/stage-15-after.jsonl)/[摘要](results/stage-15-summary.json)。不声称全仓通过。
- `ruff check .`与`npm run check:frontend`通过，[Ruff](results/stage-15-ruff.txt)/[前端](results/stage-15-frontend.txt)。核心新模块已纳入CI/README/AGENTS/节点指南。`git diff --check`通过；原工作区pyproject.toml SHA256仍E9DEAEC2052B3410FC0A3C54374DA73D3F41173D4FDDFD8FF78833A87F1011F3，未纳入生产数据/密钥/本地依赖或原临时文件。

## 兼容与限制

None状态的旧快照和直接SQL维持原有只读范围，不启用新增main/no-temp限制。三协议HTTP/响应外形不变，schema_changed/source_scope_unsupported是节点状态，业务失败仍WORKFLOW_FAILED；不自动重试，生成但不执行也不能当作已验证数据库答案。

原生namespace身份不是数据/Schema事务快照或所有表列DDL版本：普通CREATE/ALTER/DROP TABLE仍可能在TTL内复用旧元数据，需显式refresh；状态核对之后应用外部并发原生操作也没有跨进程原子性保证。本阶段未新增通用多schema、跨catalog对象授权或持久视图血缘校验，视图可能引用其他库；环境固定不等于实际依赖对象都在当前catalog。

RAG没有catalog索引字段，切换目标后仍须按db_id调用已有`POST /api/v1/schema/{db_id}/refresh`并确认rag_indexed，不能凭缓存新来源就称旧索引已更新。每次状态检查增加元数据查询，大catalog/temp对象数量的性能仍待测；未执行生产压测/真实embedding或标注评测，不推断吞吐/延迟/召回提升。PG/MySQL来源漂移仍需独立环境与方案。

后续候选先复核DuckDB普通表列DDL与Schema/RAG刷新一致性，再按实证和MVP收益单独规划。运行边界见[运行说明](runtime-and-load.md)，协议见[查询契约](query-contract.md)。

## Git与远程验收

本地验收完成，待提交、普通推送、远程SHA及LinuxCI核对后补记。
