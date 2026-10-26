# Synthetic acceptance v3

This directory freezes 30 SQL contracts and 30 retrieval qrels over public synthetic ecommerce data. It extends the repository's users/products/orders entities with UTC month boundaries, anonymous user IDs, nullable categories/emails, duplicate names and tied amounts. It does not modify v1/v2 or supply a new live model profile.

Each track has 20 dev / 10 public holdout cases, fixed by the explicit split fields. Holdout questions/labels are public: separation supports repeatable regression and disciplined tuning, not a secret or unbiased estimate. SQL runs against base, edge and schema-only empty variants. Amounts are small exact integers stored as REAL; timestamps are fixed UTC TEXT. Full annotations are in each SQL case or qrel; they are not generated from gold-query execution or retrieval output. They were authored in this coding task and checked automatically, with no claim of independent human/business annotation.

`manifest.json` hashes the six actual input files with CRLF normalized to LF for cross-platform checkouts. No other bytes are normalized. Reports also include canonical dataset hashes and raw byte hashes observed on the executing platform. The manifest detects accidental drift; it is neither a signature nor a reason to change labels to improve a score. Revise content through a new explicit version after this freeze.

Run from the repository root:

```powershell
python -m scripts.evaluate_acceptance --output data/acceptance-v3/report.json
python -m scripts.evaluate_acceptance --split dev --output data/acceptance-v3/dev.json
python -m scripts.evaluate_acceptance --split holdout --sql-predictions predictions.json --rankings rankings.json --output data/acceptance-v3/holdout.json
```

SQL predictions are a list of `{id, sql, finish_reason: "stop"}` objects (optional `status`); bound exported predictions are validated using their dataset/case digests. Explicit truncation, refusal or tool calls never execute. SQL uses the existing read policy plus query_only, 2-second deadlines and a 1000-row cap. Output columns must match exactly. Ordered cases compare sequences and enforce a top-level ORDER BY where required; unordered cases compare multisets, preserving duplicate rows. Missing/failed predictions remain in the original denominator. Each case includes a valid wrong SQL and the self-check records a fixture where it differs, rather than treating invalid syntax as a semantic counterexample.

Rankings are a list of `{id, retrieved: [document_id, ...]}` objects. Without a file, the command runs the real SQLite FTS5 BM25 baseline, filtering by both db_id and domain before ranking, and only searches the selected split. Relevant/hard-negative rationale is document-grounded. Unknown, duplicate and cross-scope hits are retained as violations and score zero; they are never silently repaired. Missing rankings are distinct from deliberate empty predictions.

Binary Recall@K, Precision@K, MRR@K and nDCG@K reuse the legacy definitions: precision divides by K, including short rankings. Empty qrels have ranking metrics zero, with a separate correct-empty rate; a missing no-answer prediction is a failure. Reports show all-query and answerable-only macro scores plus per-split statistics. They are lexical synthetic measurements, not embedding quality or business accuracy. Gold and qrels positive controls are self-checks, never model predictions: a default run reports all 30 SQL predictions missing. The command makes zero LLM/embedding calls and reports actual_cost_usd=null. Stage43/44 need separate live preflight/authorization; the existing four-call profiles stay unchanged.
