# 阶段12：有来源依据的命名空间与内省隔离

本阶段依据[先行计划](stage-12-plan.md)实施。基准81b01de，独立分支codex/mvp-hardening；未改原工作区、主分支或Harness计划/Runner/节点生命周期。范围是元数据来源与SQL引用契约，非多schema架构重构。

## 确认问题与修复

- `app/models/schema.py:TableSchema`原来没有namespace身份。现在末尾追加可选schema_name/catalog_name，兼容既有位置构造；提示词展示已知来源。未获取的catalog不从连接显示名推断。
- `app/db/schema_extractor.py`：PG沿用public内省并明确保存public；MySQL保存实际查询information_schema时使用的schema条件。两者没有外部在线环境，仅检查代码和离线SQL边界。
- SQLite原来枚举main的表却用未限定PRAGMA读取详情，temp.users可以让main.users得到secret列。现统一main.sqlite_master、main.PRAGMA和main计数，表/索引标识符正确转义，特殊单双引号名称也可内省。
- DuckDB原来读取所有catalog的main，在挂载other.main.users后混合id和secret列，且表名插值未转义。现读取current_database，表/列元数据参数化限定该catalog/main；计数使用完整转义限定名，保存实际catalog，切换当前库后抽取范围随之改变。
- `app/core/schema_validation.py:check_schema_references`验证限定表名与源metadata一致后，仅对私有AST解除限定；按方言归一来源名称。SQLGlot解析后仍保留的列schema/catalog前缀代表未在作用域中解析，明确拒绝，避免只检查表不检查列。保留CTE/别名/只读及未验证namespace拒绝，primary_sql不改写。
- `app/knowledge/schema_retriever.py`裁剪stub保留namespace。消费边界现在也检查已知schema_name：同库同表但不同/缺失schema的RAG命中被丢弃，仍用原警告前缀。`app/rag/converters.py`优先源schema，未知才用原public/显式fallback。这是发现旧索引可能污染同名表上下文后对步骤3的必要补充，未引入新检索器或索引列。

## 实测与复跑

环境沿用阶段11：Windows/Python3.13.9、Node24、SQLGlot30.18.0、Ruff0.17.1、mypy2.4.0；测试用原site-packages、LLM/embedding mock。真实SQLite/DuckDB/LanceDB/FTS5，本机.venv解释器失效，使用D:/Users/anaconda/python.exe并设置PYTHONPATH为原.venv/Lib/site-packages；类型检查另加既有隔离type-deps/driver-types，未关闭strict或默认导入追踪。

- `pytest tests/test_db tests/test_core/test_sql_quality_boundaries.py tests/test_knowledge/test_schema_linking_boundaries.py tests/test_api/test_query_contract.py tests/test_rag -q`：422 passed、1 skipped、3 warnings，28.50秒。[日志](results/stage-12-target.txt)。此轮之后补2项SQLite/DuckDB大小写回归及来源归一处理，最终由全量覆盖。
- `python -m pytest -q`：**3118 passed、1 skipped、4 warnings，60.06秒**，[日志](results/stage-12-pytest.txt)。比阶段11新增43项：38项namespace/实库/传播，3项RAG schema消费，2项JSON/SSE/MCP正确/错误限定贯通；三协议保留原SQL并一致阻断错误引用。Windows uvloop仍跳过，由LinuxCI补足。
- `mypy app/harness app/models app/nodes/execute_sql.py app/nodes/generate_sql.py app/llm/budget.py app/workflow/contracts.py app/knowledge/schema_graph.py app/core/schema_validation.py`：30源文件零错误，[记录](results/stage-12-core.txt)。
- `mypy app --output json`仍退出1：**197项/42文件**，按文件/错误码/消息多重集与阶段11比较新增0；[诊断](results/stage-12-after.jsonl)、[摘要](results/stage-12-summary.json)。不能声称全仓类型通过。
- `ruff check .`和`npm run check:frontend`通过；[Ruff](results/stage-12-ruff.txt)、[前端](results/stage-12-frontend.txt)。无UI行为更改，不重复浏览器测试。
- 固定81b01de的两个原模块通过git show只读重放，在同一合成连接上对比：SQLite临时同名表使旧列为secret，新为id；DuckDB挂载同名表使旧列为id+secret，新为id。`SELECT main.users.id FROM main.users`旧校验拒绝、新通过且实际返回[[1]]。[对照](results/stage-12-namespace.json)。无LLM调用；实库夹具及断言可通过`pytest tests/test_db/test_schema_namespaces.py -q`复现。这是功能纠错，不是模型正确率或性能结论。
- `git diff --check`通过；原工作区pyproject.toml SHA256仍为E9DEAEC2052B3410FC0A3C54374DA73D3F41173D4FDDFD8FF78833A87F1011F3。既有未跟踪日志/规划文件未提交，无密钥或生成数据加入。

## 兼容、限制与后续

旧快照SQLite main/MySQL同库fallback保留；未知PG/DuckDB限定仍拒绝。新字段只在内部模型/提示词/索引使用，Schema管理API原有投影不扩展。SQLGlot引用校验不证明SQL类型、业务正确性或对象权限。

RAG源schema优先可能将旧public文档ID改为main；升级后按连接db_id调用已有`POST /api/v1/schema/{db_id}/refresh`并确认rag_indexed=true，按库替换而非追加。缺失/错误schema命中会警告并按既有路径回退。索引没有catalog字段，不能支持同db_id多catalog并存；db_id必须稳定绑定目标，详见[评测/刷新说明](rag-evaluation.md)。

表字典仍为简单名键，只覆盖既有单namespace；PG仅public、DuckDB仅当前catalog/main，不宣称跨schema/库查询已接通。未限定SQL依旧由会话search_path/temp/当前库决定，内省修复不等于执行时名称解析已绑定。应用连接需固定配置，修改目标后刷新；对象级授权、多schema与真实PG/MySQL网络联调需独立阶段。只读/工具权限/Harness不变量继续由原守卫负责，未扩大权限。

未执行生产压测、真实LLM/embedding、业务标注评测；无新的吞吐、准确率或召回结论。下一候选阶段先核对连接目标生命周期/Schema缓存与执行时名称解析一致性，按实证选择最小修复并先行规划。

## Git与远程验收

代码提交[8fd7c7f25a66653ceae4bf5c7404de23287ff44d](https://github.com/Qorq-byte/Data_engineer/commit/8fd7c7f25a66653ceae4bf5c7404de23287ff44d)已普通推送到codex/mvp-hardening，ls-remote核对SHA一致；未强推或合并main。

[Actions39618922816](https://github.com/Qorq-byte/Data_engineer/actions/runs/39618922816) **success**：Linux/Python3.12干净安装、前端编译、全仓Ruff、既有strict30源文件门禁通过；全量 **3119 passed，1 warning，32.41秒**，uvloop实际执行通过。[远程证据](results/stage-12-ci.json)保存headSha与各步骤。阶段12远程验收完成；全仓197项类型债务、外部数据库/模型和生产性能仍按上述限制保留。
