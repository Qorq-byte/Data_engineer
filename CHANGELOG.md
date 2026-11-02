# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims
to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html). No git tag has been cut yet,
so `0.1.0` below denotes the current `main`, not a release artefact.

## [Unreleased]

### Added

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
- `assets/pipeline.gif` (960 px, seamless loop) and `assets/pipeline.svg` (static fallback).
- `docs/archive/stages/INDEX.md`: a timeline over the 53 optimisation stages plus the stage-44 live
  acceptance record, with topic, date, conclusion and a link to each report.
- `.gitignore` now excludes `.stage*/`, `.post40-plan/`, `.workbuddy/` and
  `docs/GITHUB_RELEASE_PROMPT.md`.

### Changed

- `README.md` is now the English primary document. It was previously a 664-line Chinese page whose
  final 56 lines were a chronological dump of stages 1–40.
- Every capability statement in both READMEs is now traceable to a file, and unproven work is
  labelled as unproven instead of described as a feature.

### Fixed

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
