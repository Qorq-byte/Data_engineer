# 剩余工作完成路径与验收记录

日期：2026-11-17。核查基准为当前工作区，包含开始前已有的 Snowflake 未提交切片。
截图仅作为待核查清单。状态拆成实现、接线、测试、外部验收四层；以下各阶段互不以文件存在替代验证。

**同日后续进展**：确定性 metric_query v1 已继续实施，详见
[最新范围、配置与验收](metric-query-v1.md)。以下核查和 A–D 数字保留为前一阶段的历史基线；
其中“指标缺接线/始终阻断”的描述已由新版的受限、显式目录查询替代。业务目录与扩展语义仍待签收。
共享 worker 的目录封存和独立进程验收继续见[共享目录阶段](shared-metric-catalog.md)。

## 核查结论与代码证据

- **Snowflake：已实现、已接线、本地策略已测、实库待验收。** `app/db/snowflake_source.py` 的 `connect/query/extract_schema/close`
  包含可选驱动、密码认证、固定 namespace、线程卸载、行数/超时/取消；`ConnectionFactory._connect`、
  `SchemaExtractor.extract`、`ExecuteSQLNode._execute_bounded` 与 `app/api/schema.py` 构成请求路径。
  本轮修复 `_bounded_query` 对 quoted namespace 的大小写比较和漏过的 TableFromRows，新增 51 项纯策略回归；
  这些测试不模拟驱动。原生权限、元数据、主链路、取消终态的四项实库测试当前只可跳过。
  详见 [Snowflake 验收条件](snowflake-acceptance.md)。
- **metric_query：组件已实现并测试、完整请求未接入。** `app/nodes/metric_resolve.py:164` 附近依次检查 engine 与
  `input.config.metrics`；空指标名不能从自然语言自动推断。`MetricFlowEngine.query_metrics`（`app/knowledge/metricflow_layer.py:301`）
  需要能查到的指标、可信 formula/table、匹配真实 Schema 的维度/过滤列；时间粒度依赖 time_column。
  多表不会自动构造 JOIN（当前取首个指标的 table），不应将跨表业务指标视为已支持。模板使用需要单独审核。
  `metric_query.yml:28` 仅声明 refine，`GenerateSQLNode.execute` 只处理 self_heal 等现有逻辑，并未读 metric_sql；
  `MetricResolveNode.update_context` 写 sql/metric_sql，也不直接创建执行器消费的 candidates。
  `QueryRequest` 没有 metrics 参数，`PLAN_BLOCKERS`（`app/workflow/plan_runtime.py:58`）在准入前拒绝该计划。
  `tests/test_nodes/test_metric_resolve.py`、`tests/test_knowledge/test_metricflow_layer.py` 验证组件，
  `tests/test_api/test_plan_execution.py::test_blocked_plan_reports_the_reason` 验证请求拒绝。
  即使补齐 engine/名称，也必须补目录版本/作用域、SQL→候选/校验/执行的桥接与结果测试后才能解除阻断。
- **多 Agent：组件部分实现，主链路未编排。** `main.py:105` 只创建并注册六角色；
  `app/agents/sql_generator.py::SQLGenerationAgent.execute` 无 generator 时返回空候选。
  编排算法/消息总线的 `tests/test_agents/` 不能证明真实模型/数据库多 Agent 完整执行。
  `app/subagent/instance.py:197` 的 metric capability 是 pass，delivery Web/MCP 只是配置枚举。
  本轮 REST NL 查询实际接到固定 `submit_query`，覆盖 ACL、域/库范围、执行开关和主链路安全门禁；
  证据为 `tests/test_api/test_subagent_workflow.py`。内部实验接口仍不是安全执行入口。
  详见 [REST 支持边界与兼容变化](subagent-rest-boundary.md)。
- **多租户：未实现，不能给多个互不信任团队共享部署。** `app/auth/models.py:69` 的内部 User dataclass 没有 tenant；
  `app/auth/access.py:11` 只做 operator allow-list；`app/storage/mysql_store.py` 的连接/设置/历史表与全局 `get_store`、
  `ConnectionFactory`、查询会话、RAG 索引、学习规则库均为共享状态。
  影响包括管理员间可见连接/密钥设置（加密不等于权限隔离）、历史与 Schema/检索/学习结果交叉影响；
  子 Agent 的单接口范围不改变普通工作台权限。实现边界必须覆盖身份、所有读写路径、唯一键、缓存、备份与 MCP，
  还需审计和跨租户负例，不能仅给 SQL 加 WHERE 或仅加 API 参数。
- **业务准确率/语义召回/生产容量：未建立。** `app/evaluation/ranking.py:6` 计算二元相关性的 Recall@K/Precision@K/MRR/nDCG；
  `scripts/evaluate_quality.py` 默认只跑 BM25 合成集，sql_model_metrics 在无预测时为空。
  `app/evaluation/contract_suite.py` 与 `scripts/evaluate_sql_contracts.py` 的 gold self-check 不是模型准确率；
  `scripts/load_test.py` 明确为进程内 ASGI、固定 SQL、SQLite、无 lifespan/RAG/真实模型。
  历史真实供应商合成证据见 [当前运行时合成基线](current-runtime-baselines.md) 和
  [30 次 DeepSeek 容量样本](deepseek-capacity-run-2026-11-12.md)，不能改称业务基线。

## 阶段 A：连接器安全与验收入口（必须，P0）

- 目标：使 Snowflake 的本地 SQL 边界可重复验证，并能在独立账号提供后运行同一验收套件。
- 范围：`app/db/snowflake_source.py` 的确定性策略；专门的本地策略测试；`tests/real_db/test_snowflake.py`；部署验收说明。保留原有连接器接线。
- 前置：本地需 Python 3.12+ 与开发依赖；实库需可选驱动、专用数据库/schema/warehouse、原生只读角色、预置合成数据、允许的云计算费用。
- 交付：策略回归、显式 opt-in 的实库套件、fixture 契约；无凭据默认 skip，开启却缺配置必须失败。
- 验收：本地拒绝写 SQL、多语句、跨命名空间与不安全函数；正常 SELECT/CTE/集合运算有行数边界；实库覆盖元数据、截断、主工作流执行、超时/取消与连接重用。原生拒写须独立于应用解析验证。
- 命令：`.venv/Scripts/python.exe -m pytest tests/test_db/test_snowflake_source.py -q`；外部准备后 `DE_REAL_SNOWFLAKE_TESTS=1` 运行 `tests/real_db/test_snowflake.py`。准确启用方式见 Snowflake 验收文档。

## 阶段 B：子 Agent REST 安全接线（必须，P0）

- 目标：请求不能覆盖配置的域/数据库范围；运行共享的固定 YAML Harness，继承权限、只读、预算、准入、超时与追踪。
- 范围：REST 适配层与真实 SQLite API 集成测试。保留实验编排器组件及其测试，不将其注册状态宣称为主链路多 Agent 已完成。
- 前置：A 的本地基线；已有 JWT operator 依赖、QueryRequest/submit_query；配置的数据库必须能映射到已登记 db_id。
- 交付：身份白名单检查、作用域校验、显式执行开关、受保护的 query 路径；context 不得注入引擎对象/SQL/连接或修改范围。
- 验收：允许身份可用固定五节点读取真实 SQLite；越域/越库/越权被拒绝；execute=false 不执行；配置禁执行无法由请求开启；恶意生成 SQL 无法写库；预算和响应追踪来自主服务。
- 命令：`.venv/Scripts/python.exe -m pytest tests/test_api/test_subagent_workflow.py tests/test_subagent tests/test_agents tests/test_api/test_plan_execution.py -q`。
- 边界：本阶段不提供租户隔离、独立子 Agent LLM 配额、自主协商执行或跨进程子 Agent 每小时限流。

## 阶段 C：指标、隔离与评估证据收口（必须，P1）

- 目标：给出可操作的完整执行条件；建立本地工程基准与真实质量基准之间的证据边界。
- 范围：支持矩阵、本文、指标/租户/评估验收说明；运行已有离线评估和有界本地负载探针。
- 前置：不依赖云凭据；生产准确率须有授权且冻结的业务数据、专家 gold SQL/结果、独立测试集；语义召回须有相关性标注与真实 embedding 配置。
- 交付：实现/接线/测试/外部状态分开记录；报告包含命令、数据来源、范围、哈希与运行环境。
- 验收：metric_query 继续在 API 提前明确拒绝；不会将引擎单测等同完整执行；合成集/固定 SQL 数据不宣称业务准确率或生产容量。
- 命令：`python -m scripts.evaluate_quality`、`python -m scripts.evaluate_schema_linking`、`python -m scripts.evaluate_sql_validation`、`python -m scripts.evaluate_sql_contracts`、`python -m scripts.load_test`（均使用 `.venv/Scripts/python.exe` 和本次专用输出路径）。

## 阶段 D：整体本地验收（必须，P1）

- 目标：变更达到可审查、可复验状态；任何红门禁必须修复或如实作为阻塞记录。
- 范围：相关测试、全量无外部服务测试、ruff、AGENTS.md 核心 mypy、新增模块类型检查、前端和文档检查。
- 前置：A/B/C 本地工作；外部服务的 skipped 不计入 passed。
- 交付：`progress.md` 中的命令与结果、支持矩阵、外部阻塞清单。
- 验收命令：`pytest -m 'not real_database'`、`ruff check .`、AGENTS.md 的核心 mypy 命令、`npm run check:frontend`、`npm run check:frontend:build`、`python scripts/check_readmes.py`。

## 需要进一步确认的扩展（依赖产品或外部条件）

1. **metric_query 生产接线（P2）**：可信版本化指标目录、请求的显式指标名/维度/过滤契约、域与库绑定、跨表 join 规则，以及确定性指标 SQL 是否允许 LLM 改写。当前 YAML 的 `mode: refine` 不代表生成节点已经消费 `metric_sql`。补齐后再开放 API，使用真实 SQLite 验证指标结果、未知指标、跨域、危险公式与多方言。
2. **租户隔离（P2，高风险/大工作量）**：确定一部署一团队还是共享 SaaS。共享方案需可信身份→tenant 映射，数据库和复合唯一键迁移，连接/设置/会话/追踪/历史/学习规则/指标/RAG/缓存/审计/MCP 的统一作用域；回填、回滚、备份和跨租户负例。不能只加 tenant_id 或依赖 SQL 提示词。
3. **自主多 Agent（P2，高风险）**：明确用户价值和协作模式；角色产出只能作为固定计划的数据，不能决定节点下一步。所有调用进入同一预算/权限/审计与 ExecuteSQLNode，按失败/取消/并发的端到端证据验收。
4. **真实质量与容量（P1，外部阻塞）**：冻结标注集并按域/难度/语言切片，防训练泄漏；缺失/失败/截断预测保留分母；执行结果比较须约定顺序、NULL、重复行、浮点/时间语义；报告置信区间。容量需固定机器、拓扑、模型/embedding/DB、数据量、冷热缓存、并发/到达率、持续时间与通过阈值，包含错误率/503/超时/p50/p95/p99/吞吐/资源/费用。业务负责人签收，不从合成小样本外推。
5. **BigQuery/Redshift/Databricks（P3）**：仅方言定义，按实际部署需求逐一实现和独立验收，不因方言已定义而启用连接器。

## 当前外部阻塞

- 未提供本轮 Snowflake 专用验收凭据、预置 fixture 与计算费用范围；不连接未知账号或使用本机潜在生产凭据。
- 未提供业务指标目录与上述指标执行语义。
- 未提供业务标注数据、真实质量阈值及生产容量环境/SLO。
- 多租户和自主多 Agent 的产品范围未确定；当前支持边界仍是共享管理员工作台。

## 本轮结果

- A 本地部分完成，外部验收待提供专用环境；B 的受约束 REST 适配已完成；C 本地评估/边界文档已完成。
- 相关测试：435 passed / 4 skipped；其中 Snowflake 51 项纯策略、子 Agent 15 项 SQLite API 回归。
- 第一轮全量（原 `.venv`，Python 3.13.6）4659 passed / 1 skipped / 145 deselected；但依赖检查有 18 项版本偏差，
  不用这轮替代锁定环境验收。33 项检查完全匹配的现有 `.stage62/.venv` 为 Python 3.12.13，实际导入确认是**当前工作区 app**，
  不是旧快照代码；使用它进行最终回归及评估，不安装/升级或改写用户的原环境，也不改变 constraints.txt。
- 新增 Snowflake 协程清理类型及原 API probe result 重定义修复后，锁定环境 `ruff check .` 与 `mypy app/`（209 files）通过。
- `npm run check:frontend`、`npm run check:frontend:build`、`python scripts/check_readmes.py` 通过；
  build 检查中故意注入的编译错误是“失败时保留旧产物”的通过条件，不是项目构建失败。
- 离线数据：`tests/fixtures/retrieval_eval.json`（7 查询/8 文档，K=3），BM25 Recall@3=1、Precision@3=0.380952、MRR/nDCG=1；
  Schema linking 合成集 12/12，validator 合成集 18/18。SQL contract 的 12 项 gold self-check 通过，**无模型预测**，
  所以 robust_matches=0/4、MISSING_PREDICTION=12 不代表模型准确率为 0，也不代表结果通过质量阈值。
- 本地负载：20 请求/档、1000 行、并发 1/2/5/10、两场景（direct SQL/fixed-SQL pipeline），每场景预热 3 次；
  此短探针只验证本地行为，未运行真正模型/embedding，没有业务数据或生产 SLO；具体环境、各分位数、错误/503 和哈希见验收汇总。
- D 的锁定环境验收已完成：Python 3.12.13，全量 `pytest -m 'not real_database'` 为 **4659 passed / 1 skipped /
  145 deselected**（274.96s）；唯一本地 skipped 是 Windows 不支持/未安装 uvloop，145 项外部数据库案例未纳入本次运行。
  独立 Snowflake 入口为 **4 skipped**，不计实库通过。现有两项 Pydantic ReadOnly 提示为非失败警告。
- 锁定环境查询流水线在并发 1/2/5 各 20/20 成功；并发 10 为 8 成功/12 HTTP 503（准入槽=8），
  属于背压行为验证。直接 SQL 四档均 20/20；原始分位数、短批次时长、CPU/RSS 缺测字段均保留在汇总，不外推生产容量。
- 机器可读汇总：[completion-2026-11-17.json](results/completion-2026-11-17.json)，含源码文件、数据集和报告 SHA-256、
  锁定版本、JUnit 计数、评估口径与完整 8 组探针指标。原始本地证据在 ignored `data/acceptance-20261117/`。

## 复验命令与下一步

在匹配 `constraints.txt` 的 Python 环境运行；本机已验证解释器为 `.stage62/.venv/Scripts/python.exe`，
工作目录必须是当前仓库根目录。下方以 `python` 代表该解释器：

```powershell
python -m scripts.check_dependencies
python -m pytest -m 'not real_database' -q --tb=short
python -m pytest tests/real_db/test_snowflake.py -q
python -m ruff check .
python -m mypy app/
npm run check:frontend
npm run check:frontend:build
python scripts/check_readmes.py
python -m scripts.evaluate_quality --output data/acceptance-20261117/recheck-quality.json
python -m scripts.evaluate_schema_linking --output data/acceptance-20261117/recheck-linking.json
python -m scripts.evaluate_sql_validation --output data/acceptance-20261117/recheck-validation.json
python -m scripts.evaluate_sql_contracts --output data/acceptance-20261117/recheck-contracts.json
python -m scripts.load_test --output data/acceptance-20261117/recheck-load.json --requests 20 --rows 1000
```

本轮没有发起真实模型/embedding/Snowflake 调用，没有运行管理员 DDL，没有部署或发布。
下一步优先按 Snowflake 验收说明提供专用 fixture、只读角色与费用范围并运行四项实库测试；
并行由业务方确认指标定义/目录和标注集，冻结质量与容量阈值。多租户先确定部署边界再实施迁移，
自主多 Agent 先确定协作语义及固定计划约束。上述扩展和外部项保持未完成，不能把本地阶段通过等同项目全部生产可用。
