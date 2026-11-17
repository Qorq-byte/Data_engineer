# 未实现领域收口实施计划

日期：2026-11-16。代码基准：`445e195` 加当前工作区（工作区已存在 Stage 67 Snowflake 未提交切片，本计划不改动这些文件）。

本文回答一个问题：**图上标为“部分实现／未实现／未建立”的领域，哪些是真差距、差距在哪、按什么顺序补、补完怎么验收。**
判定口径沿用 `docs/audits/2026-10-20-project-assessment.md`：只按“用户请求真正走到的链路”判完成度，不以文件存在、YAML 数量或测试数量换算完成百分比。

## 一、现状核对

| # | 领域 | 图上状态 | 代码事实（核对结论） |
|---|---|---|---|
| 1 | 固定五节点 request_query 流水线 | 已实现 | ✅ 属实。`app/workflow/query_service.py:274-320` 固定构造 parse/link/gen/validate/exec，JSON/SSE/MCP 共用 `submit_query`。 |
| 2 | 约束、权限与 SQL 策略执行 | 已实现 | ✅ 属实。`ConstraintEnforcer` + `PermissionManager` + `ExecuteSQLNode` 只读检查，均有测试。 |
| 3 | Schema 链接、多阶段校验、混合检索 | 已实现 | ✅ 属实，组件层可用。 |
| 4 | 7 个只读数据库连接器 | 已实现并测试 | ✅ 属实；Snowflake 已在工作区实现但无实库验收，不计入已验证范围。 |
| 5 | 其余 6 个工作流计划 | 仅模板 | ⚠️ 属实。6 个 YAML 可加载、节点类也都在，但 `query_service` 只认 request_query；`QueryRequest` 没有 plan 字段，因而 API 无法执行任何其他模板。 |
| 6 | 自动计划选择 | 部分实现 | ⚠️ 属实。`app/workflow/plan_selector.py` 已实现 L1/L2/L3 + 历史存储，但 `grep PlanSelector` 在请求链路中零调用；`/workflows/simulate` 是另一套写死的关键词猜测（`app/api/workflows.py:168-200`）。 |
| 7 | 多 Agent 编排与子 Agent | 部分实现 | ⚠️ 属实。`OrchestratorAgent`/`collaboration`/`subagent` 完整，但只有 `/api/v1/subagents/{name}/query` 旁路；主请求仍走 WorkflowRunner。 |
| 8 | 反馈驱动的规则学习 | 部分实现 | ⚠️ 属实。`PatternAnalyzer` 能产出候选、`/learning/rule-candidates/review` 能改状态，但 approved 规则没有任何消费方：生成提示、校验、修复三条链路都不读它。 |
| 9 | 多租户隔离 | 未实现 | ⚠️ 属实。`app/auth/models.py` 的 `User` 无 tenant 字段；`mysql_store` 全部表按全局唯一键；连接、设置、历史、`data/learning.db` 都是进程/部署级共享。 |
| 10 | 校准的准确率/召回率/容量基线 | 未建立 | ⚠️ 属实。`acceptance_suite.self_check()` 明确写了 “not model accuracy”；既有 63/64 证据是合成集契约匹配，不是业务语义准确率。 |

**结论**：第 1–4 项可以继续按“已实现”对外表述；第 5–8 项的差距是**接线**，不是组件缺失；第 9–10 项是**新领域**，受 DDL/部署决策与外部标注数据阻塞。

## 二、差距定义

### G1 工作流模板可执行（优先级 P0）

- **目标**：`gensql_agentic`、`ez_query`、`reflection`、`chat_agentic`、`explore` 通过同一个查询 API 可执行，且只读、LLM 预算、行数上限、语句超时、审计、会话语义与 request_query 等价。
- **范围**：
  - 做：plan 驱动的节点装配；按节点名映射 SSE stage；`plan_id` 入参；不可执行模板的显式拒绝与原因；响应中的 `plan_output`。
  - 不做：`metric_query`（没有指标目录，`MetricResolveNode` 需要 `config.metrics` 显式指标名）；自定义计划落盘；前端计划编排器。
- **依赖**：无。`WorkflowRunner` 已支持 `on_pass/on_fail` 反射回跳。
- **验收标准**：
  1. `POST /query {plan_id:"ez_query"}` 返回 `plan_id="ez_query"`，实际节点顺序为 link→gen→exec（无 validate）。
  2. `reflection` 在首次执行报错时按 `reflect→revise→exec2` 回跳，且 `max_iterations` 生效；成功时直接结束。
  3. 未注册或不可执行的 `plan_id` 返回 400 且给出具体原因，不静默降级。
  4. 每个模板都受 `max_result_rows`、`statement_timeout_ms`、`max_llm_calls_per_query` 约束；`execute=false` 时任何模板都不执行 SQL。
  5. JSON 与 SSE 对同一请求给出相同 `plan_id`、节点集合与 `status`。

### G2 自动计划选择（优先级 P0，依赖 G1）

- **目标**：请求链路真正调用 `PlanSelector`，只在可执行模板中选择；选择结果可解释、可回放，默认关闭以保持现有契约。
- **范围**：
  - 做：`auto_plan` 开关；L1/L2/L3 选择；不可执行计划过滤；选择元数据入响应；`/workflows/simulate` 改用同一实现；计划目录的 `query_executable` 由能力表推导。
  - 不做：在线学习式路由（`record_outcome` 的自动调参）；按用户/租户的差异化路由。
- **依赖**：G1 的能力表；L2 需要 `PlanHistoryStore` 落盘路径。
- **验收标准**：
  1. `auto_plan=true` 时，含 JOIN 的查询命中 `gensql_agentic`，短列表查询命中 `ez_query`，选择结果带 `layer/reason/confidence`。
  2. 历史命中：同一查询二次提交时 `layer="L2"`（前提 L1 未命中）。
  3. 自动候选集只能落在 `request_query` 与 `ez_query`；`metric_query`、`reflection`、`chat_agentic`、`explore` 永不被自动选中。`/workflows` 的 `query_executable` 是全部可执行模板的集合，二者不相等是预期行为，需在文档写明。
  4. `auto_plan=false`（默认）时既有字段与改造前一致，仅新增默认空值的 `plan_output/plan_selection/applied_rules`；`plan_id` 仍为 `request_query`。
  5. `/workflows/simulate` 返回的 `selected_workflow/layer/reason` 与真实请求一致。

### G3 反馈驱动规则闭环（优先级 P1）

- **目标**：审批通过的候选规则进入下一次生成的提示与校验，形成“用户编辑→候选→审批→生效→可观测”的闭环。
- **范围**：
  - 做：approved 规则持久化到独立 SQLite（无 MySQL 也可用）；映射为 `BusinessRule`；注入 PromptBuilder 的 rules 段与 `SQLValidator` 第 5 步；响应暴露 `applied_rules`；拒绝/撤回即时失效。
  - 不做：用规则自动改写 SQL（`sql_template` 语义不保证安全，越权风险高于收益）；跨租户规则共享（依赖 G5）。
- **依赖**：无（`BusinessRule(pattern, enforce)` 与 `CandidateRule.suggested_pattern/suggested_enforcement` 已可直接映射）。
- **验收标准**：
  1. approve 一条规则后，下一次同域查询的 `business_rules` 含该规则，且 `applied_rules` 列出其 id。
  2. pattern 与查询不匹配时不注入、不出现在 `applied_rules`。
  3. reject 后不再注入；重复 approve 幂等。
  4. 无 MySQL、无 LLM 的环境下闭环仍可跑（mock 路由器 + SQLite）。
  5. 规则存储损坏/不可读时查询不失败，只在 `warnings` 说明降级。

### G4 多 Agent 编排与子 Agent（优先级 P2，本轮只做边界诚实化）

- **目标**：要么让主链路可选地走编排器，要么明确它只是旁路能力，不再让“启动时注册”被读成“请求可用”。
- **范围（若接线）**：`OrchestratorAgent` 需要与 `ConstraintEnforcer`、只读策略、审计、行数上限对齐；`SQLGenerationAgent` 需要真实 generator/连接注入；子 Agent 需要租户/域级访问控制。
- **依赖**：G1（计划执行语义）、G5（访问控制）。
- **验收标准（若接线）**：编排模式的 SQL 执行必须经过 `ExecuteSQLNode` 与只读策略；`max_llm_calls` 与审计计数覆盖所有 Agent 调用；失败可回退到 request_query。**本轮不满足，故不接线。**
- **本轮动作**：文档明确“旁路 API 可用、主链路不调用”，不再列为产品能力。

### G5 多租户隔离（优先级 P2，受 DDL/部署决策阻塞）

- **目标**：每个租户拥有独立的数据源、设置、历史、会话与学习库；管理员组不再共享同一份数据。
- **范围**：租户模型与 `users.tenant_id`；JWT claim；`datasource_connections/user_settings/query_history/feedback/learned_rules` 的租户过滤；会话键加租户前缀；迁移与回退脚本；默认 `tenant=1` 保持单租户行为。
- **依赖**：部署拓扑（单库多租户 vs 每租户独立 schema/库）；MySQL DDL 授权；备份/回退演练。
- **验收标准**：跨租户读、写、执行、学习库访问全部 403/404；迁移可在一次性 MySQL 上重复执行；回退后旧数据可见性不变；现有单租户测试全绿。
- **本轮不实现**：无 DDL 授权与拓扑决策时，任何“半隔离”都会制造假安全。先给出迁移草案与影响面清单。

### G6 校准基线（优先级 P2，受外部输入阻塞）

- **目标**：给出可分层的基线：执行成功率/契约通过率（离线可得）、语义准确率与检索召回（需人工标注）、容量与成本（需真实配额）。
- **范围**：冻结输入清单与 SHA；区分 dev/holdout；报告失败分类与未覆盖边界；禁止用合成集数字冒充业务准确率。
- **依赖**：脱敏业务问题、Schema、人工答案；模型/embedding 预算授权。
- **验收标准**：同一清单可重放；报告含分母、失败分类、置信边界与“不覆盖项”；契约指标与语义指标分表呈现。
- **本轮动作**：只维护缺口清单，不发布新数字。

## 三、实施阶段

### S1（本轮）计划运行时：让模板可执行

- **内容**：新增计划能力表与节点装配器；`QueryRequest` 增加 `plan_id`（默认 `request_query`）；请求链路按计划装配节点；按节点名映射事件；响应增加 `plan_output` 与真实 `plan_id`。
- **关键改动**：`app/workflow/plan_runtime.py`（新）、`app/workflow/query_service.py`、`app/api/query.py`、`app/workflow/contracts.py`、`app/api/workflows.py`。
- **风险**：
  - 请求主链路重构导致 request_query 行为漂移 → 用现有 362 项 API 测试作回归门禁；request_query 的节点配置映射保持逐字段等价。
  - 反射计划重复执行导致 LLM 调用翻倍 → 依赖 `max_llm_calls` 与 `max_iterations` 双重上限。
  - `chat_agentic` 的 respond 节点额外调用 LLM → 用 `BudgetedRouter` 包住，计入同一预算。
- **验证**：新增 `tests/test_workflow/test_plan_runtime.py`、`tests/test_api/test_plan_execution.py`；运行 `tests/test_api`、`tests/test_workflow`、`tests/test_nodes`。

### S2（本轮）计划选择接线

- **内容**：`auto_plan` 开关；选择器单例（PlanLoader + PlanHistoryStore）；能力过滤；结果入响应；历史记录；`/workflows/simulate` 复用同一实现；目录 `query_executable` 由能力表生成。
- **关键改动**：`app/workflow/plan_selection.py`（新）、`query_service.py`、`api/workflows.py`、`plan_runtime.py`。
- **风险**：自动选择把简单查询送进 reflection 导致延迟/成本上升 → L1 规则只映射到低成本模板，reflection 仅在显式请求时使用；默认关闭。
- **验证**：`tests/test_workflow/test_plan_selection.py`、`tests/test_api/test_plan_selection_api.py`，覆盖 L1/L2/过滤/默认关闭。

### S3（本轮）规则闭环

- **内容**：`learned_rules` SQLite 存储；审批写入；`BusinessRule` 映射与 TTL 缓存；注入生成提示与校验；响应 `applied_rules`；异常降级。
- **关键改动**：`app/learning/rule_registry.py`（新）、`app/api/learning.py`、`query_service.py`。
- **风险**：规则误伤（pattern 过宽）导致提示污染 → 只在 pattern 命中时注入，校验仍为非阻断 warning；规则来源与 id 全部回显。
- **验证**：`tests/test_learning/test_rule_registry.py`、`tests/test_api/test_query_rules.py`。

### S4（后续）编排模式、租户、基线

- **编排**：先做访问控制与约束对齐，再决定是否接线；验收以“只读/预算/审计等价”为门槛。
- **租户**：先冻结拓扑与 DDL，再写迁移；验收以跨租户拒绝与回退演练为准。
- **基线**：等待业务标注与预算；先补“缺口清单 + 可重放输入”，不发布新数字。

## 四、执行记录（2026-11-16）

### S1 已完成：计划运行时

- 新增 `app/workflow/plan_runtime.py`：节点装配、能力表、SSE stage 映射、请求级配置、`plan_output`。
- `QueryRequest` 增加 `plan_id`；请求链路按计划装配节点；`plan_output`/`plan_id` 如实返回。
- 修掉两个上线才会暴露的缺陷：`reflection.yml` 的 `exec` 改为 `on_failure: skip`（执行错误是反射输入），
  `agent.yml` 补上 `compare_results: allow`（声明了工具却拒绝，计划必然失败）。
- 验证：`tests/test_workflow/test_plan_runtime.py`（15 项）、`tests/test_api/test_plan_execution.py`（10 项）、
  `tests/test_nodes/test_generate_sql_node.py` 新增自愈上下文 2 项，覆盖五个模板、反射回跳、
  revise 携带上一轮 SQL 与错误、SSE、未知/受阻计划的 400、默认计划不变。

### S2 已完成：自动选择

- 新增 `app/workflow/plan_selection.py`：`DE_PLAN_HISTORY` 可覆盖历史库路径；自动集合固定为
  `request_query`/`ez_query`；`PlanSelector` 的 `gensql_agentic` 判决映射为 `request_query`
  （后者多一个 `parse_nl`，不能静默丢掉语言与意图解析），映射原因写入 `reason`。
- `/workflows/simulate` 与真实请求共用同一选择器；目录 `query_executable` 由能力表推导。
- 验证：`tests/test_workflow/test_plan_selection.py`（12 项）、`tests/test_api/test_plan_selection_api.py`（7 项）。

### S3 已完成：规则闭环

- 新增 `app/learning/rule_registry.py`：`learned_rules` SQLite、TTL 缓存、JSON 字段损坏降级、
  审批写入、`BusinessRule` 映射与 pattern 匹配。
- 生成侧：匹配到的规则合并进 `DomainConfig.rules`，进入 PromptBuilder 的规则段。
- 校验侧：仅对带 `enforce` 的规则、且已按查询匹配，清空 pattern 后交给 `SQLValidator`，避免
  “规则 pattern 描述中文、SQL 里永远匹配不到”的假生效。
- 响应侧：`applied_rules` 回显 id/描述/pattern；存储不可读只加 warning，不失败。
- 验证：`tests/test_learning/test_rule_registry.py`（10 项）、`tests/test_api/test_query_rules.py`（7 项）。

### 静态与回归

- `ruff check` 通过（改动文件与 `app/workflow`、`app/learning`）。
- `mypy` 通过（`plan_runtime.py`、`plan_selection.py`、`query_service.py`、`rule_registry.py`）。
- `pytest tests/test_api tests/test_workflow tests/test_learning tests/test_nodes tests/test_harness`：
  1308 项通过；随后 `pytest` 全量：4592 passed、143 skipped。
- `scripts/check_readmes.py` 与 `npm run check:frontend` 通过。
- 未跑：真实模型调用、真实浏览器验收、真实 MySQL 多租户迁移演练。

### 仍未完成（保持如实标注）

- G4 多 Agent 接线、G5 多租户、G6 校准基线：未实现，理由与前置条件见第二节。
- `metric_query`：仍不可执行；需要指标目录或显式 `metrics` 配置。
- 前端没有计划选择控件：本轮只提供 API 与真实目录，不改工作台交互。

## 五、附录 A：多租户影响面清单（G5 草案，未实施）

**需要加 `tenant_id` 的表（MySQL，全部当前无租户列）**

| 表 | 现有唯一键 | 租户化后 |
|---|---|---|
| `user_settings` | `uk_section(section)` | `uk_tenant_section(tenant_id, section)` |
| `api_keys` | `uk_provider(provider)` | `uk_tenant_provider(tenant_id, provider)` |
| `datasource_connections` | `uk_db_id(db_id)` | `uk_tenant_db(tenant_id, db_id)` |
| `domains` / `glossary_terms` / `domain_rules` | `uk_name`、`idx_domain_term`、`uk_rule` | 全部前置 `tenant_id`；领域名不再全局唯一 |
| `query_history` | 仅索引 | 加 `tenant_id` + `(tenant_id, session_id)` 索引 |
| `feedback` | `feedback_id` | 加 `tenant_id`，按租户过滤导出 |
| `learning_rule_candidates` / `learning_quality_trends` / `learning_query_pairs` | 规则 id / 域 | 加 `tenant_id`，审批只在租户内可见 |
| `worker_sessions` / `worker_session_config` | 会话键 | 会话键加租户前缀，避免跨租户冲突 |

**需要租户化的文件与进程状态**

- `data/learning.db`（反馈）、`data/query_pairs.db`、`data/plan_history.db`、`data/learned_rules.db`：
  路径按 `tenants/<tenant_id>/` 分目录，或表内加 `tenant_id`。
- `_SESSION_STORE`（`app/api/query.py`）与 `data/traces/<date>/`：键/目录带租户。
- `settings.admin_user_ids`：从全局白名单改为租户内角色表，否则租户管理员可管理其他租户。

**迁移顺序**：建 `tenants` → 各表加可空 `tenant_id` → 回填默认租户 1 → 收紧为 `NOT NULL` →
重建唯一键 → 部署期双写/开关 → 回退脚本保留旧唯一键。**每一步都要在一次性 MySQL 上演练，且回退后
旧数据可见性不变。**

**为什么不在本轮做**：唯一键重建 + 认证 claim + 全部存储过滤是跨模块改动，缺少 DDL 授权与部署拓扑
时半实现只会产生“看起来隔离、实际共享”的假安全。

## 六、附录 B：基线分层（G6 草案，未实施）

| 层 | 指标 | 输入 | 当前状态 |
|---|---|---|---|
| L1 契约/执行 | 语法通过、Schema 合规、执行成功、行列契约 | 合成 30 题 + 本地 SQLite/PG/MySQL fixture | 已有离线报告，不等于业务准确率 |
| L2 检索 | Recall@K、无答案正确空结果 | 冻结文档集 + 标注问题 | 已有合成结果（K=3） |
| L3 语义 | 与人工答案等价的准确率 | 脱敏业务问题 + Schema + 人工 SQL/结果 | **缺失**，需业务标注 |
| L4 容量/成本 | P50/P95、吞吐、失败分类、token/账单 | 真实配额 + 固定清单 | 已有 DeepSeek 30 题批次，非并发容量 |

报告规则：L1/L2 与 L3 必须分表；分母、失败分类、未覆盖边界逐项列出；禁止把合成集数字标成业务准确率。

## 七、交付边界

本轮交付 S1–S3 的实现、测试与本文档；不改动工作区已有的 Snowflake 切片；不宣称 `metric_query` 可用、不宣称多租户或语义基线已建立。任何未跑过的验证都会在完成报告中注明。
