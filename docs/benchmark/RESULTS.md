# AgentKernel Runtime Benchmark Results

This report records one actual offline run of the AgentKernel Runtime Benchmark
Suite v0.1. The benchmark command was:

```bash
python -m benchmarks.run_all
```

Generated result files:

- `benchmarks/results/resource.json`
- `benchmarks/results/durable_tool.json`
- `benchmarks/results/recovery.json`
- `benchmarks/results/context_vm.json`
- `benchmarks/results/all.json`

## Environment

| Field | Value |
|---|---|
| Run timestamp | 2026-08-25T20:05:08.3077207+08:00 |
| Python | 3.12.10 |
| OS | Windows-11-10.0.26100-SP0 |
| Machine | AMD64 |
| CPU | Intel(R) Core(TM) Ultra 7 255H |

The run used the offline benchmark entrypoint. It used synthetic fixtures,
`ScriptedLLM`, temporary local stores, and fake services. No real provider API
was called by `python -m benchmarks.run_all`.

## Resource Benchmark Result

Source: `benchmarks/results/resource.json`

| Case | Strategy | context_bytes | estimated_tokens | resource_bytes | read_latency_ms | preserved_marker | restart_success | success |
|---|---|---:|---:|---:|---:|---|---|---|
| 10MB_tool_result | full_tool_result | 10485868 | 2621467 | 0 | 0.0 | true | false | true |
| 10MB_tool_result | v0_4_pruning | 12430 | 3108 | 0 |  | false |  | false |
| 10MB_tool_result | artifact_handle | 12806 | 3202 | 10485760 | 10.989 | true | true | true |
| 100MB_tool_result | full_tool_result | 104857708 | 26214427 | 0 | 0.0 | true | false | true |
| 100MB_tool_result | v0_4_pruning | 12431 | 3108 | 0 |  | false |  | false |
| 100MB_tool_result | artifact_handle | 12809 | 3203 | 104857600 | 11.199 | true | true | true |
| 500MB_tool_result | full_tool_result | 524288108 | 131072027 | 0 | 0.0 | true | false | true |
| 500MB_tool_result | v0_4_pruning | 12431 | 3108 | 0 |  | false |  | false |
| 500MB_tool_result | artifact_handle | 12809 | 3203 | 524288000 | 11.99 | true | true | true |

Observed from this run:

- Full Tool Result context grew from 10,485,868 bytes at 10 MiB to
  524,288,108 bytes at 500 MiB.
- Artifact Handle context stayed within 12,806 to 12,809 bytes while storing
  10,485,760 to 524,288,000 bytes outside the model context.
- V0.4-style pruning kept context small, but the benchmark-required middle
  marker was not preserved in any of the three large-result cases.
- Artifact Handle successfully read required ranges after restart in all three
  cases.

## Durable Tool Result

Source: `benchmarks/results/durable_tool.json`

| Case | Strategy | execution_count | duplicate_execution | recovery_status | latency_ms | success |
|---|---|---:|---|---|---:|---|
| payment_success_then_crash | ordinary_tool | 2 | true | retried_with_new_request_id | 0.022 | false |
| payment_success_then_crash | agentkernel_wal | 1 | false | succeeded | 11.551 | true |

Observed from this run:

- The baseline retry executed the fake payment side effect twice.
- AgentKernel WAL recovery used the stable operation id and reconcile path, then
  completed the session with one external operation.

## Recovery Result

Source: `benchmarks/results/recovery.json`

| Crash point | recovery_status | replay_time_ms | pending_operation | pending_tool_calls | duplicate_risk | lost_events | success |
|---|---|---:|---|---:|---|---:|---|
| after_user_message | interrupted | 1.015 |  | 0 | false | 0 | true |
| after_step_start | interrupted | 0.855 |  | 0 | false | 0 | true |
| after_tool_call | interrupted | 1.762 |  | 1 | false | 0 | true |
| after_tool_dispatch | interrupted | 2.109 | reconcile_required | 1 | false | 0 | true |
| before_commit | interrupted | 1.396 | reconcile_required | 1 | false | 0 | true |
| after_result | interrupted | 1.818 | completed | 0 | false | 0 | true |

Observed from this run:

- All six crash prefixes replayed without lost events.
- Dispatch and before-commit crashes were classified as requiring reconcile.
- A crash after result recorded the durable operation as completed.
- No crash point produced duplicate-action risk in this benchmark run.

## Context VM Result

Source: `benchmarks/results/context_vm.json`

| Case | Strategy | context_tokens | reclaim_tokens | compaction_cost | final_correctness | recovery_ability | latency_ms | success |
|---|---|---:|---:|---:|---|---|---:|---|
| 1000_turn_agent | full_history | 298891 | 0 | 0 | true | true | 6.688 | true |
| 1000_turn_agent | simple_summary | 618 | 298273 | 0 | false | false | 9.336 | false |
| 1000_turn_agent | replacement_history | 661 | 298230 | 0 | true | false | 9.802 | true |
| 1000_turn_agent | agentkernel_context_vm | 2091 | 284791 | 284925 | true | true | 517.652 | true |

Observed from this run:

- Full History preserved correctness but used 298,891 context tokens.
- Simple Summary reduced context to 618 tokens but lost required benchmark facts.
- Replacement History retained correctness with 661 tokens, but the benchmark did
  not classify it as recovery-capable.
- AgentKernel Context VM produced a 2,091-token working set, preserved final
  correctness, and retained recovery ability through session-backed projection
  and durable compaction.

## Interpretation

These results measure runtime mechanisms, not model quality.

Resource Handle shows that large tool results can be moved out of model context
without losing exact access. In this run, Artifact Handle kept the prompt-facing
representation around 12.8 KiB across 10 MiB, 100 MiB, and 500 MiB payloads,
while restart reads still succeeded.

Durable Tool shows that side effects need stable operation identity and replay
state. In this run, a plain retry duplicated the fake payment operation, while
AgentKernel used WAL state plus reconcile to complete with one external
operation.

Crash Recovery shows that session event replay can classify interrupted runtime
states. In this run, every injected prefix reloaded with zero lost events and no
duplicate-action risk.

Context VM shows that context construction can be treated as a runtime service
rather than only a prompt string. In this run, AgentKernel bounded the working
set while preserving required facts and keeping recovery ability.

## Limitations

- The fixtures are synthetic and intentionally narrow.
- The benchmark is offline.
- The suite does not measure model intelligence.
- The suite does not compare GPT, Claude, Gemini, Codex, or any other product.
- Latency numbers are local-machine measurements and should be compared only
  within the same run and environment.
- The fake payment service models idempotency and reconciliation behavior, not a
  complete payment provider API.
- Context correctness is marker-based, not human answer-quality evaluation.
