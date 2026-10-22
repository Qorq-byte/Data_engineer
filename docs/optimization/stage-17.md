# 阶段17：Schema 双索引失败补偿与检索隔离

2026-10-22；依据[先行计划](stage-17-plan.md)，基准 cd45122，在 codex/mvp-hardening 独立工作树实施。保持固定 YAML 计划、WorkflowRunner、节点生命周期和只读 SQL 边界，未修改原工作区。

## 问题与实现

阶段16已记录 FTS 写失败时 LanceDB/FTS5 计数由 2/2 变为 3/1。先将原回归改为要求旧索引恢复，运行 `pytest tests/test_rag/test_schema_refresh_source.py -k partial_backend -q`：旧实现 **1 failed、26 deselected，7.60 秒**，实际正是 3/1。此前失败后需显式重建；本阶段补偿恢复该路径。

- `LanceDBStore.snapshot_database/restore_database` 按 db_id 完整导出/恢复原始记录，扫描显式 `limit(None)`，保留 float32 向量和 UTC 时间戳；恢复前校验每条记录的 db_id。准备时检查新 doc_id 是否占用另一库记录，拒绝覆盖。
- `BM25Index.snapshot_database/restore_database` 保存原始 `(doc_id, tokenized_text, metadata_json)`，不调用 embedding 或重复分词。恢复前回滚失败写操作遗留的未提交事务，然后在本地事务内仅替换目标库记录。值绑定参数；碰撞检查用 JSON 参数，不拼接值。
- 新增 `BM25Index.delete_database` 严格删除路径。旧 `delete_by_field` 对 OperationalError 返回 0，无法支持恢复判断；协调替换和 Schema 删除改用传播异常的严格路径，其他 namespace 的旧 API 不改。真实 SQLite authorizer 拒绝 FTS DELETE 时，返回失败并隔离，不能误报成功。
- `SchemaMetadataRAG._replace_database` 在同 db_id 的既有 gate 内，embedding 和 before_replace 校验后保存两个快照，全部准备成功后才删除。同步删除/两边写入发生异常时分别尝试恢复两边，即使一边恢复失败也尝试另一边。新批次 doc_id 必须唯一，防止同一批在两后端产生不同文档数。
- `SchemaIndexWriteError.restored` 与固定消息区分恢复结果。补偿成功仍然是本次更新失败，不能将旧内容当作新内容成功发布。共享刷新协调器保持 HTTP200/rag_indexed=false，warnings 表示 previous index restored 或 recovery failed，不输出原始后端消息。前端沿用阶段16业务成功判定。
- 补偿失败或旧状态已经不可信时，将 db_id 记入同实例 `unavailable_databases`。指定受影响库及无范围检索（含空字符串）均拒绝，健康库指定检索继续可用；查询 embedding 等待前后均检查，避免等待中失效后继续读混合索引。Schema Linking 沿用已存在的异常降级与 warning，使用已捕获 Schema 的关键词匹配，不会消费被隔离索引。
- 完整的同库替换、空替换或成功删除能解除对应标记；完整清空能解除所有标记，向量清空使用严格 delete 而非吞错误的 drop。普通 upsert、其他库刷新、失败的再刷新均不解除标记。未新增后台自动重试。

## 验证环境、命令与结果

Windows、Python3.13.9、SQLite3.51.0、DuckDB1.5.5、SQLGlot30.18.0、Ruff0.17.1、mypy2.4.0、Node24。使用 `D:/Users/anaconda/python.exe`，PYTHONPATH 指向原 `.venv/Lib/site-packages`；类型检查额外指向既有 type-deps/driver-types。设置 LITELLM_LOCAL_MODEL_COST_MAP=True、RAG_EMBEDDING_PROVIDER=mock、DE_TRACE_DIR 为忽略目录。LanceDB/FTS5/数据库真实，embedding 为 mock，未访问生产数据或调用真实模型。

- 定向 `pytest tests/test_rag tests/test_knowledge/test_schema_retriever.py -q`：**339 passed、3 warnings，37.10 秒**，[日志](results/stage-17-target.txt)。随后补充真实 FTS 删除权限拒绝测试，运行 `pytest tests/test_rag/test_schema_refresh_source.py -k 'real_fts_delete or quoted_database' -q`：**2 passed、50 deselected、3 warnings，5.83 秒**，[日志](results/stage-17-native-denial.txt)。两条命令有一个重复测试，不将通过数简单相加。
- 新增25项回归，原阶段16部分写失败用例升级为完整恢复要求。覆盖向量/FTS 删除和插入前后异常、26条原始记录完整备份（超过默认搜索数量）、精确内容恢复、中文分词文本、未提交 FTS 部分插入、快照准备失败、单/双后端补偿失败、同库重试/旧不可信状态、健康库隔离、embedding 等待后再检查、空替换、跨库 doc_id 碰撞、重复 ID、引号标识、恢复范围拒绝及原生权限拒绝。
- 最终 `python -m pytest -q`：**3250 passed、1 skipped、4 warnings，63.48 秒**，[日志](results/stage-17-pytest.txt)。Windows uvloop 跳过由 Linux CI 补验。既有 SQL/权限/预算/来源绑定/Harness 回归不弱化。
- 核心 strict 命令沿用 `.github/workflows/checks.yml` 的 `mypy app/harness app/models app/nodes/execute_sql.py app/nodes/generate_sql.py app/llm/budget.py app/workflow/contracts.py app/knowledge/schema_graph.py app/core/schema_validation.py app/db/query_targets.py app/db/sqlite_source.py app/db/duckdb_source.py app/db/schema_refresh.py`：**34源文件无错误**，[记录](results/stage-17-core.txt)。全仓 `mypy app --output json` 仍退出1，**197项/42文件**，按文件/错误码/消息多重集比较新增0、删除0，[诊断](results/stage-17-after.jsonl)/[摘要](results/stage-17-summary.json)。没有宣称全仓 strict 通过。
- 全仓 `ruff check .`、`npm run check:frontend` 通过，[Ruff](results/stage-17-ruff.txt)/[前端](results/stage-17-frontend.txt)。前端覆盖5段页面脚本编译及12个实际刷新处理函数契约；未修改布局、未进行新浏览器 QA。
- `git diff --check` 及原工作区保护检查通过；原 pyproject.toml SHA256 仍为 E9DEAEC2052B3410FC0A3C54374DA73D3F41173D4FDDFD8FF78833A87F1011F3。验证日志/类型诊断保存到 results，不提交本地依赖、数据目录、密钥和原有临时文件。

## 计划调整与边界

复核发现 FTS 删除吞异常、跨库 doc_id 碰撞和同批重复 ID 均会破坏恢复前提，故一并增加严格删除及准备期拒绝；计划已明确这些范围。恢复也不能修复一个已经不可用的历史快照，因此重复失败继续隔离，只有完整替换成功才确认修复。没有关闭检查或弱化既有安全回归。

这是进程内、同实例、同事件循环的补偿机制，同步写/恢复区间无 await；既有同库 gate 仍负责准备期顺序。两个持久系统没有原子事务，进程崩溃/掉电、跨进程直接读写及多个 RAG 实例不在保证内。隔离标记不持久化；重启不能被当作修复，持久部署发生异常退出后应在开放检索前显式逐库全量刷新并核对 rag_indexed=true。快照只作内存备份，不包含可供重启恢复的日志。

首次已有的不一致数据不会被本阶段自动识别/对账；普通 upsert 和其他 namespace 不纳入补偿。低级 clear/remove 没有来源校验和本阶段替换的备份补偿，生产修复优先走 source-checked 的 schema refresh；它们成功时才清理已存在标记。不要绕过 SchemaMetadataRAG 直接查询底层存储来规避隔离。FTS rowid 不保证恢复原值，原始内容/向量/元数据保持，不承诺同分数排序完全相同。

备份按目标库记录量占用额外内存，扫描/写/恢复仍同步，可能阻塞服务事件循环；大 Schema、磁盘满/损坏、多进程及生产负载尚未实测，不能宣称性能提升或事务级稳定性。真实 embedding、PG/MySQL、业务标注 Recall/SQL 正确率仍未验收；全仓类型债务继续保留。下一候选先审查持久索引启动就绪与失败可见性，再按收益确定最小阶段范围，不直接引入分布式事务。

## Git 与远程验收

待本地验收后审查暂存文件、提交普通推送、核对 origin 分支 SHA 及 Linux CI，并补记证据。不合并 main、不强推。
