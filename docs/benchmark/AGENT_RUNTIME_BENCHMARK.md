# Agent Runtime Benchmark

## 1. Why Runtime Benchmark Exists

AgentKernel is an OS-inspired runtime kernel for tool-using agents. Its value is
not that a model becomes smarter. Its value is that core runtime behavior becomes
explicit, bounded, replayable, and auditable.

These benchmarks test runtime mechanisms:

- how model context is constructed and bounded;
- how a crashed session is replayed;
- how externally visible side effects avoid unsafe duplicate execution;
- how large tool outputs stay out of model context while remaining available.

The suite does not compare model providers and does not claim that AgentKernel is
better than Codex, Claude, Gemini, or any other product.

## 2. Test Objects

| Benchmark | Runtime Mechanism |
|---|---|
| Resource Handle | V0.5 Resource Layer, `ResourceHandle`, `ResourceService`, `LocalResourceStore`, `resource_read`. |
| Durable Tool | V0.3 WAL, `operation_id`, prepare/dispatch/reconcile/commit flow. |
| Crash Recovery | V0.2 Session replay and `RecoveryAnalysis`. |
| Context VM | V0.4 event projection, Context Page, working set, pruning, and compaction. |

## 3. Benchmark Method

All benchmarks are deterministic and offline by default.

Resource Handle:

- generate 10 MiB, 100 MiB, and 500 MiB logical tool-result cases;
- compare full context, V0.4-style head/tail pruning, and V0.5 Artifact Handle;
- use real ResourceStore/ResourceService/ResourceHandle for the Artifact Handle
  strategy;
- verify restart reads through a newly created ResourceService over the same
  store directory.

Durable Tool:

- simulate a payment API where external success happens before local completion;
- compare a plain tool retry that generates a new request id with AgentKernel WAL
  recovery using a stable `operation_id`;
- use reconciliation to observe external payment state after restart.

Crash Recovery:

- write JSONL Session prefixes at named crash points;
- reload the session and replay recovery analysis;
- record active turn/step, pending tool calls, durable operation classification,
  and duplicate-action risk.

Context VM:

- build a 1000-turn session with an early constraint, middle decision, large tool
  output, and recent final task;
- compare Full History, Simple Summary, Replacement History, and AgentKernel
  Context VM;
- use `ScriptedLLM` for compaction so the benchmark measures runtime mechanics,
  not model quality.

## 4. Metric Definitions

| Metric | Meaning |
|---|---|
| `context_bytes` | Model-visible byte size for a strategy. |
| `estimated_tokens` / `context_tokens` | Approximate input size using AgentKernel token accounting or byte estimates. |
| `resource_bytes` | Exact payload bytes stored outside context. |
| `read_latency_ms` | Time spent reading selected resource ranges. |
| `restart_recovery` | Whether the resource or session remains usable after restart. |
| `duplicate_execution` | Whether a crash/retry produced duplicate external effects. |
| `operation_count` | Number of externally recorded side-effect operations. |
| `recovery_status` | Replay classification from `RecoveryAnalysis`. |
| `reclaim_tokens` | Tokens saved by Context VM reclaim mechanisms. |
| `compaction_cost` | Tokens involved in Context VM compaction source and summary. |
| `final_correctness` | Whether required fixture facts are present in the final model-visible surface. |
| `success` | Benchmark-specific pass/fail for the runtime mechanism being measured. |

## 5. Current Results

Run:

```bash
python -m benchmarks.run_all
```

Default JSON output is written to:

- `benchmarks/results/resource.json`
- `benchmarks/results/durable_tool.json`
- `benchmarks/results/recovery.json`
- `benchmarks/results/context_vm.json`
- `benchmarks/results/all.json`

The expected qualitative result is:

- Full History preserves information but grows with tool output size.
- Pruning bounds context but loses middle information when exact ranges matter.
- Artifact Handle keeps context bounded and preserves exact restartable reads.
- Plain tool retry can duplicate external payment effects.
- AgentKernel WAL uses `operation_id` and reconcile to avoid duplicate payment.
- Session replay reconstructs legal interrupted or completed prefixes.
- Context VM keeps a bounded working set while recording durable compaction.

## 6. Limits

The suite is intentionally narrow:

- it does not measure model intelligence;
- it does not use real provider APIs;
- latency is local-machine runtime latency, not cloud latency;
- Resource Handle payloads use deterministic synthetic bytes;
- fake payment reconciliation is deterministic and does not model all payment API
  edge cases;
- Context VM correctness checks only marker retention, not natural-language
  answer quality.
