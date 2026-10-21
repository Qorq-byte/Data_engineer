# 阶段14：SQLite Schema版本与实际读取范围

依据[先行计划](stage-14-plan.md)，基准5212be2及成功CI，在codex/mvp-hardening独立工作树实施。原工作区/临时文件不动；固定计划、WorkflowRunner、节点流转与生命周期不改。

## 实证与范围

SQLite同连接或另一文件连接的CREATE/ALTER/DROP会改变main.schema_version，但原缓存只检查TTL/句柄身份；DDL后仍返回旧表列。执行也没有来源版本约束，生成期间DDL后可继续执行旧请求。内省只读取main，但未限定SQL可由SQLite解析到temp同名表，甚至COUNT/零行查询也可访问temp。

本阶段优先完成SQLite这条可实库闭环的链路，不把它扩大为通用schema/search_path框架。DuckDB的USE及PG/MySQL漂移机制不同，仍需独立规划；没有外部数据库/模型验收环境。

## 实现

- `app/models/schema.py:SchemaSnapshot`末尾追加可选source_schema_version，表示SQLite main.schema_version，不替代原version；旧快照及其他方言默认None。两条Schema裁剪路径保留该值。
- `app/db/sqlite_source.py`提供版本读取/核对和共享read_scope。`SchemaExtractor._extract_sqlite`前后核对，DDL夹入时拒绝混合快照；cached_extract在lease内经native_call读版本，缓存还须版本相同，沿用TTL/身份/深拷贝及关闭失效。
- `ExecuteSQLNode`只为实际SQLite来源快照传入版本；原生worker在结果执行或绑定EXPLAIN前核对。过期输出schema_changed及固定“Schema source changed; submit a new query”，不自动刷新后执行旧SQL。绑定EXPLAIN最多1000计划行，失败不回退到无守卫EXPLAIN。
- read_scope复用只读动作集合，限制绑定结果实际读取main。明确temp/attached拒绝；SQLite会为某些COUNT/覆盖索引优化回调提供database=None，因此先取main/temp对象集合，只允许确实存在main且无temp同名遮蔽的缺省来源，不能简单放行所有None。预存临时表/语句缓存也不能绕过。正常main限定/CTE/聚合保留，SQL不重写；退出清理authorizer，原有进度/超时恢复和取消等待不变。
- None版本的旧快照和直接SQL保持原读范围及只读约束；新模块纳入核心strict，CI/README/AGENTS/节点指南同步。

## 验证

Windows/Python3.13.9、Node24、SQLite3.51.0、SQLGlot30.18.0、Ruff0.17.1、mypy2.4.0；沿用D:/Users/anaconda/python.exe和原site-packages，类型检查追加既有type-deps/driver-types，默认导入追踪不关闭。LITELLM_LOCAL_MODEL_COST_MAP=True、RAG_EMBEDDING_PROVIDER=mock、DE_TRACE_DIR为忽略目录。SQLite及其他本地驱动真实，LLM/embedding为mock。

- 修改实现前，`pytest tests/test_db/test_sqlite_schema_revision.py -k 'before_ttl or stale_source or preexisting_temp' -q`：**12 failed、2 passed、5 deselected，1.42秒**，包含同/外部DDL缓存6项、过期结果/EXPLAIN2项、temp遮蔽4项失败；明确main限定2项原本成功。[基线](results/stage-14-before.txt)。首轮修复后该文件19项通过（0.79秒），[记录](results/stage-14-initial-after.txt)；后续增加7项优化回调/attached边界。
- 首轮接口回归暴露database=None兼容问题，曾11失败；原生回调探针确认，并通过预先核对main/temp对象修复，未放开未知来源。新增4项正常COUNT/索引/空结果/元数据回归及3项attached同名表拒绝。旧缓存测试从“DDL后仍应旧缓存”改为“版本变化即刷新”，保留深拷贝/显式失效断言，属于有意行为变化。
- 最终`pytest tests/test_db tests/test_api/test_query_contract.py tests/test_nodes/test_execute_sql_node.py tests/test_nodes/test_execute_sql_explain.py -q`：**153 passed、1 skipped、3 warnings，15.95秒**，[日志](results/stage-14-target.txt)。新增32项：26项SQLite缓存/版本/范围/传播/兼容，6项JSON/SSE/MCP在生成中DDL/temp变更的失败及显式新请求恢复；失败无execution事件/结果，MCP沿用WORKFLOW_FAILED。
- 最终`python -m pytest -q`：**3163 passed、1 skipped、4 warnings，92.23秒**，[日志](results/stage-14-pytest.txt)。Windows uvloop跳过由Linux补足。
- `mypy app/harness app/models app/nodes/execute_sql.py app/nodes/generate_sql.py app/llm/budget.py app/workflow/contracts.py app/knowledge/schema_graph.py app/core/schema_validation.py app/db/query_targets.py app/db/sqlite_source.py`：**32源文件零错误**，[记录](results/stage-14-core.txt)。全仓`mypy app --output json`仍退出1：197项/42文件，按文件/错误码/消息多重集比较新增0，[诊断](results/stage-14-after.jsonl)/[摘要](results/stage-14-summary.json)；不是全仓类型通过。
- `ruff check .`、`npm run check:frontend`通过，[Ruff](results/stage-14-ruff.txt)/[前端](results/stage-14-frontend.txt)。`git diff --check`通过；原工作区pyproject.toml SHA256仍E9DEAEC2052B3410FC0A3C54374DA73D3F41173D4FDDFD8FF78833A87F1011F3。未加生产数据/密钥/本地生成依赖。

## 兼容与剩余边界

版本是Schema cookie，不是数据快照/全局唯一版本或DDL事务。外部连接仍可在核对后发生DDL，不能保证跨进程核对与编译执行原子；不得手工重置schema_version。原生main限制不是表/列对象授权。None版本的旧快照、直接SQL仍保留既有读范围；PG/MySQL/DuckDB没有套用此机制。生成但不执行可返回原Schema SQL，不是当前数据库答案。

缓存自动感知DDL不会自动重建RAG，需要`POST /api/v1/schema/{db_id}/refresh`并确认rag_indexed；旧命中由已有来源/表列过滤与warnings处理。每个绑定读新增元数据扫描，大Schema的性能是待测风险；全量测试耗时变化不可推断生产吞吐/延迟，未做生产压测、真实模型/语义评测或新增容量结论。

当前业务失败HTTP/三协议外形不变，schema_changed是节点状态；重连身份守卫继续有效。下一候选先实证DuckDB同句柄catalog/schema/search_path/temp范围，选最小修复并先行规划。详见[查询契约](query-contract.md)/[运行说明](runtime-and-load.md)。

## Git与远程验收

本地验收完成，待提交、普通推送、远程SHA/Linux CI核对后补记。
