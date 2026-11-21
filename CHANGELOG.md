# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims
to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html). No git tag has been cut yet,
so `0.1.0` below denotes the current `main`, not a release artefact.

## [Unreleased]

### Added

- Frozen business holdout import, SQL response and retrieval ranking capture, stratified scoring,
  and explicit acceptance thresholds.
- Authenticated capacity probes with per-phase and per-tenant checks, SSE disconnect recovery
  coverage, and offline resource and billing review.
- Deterministic version 2 metric catalog with calendar grains and primary-key lookup dimensions,
  with fixture-based execution evidence for SQLite, MySQL and PostgreSQL.
- An opt-in fixed candidate review workflow plus paired capture, offline comparison and pilot
  gates for latency, model calls and reviewed cost.
- Tenant-scoped subagents in REST, MCP HTTP and the workbench, with optional immutable catalog
  sealing and drift rejection across shared workers.
- Tenant-scoped account authorization, query targets, sessions, retrieval, domain catalogs and
  model routing, plus a workbench that loads capabilities from the verified account.
- Encrypted v2 immutable worker releases with tenant-scoped target restoration, fixed settings
  and domain snapshots, and drift rejection. MySQL acceptance covers restart and MCP HTTP.
- A reproducible synthetic baseline for retrieval, SQL contracts and bounded query load.

- Deterministic `metric_query` with explicit versioned server catalogs, scoped discovery,
  and authenticated MCP metric tools. Fixed YAML resolution, validation and optional read-only
  execution use no model calls. Immutable shared workers seal catalog paths, versions and hashes.
- Snowflake password connector with selected-namespace metadata and bounded read-only execution;
  cloud acceptance remains pending dedicated credentials and fixtures.
- Scoped subagent REST queries now use the fixed workflow, operator/user checks and explicit db_id.
- Synthetic SQLite and MySQL multi-process acceptance evidence, including restart recovery and
  catalog/control-plane drift rejection; this does not establish production business quality.

- Plan-driven execution: `POST /api/v1/query` and `/query/stream` accept `plan_id`, so
  `gensql_agentic`, `ez_query`, `reflection`, `chat_agentic` and `explore` run through the same
  admission, read-only, row-cap, timeout and LLM-budget path as `request_query`. `metric_query`
  additionally requires a configured catalog; shared workers require a sealed active release.
- Automatic plan selection behind `auto_plan=true`: L1 rules, L2 history and a capability-filtered
  default. The selector only chooses between `request_query` and `ez_query`; the default request
  remains `request_query`.
- Responses carry `plan_output`, `plan_selection` (only when selection ran) and `applied_rules`.
- `/api/v1/workflows/simulate` now runs the same selector as the query path instead of a separate
  keyword guess, and the plan catalog derives `query_executable` from the capability table.
- Approved review candidates persist to a local learned-rule store and reach the generation prompt
  and validation as business rules; the response lists which rules were applied.
- `docs/optimization/gap-closure-plan-2026-11-16.md` records the remaining gaps (multi-agent
  routing, tenant isolation, calibrated baselines) with priorities and acceptance gates.

### Changed

- `reflection.yml` marks its first `execute_sql` as `on_failure: skip`: an execution error is the
  reflection input, and the revise step now receives the previous SQL plus the error analysis.
- `agent.yml` allows the reflection node's declared `compare_results` tool; denying a declared tool
  made the plan impossible to run once it became executable.
- README capability tables in all four languages and `docs/support-matrix.md` no longer claim that
  the other plans are template-only or that plan selection is never consulted.

- SQL generation accepts user-provided CREATE TABLE declarations, field descriptions and an
  offline dialect, while connected targets retain their introspected structure and dialect.
- Per-candidate SQL evidence identifies live, cached, user-supplied and inferred structure,
  assumptions, clarification requests, static checks and actual execution outcomes.
- JSON/SSE regression cases cover offline generation, provided structure, complex queries,
  missing columns, metadata failures, execution errors and dialect aliases.

- `LICENSE` (MIT, 2026 Majiaqi). The README already claimed MIT; GitHub could not detect it without
  the file.
- `CONTRIBUTING.md`: environment setup, branch model, Conventional Commits, pull-request checklist,
  and copy-paste skeletons for a new node and a new workflow plan.
- `SECURITY.md`: supported versions, private reporting channels, response targets, disclosure
  process, scope, and the rule that secrets never go in an issue.
- `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1).
- Issue forms (`.github/ISSUE_TEMPLATE/bug_report.yml`, `feature_request.yml`) and
  `.github/PULL_REQUEST_TEMPLATE.md`.
- `README.zh-CN.md`: the Chinese README, reorganized and condensed, with the stage-by-stage progress
  log replaced by a link to the archive index.
- `README.zh-TW.md`: a Traditional Chinese edition using Taiwan technical vocabulary
  (資料庫 / 資料表 / 專案 / 預設 / 支援). Same 14-section structure as the other two, and the language
  switcher is now three-way in every edition.
- `README.ja.md`: a Japanese edition. It states plainly that Japanese is not a first-class input
  language — the prompt reaches the model untouched, but language detection, limit extraction and
  ordering extraction only carry Chinese and English patterns, and `language` is `zh` / `en` /
  `mixed` only. The switcher is now four-way in every edition.
- `assets/pipeline.gif` (960 px, seamless loop) and `assets/pipeline.svg` (static fallback).
- `assets/runtime-flow.svg` and `assets/runtime-flow-static.svg`, plus
  `scripts/render_runtime_flow.py` (standard library only) to regenerate them.
- `docs/runtime-flow.md` now states what the diagram claims, band by band, in both languages, and
  carries the five-line text form of the same path.
- `docs/usage.md`, `docs/architecture.md` and `docs/support-matrix.md`: the reference material moved
  out of the READMEs.
- `docs/archive/stages/INDEX.md`: a timeline over the 53 optimisation stages plus the stage-44 live
  acceptance record, with topic, date, conclusion and a link to each report.
- `.gitignore` now excludes `.stage*/`, `.post40-plan/`, `.workbuddy/` and
  `docs/GITHUB_RELEASE_PROMPT.md`.

### Changed

- **Documentation restructure, superseding the short-README pass below.** `README.md` is the English
  entry; `README.zh-CN.md`, `README.zh-TW.md` and `README.ja.md` are the Simplified Chinese, Traditional
  Chinese and Japanese mirrors. All four follow one 14-section structure: overview, core features,
  project status, quick start, installation and configuration, usage examples, runtime flow,
  documentation index, running and testing, repository layout, FAQ, contributing, security and license.
  The languages are separate pages linked by the language switch; the intermediate bilingual page and
  `README.en.md` were removed. `scripts/check_readmes.py` checks section parity across the four views,
  local links and anchors, rejects a duplicated English page, and verifies that the static diagram
  carries no animation.
- `README.zh-TW.md` and `README.ja.md` were rebuilt from the old long-form guides to the same 14-section
  structure, with the compact diagram, aligned terminology and the current project-status table.
- `assets/runtime-flow.svg` and `assets/runtime-flow-static.svg` were redrawn as a one-page flow: a
  boot line, the entry/admission row, the fixed plan bar, five nodes with their context keys, the
  response card, and guard/branch footnotes. The 1000 × 398 canvas keeps the README compact while
  still covering startup, the call chain, data flow, branches and the terminal state. Both files are
  regenerated by `scripts/render_runtime_flow.py`, which now fails on label overflow.
- `CONTRIBUTING.md`, `SECURITY.md` and `docs/runtime-flow.md` were aligned with the four README views
  and the compact diagram.

- `README.md` became the English primary document at this point. It was previously a 664-line Chinese page whose
  final 56 lines were a chronological dump of stages 1–40.
- Both primary READMEs were cut to roughly 110 lines: positioning, badges, the runtime diagram,
  features, a copy-paste quick start, one request example, a documentation index, and the known
  limits. The detail that used to live there — API fields, endpoints, response evidence, layer
  tables, repository layout, design notes, database and model support, the per-feature status table
  — moved to `docs/usage.md`, `docs/architecture.md` and `docs/support-matrix.md`. Every claim still
  links to the file or acceptance report that supports it.
- The hero illustration is now `assets/runtime-flow.svg`, a generated animated diagram of the request
  lifecycle, with `assets/runtime-flow-static.svg` as the motion-free fallback. It replaces the
  960 px `assets/pipeline.gif` in the English and Simplified Chinese READMEs; the older file remains
  for the Traditional Chinese and Japanese editions.
- Every capability statement in both READMEs is now traceable to a file, and unproven work is
  labelled as unproven instead of described as a feature.

### Fixed

- Keep smart-query conversations and in-flight requests across module navigation. New
  conversations isolate late responses and preserve the original conversation's history.
- Restore the registration route and login-page entry, return successful signups to login,
  and report invalid invitations, duplicate accounts and UTF-8 password length errors.
- Replace SQL confidence percentages with performed-check evidence. Document the legacy static
  diagnostic formula and explicitly disclose incomplete verification and business semantics.
- Preserve the single-statement read-only policy for SQL containing trailing assumption comments,
  normalize supported driver dialect aliases, and block clarification drafts from execution.
- Keep partial composite-key relationships and type warnings explicitly unverified, and refresh
  frozen synthetic prompt fingerprints after the prompt changes.
- README claimed an `app/web/` Streamlit fallback UI. That directory does not exist.
- README listed 12 API modules; there are 13 routers across 16 files.
- README advertised 6 preset workflows with 4/3/5/5/3/4 nodes. There are 7 plan files with
  5/3/3/4/4/6/5 nodes, and only `request_query` is executable through the API.
- README's database table listed 6 of the 11 declared dialects.

## [0.1.0] - unreleased

Initial MVP state of `main`.

### Added

- Fixed harness: YAML plans with an explicit `node_order`, a runner that walks it, and nodes that
  never call each other.
- `ConstraintEnforcer` (read-only, row cap, LLM call cap) and `PermissionManager` (allow / deny /
  ask, default deny).
- Single-statement AST read-only SQL policy, with read-only driver sessions as a second line.
- Twelve built-in nodes; `request_query` wires `parse_nl → schema_linking → generate_sql →
  validate_sql → execute_sql`.
- Schema linking over tables, columns, comments and foreign keys; multi-stage validation covering
  syntax, schema references, types, execution plan and business rules.
- Hybrid retrieval: local Ollama embeddings in LanceDB, BM25/FTS5, reciprocal-rank fusion.
- Four MCP servers (`db`, `knowledge`, `vector`, `learning`) plus an MCP client.
- JWT authentication against MySQL, with an administrator allow-list and encrypted storage for
  user-configured LLM keys.
- FastAPI surface: 93 routes across 13 routers, including JSON, SSE and MCP views of the same query
  contract.
- Three-page web workspace built locally with esbuild; no CDN dependency at runtime.
- Offline evaluation tooling: frozen acceptance sets, SQL output contracts, prediction export,
  usage-cost estimation, and a controlled model-evaluation entry point.
- Optional local usage audit that stores no question text and no SQL.
- CI: `checks.yml` (install, frontend checks, ruff, 20 mypy gates, pytest) and
  `real-databases.yml` (PostgreSQL 16 and MySQL 8.4 service containers).

### Known limitations

- Four of eleven declared database dialects are implemented: SQLite, DuckDB, PostgreSQL, MySQL.
- `PlanSelector` is constructed at boot and never consulted; the request path is fixed to
  `request_query`.
- No accuracy, recall or capacity baseline has been established on real data.
- Single-tenant by design: one administrator group shares one data set.

[Unreleased]: https://github.com/Qorq-byte/Data_engineer/compare/main...HEAD
