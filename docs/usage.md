# Usage

The operational detail behind the README: authentication, connecting a target, the request contract,
the other endpoints and what the response fields mean.

## 1. Create an account

Registration is closed unless `REGISTRATION_TOKEN` is set. The invitation travels in a header, not the
body.

```bash
curl -X POST http://localhost:8001/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -H "X-Registration-Token: <the value from .env>" \
  -d '{"username":"admin","email":"admin@example.com","password":"<strong-password>"}'
# → 201 {"access_token":"…","token_type":"bearer","user":{…}}
```

Sign in later with `POST /api/v1/auth/login` and the same email and password. Business access requires
an operator allow-list entry. The default tenant uses `ADMIN_USER_IDS`. See
[tenant operator setup](#tenant-operator-setup) for additional tenants.

## 2. Connect a target

```bash
curl -X POST http://localhost:8001/api/v1/databases/connect \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"db_type":"sqlite","config":{"path":"data/app.db"},"alias":"demo"}'
# → {"status":"connected","db_id":"sqlite_demo_20261101143012", ...}
```

Use a native read-only account on the target database. The `db_id` is what the query endpoint expects.

## 3. `POST /api/v1/query`

| Field | Type | Default | Notes |
|---|---|---|---|
| `nl_text` | string | required | 1–10000 characters, Chinese or English |
| `session_id` | string | `""` | Reuse to continue a multi-turn session |
| `domain` | string | `default` | Directory name under `app/config/domains/` |
| `database` | string | `""` | `db_id` returned by `/api/v1/databases/connect` |
| `dialect` | string | `ansi` | SQL dialect used for generation and validation |
| `schema_ddl` | string | `""` | Offline `CREATE TABLE` declarations (≤50000 chars) |
| `schema_notes` | string | `""` | Field descriptions for offline generation |
| `num_candidates` | int | `3` | 1–5 |
| `execute` | bool | `false` | Run the statement after validation |
| `validate` | bool | `true` | Accepted as an alias of `run_validation` |
| `link_schema` | bool | `true` | Skip only if you know the statement needs no schema |
| `model` | string | `null` | Must be an enabled provider model, otherwise 422 |

```bash
curl -X POST http://localhost:8001/api/v1/query \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "nl_text": "top 10 products by revenue last month",
    "domain": "ecommerce",
    "dialect": "postgresql",
    "database": "postgresql_prod_20261101143012",
    "num_candidates": 1,
    "execute": true
  }'
```

```jsonc
// Abridged: nested payloads are trimmed to the fields that carry decisions.
{
  "query_id": "q_4f1c2a9b7e01",
  "status": "completed",
  "plan_id": "request_query",
  "llm_calls": 2,
  "language": "en",
  "intent": "aggregation",
  "confidence": 0.0,
  "primary_sql": "SELECT p.name, SUM(o.amount) AS revenue FROM orders o JOIN products p ON p.id = o.product_id WHERE o.created_at >= DATE_TRUNC('month', CURRENT_DATE - INTERVAL '1 month') GROUP BY p.name ORDER BY revenue DESC LIMIT 10",
  "linked_tables": ["orders", "products"],
  "candidates": [
    {
      "id": "cand_1",
      "sql_text": "SELECT p.name, SUM(o.amount) AS revenue FROM orders o ...",
      "confidence": 0.0,
      "generation_mode": "primary",
      "generation_source": "model_response",
      "finish_reason": "stop",
      "evidence": { "structure_source": "introspection", "dialect": "postgresql" },
      "execution": { "status": "success", "row_count": 10, "truncated": false }
    }
  ],
  "validation": { "passed": true, "score": 0.95 },
  "errors": [],
  "warnings": []
}
```

`status` is `completed` or `failed`. Business failures return HTTP 200 with `status=failed` and the
reason in `errors[]`. Infrastructure failures use 4xx/5xx with a machine-readable `detail.code`
(`QUERY_TIMEOUT`, `QUERY_CAPACITY`, `SESSION_CONFLICT`, `SHARED_STATE_UNAVAILABLE`,
`INTERNAL_ERROR`) plus a `Retry-After` header when waiting could help; request validation keeps
FastAPI's 422 format. The full contract is in [optimization/query-contract.md](optimization/query-contract.md).

## What the response fields mean

- **`confidence` is always `0`.** The project does not ship a calibrated probability, and the UI never
  shows a confidence percentage.
- **`validation.score` is a diagnostic, not a grade.** It is `0` on a blocking failure, otherwise
  `max(0.5, 1 - 0.05 × H - 0.02 × S)`, where `H` counts syntax/schema/type diagnostics and `S` counts
  performance/business-rule diagnostics. It measures neither coverage nor correctness.
- **`evidence` is the part you should read.** Each candidate records its structure source (live
  introspection, cache, user-supplied DDL or inference), capture time, dialect, assumptions,
  clarification requests and a pass/fail/unverified state per check. Syntax, schema references, type
  heuristics, declared foreign keys, static business rules and actual execution have separate scopes;
  undeclared or complex joins stay unverified, and a model-requested clarification blocks execution.
- **Workspace behaviour.** Switching modules keeps the current conversation, rendered messages and a
  running SSE request. “+ 新对话” opens a blank conversation; a detached request finishes into its
  original history entry, including failures, without leaking into the new one.
- **Offline generation.** Without a connected target you can still generate SQL by expanding
  “SQL 生成选项” and supplying a dialect plus `schema_ddl` / `schema_notes`. Execution is then skipped
  and the result is marked unverified rather than presented as a real run.

## Other endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/v1/query/stream` | POST | Same pipeline over SSE, one event per node |
| `/api/v1/query/generate` | POST | Generate and validate, never execute |
| `/api/v1/execute` | POST | Run a SQL string directly, bypassing the NL pipeline |
| `/api/v1/schema/{db_id}` | GET | Schema snapshot; `/refresh` re-introspects |
| `/api/v1/settings/llm` | GET/PUT | Providers and default model; keys are never echoed |
| `/health` `/health/ready` | GET | Liveness vs. readiness (init, auth, real model, databases) |

## Python client

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

## NL2SQL MCP over HTTP

Configure the MCP client to send `Authorization: Bearer <access_token>` with every request.
Use a token from `POST /api/v1/auth/login` for an allow-listed administrator.
This applies to Streamable HTTP and to both routes of the SSE transport.
HTTP `401` indicates an absent or invalid token. HTTP `403` indicates that the account is
outside the supported workspace or administrator allow-list. Handle these at the transport layer.
Session requests with another administrator's token return `404`. A login after token revocation
requires a fresh MCP session.
Authorized tool and resource calls keep their MCP result formats.

The five original tools and four resources remain available to the local stdio or in-process host.
The two metric tools require `trust_local_metric_tools=True` for local calls.
See [the transport acceptance record](optimization/metric-mcp-transports.md#mcp-http-operator-gate).

## Tenant operator setup

For a single-worker deployment, assign existing accounts to tenants in the authentication
database and configure their actual user IDs in the deployment environment. REST and MCP HTTP
validate the account's current tenant and token version on each request.

```dotenv
TENANT_ADMIN_USER_IDS={"2":[42],"3":[57]}
TENANT_LLM_ENDPOINTS={"2":["https://models-a.example.test/v1"],"3":["https://models-b.example.test/v1"]}
TENANT_QUERY_PROFILES_PATH=/absolute/path/tenant-query-profiles.json
```

The host-owned profile file maps tenant IDs to connection lists. Use separate database-native
read-only accounts and limit file access to the deployment service. Both MySQL and PostgreSQL
profiles use the following structure.

```json
{
  "2": [
    {
      "db_id": "reporting",
      "db_type": "mysql",
      "config": {
        "host": "mysql.example.test",
        "port": 3306,
        "database": "tenant2",
        "user": "tenant2_reader",
        "password": "<database credential>"
      }
    }
  ]
}
```

Use the configured `db_id` for queries. Tenant administrators can use REST JSON/SSE queries,
domain management, metadata reads and model settings. A model configuration needs a tenant key
and an exact approved HTTPS base URL. The runtime validates DNS and pins the connection to a
checked public IP while keeping TLS verification against the approved hostname. Database
connection creation, global settings and subagent administration require a default-tenant operator.
For a deployment with frozen settings, see [v2 tenant releases](optimization/immutable-workers.md#v2-租户发布包).
Those workers load tenant data sources from the release and expose model and domain configuration
as read-only. Use a stopped maintenance window to change the release.
When retrieval uses a shared HTTP embedding service, non-default tenant queries use the tenant's
local keyword index. In-process embedding providers can still supply semantic search.

## Web workspace

After sign-in, the browser confirms the current account's workbench access with the server.
Tenant administrators can use natural-language queries, read assigned schemas, manage domain
knowledge and configure their models. Ordinary accounts can manage personal settings. Default
operators can also use database connections, the SQL workbench and system administration.
Browser query history is scoped to the account and tenant. Earlier browser entries that lack
account ownership are excluded from the workbench history view.

For explicit metric requests, see [metric_query v1](optimization/metric-query-v1.md). This JSON/SSE/MCP
plan uses a versioned server catalog and fixed resolve/validate/execute nodes with no model calls.
The default remains disabled until configured. Operator-only `GET /api/v1/metrics` and MCP
`list_metrics` discover one explicit domain/database binding; MCP `metric_query` reuses the same plan.
See [metric discovery and MCP access](optimization/metric-discovery-mcp.md) for request fields,
HTTP Bearer authentication, explicit host trust for local calls and result limits.
Fixed-configuration shared workers additionally require a catalog sealed in the active release;
see [shared metric catalog setup and evidence](optimization/shared-metric-catalog.md).

Scoped subagent REST queries share the same fixed Harness and operator gate. See
[subagent REST configuration and limits](optimization/subagent-rest-boundary.md) for explicit db_id
bindings, user allow-lists, execution opt-in and the QueryResponse inside the response envelope.

Three pages, all served from `frontend_dist/`:

| Page | Path | Purpose |
|---|---|---|
| Landing | `/` | What the project is and how to enter |
| Sign in / register | `/login` | Account creation against the MySQL auth store |
| Workspace | `/workspace` | Chat, schema, connections, domains, SQL workbench, MCP, workflows, learning, settings |

The sidebar holds nine panels; the query panel streams node-by-node progress and shows the SQL, the
validation report and the result table side by side.
