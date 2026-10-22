# 阶段19：管理员按库 Schema 索引清理与恢复

2026-10-22，依据[先行计划](stage-19-plan.md)，基准22794b9及成功CI，继续codex/mvp-hardening独立工作树。固定计划、WorkflowRunner、节点生命周期、只读SQL约束与原工作区不变。

## 问题、取舍与实现

阶段18已隔离持久遗留库，但只有全量清理HTTP入口；旧接口忽略db_id后默认all，未知scope还会零操作报成功。断开连接直接同步remove_database，未使用补偿及同库刷新顺序，失败被吞。选择增加单库维护闭环，保留既有全量维护窗口操作；未引入持久tombstone、分布式任务或新前端。

- `SchemaIndexClearResult` 是纯frozen dataclass。`SchemaMetadataRAG.clear_database` 使用同实例同库替换gate，复用空批次 `_replace_database` 原始双后端备份与补偿；返回目标库真实旧记录数（dense/sparse可不同），完整成功才解除对应待刷新/恢复失败状态。未知ID或重复操作返回0/0，其他库及namespace不改。
- `app/db/schema_refresh.py:clear_database_schema_index` 使用与完整刷新相同的外层schema-refresh gate，锁顺序仍是外层刷新→内层替换。刷新已开始则先完成，再清理，不让旧准备任务在清理后回写；清理后开始的新刷新可以重新建索引，不是永久禁用。等待可取消，清理同步提交区间无await；其他库独立。
- `IndexRefresher.invalidate_database` 只pop目标库版本/最后成功时间，成功回调在外层gate释放前运行；失败不失效缓存，不清其他库、pending或历史startup_schema_results。后续显式刷新无需等原TTL。
- 新管理员入口 `POST /api/v1/learning/rag/schema/{db_id}/clear`，路径必须指定目标，不依赖源库仍在线，不调用源SQL或embedding。200返回status=cleared、scope=schema、db_id、removed两计数、cleared_at。401/403沿用既有operator鉴权；引擎不可用503/RAG_UNAVAILABLE；准备或写失败503/SCHEMA_INDEX_CLEAR_FAILED，restored=true/false/null分别表示已补偿、恢复失败/旧不可信、未能进入写入恢复阶段。原始后端错误不公开。
- 旧 `POST /learning/rag/clear` 保持有效body scope的维护窗口调用，但写前422拒绝任何query参数、未知body字段（含db_id）和非法scope；固定detail.code=INVALID_RAG_CLEAR_REQUEST。缺body/空body仍兼容默认all，不能用于定向清理。
- `disconnect_database` 关闭前将该库索引标为待刷新，再断开原连接并走共享清理；取消在关闭之后发生也不会继续信任旧索引。响应保留连接状态，增加rag_index_cleared/warnings；无法清理不假装连接断开失败，固定warning说明按库重试。已断开db_id可用新接口重试，无需重建原连接。同步remove_database保留低级兼容边界，生产HTTP不再直接使用它。

## 验证环境与证据

Windows/Python3.13.9、SQLite3.51.0、DuckDB1.5.5、SQLGlot30.18.0、Ruff0.17.1、mypy2.4.0、Node24。`D:/Users/anaconda/python.exe`，PYTHONPATH指向原site-packages，类型检查增加既有type-deps/driver-types；LITELLM_LOCAL_MODEL_COST_MAP=True、RAG_EMBEDDING_PROVIDER=mock、DE_TRACE_DIR为忽略目录。实际持久LanceDB/FTS5与SQLite源库，16维mock embedding，HTTPX ASGITransport在同事件循环执行真实鉴权/路由；不访问生产数据、真实模型或外部PG/MySQL。

- 先加2条HTTP回归，旧实现 `pytest tests/test_rag/test_schema_maintenance.py -q`：**2 failed、3 warnings，8.69秒**，[日志](results/stage-19-before.txt)。实际404缺按库接口、200忽略db_id。首轮修正 **2 passed、3 warnings，8.70秒**，[记录](results/stage-19-initial.txt)。
- 最终定向 `pytest tests/test_rag/test_schema_maintenance.py tests/test_rag/test_schema_refresh_source.py tests/test_rag/test_engine_startup.py tests/test_api/test_schema_api.py -q`：**138 passed、3 warnings，39.46秒**，[日志](results/stage-19-target.txt)。新增30项覆盖401/403、legacy误用拒绝/有效兼容、单侧记录/真实计数/幂等/缓存隔离、其他库和文档指标完整内容保留、源表不改、准备/删除前后/恢复故障、脱敏、显式重试/重建、引号ID与重启、同库刷新双向顺序、取消及其他库独立、断开成功/部分失败/无RAG/关闭后取消。
- 最终 `python -m pytest -q`：**3298 passed、1 skipped、4 warnings，95.04秒**，[日志](results/stage-19-pytest.txt)。Windows uvloop跳过由Linux补验，既有Harness/只读/权限/来源绑定/恢复回归不弱化。
- 核心 `mypy app/harness app/models app/nodes/execute_sql.py app/nodes/generate_sql.py app/llm/budget.py app/workflow/contracts.py app/knowledge/schema_graph.py app/core/schema_validation.py app/db/query_targets.py app/db/sqlite_source.py app/db/duckdb_source.py app/db/schema_refresh.py`：**34源文件零错误**，[记录](results/stage-19-core.txt)。全仓 `mypy app --output json` 仍退出1：**197项/42文件**，对阶段18按文件/错误码/消息多重集比较新增0/删除0，[诊断](results/stage-19-after.jsonl)/[摘要](results/stage-19-summary.json)。新API首轮多1项dict缺参数，已补足dict[str, object]，没有放宽检查或移动存量基线。
- `ruff check .`、`npm run check:frontend`通过，[Ruff](results/stage-19-ruff.txt)/[前端](results/stage-19-frontend.txt)，包括5段脚本编译/12实际刷新函数契约；未改UI，未新增浏览器QA。没有生产压测或真实语义质量评测，不用功能耗时推断吞吐/召回提升。
- 原工作区pyproject.toml SHA256仍为E9DEAEC2052B3410FC0A3C54374DA73D3F41173D4FDDFD8FF78833A87F1011F3，原有未提交文件与临时文件不纳入提交。

## 使用与边界

1. 管理员先 `GET /api/v1/learning/rag/status` 核对schema_index两个当前列表，历史startup_schema_results不是当前失败列表。
2. 仅清遗留/需重建的目标：`POST /api/v1/learning/rag/schema/{URL编码的db_id}/clear`。核对200、scope/schema、db_id及removed；不调用无scope的旧全量接口。
3. 仅清遗留库不必刷新其他库；目标若仍连接且希望恢复检索，再 `POST /api/v1/schema/{db_id}/refresh`，确认rag_indexed=true。清理返回0/0是幂等完成，不代表该库有可检索数据。
4. 503时按restored和固定message排查底层存储，再显式重试；restored=true说明本次删除失败后旧记录已恢复，启动待刷新仍可能存在，不代表已重新验证。restored=null没有证明发生写入，不能当作恢复成功。补偿失败维持隔离，成功按库清理后解除。

引擎因损坏索引/版本不兼容未初始化时503，不能靠新入口自动删除文件，需要既有离线备份/新目录重建流程。清理只删索引，不删源表/数据、不断开连接、不影响文档指标；disconnect是另一个明确关闭连接的操作。备份内存、同步I/O、同一事件循环/实例及非崩溃原子事务的阶段17限制仍在；外部直接写、跨进程、直接低级upsert/全量clear竞争未保证。全量清理仍需维护窗口，不能与在线逐库刷新混用。

清理与刷新按实际取得gate顺序生效，后续新请求/定时或后台任务可以再次建索引，未实现永久禁用。未增加UI入口，管理员目前使用HTTP API；没有真实存储损坏恢复/容量/Recall/SQL准确率验收。下一候选先核对工作台索引维护是否完整消费当前状态及新接口，再按收益独立规划。

## Git 与远程验收

待本地通过后完成暂存差异/凭据检查、commit/普通push、远程SHA和Linux CI核对，并补记文档。不合并main、不强推。
