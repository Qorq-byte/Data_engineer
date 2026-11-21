# Support matrix

What is wired, what is only declared, and which databases and models have actually been exercised.

## Feature status

Status reflects running code, not design documents. Track four separate questions: code implemented,
request path wired, local behavior tested, and target deployment externally accepted. `Implemented`
in the summary below does not certify every deployment, model or real-data quality. `Partial` means
a documented part of the surface does not work or is not reachable from the supported request path.

| Feature | Status | Where |
|---|---|---|
| Fixed five-node NL→SQL pipeline | Implemented | `app/workflow/plans/request_query.yml`, `app/harness/runner.py` |
| Constraint enforcement (read-only, row cap, LLM call cap) | Implemented | `app/harness/constraint.py` |
| Per-tool permission gate, default deny | Implemented | `app/harness/permission.py`, `app/config/agent.yml` |
| Single-statement AST read-only policy | Implemented | `app/security/sql_policy.py` |
| Schema linking over tables, columns, comments, foreign keys | Implemented | `app/nodes/schema_linking.py`, `app/knowledge/schema_graph.py` |
| Multi-stage validation (syntax, schema refs, types, plan, business rules) | Implemented | `app/nodes/validate_sql.py`, `app/core/schema_validation.py` |
| Hybrid retrieval: BM25 + vectors + reciprocal-rank fusion | Implemented | `app/rag/engine.py`, `app/knowledge/retrieval/rrf_fusion.py` |
| Business glossary and domain rules (YAML per domain) | Implemented | `app/config/domains/` |
| JWT auth against MySQL, administrator allow-list | Implemented | `app/auth/`, `app/api/auth.py` |
| MCP in both roles: four servers exposed, external tools consumed | Implemented | `app/mcp/servers/`, `app/mcp/client` |
| SQL workbench utilities: explain, format, validate, translate | Implemented | `app/api/sql_utils.py`, `app/nodes/` |
| Request-scoped usage record plus optional SQL-free audit | Implemented | `app/llm/usage.py`, `app/audit/store.py` |
| Offline evaluation CLIs (SQL contracts, retrieval, NL2SQL batches) | Implemented | `app/evaluation/`, `scripts/` |
| Scoped metric discovery and MCP metric tools | Wired and tested — REST operator gate, independent stdio client with explicit host trust, loopback HTTP MCP operator JWT, shared-worker drift fences | `app/api/metrics.py`, `app/mcp/metric_tools.py`, `app/mcp/query_projection.py`, `app/workflow/metric_runtime.py`. [REST and Streamable HTTP release acceptance](optimization/remaining-implementation-roadmap-2026-11-20.md#m1-指标结果验收命令) |
| QueryHarness FastMCP HTTP authentication | Operator JWT required for Streamable HTTP and SSE sessions, tools and resources | [HTTP authentication](usage.md#queryharness-mcp-over-http) |
| Seven read-only database connectors | Implemented and tested | `app/db/connections.py` |
| Automatic plan selection at runtime | Implemented — `auto_plan=true` routes through L1 rules, L2 history and a filtered default; off by default | `app/workflow/plan_selection.py` |
| Template plans besides `request_query` | Five available by default. `metric_query` uses a valid server catalog. `candidate_review` is an explicit preview with fixed validation and execution | `app/workflow/plan_runtime.py`, `app/workflow/metric_runtime.py` |
| Multi-agent orchestrator / sub-agents | **Partial** — fixed candidate-review preview and tenant-scoped subagent queries through REST, authenticated MCP HTTP and workbench. Template comparison supports bounded HTTP capture and offline pilot gates | [Subagent access](optimization/subagent-rest-boundary.md), [candidate review](optimization/remaining-implementation-roadmap-2026-11-20.md#候选复核预览与子-agent-租户入口) |
| Feedback-driven rule learning | Implemented — approved rules reach the generation prompt and validation warnings, and are echoed in `applied_rules`; rules never rewrite SQL | `app/learning/rule_registry.py`, `app/workflow/query_service.py` |
| Fixed-configuration MySQL shared workers | Opt-in, single host | [immutable-workers.md](optimization/immutable-workers.md) |
| Multi-tenant isolation | **Partial** — opt-in workbench, REST and MCP HTTP use tenant-scoped access on single-worker and single-host immutable deployments | [Migration and runtime acceptance](optimization/tenant-mysql-migration.md) |
| Answer-accuracy / recall baseline on real data | **Not established** | see the roadmap in `README.md` |

### Focused audit, updated 2026-11-20

| Capability | Implemented | Wired | Local evidence | External acceptance |
|---|---|---|---|---|
| Snowflake | Password connector, bounded SELECT, selected namespace metadata | Factory, schema, execute node, API and form | 51 pure policy tests; quoted namespace/table-function regressions fixed; no simulated driver evidence | **Pending**: four opt-in real tests skip without an explicitly supplied fixture/account |
| `metric_query` | Versioned catalog, scoped selection and release sealing. v2 adds DATE grains and integer primary-key lookup dimensions | JSON/SSE and MCP use the same deterministic resolve/validate/execute plan and discovery | v2 calendar boundaries and missing lookups checked on SQLite. Weekly grouping and lookup aggregates verified in two-worker MySQL and PostgreSQL fixtures | Approved business metric definitions and production target acceptance pending. [Metric v2](optimization/remaining-implementation-roadmap-2026-11-20.md#m2-日历分组与查找表维度) |
| Multi-agent / subagents | Tenant-scoped registry and ACL, immutable release catalog, fixed candidate-review workflow | Scoped natural-language and explicit metric queries through REST and MCP HTTP, plus the workbench natural-language entry | Two-worker MySQL and PostgreSQL fixtures cover same-name isolation, operator rejects, independent MCP and catalog drift fencing | Real business comparison pending. [Subagent access](optimization/subagent-rest-boundary.md) |
| Tenant isolation | **Partial**, with tenant-scoped control storage, models, query targets, browser history and v2 worker releases | Opt-in browser workbench, REST and MCP HTTP. Immutable releases provide read-only configuration | [MySQL migration and rollback](optimization/tenant-mysql-migration.md) | Single-host v2 verified with MySQL and [PostgreSQL targets](optimization/immutable-workers.md#双租户-postgresql-验收) on a shared MySQL control plane. Production capacity acceptance pending |
| Accuracy / recall / capacity | Frozen business dataset replay, stratified holdout thresholds, bounded HTTP workload replay with phase and tenant SLOs, and offline review of deployment resources and billed cost | Offline scoring, authenticated query-result capture, scoped document ranking capture, and single-tenant and multi-tenant JSON/SSE capacity probes. Calibration in `confidence` pending | [Query results and holdout scoring](optimization/remaining-implementation-roadmap-2026-11-20.md#q2-查询接口结果采集), [document rankings](optimization/remaining-implementation-roadmap-2026-11-20.md#q2-文档混合检索排名采集), [capacity evidence review](optimization/remaining-implementation-roadmap-2026-11-20.md#q3-资源和账单联合验收), and [SSE disconnect recovery](optimization/remaining-implementation-roadmap-2026-11-20.md#sse-主动断流与服务端回收) tested with synthetic data and zero external model calls | Business labels, semantic relevance, calibrated accuracy and production SLO acceptance pending |

Commands, code evidence, stages, results and blockers are in
[the completion path](optimization/completion-path-2026-11-17.md) and
[metric_query v1 configuration and acceptance](optimization/metric-query-v1.md), plus
[metric discovery/MCP access boundaries](optimization/metric-discovery-mcp.md) and
[shared catalog sealing and MySQL process acceptance](optimization/shared-metric-catalog.md).
The independent MCP transport evidence is in
[metric MCP transport acceptance](optimization/metric-mcp-transports.md).
This stage exercised disposable MySQL metrics; it did **not re-certify all seven connector acceptance suites**. Test counts certify
the exercised conditions, not a completion percentage.

## Databases

Seven connectors have read-only database acceptance. Snowflake has a connection implementation, but
its standalone real-database acceptance is pending.

| Dialect | Driver | Status |
|---|---|---|
| SQLite | `sqlite3` | **Implemented** — authorizer, progress deadline, schema-version tracking |
| DuckDB | `duckdb` | **Implemented** — external access disabled, interrupt-based cancel |
| PostgreSQL | `asyncpg` | **Implemented** — verified against PostgreSQL 16 in CI |
| MySQL | `aiomysql` | **Implemented** — server-side row cap, verified against MySQL 8.4 in CI |
| ClickHouse | `httpx` | [Standalone acceptance](optimization/clickhouse-acceptance.md) — one database |
| Trino | `httpx` | [Standalone acceptance](optimization/trino-acceptance.md) — one catalog and schema |
| StarRocks | `aiomysql` | [Standalone acceptance](optimization/starrocks-acceptance.md) — one database in the internal catalog |
| Snowflake | `snowflake-connector-python` (optional extra) | Connection, read-only query and column-level schema paths implemented; standalone acceptance pending |
| BigQuery, Redshift, Databricks | — | Dialect definitions only; connector implementation and real-database acceptance pending |

Snowflake supports password authentication and unquoted database, schema and warehouse identifiers.
Schema extraction includes tables, columns, types, nullability, defaults and comments; foreign-key
relationships are not exposed as verified evidence. Install the optional driver with
`pip install -e ".[snowflake]"`; this extra is not in the default `constraints.txt` verification
snapshot and must be pinned separately for deployment. Row limits and statement timeouts do not cap
Snowflake warehouse charges. See
[Stage 67.1 implementation and acceptance limits](optimization/stage-67-snowflake.md).
The dedicated fixture, native read-only/timeout/cancel checks and opt-in command are in
[Snowflake acceptance](optimization/snowflake-acceptance.md); its four tests have not run on a real
Snowflake account in this session.

Acceptance against one disposable instance does not establish compatibility with every server version,
catalog, deployment or authentication mode. Use a native read-only account on the target database. The
fixed-configuration multi-worker deployment was rehearsed on a single host with a shared MySQL control
plane and MySQL and PostgreSQL query targets. See
[deployment and recovery](optimization/immutable-workers.md) for its constraints.

## Models

Routing goes through LiteLLM, so any OpenAI-compatible endpoint works:

- **Providers with first-class settings:** OpenAI, Anthropic, Google, DeepSeek, Qwen
- **Anything else:** register it as a custom provider with a `base_url` in the workspace or via
  `PUT /api/v1/settings/llm`; keys are AES-GCM encrypted before being stored in MySQL
- **No key at all:** `LLM_MOCK_MODE=1` returns placeholders so you can exercise the harness

Keys configured through the API take effect immediately and survive restarts. Keys in `.env` are read
at startup and never written back.
