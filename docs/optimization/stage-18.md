# 阶段18：持久 Schema 索引启动验证与失败可见性

2026-10-22，依据[先行计划](stage-18-plan.md)，基准836d83e及成功CI，在 codex/mvp-hardening 独立工作树实施。固定工作流计划、WorkflowRunner、节点生命周期与只读边界不变，原工作区不改。

## 已确认问题与实现

旧引擎在启动索引任务之前发布 _engine，逐库刷新失败被忽略；重启构造的 Schema RAG 没有阶段17隔离记录，持久旧内容不经来源重新验证即可读取。`learning/rag/status` 只显示刷新器缓存时间，`health/ready` 的 rag 布尔值来自启动快照，不能反映当前索引隔离。

- `LanceDBStore.database_ids` 仅读取完整 db_id 列，显式取消默认搜索条数；`BM25Index.database_ids` 从全部原始 metadata 中提取不同 db_id。只存在某一侧的记录也必须重新验证；空/缺失/非字符串所有者或损坏 JSON 使初始化失败，不自动认领或删除旧数据。
- `SchemaMetadataRAG.require_refresh/refresh_required_databases` 增加独立待刷新状态；指定目标库以及无范围检索在 embedding 等待前后检查。局部 upsert、其他库的完整刷新和失败的再刷新不解除；完整替换、空替换、成功删除/清空才解除对应状态。阶段17的 recovery-failed 隔离继续单独保留。
- `initialize_rag_engine` 使用既有 gate 串行、幂等；`_build_rag_engine` 局部构建，按两后端持久库ID与当前连接库的并集标记待刷新，再进行启动刷新。全部初始化任务返回后才发布全局 candidate，构建中 getter 仍报未初始化。异常或取消捕获 BaseException 只用于清理并原样重抛，分别尝试释放两存储句柄；清理失败不覆盖原错误或跳过另一个后端。
- `_index_all_connected_schemas` 记录每库成功/失败，单库失败继续处理其他库，固定日志不输出底层异常；`startup_schema_results` 是启动历史，不因后续手动刷新改变。真正当前状态取自待刷新/恢复失败集合。术语初始化保持原有行为，未承诺文档/指标 namespace 的完整性验证。
- `get_schema_index_status` 提供当前 Schema trust 状态及启动结果。受鉴权保护的 `GET /api/v1/learning/rag/status` 保留 last_refresh_time/pending_count/tracked_databases/ttl_minutes/current_time，新增 schema_index；顶层 status 为 ok/degraded/error，初始化不可用时固定 message=`RAG engine unavailable`，不公开原始异常。
- 公开 `GET /health/ready` 动态计算 components.rag，新增 rag.status 与待刷新/恢复失败计数，不公开数据库ID、路径或启动详情。整体SQL就绪仍按初始化/auth/真实LLM/连接注册判断，RAG可关键词降级，不将索引降级机械变为整体503；探测声明仅进程状态，不调用存储/模型探活。`/health`存活语义不变。

## 验证环境与结果

Windows/Python3.13.9、SQLite3.51.0、DuckDB1.5.5、SQLGlot30.18.0、Ruff0.17.1、mypy2.4.0、Node24。`D:/Users/anaconda/python.exe` 加原 `.venv/Lib/site-packages`，类型检查加既有 type-deps/driver-types；LITELLM_LOCAL_MODEL_COST_MAP=True、RAG_EMBEDDING_PROVIDER=mock、DE_TRACE_DIR 指向忽略目录。LanceDB/FTS5 使用真实临时持久目录，重开时保留 embedding manifest，embedding 为16维 mock。没有真实模型/生产数据或外部 PG/MySQL。

- 先写两项回归，旧实现 `pytest tests/test_rag/test_engine_startup.py -q`：**2 failed，8.84秒**，[失败日志](results/stage-18-before.txt)。分别证实遗留索引可读、初始化中全局实例已发布。首轮修改后 **2 passed，8.26秒**，[记录](results/stage-18-initial.txt)。
- 定向 `pytest tests/test_rag/test_engine_startup.py tests/test_rag/test_schema_refresh_source.py tests/test_api/test_bootstrap_lifecycle.py tests/test_api/test_runtime_limits.py -q`：**78 passed、3 warnings，23.39秒**，[日志](results/stage-18-target.txt)。新增18项覆盖持久重开/恢复失败后重启、单库失败与健康库独立、显式恢复、两阶段取消清理、三类部分构建失败、并发/重复初始化、超过默认搜索数的所有者并集、非法/损坏所有者、embedding等待后检查、状态权限与动态就绪、清理异常不掩盖原失败。
- 最终 `python -m pytest -q`：**3268 passed、1 skipped、4 warnings，73.01秒**，[日志](results/stage-18-pytest.txt)。Windows的uvloop跳过由Linux补验，既有安全/来源绑定/补偿/Harness回归不弱化。
- 既有核心 strict 命令见 `.github/workflows/checks.yml`：`mypy app/harness app/models app/nodes/execute_sql.py app/nodes/generate_sql.py app/llm/budget.py app/workflow/contracts.py app/knowledge/schema_graph.py app/core/schema_validation.py app/db/query_targets.py app/db/sqlite_source.py app/db/duckdb_source.py app/db/schema_refresh.py`，**34源文件零错误**，[记录](results/stage-18-core.txt)。全仓 `mypy app --output json` 仍退出1：**197项/42文件**，对比阶段17按文件/错误码/消息多重集新增0、删除0，[诊断](results/stage-18-after.jsonl)/[摘要](results/stage-18-summary.json)。不宣称全仓API/RAG已过strict。
- 全仓 `ruff check .`、`npm run check:frontend` 通过，[Ruff](results/stage-18-ruff.txt)/[前端](results/stage-18-frontend.txt)，包括5段页面脚本和12个实际刷新处理函数契约。无布局变化，未进行新浏览器QA；这些功能测试耗时不是生产延迟或检索质量指标。
- 原工作区pyproject.toml SHA256仍为E9DEAEC2052B3410FC0A3C54374DA73D3F41173D4FDDFD8FF78833A87F1011F3，原有未提交内容与临时文件不纳入提交。

## 取舍、运维与限制

本阶段没有持久事务日志；通过每次启动重新建立待刷新门禁，避免进程内 failure 标记丢失后直接信任旧内容。单次运行期间外部进程写坏索引仍可能不被状态感知；直接自建 SchemaMetadataRAG 的兼容调用不自动启用引擎启动门禁。生产入口应使用引擎/共享刷新协调器，不直接操作底层表绕过状态。

仍可连到相同 db_id 的库用 `POST /api/v1/schema/{db_id}/refresh` 完整刷新，必须确认 rag_indexed=true；补偿恢复旧记录的 rag_indexed=false 不解除启动待刷新。不能重连的遗留库保持 blocked，健康库指定检索仍可用；目前无逐库 HTTP 索引清理接口，管理员可在维护窗口调用受保护 `POST /api/v1/learning/rag/clear`，显式 body={scope: schema}，再逐个刷新当前连接库并核对业务成功及状态。该操作会清空全部 Schema 索引，不能用默认 all 代替；文档/指标原始内容无需因 Schema 遗留库而删除。底层 `remove_database` 仅供受控维护调用，不是新增HTTP接口。

待刷新/恢复失败列表仅表示引擎当前已知信任状态，不是TTL承诺、实时DDL监视或后端健康。历史 startup_schema_results=false 在后续修复后仍保留，不能把历史结果当当前失败；tracked_databases 仍是刷新器启发式缓存规模，不代表已验证库数。无待刷新内容的空索引可报 Schema 状态 ready，不代表有业务数据或真实 embedding 可用。

初始化串行 gate 为同一事件循环；不支持多进程/跨事件循环单例协调。应用已有10秒初始化截止，存储枚举同步、启动还要刷新 Schema/术语，大规模时可能阻塞或超时后整体RAG降级，需实测。没有新增吞吐/P50/P95/P99、Recall/MRR/SQL正确率结论。其他namespace、热配置重新初始化、后台周期调度、持续对账及生产资源容量不在本阶段。下一候选先检查管理员定向清理/恢复操作能否完整闭环，再单独规划最小范围。

## Git 与远程验收

代码提交 [1fa0c1b761e78c2d4ce9d43266fa194583d8dadc](https://github.com/Qorq-byte/Data_engineer/commit/1fa0c1b761e78c2d4ce9d43266fa194583d8dadc) 已普通推送至 codex/mvp-hardening，ls-remote 核对SHA与本地一致。暂存23个范围内文件经过凭据模式扫描、完整差异审查和diff check，失败日志仅清除尾随空格，保留原始命令/断言/结果；阶段文档10个本地链接检查通过。原工作区及已有临时文件不改，不强推、不合并main。

[Actions39768158966](https://github.com/Qorq-byte/Data_engineer/actions/runs/39768158966) **success**：Linux/Python3.12、Node22干净安装，前端编译/12处理函数契约、Ruff、strict34源文件全部通过；全量 **3269 passed、1 warning，40.79秒**，uvloop在Linux实际执行通过。[远程证据](results/stage-18-ci.json)保存对应headSha/结果/各步骤。阶段18代码远程验收完成，生产容量/多进程/其他namespace等限制继续保留。
