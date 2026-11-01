# Contributing

Thanks for taking the time to look at this project. It is an MVP with an unusually strict harness,
and most of the rules below exist to keep that harness honest rather than to make review easier.

## Before you start

- Read [`docs/optimization/query-contract.md`](docs/optimization/query-contract.md) if you are
  touching the query path. It defines the failure semantics you must preserve.
- Read [`README.md`](README.md) § Architecture. The invariant there is not a suggestion.
- Look at `docs/archive/stages/INDEX.md` before proposing a large change; there is a reasonable
  chance it was attempted and rejected, or attempted and left incomplete on purpose.

## Environment

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev]" -c constraints.txt

npm ci --ignore-scripts
npm run build:frontend               # required before page routes work

cp .env.example .env                 # then set JWT_SECRET (32+ chars) and REGISTRATION_TOKEN
```

MySQL 8.0+ is required for auth. If you do not want to install MySQL, you can still run the test
suite — it never needs an external service except under the `real_database` marker:

```bash
pytest -m "not real_database"
```

Run the same gates CI runs before you open a pull request:

```bash
ruff check .
mypy app/
npm run check:frontend
npm run check:frontend:build
```

`mypy app/` reports known debt; your change must not add to it. The strict core set listed in
`.github/workflows/checks.yml` must stay clean with full import following.

## Branch model

`main` is the integration branch and is protected: changes land through a pull request with the
`MVP checks` workflow green. The repository accumulated a long tail of `codex/*` branches, one per
optimisation stage. They are history, not a workflow to copy.

For new work:

1. Branch from `main` as `feat/<slug>`, `fix/<slug>` or `docs/<slug>`.
2. Keep one concern per branch. If you find yourself writing "and also", open a second branch.
3. Rebase on `main` rather than merging `main` in; the history stays readable.
4. Delete the branch after merge.

Existing `codex/*` branches are not merged wholesale. If a change from one of them is still wanted,
re-implement it on a fresh branch against current `main` and reference the old branch in the pull
request description.

## Commit messages

Conventional Commits, scoped by area:

```
feat(nodes): add dialect_translate node to the plan catalog
fix(db): read PostgreSQL index columns instead of hardcoding an empty list
perf(schema): reuse the cached snapshot instead of deepcopying it
docs(readme): rewrite the pipeline section in English
ci: add a second mypy gate for the audit store
chore(deps): pin sqlglot to 30.18.0
```

Subject in the imperative mood, 72 characters or fewer, no trailing period. The body explains why
the change is needed and what it does *not* cover — the second part matters more here than usual,
because this project's documentation is careful about limits.

## Pull request checklist

Copy this into the description:

```markdown
## What changed

## Why

## What this does NOT cover

## Verification
- [ ] `pytest -m "not real_database"` passes
- [ ] `ruff check .` passes
- [ ] `mypy app/` does not add new errors to the known debt
- [ ] `npm run check:frontend` passes (if the frontend changed)
- [ ] new behaviour has a test that fails without the change

## Claims
- [ ] README / docs describe only what the code does
- [ ] anything unproven is labelled as unproven
```

Two review rules that are enforced rather than suggested:

- **No secrets.** Not in code, not in tests, not in fixtures, not in docs. Use environment
  variables and `.env.example` placeholders.
- **No new "looks like success" paths.** If a step is skipped, degraded or unavailable, the response
  must say so. Returning an empty result that reads like a real one is a bug.

## Adding a node

A node is a class with three lifecycle methods. Only `execute()` is required.

```python
# app/nodes/row_count.py
"""RowCountNode — annotate a result set with its row count."""

from __future__ import annotations

from app.nodes.base import BaseNode, NodeInput, NodeOutput


class RowCountNode(BaseNode):
    name = "row_count"
    description = "Count the rows in the current execution result."

    async def execute(self, input: NodeInput) -> NodeOutput:
        rows = input.context.get("last_execution", {}).get("rows", [])
        return NodeOutput(
            result={"row_count": len(rows)},
            context={"row_count": len(rows)},
        )
```

Then:

1. Register it in `main.py` next to the other `node_registry.register(...)` calls. The registry is
   request-local and rejects duplicate names, so registration is explicit by design.
2. If it calls a tool, declare it in `app/config/agent.yml` under `nodes.<name>.tools` and give it a
   permission. **Anything not listed is denied**, so forgetting this makes the node fail closed —
   which is the intended direction.
3. Add a test under `tests/test_nodes/`.
4. Do not import another node, and do not call one. Nodes communicate only through
   `NodeOutput.context`.

If the node needs an LLM, subclass `AgenticNode` instead of `BaseNode`; it wires the router, budget
and usage accounting for you.

## Adding a workflow plan

A plan is a YAML file in `app/workflow/plans/`. Minimal working skeleton:

```yaml
id: sample_plan
name: "Sample plan"
description: "What this plan is for, and what it deliberately does not do."
plan:
  node_order:
    - id: link
      node: schema_linking
      on_failure: abort
    - id: gen
      node: generate_sql
      depends_on: [link]
      config:
        temperature: 0.1
        max_candidates: 1
    - id: exec
      node: execute_sql
      depends_on: [gen]
      config:
        max_rows: 100
        timeout_ms: 10000
  failure_policy:
    max_retries: 0
    on_exhausted: abort
  max_iterations: 5
  on_exhausted: abort
```

Notes that will save you a debugging session:

- `node_order` is the only source of ordering. `depends_on` documents intent; the runner follows
  the list.
- Only `request_query` is reachable through `POST /api/v1/query`. A new plan appears in
  `GET /api/v1/workflows` with `"query_executable": false` until you also change the request path —
  do not change that path without a discussion first, since it is the fixed-contract surface.
- Every `node:` value must be registered in `main.py`, otherwise the plan loads and fails at run
  time.
- `pytest tests/test_workflow/test_plans.py` validates that the shipped plans parse and reference
  known nodes. Run it after editing YAML.

## Reporting a security issue

Do not open an issue. See [`SECURITY.md`](SECURITY.md).
