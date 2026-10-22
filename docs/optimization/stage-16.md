# 阶段16：Schema/RAG显式刷新同源与顺序

2026-10-22；依据[先行计划](stage-16-plan.md)，基准9ad0ff3及成功CI，在codex/mvp-hardening独立工作树实施。固定计划/WorkflowRunner/节点生命周期、原工作区及原有临时文件不改。

## 已确认问题与实现

原refresh_schema内省后await embedding，再无来源核对地按db_id删除/替换索引。等待期间同句柄DDL、重连或断开，旧文档仍会写入并返回rag_indexed=true。后台/启动/IndexRefresher还有重复写入口。同库并发缺少完整刷新顺序；工作台两处将HTTP fulfilled当作索引成功。

- `app/db/schema_refresh.py`共享协调器以db_id串行完成捕获句柄/info、强制内省、失效缓存、文档准备和索引提交。索引与文档转换函数由入口注入，结构类型明确，避免RAG单例循环依赖；不在LLM阶段持有数据库lease。
- `SchemaMetadataRAG.index_schemas`在同实例同db_id替换期间串行；新增before_replace回调，在embedding数量/维数检查后、任何删除前运行，空Schema删除也运行。回调核对注册身份、重新内省的namespace/源版本与当前可提取TableSchema结构，忽略行数估计/时间戳。结构变化或不能再证明来源则SchemaRefreshConflictError，缓存再次失效，旧索引保留；没有自动重试/回退到新连接。
- `app/api/schema.py`冲突HTTP409，detail.code=SCHEMA_REFRESH_CONFLICT，message固定；仅捕获连接不存在为404，内部内省异常固定500，不泄漏原生错误。200的status/rag_indexed/warnings结构保留；embedding不可用/失败或后端写失败均不报索引成功，取消继续传播。
- 连接后台任务、RAG引擎启动、手动端点和`IndexRefresher`四条入口使用同一协调器。后者仅在成功发布后记录版本/时间；周期schedule_refresh没有默认启动调用，不能说已接通实时DDL监视。
- `workspace.html`两处索引操作分别要求rag_indexed===true、refreshed===true才计成功；false/缺字段/409计失败，部分成功如实统计，无自动POST重试。新增Node检查从实际页面提取两个完整处理函数执行12个契约场景，并纳入check:frontend；未改布局。

## 验证与结果

Windows/Python3.13.9、Node24、DuckDB1.5.5、SQLite3.51.0、SQLGlot30.18.0、Ruff0.17.1、mypy2.4.0；沿用D:/Users/anaconda/python.exe加原site-packages，类型检查增加既有type-deps/driver-types，默认导入追踪不关闭。测试设置LITELLM_LOCAL_MODEL_COST_MAP=True、RAG_EMBEDDING_PROVIDER=mock、DE_TRACE_DIR为忽略目录；数据库与LanceDB/FTS5真实，embedding为16维mock。

- 先写回归后改实现：`pytest tests/test_rag/test_schema_refresh_source.py -k rejects_source_changed -q`，SQLite/DuckDB的DDL/重连/断开共**6 failed、4 deselected，12.22秒**，[基线](results/stage-16-before.txt)。首轮共享入口后**10 passed，6.07秒**，[记录](results/stage-16-initial-after.txt)；后续新增并发/空Schema/其他入口/HTTP/取消等边界。
- 最终`pytest tests/test_rag/test_schema_refresh_source.py tests/test_rag/test_index_consistency.py tests/test_api/test_schema_api.py -q`：**71 passed、3 warnings，15.88秒**，[日志](results/stage-16-target.txt)。新增27项：真实源冲突与重试、普通DML不误判、embedding异常/坏维数/取消、同库顺序/其他库独立、空替换/回调失败、四入口、HTTP409/404/500、后端部分写结果。
- 最终`python -m pytest -q`：**3225 passed、1 skipped、4 warnings，55.96秒**，[日志](results/stage-16-pytest.txt)。Windows uvloop跳过由Linux补足；既有SQL、来源绑定、权限及Harness回归未弱化。
- 扩展核心`mypy app/harness app/models app/nodes/execute_sql.py app/nodes/generate_sql.py app/llm/budget.py app/workflow/contracts.py app/knowledge/schema_graph.py app/core/schema_validation.py app/db/query_targets.py app/db/sqlite_source.py app/db/duckdb_source.py app/db/schema_refresh.py`：**34源文件零错误**，[记录](results/stage-16-core.txt)。全仓`mypy app --output json`仍退出1：197项/42文件，按文件/错误码/消息多重集比较新增0，[诊断](results/stage-16-after.jsonl)/[摘要](results/stage-16-summary.json)。不声称全仓API/RAG已通过strict。
- 全仓Ruff和`npm run check:frontend`通过，[Ruff](results/stage-16-ruff.txt)/[前端](results/stage-16-frontend.txt)。实际处理函数契约：旧页面在HTTP成功/业务失败混合例错误显示2/2完成，[原失败](results/stage-16-ui-before.txt)；修复后12场景通过，包括全部/部分失败、缺字段、409和无连接。测试验证函数行为，未作新浏览器/视觉QA，页面结构不变。
- `git diff --check`通过；原工作区pyproject.toml SHA256仍E9DEAEC2052B3410FC0A3C54374DA73D3F41173D4FDDFD8FF78833A87F1011F3，未提交原临时文件、生产数据、密钥或本地依赖。

## 计划调整与实际限制

初版协调器位于RAG包，造成与引擎单例循环依赖、核心检查卷入177项存量诊断；改为数据库层并显式注入writer/converter，恢复34源文件门禁，没有silent/skip/ignore。复核发现第四条IndexRefresher入口与前端假成功，因直接关系刷新可信度而纳入本阶段，原因已补入计划；没有新建周期任务。IndexRefresher既有Schema hash仍是启发式，不升级为数据库DDL版本。

提交前来源/embedding准备失败保留旧索引，但不是两后端事务。实测FTS5写入故意失败时，LanceDB为3条、FTS5为1条（均含另一库保留项），返回rag_indexed=false；当前没有原子回滚，需要显式重试。自定义低级upsert/clear不自动获得已注册来源验证；生产使用共享入口，后端恢复机制仍待下一阶段。

比较只覆盖当前提取模型的结构；DuckDB尚未采集的某些索引/FK等不在已验证范围。source检查与两系统持久写不构成跨进程事务；应用外部DDL仍可在核对后发生。行数估计允许随DML变化，文档统计是近似快照。普通DuckDB表列DDL未新增热缓存版本检测，仍依赖TTL/显式刷新，索引无catalog字段。PG/MySQL、真实embedding/标注检索及生产并发未验收。

刷新增加二次内省，原提取还可能COUNT估算，大Schema/大表成本待测；测试超时用于防止挂起，不是延迟SLO，没有新增吞吐/准确率/Recall等结论。下一候选优先核对双索引部分写失败恢复，先规划再实施。[查询契约](query-contract.md)/[运行说明](runtime-and-load.md)/[RAG说明](rag-evaluation.md)已同步。

## Git与远程验收

代码提交[26aef3e2b9bc05d9333bde5424d12a58f9dcdd0c](https://github.com/Qorq-byte/Data_engineer/commit/26aef3e2b9bc05d9333bde5424d12a58f9dcdd0c)已普通推送到codex/mvp-hardening，ls-remote核对SHA一致；未强推或合并main。

[Actions39732926863](https://github.com/Qorq-byte/Data_engineer/actions/runs/39732926863) **success**：Linux/Python3.12干净安装、前端编译及12处理函数契约、全仓Ruff、扩展strict34源文件门禁通过；全量 **3226 passed，1 warning，37.70秒**，uvloop实际执行通过。[远程证据](results/stage-16-ci.json)保存对应headSha及各步骤。阶段16代码远程验收完成；双索引写后失败恢复、全仓类型债务等限制仍按上述记录保留。
