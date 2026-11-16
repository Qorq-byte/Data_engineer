# Usage

Everything the README does not carry: authentication, connecting a target, the request contract, the
other endpoints and what the response fields mean.

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

Sign in later with `POST /api/v1/auth/login` and the same email and password. Business access is
governed by the operator allow-list (`ADMIN_USER_IDS`), not by registration alone.

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
  "query_id": "q_20261101T143012Z_4f1c",
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

`status` is `completed` or `failed`. HTTP errors carry a machine-readable `code` (`INVALID_SQL`,
`TIMEOUT`, `PAYLOAD_TOO_LARGE`, `INTERNAL_ERROR`, …) plus a `Retry-After` header when waiting could
help. The full contract is in [optimization/query-contract.md](optimization/query-contract.md).

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

## Web workspace

Three pages, all served from `frontend_dist/`:

| Page | Path | Purpose |
|---|---|---|
| Landing | `/` | What the project is and how to enter |
| Sign in / register | `/login` | Account creation against the MySQL auth store |
| Workspace | `/workspace` | Chat, schema, connections, domains, SQL workbench, MCP, workflows, learning, settings |

The sidebar holds nine panels; the query panel streams node-by-node progress and shows the SQL, the
validation report and the result table side by side.
