## What changed

<!-- One paragraph. If you cannot summarise it, the pull request is probably too large. -->

## Why

<!-- The problem this solves. Link the issue if there is one. -->

## What this does NOT cover

<!-- Known gaps, deferred cases, follow-up work. This section is not optional — it is how reviewers
     avoid assuming coverage that does not exist. -->

## Verification

<!-- Commands you ran and what they returned. Paste counts, not adjectives. -->

- [ ] `pytest -m "not real_database"` passes
- [ ] `ruff check .` passes
- [ ] `mypy app/` adds no new errors to the known debt
- [ ] `npm run check:frontend` passes (if the frontend changed)
- [ ] New behaviour has a test that fails without this change

## Claims

- [ ] Documentation describes only what the code does, with a file reference where it matters
- [ ] Anything unproven is labelled as unproven, not as a feature
- [ ] No secrets, connection strings or local absolute paths are added
- [ ] Harness invariants are intact: the model still cannot choose node order, retry itself, or write

## Checklist

- [ ] Conventional Commits subject, imperative, ≤72 characters
- [ ] One concern per pull request
- [ ] Rebased on `main`, not merged with it
