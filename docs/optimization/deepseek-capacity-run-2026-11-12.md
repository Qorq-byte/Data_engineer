# Stage 64: DeepSeek fixed capacity batch

Date: 2026-11-12. Run: `stage64-20261112-a`.

## Frozen run

- Profile: `synthetic-nl2sql-capacity-v1`; model alias `deepseek-v4-flash`; endpoint `https://api.deepseek.com/v1`.
- 30 public synthetic questions, split into 10 requests at concurrency 1, 2, and 4.
- Maximum 30 physical calls, one per case, `n=1`, no retries, 1,024 output tokens per request.
- Request timeout 30 seconds; per-case timeout 45 seconds; supervised worker limit 1,500 seconds.
- Runtime: Python 3.13.6, LiteLLM 1.102.0, SQLGlot 30.18.0, Pydantic 2.13.5, HTTPX 0.28.1.
- Manifest SHA-256: `7c48254eb4ee9e2989f422a3ea20638adc505d859d99b9745af2eca3024c62eb`.
- The run used the local working tree at Git HEAD `0988118` with additional uncommitted changes. The manifest binds the run to the source and prompt hashes; it does not claim a clean commit build.

## Results

All 30 calls were reserved before transport and received a provider response. There were no unknown transport outcomes or automatic retries.

| Concurrency | Cases | Wall time | Responses/s | Provider latency P50 | Provider latency P95 |
|---:|---:|---:|---:|---:|---:|
| 1 | 10 | 29.48 s | 0.339 | 1.46 s | 5.70 s |
| 2 | 10 | 17.60 s | 0.568 | 1.66 s | 6.76 s |
| 4 | 10 | 15.04 s | 0.665 | 1.50 s | 6.14 s |

Across all attempted cases, P50/P95/P99 elapsed time was 1.70/6.79/6.88 seconds. Each concurrency level has only ten samples, so its tail percentiles are descriptive for this run, not a stable SLO.

- 25 cases produced a complete SQL candidate; 24 passed SQL validation.
- Five outputs were truncated at the configured token cap; one generated SQL candidate failed validation.
- Synthetic contract evaluation matched 72/90 checks, with all three checks matching for 24/30 cases (80%); the development split matched 16/20 and the holdout split 8/10. This is not a business accuracy result.
- Usage reported for all calls: 25,934 prompt tokens and 12,106 completion tokens.
- `actual_cost_usd` was not supplied by the provider response or the audit artifact. Cost is unknown; token counts are not presented as a bill.

## Scope

This is one bounded capacity sample against a model alias, not a production capacity SLO or model-version guarantee. It does not satisfy Stage 63 business-quality acceptance. Raw prompts, SQL, responses, and worker logs remain in the ignored local run directory `data/nl2sql-acceptance/stage64-20261112-a/`; only this aggregate report is intended for source control.
