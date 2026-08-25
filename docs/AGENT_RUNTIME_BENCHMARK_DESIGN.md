# Agent Runtime Benchmark Design

## 1. Scope

This document designs runtime benchmarks for AgentKernel. It does not implement
benchmark code and does not change `agentkernel/`.

The goal is to show where AgentKernel has runtime value compared with ordinary
agent frameworks:

- bounded context construction instead of unbounded history;
- recoverable sessions instead of volatile loop state;
- durable side-effect handling instead of blind tool retry;
- resource handles instead of stuffing large payloads into model context.

Each benchmark should run offline by default with deterministic fixtures. Real
provider runs may be added later as optional, explicitly enabled measurements.

## 2. Shared Methodology

Common controls:

- fixed task fixtures and expected outputs;
- deterministic scripted LLM baseline for correctness/recovery tests;
- optional real LLM runner behind explicit environment flags;
- same tool behavior across modes;
- identical max step/tool budgets unless a mode cannot express them;
- isolated temp workspace and durable state directory per run;
- crash injection at named boundaries;
- metrics exported as JSONL plus a human-readable summary.

Required output per case:

- mode name;
- success/failure result;
- model calls;
- tool calls;
- durable events written;
- session bytes;
- resource bytes stored/read;
- estimated input/output tokens;
- actual provider usage if available;
- wall-clock latency;
- recovery status after restart;
- duplicate or lost external actions.

Benchmark results should not claim broad coding-agent superiority. They should
claim only the specific runtime property being measured.

## 3. Benchmark 1: Context Management

Purpose: prove the value of Context VM over naive history handling.

Scenario:

```text
1000-step agent run
  - early steps define durable constraints
  - middle steps introduce decision facts
  - later steps generate repeated noisy tool output
  - final step must answer using early + middle + tail facts
```

Comparison modes:

| Mode | Description |
|---|---|
| A. Full History | Send all messages and tool results until context overflow or provider failure. |
| B. Simple Summary | Replace older history with a coarse rolling summary without page provenance or tool atomicity guarantees. |
| C. Codex-style Replacement | Replace a span of prior surface/history with a compact representation while preserving a recent tail. This is a product/runtime baseline, not an AgentKernel implementation requirement. |
| D. AgentKernel Context VM | Use Event Log -> Projection -> Context Page -> Working Set, including pinning, atomic groups, pruning, compaction, and overflow recovery. |

Fixture shape:

- one early user constraint that must be obeyed at the end;
- one middle tool result containing the decisive answer;
- one large late tool result containing an error only in its tail;
- distractor tool calls with high token volume;
- final question requiring all three useful facts.

Metrics:

| Metric | Why it matters |
|---|---|
| Token usage | Measures whether the runtime controls physical context growth. |
| Latency | Captures extra compaction/reclaim cost versus request-size savings. |
| Task success | Prevents token reduction from becoming silent quality loss. |
| Recovery ability | Confirms summaries/projections remain reconstructable after restart. |
| Tool protocol validity | Ensures eviction does not orphan assistant tool calls or tool results. |
| Context pressure events | Shows when and why reclaim mechanisms trigger. |

Expected evidence:

- Full History should fail or become uneconomical as the run grows.
- Simple Summary should save tokens but may lose early/middle/tail facts.
- Replacement-style baselines should be competitive when the replacement is
  correct, but should be evaluated for provenance and recovery behavior.
- AgentKernel should preserve critical facts through pinned pages, atomic
  groups, summaries with provenance, and bounded retries.

Acceptance condition:

AgentKernel must reduce final request size significantly versus Full History
while passing all fixture checks and recovering a valid working set from durable
session state.

## 4. Benchmark 2: Crash Recovery

Purpose: prove that session replay and recovery analysis preserve runtime state
across crash boundaries.

Scenario:

```text
Agent
  -> Step
  -> Tool
  -> Crash
  -> Restart
  -> Recovery analysis
```

Crash injection points:

| Point | Meaning |
|---|---|
| after `turn/start` | Turn opened but no model response. |
| after `step/start` | Step opened but no assistant message. |
| after `assistant/message` with tool calls | Tool calls announced but not executed. |
| after `tool/call` | Tool call pending. |
| after `tool/prepare` | Mutation intent durable, no dispatch. |
| after `tool/dispatch` | External outcome ambiguous. |
| after `tool/commit` | Tool outcome durable, result not yet appended. |
| after `tool/result` | Step can be closed after restart. |

Comparison:

| Runtime | Expected behavior |
|---|---|
| Ordinary Agent | Usually reconstructs from in-memory loop variables or model transcript only; loses precise pending operation state. |
| AgentKernel | Replays Session Event Log and classifies active turn, active step, pending tool calls, completed calls, durable operations, and compaction state. |

Metrics:

- lost state count;
- duplicate action count;
- recovery classification accuracy;
- recovery time;
- number of events replayed;
- whether final answer can continue without corrupting tool protocol;
- whether corrupted logs are rejected.

Expected evidence:

AgentKernel should produce deterministic `COMPLETED`, `INTERRUPTED`, or
`CORRUPTED` analysis from the durable prefix. It should not rely on hidden
in-memory loop state.

Acceptance condition:

For every valid prefix, AgentKernel must report a legal recovery position. For
every intentionally corrupted prefix, it must reject the session rather than
silently projecting invalid model history.

## 5. Benchmark 3: Durable Side Effect

Purpose: prove durable Tool WAL value for externally successful effects followed
by a crash.

Scenario:

```text
Payment API
  external success
  -> crash before local final result
  -> restart
  -> retry or reconcile decision
```

Fake payment fixture:

- `create_payment(amount, operation_id)` accepts a stable idempotency key;
- ledger records external charges by operation ID;
- optional reconcile endpoint returns `SUCCEEDED`, `FAILED`, `NOT_FOUND`,
  `IN_PROGRESS`, or `UNKNOWN`;
- crash injector can stop after dispatch, after external success, after commit,
  or after result append.

Comparison modes:

| Mode | Description |
|---|---|
| Ordinary Tool Agent | Calls payment tool directly and may retry after crash with a new implicit operation. |
| AgentKernel WAL | Uses `tool/prepare`, stable `operation_id`, `tool/dispatch`, `tool/commit`/`abort`, and reconciliation classification. |

Metrics:

| Metric | Why it matters |
|---|---|
| Duplicate execution | Counts duplicate charges or duplicate external mutations. |
| Recovery correctness | Confirms completed, retryable, reconcilable, and manual-required cases are classified correctly. |
| Lost successful result | Detects external success that cannot be represented locally after restart. |
| Manual intervention rate | Measures how often opaque mutations become `MANUAL_REQUIRED`. |
| Recovery time | Measures replay and reconciliation overhead. |

Expected evidence:

- With idempotent mutation, retry should reuse the operation identity and avoid
  duplicate external effects.
- With reconcilable mutation, restart should query external state before
  deciding whether to commit, abort, retry, or wait.
- With opaque mutation after dispatch, AgentKernel should refuse automatic blind
  retry and classify manual intervention.

Acceptance condition:

AgentKernel must never double-charge in idempotent or successfully reconciled
cases, and must prefer explicit `MANUAL_REQUIRED` over unsafe replay for opaque
ambiguous mutations.

## 6. Benchmark 4: Resource Management

Purpose: prove the value of Artifact Handle / Virtual Resource for large tool
results.

Scenario:

```text
Tool returns 100 MB or 1 GB logical result
  -> agent must inspect selected ranges
  -> final answer references exact facts from the payload
```

Comparison modes:

| Mode | Description |
|---|---|
| Full Context | Attempt to place the full tool result in model history/context. |
| Pruning | Keep deterministic head/marker/tail preview only. |
| Artifact Handle | Store full bytes in ResourceStore; expose preview + `artifact://` handle; use `resource_stat` and bounded `resource_read`. |

Fixture variants:

- target fact near head;
- target fact near middle;
- target fact near tail;
- multiple target facts requiring two or more range reads;
- malformed/large binary-like data that must not be decoded into the prompt as
  unbounded text.

Metrics:

- context size;
- session event size;
- resource bytes stored;
- resource bytes read;
- model-visible bytes saved;
- memory usage;
- read latency;
- final answer correctness;
- restart read correctness;
- unauthorized read denial.

Expected evidence:

- Full Context should fail or explode token/memory cost at large sizes.
- Pruning should be cheap but may miss middle facts.
- Artifact Handle should keep session/context bounded while preserving exact
  recoverable bytes behind authorized range reads.

Acceptance condition:

AgentKernel should answer correctly with bounded context growth, stable restart
resolution, and no direct exposure of store paths or unauthorized handles.

## 7. Benchmark Report Format

Recommended future report structure:

```text
benchmarks/results/<timestamp>/
  context_management.jsonl
  crash_recovery.jsonl
  durable_side_effect.jsonl
  resource_management.jsonl
  summary.md
```

`summary.md` should include:

- environment;
- commit hash;
- Python version;
- provider/model only if a real provider was used;
- fixture versions;
- pass/fail table;
- token/latency/resource charts;
- known limitations.

## 8. What These Benchmarks Prove

These benchmarks prove runtime properties, not general agent intelligence:

- Context VM controls physical model input while preserving selected facts.
- Session replay gives deterministic recovery state.
- Durable Tool WAL prevents unsafe duplicate side effects where external systems
  provide idempotency or reconciliation.
- Resource handles keep large payloads out of context without losing durable
  access to exact bytes.

That is the core value claim for AgentKernel versus a typical prompt/tool agent
framework.
