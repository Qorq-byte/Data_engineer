# NL2SQL Data Engineering Agent

**Ask a relational database a question in plain language. Get SQL you can inspect, validate and defend — or an explicit reason why you cannot.**

**English** · [简体中文](README.zh-CN.md) · [繁體中文](README.zh-TW.md) · [日本語](README.ja.md) · [Bilingual README](README.md)

[![MVP checks](https://github.com/Qorq-byte/Data_engineer/actions/workflows/checks.yml/badge.svg?branch=main)](https://github.com/Qorq-byte/Data_engineer/actions/workflows/checks.yml)
[![Real database acceptance](https://github.com/Qorq-byte/Data_engineer/actions/workflows/real-databases.yml/badge.svg?branch=main)](https://github.com/Qorq-byte/Data_engineer/actions/workflows/real-databases.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-3DA639.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-3776AB.svg?logo=python&logoColor=white)](pyproject.toml)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688.svg?logo=fastapi&logoColor=white)](app/api/)
[![Node.js 22+](https://img.shields.io/badge/Node.js-22%2B-339933.svg?logo=nodedotjs&logoColor=white)](package.json)

[![SQL: read-only](https://img.shields.io/badge/SQL-read--only-0B7285.svg)](app/security/sql_policy.py)
[![Status: pre-1.0 MVP](https://img.shields.io/badge/status-pre--1.0%20MVP-F5C451.svg)](#project-status)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-4C9A5A.svg)](CONTRIBUTING.md)
[![Security policy](https://img.shields.io/badge/security-policy-4B5563.svg)](SECURITY.md)

<a href="assets/runtime-flow-static.svg"><img src="assets/runtime-flow.svg" width="1200" alt="Runtime flow: boot sequence, operator gate, fixed request_query plan, five nodes with their context keys, guard rails, branch and failure outcomes, and the terminal response/persistence state"></a>

*Generated illustration, not telemetry. [Static version](assets/runtime-flow-static.svg) · [what each band claims](docs/runtime-flow.md).*

## Overview

This is an agent harness for natural-language analytics over relational databases. A language model
writes SQL; a fixed YAML plan and declarative limits decide which step runs next, which tools a step
may touch, how many rows may come back and when the request must stop. The output is not a score — it
is a SQL statement plus the evidence behind it, and a stated reason whenever a check could not be
performed.

The design follows three rules:

- **The model never routes.** `request_query.yml` fixes the node order; `WorkflowRunner` executes it.
- **Read-only is enforced, not requested.** A single-statement AST policy, a write check inside
  `execute_sql`, and driver-side guards where the driver exposes them (SQLite authorizer, ClickHouse
  read-only profile) each stand on their own.
- **A gap is reported as a gap.** Skipped execution, missing schema, mock providers and unverified
  joins are explicit fields, warnings or `null` values — never a silent success.

It is a pre-1.0 MVP for a single administrator group. [Project status](#project-status) lists what is
wired, what is partial and what is deliberately absent.

## Core features

- **Deterministic control flow.** Five nodes in an order fixed by `request_query.yml`. Nodes never
  call one another and communicate only through `WorkflowContext`; a failed node aborts instead of
  rerouting. `max_retries` is `0` on this plan.
- **Read-only in layers.** One statement, parsed and AST-checked; `execute_sql` blocks writes before
  touching a database; SQLite and ClickHouse add driver-side guards. Deploy with a native read-only
  account on the target.
- **Evidence instead of a confidence score.** Every candidate carries its structure source, capture
  time, dialect, assumptions and a pass/fail/unverified state per check. `confidence` is `0` by design
  because no calibrated probability exists.
- **Schema-aware generation.** Tables, columns, keys, comments, a business glossary and domain rules,
  with BM25 and vector retrieval fused by reciprocal rank fusion.
- **Seven connectors with real-database acceptance.** SQLite, DuckDB, PostgreSQL, MySQL, ClickHouse,
  Trino and StarRocks. Snowflake is implemented but its standalone acceptance is still pending.
- **One pipeline, three surfaces.** The web workspace, the JSON/SSE API and the MCP tool all call
  the same request service. JSON and SSE return `QueryResponse`; MCP projects the same status,
  identifiers, SQL and errors into a tool-result dictionary.
- **Provider routing with hard budgets.** LiteLLM routes across OpenAI, Anthropic, Google, DeepSeek,
  Qwen or a custom OpenAI-compatible endpoint; `max_llm_calls_per_query`, retries, timeouts and
  concurrency are enforced by the harness.
- **MCP in both directions.** Four FastMCP servers expose NL2SQL and data tools to external clients;
  the same process can consume external MCP tools.

## Project status

Status reflects running code, not design documents. The per-feature matrix is in
[docs/support-matrix.md](docs/support-matrix.md).

| Area | Status |
|---|---|
| Fixed five-node `request_query` pipeline | **Implemented** — wired through JSON, SSE and MCP |
| Constraint, permission and SQL-policy enforcement | **Implemented** |
| Schema linking, multi-stage validation, hybrid retrieval | **Implemented** |
| Seven read-only database connectors | **Implemented and tested**; Snowflake pending acceptance |
| Six other workflow plans | **Template only** — loaded and listed, not executable through the API |
| Automatic plan selection | **Partial** — `PlanSelector` is built at boot and never consulted |
| Multi-agent orchestrator and sub-agents | **Partial** — registered at boot; requests use the workflow runner |
| Feedback-driven rule learning | **Partial** — collection and review exist; no automatic repair |
| Multi-tenant isolation | **Not implemented** — one administrator group shares one data set |
| Calibrated accuracy, recall or capacity baseline | **Not established** — synthetic suites score contract matching |

## Quick start

**Requirements:** Python 3.12+, Node.js 22+ (frontend build only) and a MySQL 8.0+ account store.
The server still starts without MySQL, but login and registration are then disabled.

```bash
git clone https://github.com/Qorq-byte/Data_engineer.git
cd Data_engineer

python -m venv .venv
source .venv/bin/activate                 # Windows: .venv\Scripts\activate
pip install -e ".[dev]" -c constraints.txt

npm ci --ignore-scripts && npm run build:frontend

cp .env.example .env                      # Windows: copy .env.example .env
# Set JWT_SECRET (32+ chars), REGISTRATION_TOKEN, AUTH_MYSQL_* and ADMIN_USER_IDS
python main.py                            # http://localhost:8001 · API docs at /docs
```

1. Open <http://localhost:8001> and register with the invitation token from `REGISTRATION_TOKEN`.
2. Connect a target database in the workspace (or `POST /api/v1/databases/connect`).
3. Ask a question. The workspace streams one event per node; the SQL, validation report and result
   table are shown side by side.

No model key? The router falls back to mock mode: the response is well formed, the SQL is a
placeholder and the warning says so. No local Ollama? Set `RAG_EMBEDDING_PROVIDER=mock` in `.env` to
use deterministic embeddings instead of semantic ones.

## Installation and configuration

### Install

```bash
pip install -e ".[dev]" -c constraints.txt   # application + test and lint tooling
pip install -e ".[snowflake]"               # optional Snowflake driver (not in constraints.txt)
```

The runtime does not need Node.js. Node 22+ is only used by `npm run build:frontend`, which compiles
`frontend_design/` into `frontend_dist/`; the FastAPI app serves those files.

### Environment variables

Copy `.env.example` to `.env`. The values below are the ones most likely to need changing; every
remaining knob is commented in the example file.

| Variable | Default | Purpose |
|---|---|---|
| `APP_HOST` / `APP_PORT` | `0.0.0.0` / `8001` | Bind address and port |
| `DATABASE_URL` | `sqlite:///./data/app.db` | Local application store (not the account store) |
| `AUTH_MYSQL_HOST`, `AUTH_MYSQL_PORT`, `AUTH_MYSQL_USER`, `AUTH_MYSQL_PASSWORD`, `AUTH_MYSQL_DATABASE` | `127.0.0.1`, `3306`, `root`, empty, `nl2sql_auth` | MySQL account store |
| `JWT_SECRET` | none — **required** | Signing key; fewer than 32 characters or the default value aborts startup |
| `REGISTRATION_TOKEN` | empty | Invitation required by `POST /api/v1/auth/register`; empty closes registration |
| `ADMIN_USER_IDS` | `[1]` | Operator allow-list; registration alone does not grant access |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY` / `DEEPSEEK_API_KEY` | empty | Provider credentials read at startup |
| `LLM_MOCK_MODE` | `false` | Force the mock router; no provider calls |
| `LLM_MAX_CONCURRENCY` / `LLM_TIMEOUT_SECONDS` / `LLM_RATE_LIMIT_RPM` | `4` / `30` / `60` | Per-process LLM limits |
| `QUERY_TIMEOUT_SECONDS` | `120` | HTTP/SSE deadline for one query |
| `MAX_CONCURRENT_QUERIES` | `8` | Admission slots; excess requests receive `503` |
| `MAX_RESULT_ROWS` / `STATEMENT_TIMEOUT_MS` | `1000` / `30000` | Row cap and per-statement timeout |
| `RAG_EMBEDDING_PROVIDER` | `ollama` | `ollama`, `openai`, `bge` or `mock` |
| `OLLAMA_BASE_URL` / `OLLAMA_EMBEDDING_MODEL` / `OLLAMA_EMBEDDING_DIM` | `http://127.0.0.1:11434` / `embeddinggemma` / `768` | Local embedding endpoint |
| `LLM_AUDIT_PATH` | empty | Optional usage audit file; empty disables persistence. Stores no SQL by default |
| `MCP_SERVER_HOST` / `MCP_SERVER_PORT` | `0.0.0.0` / `8080` | MCP server binding |

### Declarative configuration

| File | Role |
|---|---|
| `app/config/agent.yml` | Provider defaults, node→tool→permission mapping, hard constraints |
| `app/workflow/plans/request_query.yml` | The executable plan: `node_order`, `failure_policy`, evaluation metrics |
| `app/workflow/plans/*.yml` | Six additional templates (chat, explore, metric, reflection, …) |
| `app/config/domains/*.yml` | Glossary, business rules and term mappings per domain |
| `app/config/subagents/*.yaml` | Scoped sub-agent definitions and delivery targets |

`ConfigLoader` and `PlanLoader` reload on file mtime, so edits apply to the next request without a
restart. Node order is still fixed at plan load time; a request cannot insert or skip a node.

Provider keys can come from two places: `.env` at startup, or `PUT /api/v1/settings/llm`, which
encrypts them with AES-GCM before storing them in MySQL. Keys set through the API take effect
immediately and survive restarts; `.env` keys are never written back.

## Usage examples

### Register and sign in

Registration is closed unless `REGISTRATION_TOKEN` is set. The invitation travels in a header, not
the body.

```bash
curl -X POST http://localhost:8001/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -H "X-Registration-Token: <value from .env>" \
  -d '{"username":"admin","email":"admin@example.com","password":"<strong-password>"}'

curl -X POST http://localhost:8001/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"<strong-password>"}'
# → {"access_token":"…","token_type":"bearer","user":{…}}
```

### Connect a target

```bash
curl -X POST http://localhost:8001/api/v1/databases/connect \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"db_type":"sqlite","config":{"path":"data/app.db"},"alias":"demo"}'
# → {"status":"connected","db_id":"sqlite_demo_20261101143012", …}
```

Use a native read-only account on the target. The returned `db_id` is what the query endpoints expect.

### Generate and validate

```bash
curl -X POST http://localhost:8001/api/v1/query \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"nl_text":"top 10 products by revenue last month","domain":"ecommerce","dialect":"postgresql","database":"<db_id>","num_candidates":1,"execute":false}'
```

```jsonc
// Abridged: nested payloads are trimmed to the fields that carry decisions.
{
  "query_id": "q_4f1c2a9b7e01",
  "status": "completed",
  "plan_id": "request_query",
  "primary_sql": "SELECT p.name, SUM(o.amount) AS revenue FROM orders o JOIN products p ON p.id = o.product_id WHERE o.created_at >= DATE_TRUNC('month', CURRENT_DATE - INTERVAL '1 month') GROUP BY p.name ORDER BY revenue DESC LIMIT 10",
  "linked_tables": ["orders", "products"],
  "validation": {
    "passed": true, "syntax_ok": true, "schema_valid": true, "type_valid": true, "score": 0.95
  },
  "execution": null,
  "confidence": 0.0,
  "errors": [],
  "warnings": []
}
```

Set `"execute": true` to run the statement after validation. The execution object then contains
`columns`, `rows`, `row_count` and `truncated`; rows are capped at `MAX_RESULT_ROWS`.

### Stream over SSE

```bash
curl -N -X POST http://localhost:8001/api/v1/query/stream \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"nl_text":"revenue by month","database":"<db_id>","execute":false}'
```

Events are emitted per stage (`parse`, `schema`, `sql`, `validation`, `execution`) and end with
`done` or `error`. A cancelled client closes the stream.

### Python client

```python
import httpx

response = httpx.post(
    "http://localhost:8001/api/v1/query",
    headers={"Authorization": f"Bearer {token}"},
    json={"nl_text": "revenue by month", "database": db_id, "execute": False},
    timeout=120,
)
payload = response.json()
print(payload["primary_sql"], payload["validation"]["passed"])
```

### Direct SQL and other endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/v1/execute` | POST | Run a SQL string directly, bypassing the NL pipeline; still read-only |
| `/api/v1/query/generate` | POST | Generate only — no schema linking, validation or execution |
| `/api/v1/schema/{db_id}` | GET | Schema snapshot; `/refresh` re-introspects |
| `/api/v1/settings/llm` | GET/PUT | Providers and default model; keys are never echoed |
| `/health`, `/health/ready` | GET | Liveness vs. readiness (initialized, auth, non-mock model, a connected database) |

### Response semantics

- **`status`** is `completed` or `failed`. A business failure still returns HTTP 200; the status and
  `errors[]` carry the verdict.
- **Infrastructure failures** return 4xx/5xx with a machine-readable `detail.code`
  (`QUERY_TIMEOUT`, `QUERY_CAPACITY`, `SESSION_CONFLICT`, `SHARED_STATE_UNAVAILABLE`,
  `INTERNAL_ERROR`) and `Retry-After` where waiting helps.
- **`confidence` is always `0`.** Read `evidence` instead: structure source, assumptions, clarification
  requests and per-check states.
- **`execution: null` means nothing ran.** `execute=false`, no connected target, or a blocked check.
  Warnings and the node trace say which.
- **`validation.score` is a diagnostic.** It is `0` on a blocking failure; otherwise it reflects
  diagnostic counts, not correctness.

Request fields and the full error contract are in [docs/usage.md](docs/usage.md) and
[docs/optimization/query-contract.md](docs/optimization/query-contract.md).

## Runtime flow

The diagram at the top is generated by `scripts/render_runtime_flow.py` and describes the shipped
path, not a recorded trace. It has six bands:

| Band | What it shows |
|---|---|
| `00` Boot | `main.py` → uvicorn factory → `create_app()` → lifespan probes → ready, plus degrade and shutdown paths |
| `01` Entry and admission | Four entry surfaces, operator gate, request validation, deadline, admission slots and session binding |
| `02` Control plane | The fixed plan, request-scoped limits and the runner's four fail-fast checks |
| `03` Five nodes | `parse_nl → schema_linking → generate_sql → validate_sql → execute_sql`, their context keys, lifecycle and guard rails |
| `04` Branches and failures | 401/403/422/503/409/504, skipped execution, blocked validation, node exceptions and explicit degradation |
| `05` End state | Status aggregation, the `QueryResponse` contract, persistence and terminal outcomes |

As five greppable lines:

```text
in    web workspace · JSON · SSE · MCP  →  auth · validate · bind session and target
plan  PlanLoader reads request_query.yml  →  WorkflowRunner owns node_order
run   parse_nl → schema_linking → generate_sql → validate_sql → execute_sql
out   executed: read-only rows  ·  skipped/blocked: explicit status  →  QueryResponse
gate  PermissionManager · ConstraintEnforcer · SQL policy · LLM budget (every step)
```

The rendering notes, the exact claims and the regeneration command are in
[docs/runtime-flow.md](docs/runtime-flow.md).

## Documentation

- [Usage](docs/usage.md) — accounts, targets, request fields, response evidence, endpoints.
- [Architecture](docs/architecture.md) — the harness invariant, layers, repository layout, decisions.
- [Support matrix](docs/support-matrix.md) — feature status, databases, models.
- [Runtime flow](docs/runtime-flow.md) — what the diagram claims and how to regenerate it.
- [Query contract](docs/optimization/query-contract.md) — status codes and error semantics.
- [Node development](docs/node-development.md) — the node lifecycle and a skeleton.
- [Deployment and recovery](docs/optimization/immutable-workers.md) — fixed-configuration workers.

## Running and testing

### Run

```bash
python main.py                 # APP_PORT overrides 8001
curl http://localhost:8001/health
curl http://localhost:8001/health/ready
```

Initialization and shutdown are owned by the FastAPI lifespan: catalogs, storage, auth, RAG and the
persisted LLM router are initialized there, and connections, sub-agents, the RAG engine and the router
are closed on shutdown.

### Tests

```bash
pytest                                  # full suite (asyncio_mode=auto)
pytest -m "not real_database"           # CI default; no external services
pytest tests/test_nodes/                # one directory
pytest -k "test_execute"                # one keyword
DE_REAL_DB_TESTS=1 pytest tests/real_db # disposable PostgreSQL/MySQL instances
```

Tests use real SQLite, DuckDB, LanceDB and FTS5 rather than mocks. The shared fixture builds an
e-commerce schema (`users`, `products`, `orders`) from `tests/fixtures/test_schema.sql`.

### Quality gates

```bash
ruff check .                            # line length 100, py312, E/F/I/N/W/UP/B/C4/SIM
mypy app/                               # strict; known debt outside the CI core
npm run check:frontend                  # page compilation and function contracts
npm run check:frontend:build            # reproducible asset build; keeps the previous output on failure
python -m scripts.check_dependencies    # dependency and environment consistency
python scripts/check_readmes.py         # section parity, local links, diagram animation
```

The CI core (`checks.yml`) runs the same gates plus `pytest -m 'not real_database'`.
`real-databases.yml` starts disposable PostgreSQL 16 and MySQL 8.4 services and runs `tests/real_db`.

### Evaluation and load probes

These are offline tools, not a correctness benchmark:

```bash
python scripts/evaluate_sql_contracts.py     # contract-level SQL checks
python scripts/evaluate_quality.py           # synthetic quality report
python scripts/run_nl2sql_acceptance.py      # batch acceptance over frozen sets
python scripts/run_real_db_tests.py          # driver-level acceptance
python scripts/load_test.py                  # bounded concurrency probe
```

The synthetic suites score contract matching, not business accuracy. Results are not a production
capacity or accuracy baseline.

## Repository layout

```text
app/api/             create_app() factory, 13 routers, operator dependency, static frontend
app/workflow/        request service, QueryResponse contract, 7 YAML plans
app/harness/         PlanLoader · WorkflowRunner · permissions · constraints · sessions
app/nodes/           12 registered nodes; the five request_query nodes are the main path
app/core/            NL parsing, prompt building, SQL generation and validation
app/db/              connections, schema introspection, dialect adapter, pinned query targets
app/rag/             LanceDB vectors, BM25/FTS5, RRF fusion, index maintenance
app/knowledge/       schema graph, glossary, business rules, retrieval primitives
app/llm/             LiteLLM router, budget, completion policy, usage, key encryption
app/auth/            JWT issuing, MySQL user store, administrator gate
app/mcp/             four FastMCP servers plus an external-tool client
app/evaluation/      frozen acceptance sets, contract suites, prediction export
assets/              runtime-flow diagrams (animated and static)
docs/                usage, architecture, support matrix, per-stage engineering record
frontend_design/     source pages compiled into frontend_dist/ by npm
scripts/             build, evaluation, acceptance and diagram entry points
tests/               unit, integration, e2e and real_db suites
```

## FAQ

**Do I need a paid model key?**
No. Without a key the router uses mock mode: the pipeline runs end to end, the SQL is a placeholder
and `warnings` says `MOCK LLM`. Useful for harness work, never for real answers.

**Do I need MySQL?**
For accounts, yes. The account store is separate from query targets. If `init_auth_db()` fails, the
server still starts but login and registration are disabled.

**Why is `confidence` always `0`?**
Because no calibrated probability has been measured. Reporting one would be invented precision. Read
the per-candidate `evidence` instead.

**`status` is `completed` but `execution` is `null` — is that a bug?**
No. `execute=false`, no `db_id`, or a skipped execution node produce this. The node outcome is
`skipped`, the execution object stays `null` and a warning explains the missing target.

**Which databases are actually verified?**
SQLite, DuckDB, PostgreSQL, MySQL, ClickHouse, Trino and StarRocks have read-only acceptance.
Snowflake has an implementation but awaits standalone acceptance; BigQuery, Redshift and Databricks
are dialect definitions only.

**Can several teams share one deployment?**
No. There is no tenant isolation; one administrator group shares one data set.

**Why does the app refuse to start with a JWT error?**
`JWT_SECRET` is missing, still `change-me-in-production`, or shorter than 32 characters. This is a
deliberate fail-closed check in `create_app()`.

**How do I add a node or a plan?**
Read [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/node-development.md](docs/node-development.md).
Nodes implement `setup_input → execute → update_context`; plans are YAML with a fixed `node_order`.

**Windows notes.**
Activate with `.venv\Scripts\activate`, copy with `copy .env.example .env`, and use PowerShell or
Git Bash for the curl examples. `tzdata` is installed automatically on Windows.

## Contributing

Contributions are welcome, especially tests that pin down failure semantics. Start with
[CONTRIBUTING.md](CONTRIBUTING.md): environment, branch model, commit conventions and skeletons for a
new node or plan. Participation is covered by the [Code of Conduct](CODE_OF_CONDUCT.md).

For changes to the query path, run the gates above and update the plan, the diagram data tables and
all three README views in the same commit.

## Security

Report vulnerabilities privately through [SECURITY.md](SECURITY.md) — never in a public issue, pull
request or discussion. Do not paste credentials, connection strings or customer data into a report;
redact them.

Security-relevant defaults:

- Read-only SQL is enforced by a single-statement AST policy and a separate write check inside
  `execute_sql`; SQLite and ClickHouse add driver-side guards, and the deployment should use a native
  read-only account.
- `JWT_SECRET` weaker than 32 characters aborts startup.
- Provider keys set through the workspace are AES-GCM encrypted before storage and never returned.
- The optional usage audit stores no question text and no SQL by default.
- Unlisted tools are denied by `PermissionManager`; row, retry, timeout and LLM-call caps are
  enforced outside the model's control.

Supported versions, scope and the disclosure process are in [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE) © 2026 Majiaqi.
