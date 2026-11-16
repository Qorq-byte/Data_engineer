# Support matrix

What is wired, what is only declared, and which databases and models have actually been exercised.

## Feature status

Status reflects running code, not design documents. `Implemented` means the path is wired and covered
by tests; `Partial` means the code exists but a documented part of the surface does not work or is not
reachable from the request path.

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
| Seven read-only database connectors | Implemented and tested | `app/db/connections.py` |
| Automatic plan selection at runtime | **Partial** — `PlanSelector` is built at boot and never consulted by the request path | `main.py`, `app/api/query.py` |
| Six template plans besides `request_query` | **Partial** — loaded and listed, but only `request_query` is executable through the API | `app/api/workflows.py` |
| Multi-agent orchestrator / sub-agents | **Partial** — registered at boot; the request path uses the workflow runner | `main.py` |
| Feedback-driven rule learning | **Partial** — collection, review and stats exist; no automatic repair is wired in | `app/learning/` |
| Fixed-configuration MySQL shared workers | Opt-in, single host | [immutable-workers.md](optimization/immutable-workers.md) |
| Multi-tenant isolation | **Not implemented** — one shared administrator workbench | `app/auth/access.py` |
| Answer-accuracy / recall baseline on real data | **Not established** | see the roadmap in `README.md` |

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

Acceptance against one disposable instance does not establish compatibility with every server version,
catalog, deployment or authentication mode. Use a native read-only account on the target database. The
fixed-configuration multi-worker deployment was rehearsed on a single host with a shared MySQL control
plane and a MySQL query target; see
[deployment and recovery](optimization/immutable-workers.md) for its constraints.

## Models

Routing goes through LiteLLM, so any OpenAI-compatible endpoint works:

- **Providers with first-class settings:** OpenAI, Anthropic, Google, DeepSeek, Qwen
- **Anything else:** register it as a custom provider with a `base_url` in the workspace or via
  `PUT /api/v1/settings/llm`; keys are AES-GCM encrypted before being stored in MySQL
- **No key at all:** `LLM_MOCK_MODE=1` returns placeholders so you can exercise the harness

Keys configured through the API take effect immediately and survive restarts. Keys in `.env` are read
at startup and never written back.
