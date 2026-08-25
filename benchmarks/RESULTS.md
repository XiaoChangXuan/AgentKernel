# Context VM real-provider benchmark result

Run date: 2026-08-25

Provider model: `azure/gpt-5.4-2026-03-05`

Command: `AGENTKERNEL_RUN_REAL_BENCHMARK=1 python -m benchmarks.context_real_provider_benchmark`

No endpoint or credential is recorded here.

## Resource efficiency

Actual Provider usage is shown in tokens. Phase 2/3 makes two calls on the first
compaction turn: one summary call and one final quality call. `Total input` includes
both so the first-compaction cost is not hidden.

| Case | Mode | Final input | Summary input | Total input | Calls | Seconds |
|---|---|---:|---:|---:|---:|---:|
| Early constraint | Full | 13,665 | 0 | 13,665 | 1 | 4.54 |
| Early constraint | Phase 1 | 5,602 | 0 | 5,602 | 1 | 3.43 |
| Early constraint | Phase 2/3 | 2,992 | 3,311 | 6,303 | 2 | 11.35 |
| Middle decision | Full | 13,670 | 0 | 13,670 | 1 | 3.95 |
| Middle decision | Phase 1 | 5,607 | 0 | 5,607 | 1 | 3.62 |
| Middle decision | Phase 2/3 | 2,993 | 3,318 | 6,311 | 2 | 10.02 |
| Large Tool tail | Full | 13,668 | 0 | 13,668 | 1 | 4.40 |
| Large Tool tail | Phase 1 | 5,605 | 0 | 5,605 | 1 | 5.34 |
| Large Tool tail | Phase 2/3 | 2,949 | 3,314 | 6,263 | 2 | 8.40 |

Mean actual usage:

| Mode | Final input | First-turn total input | Calls | Mean seconds |
|---|---:|---:|---:|---:|
| Full | 13,668 | 13,668 | 1 | 4.30 |
| Phase 1 | 5,605 | 5,605 | 1 | 4.13 |
| Phase 2/3 | 2,978 | 6,292 | 2 | 9.92 |

- Phase 2/3 first-compaction total input is 54.0% below Full.
- Its post-compaction final request is 78.2% below Full and 46.9% below Phase 1.
- The summary call makes the first Phase 2/3 turn 12.3% more input-expensive than
  Phase 1, but that checkpoint is durable and can be reused on later turns.
- No Provider overflow occurred in these nine quality requests. Overflow recovery is
  covered by deterministic offline tests, including the exactly-one-retry guard.

## Task quality

| Case | Full | Phase 1 | Phase 2/3 |
|---|---:|---:|---:|
| Early constraint retained | pass | pass | pass |
| Middle decision retained | pass | pass | pass |
| Large Tool Result tail error identified | pass | fail | pass |

Full and Phase 2/3 both passed 3/3 cases. Phase 1 passed 2/3 and failed to identify
the `FATAL: permission denied` evidence after eviction. Phase 2/3 retained that fact
through deterministic Tool Result pruning and semantic compaction.

## Accounting observations

The deterministic request estimator is intentionally not treated as exact Provider
billing:

- It overestimated the Full requests (about 16.9K estimated vs 13.7K actual).
- It underestimated Phase 1 (about 4.83K estimated vs 5.60K actual).
- It was closer for the Phase 2/3 final request (about 2.82K estimated vs 2.98K actual).

This variance confirms why normalized Provider overflow recovery remains necessary
even with complete-request fallback accounting.

## Release interpretation

The benchmark supports closing V0.4: Context VM materially reduced real Provider
input while matching Full History on all three scoped quality checks. This is not a
general coding-agent success-rate claim. The fixture is small, uses one model, and
does not measure long-horizon SWE-bench performance or production cost variance.
