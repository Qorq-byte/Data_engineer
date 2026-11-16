# Architecture

The README states the invariant in one line; this page explains how the code keeps it.

## Request lifecycle

`POST /api/v1/query` → Pydantic validation (a `model` override must match an enabled provider) →
`PlanLoader` returns `request_query.yml` → `WorkflowRunner` walks `node_order` → JSON and SSE return a
`QueryResponse`, while MCP projects the same result into a tool dictionary → the trace is persisted.

Each node runs `setup_input()` → `execute()` → `update_context()` → evaluation. Nodes never call one
another; they communicate only through `WorkflowContext`. A skipped or blocked execution is still an
explicit node outcome, never a silent success.

## The harness invariant

> **The model never chooses the next step.** A plan file fixes the node order; `WorkflowRunner`
> executes it.

| Guarantee | Mechanism | Where |
|---|---|---|
| Ordering | `node_order` in YAML, no runtime insertion | `app/workflow/plans/request_query.yml` |
| Tool access | per-tool allow/deny/ask, default deny | `app/harness/permission.py`, `app/config/agent.yml` |
| Limits | `read_only`, `max_rows`, `max_llm_calls` | `app/harness/constraint.py` |
| SQL shape | one statement, no DML/DDL, AST checked | `app/security/sql_policy.py` |
| Retries | `max_retries: 0`, abort on failure | plan `failure_policy` |

## Layers

| Layer | Responsibility |
|---|---|
| **API** | FastAPI factory and routers; every `/api/v1/*` route except auth requires an operator token |
| **Node** | `BaseNode` → `AgenticNode`; the only place computation happens |
| **Harness** | Config and plan loading (hot-reload on mtime), execution, sessions, permissions, constraints, action history |
| **DB** | `ConnectionFactory` for 11 dialects, 4 complete drivers, sqlglot-based translation, frozen query targets |
| **Model** | Plain dataclasses; Pydantic only at the API boundary and for settings |
| **RAG** | Hybrid retrieval, schema graph, glossary, metrics |
| **LLM** | Routing, budgets, completion-status checks, usage accounting, AES-GCM key storage |

## Repository layout

```text
app/api/          create_app() factory, 13 routers, 93 routes, auth dependency
app/workflow/     request service, QueryResponse contract, 7 YAML plans
app/harness/      PlanLoader · WorkflowRunner · permissions · constraints · sessions
app/nodes/        12 registered nodes; the five in request_query are the main path
app/core/         NL parsing, prompt building, SQL generation, validation
app/db/           connection factory, schema introspection, dialect adapter, leases
app/rag/          LanceDB vectors, BM25/FTS5, RRF fusion, index maintenance
app/knowledge/    schema graph, glossary, business rules, retrieval primitives
app/llm/          LiteLLM router, budget, completion policy, usage, key encryption
app/auth/         JWT issuing, MySQL user store, administrator gate
app/mcp/          four FastMCP servers plus an external-tool client
app/evaluation/   frozen acceptance sets, contract suites, prediction export
assets/           runtime-flow diagrams (animated and static)
docs/             usage, architecture, support matrix, per-stage engineering record
frontend_design/  source pages, compiled into frontend_dist/ by npm
scripts/          build, evaluation, load and acceptance entry points
tests/            177 test modules, 856 test functions
```

## Design decisions

- **Python 3.12+** — `match`/`case`, `StrEnum`, PEP 604 unions.
- **Dataclasses, not Pydantic, for domain objects** — Pydantic is used for request bodies and settings.
- **Secure by default** — unlisted tools are denied; SQL is single-statement and read-only by AST.
- **Global singletons as the DI container** for `mcp_registry`, `settings` and `ConnectionFactory`;
  each request builds its own `NodeRegistry`.
- **Tests use real SQLite, DuckDB, LanceDB and FTS5**, not mocks. The shared fixture builds an
  e-commerce schema (`users`, `products`, `orders`) from `tests/fixtures/test_schema.sql`.
- **Declarative configuration** — `agent.yml` and workflow plans are YAML; `ConfigLoader` hot-reloads
  on mtime.

## MCP in both directions

The system is both an MCP **server** (exposing NL2SQL as tools and resources to external AI clients,
`db_server` on port 8081 by default) and an MCP **client** (consuming external database, knowledge and
vector tools through `mcp_registry`).
