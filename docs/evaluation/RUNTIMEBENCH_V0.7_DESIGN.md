# RuntimeBench V0.7 Design

Status: design freeze for AgentKernel RuntimeBench V0.7.

Post-freeze implementation note: current V0.7 release evidence implements B1
through B7 and leaves B8 Scheduler Scalability unimplemented as a future
micro/stress benchmark. The measured release evidence lives in
`benchmarks/results/runtimebench_v0.7.json` and
`docs/evaluation/V0.7_RUNTIMEBENCH_REVIEW.md`.

Decision:

```text
READY_FOR_IMPLEMENTATION
```

Scope:

- Define a unified RuntimeBench architecture for AgentKernel V0.7.
- Freeze benchmark families B1 through B8.
- Define the result schema, fixture model, metrics dictionary, baseline matrix,
  and ablation plan.
- Review current module benchmarks and define KEEP / EXTEND / REWRITE migration.
- Do not implement a runner.
- Do not modify `agentkernel/`.
- Do not implement V0.8, Process Tree, IPC, Multi-Agent, Delegation, Namespace,
  or Memory.

## 1. Motivation

AgentKernel already has several deterministic offline benchmarks, but they are
organized by implementation module:

- Resource Handle benchmark.
- Durable Tool benchmark.
- Recovery benchmark.
- Context VM benchmark.
- Capability runtime benchmark.
- V0.7 scheduler/accounting benchmark.

That shape was useful while V0.1 through V0.7 were built incrementally. It is
not sufficient as the long-term evaluation surface because AgentKernel's claim
is not "this module works in isolation." The research claim is that a small
trusted runtime kernel can preserve system-level invariants while an LLM agent
uses tools, crashes, accumulates context pressure, triggers external side
effects, consumes resources, and crosses authorization boundaries.

RuntimeBench V0.7 should therefore become a property-oriented benchmark suite:

```text
runtime property -> deterministic fixture -> controlled baseline -> metrics -> result schema
```

The benchmark should remain offline and deterministic by default. It should not
measure model intelligence, product UX, provider quality, or cross-product
superiority.

## 2. RuntimeBench Goals

RuntimeBench V0.7 has four design goals.

1. Preserve the current evidence.

   Existing benchmark results are valuable and should not be deleted. The first
   RuntimeBench version should wrap and normalize them, not discard them.

2. Move from module benchmarks to runtime-property benchmarks.

   The benchmark categories should align with AgentKernel's claims:
   recoverability, side-effect safety, context efficiency, authority isolation,
   resource governance, long-horizon stability, and boundary isolation.

3. Provide a stable result contract.

   Results should be emitted as a single JSON document at:

   ```text
   benchmarks/results/runtimebench_v0.7.json
   ```

   The planned entrypoint is:

   ```text
   python -m benchmarks.runtimebench
   ```

   This document only designs the entrypoint and output. It does not implement
   them.

4. Keep future versions on the same evaluation track.

   V0.8 should extend RuntimeBench with Multi-Agent Isolation. V0.9 should
   extend it with Persistent Memory Correctness. They should not create unrelated
   ad hoc benchmark suites.

## 3. Current Benchmark Inventory

Current benchmark files:

| Benchmark area | Current implementation | Current result file | Status |
| --- | --- | --- | --- |
| Resource Handle | `benchmarks/resource_handle/runner.py`, `benchmarks/resource_handle_benchmark.py` | `benchmarks/results/resource.json` | Existing release evidence. |
| Durable Tool | `benchmarks/durable_tool/runner.py` | `benchmarks/results/durable_tool.json` | Existing release evidence. |
| Recovery | `benchmarks/recovery/runner.py` | `benchmarks/results/recovery.json` | Existing release evidence. |
| Context VM | `benchmarks/context_vm/runner.py` | `benchmarks/results/context_vm.json` | Existing release evidence. |
| Capability runtime | `benchmarks/capability_runtime_benchmark.py` | `benchmarks/results/capability_runtime.json` | Existing V0.6 evidence. |
| V0.7 runtime | `benchmarks/v0_7_runtime_benchmark.py` | `benchmarks/results/v0.7_runtime.json` | Existing V0.7 evidence. |
| Aggregate v0.1 runner | `benchmarks/run_all.py` | `benchmarks/results/all.json` | Existing module aggregate. |

Current schema shape:

```json
{
  "benchmark": "resource_handle",
  "case": "100MB_tool_result",
  "strategy": "artifact_handle",
  "metrics": {
    "context_bytes": 12809,
    "success": true
  }
}
```

This is simple and useful, but RuntimeBench needs more metadata:

- benchmark family and category;
- runtime version;
- environment;
- fixture version;
- baseline identity;
- limitations;
- success oracle;
- failure injection description;
- mechanism under test.

## 4. Existing Benchmark Migration Decision

No current benchmark should be deleted. The migration strategy is to keep the
module runners as leaf fixtures and add a future RuntimeBench aggregator above
them.

| Existing benchmark | Decision | Why | Migration target |
| --- | --- | --- | --- |
| Resource Handle benchmark | KEEP + EXTEND | It uses real large payloads and demonstrates context growth versus handle stability with restart reads. | B3 Context Efficiency + Truth Preservation, plus B6 Long-Horizon Runtime Stability. |
| Durable Tool benchmark | KEEP + EXTEND | It demonstrates naive retry duplicate execution versus AgentKernel WAL/reconcile behavior. | B2 Side Effect Safety and B1 Fault Tolerance. |
| Recovery benchmark | KEEP + EXTEND | It validates six crash prefixes and replay classification. | B1 Fault Tolerance. |
| Context VM benchmark | KEEP + REWRITE_IN_RUNTIMEBENCH | It proves context reduction plus correctness/recovery for the 1000-turn fixture, but it should be merged with large resource and recall checks. | B3 Context Efficiency + Truth Preservation and B6 Long-Horizon Runtime Stability. |
| Capability benchmark | KEEP + EXTEND | It validates Tool, Resource, Durable Tool, and legacy compatibility denial cases. | B4 Capability / Security Isolation and B7 Boundary Isolation. |
| V0.7 scheduler/accounting benchmark | KEEP + EXTEND | It validates Process lifecycle, WAITING/BLOCKED/READY, budget blocking, usage accounting, recovery mapping, and Agent/Process/Session isolation. | B5 Resource Governance, B7 Boundary Isolation, and B8 Scheduler Scalability. |
| Queue throughput or 1000-process scheduling | MICROBENCH_ONLY | Useful for scheduler data structure cost, but not a central research claim. | B8 Scheduler Scalability only. |

The important rewrite is not a code rewrite yet. It is a semantic migration:
RuntimeBench should ask one runtime-property question per benchmark family, then
reuse or extend the existing leaf runners as fixtures.

## 5. Unified RuntimeBench Architecture

Planned command:

```text
python -m benchmarks.runtimebench
```

Planned output:

```text
benchmarks/results/runtimebench_v0.7.json
```

Conceptual execution graph:

```text
RuntimeBench CLI
  -> environment collector
  -> fixture catalog
  -> benchmark family runner
  -> leaf module adapters
  -> schema normalizer
  -> result writer
```

Conceptual file layout for a future implementation:

```text
benchmarks/
  runtimebench.py
  runtimebench/
    __init__.py
    schema.py
    environment.py
    fixtures.py
    families.py
    adapters.py
```

Design constraints:

- Existing module runners remain runnable.
- The aggregate result should be deterministic for offline fixtures.
- RuntimeBench should record raw module output plus normalized records.
- A failing benchmark case should not erase other case results.
- Network and real provider calls remain disabled by default.
- V0.7 must stay single-agent and must not imply V0.8 functionality.

## 6. Benchmark Schema

RuntimeBench V0.7 should emit a top-level document with environment metadata and
an array of normalized benchmark records.

Top-level schema:

```json
{
  "runtimebench_version": "0.7",
  "runtime_version": "AgentKernel V0.7",
  "commit": "git-sha",
  "generated_at": "ISO-8601 timestamp",
  "environment": {
    "python": "3.12.10",
    "os": "Windows-11-10.0.26100-SP0",
    "machine": "AMD64",
    "processor": "Intel64 Family ...",
    "cpu_count": 16
  },
  "summary": {
    "total": 8,
    "passed": 8,
    "failed": 0,
    "decision": "PASS"
  },
  "benchmarks": []
}
```

Normalized benchmark record schema:

```json
{
  "benchmark_id": "B2_side_effect_safety",
  "category": "runtime_correctness",
  "description": "Kernel-managed WAL behavior after crash around an external side effect.",
  "runtime_version": "AgentKernel V0.7",
  "fixture": {
    "fixture_id": "fake_payment_success_then_crash",
    "fixture_version": "v0.7",
    "deterministic": true,
    "offline": true
  },
  "mechanism_under_test": [
    "DurableToolExecutor",
    "operation_id",
    "tool_prepare",
    "tool_dispatch",
    "tool_commit",
    "reconcile"
  ],
  "baseline": {
    "name": "naive_retry",
    "type": "internal_baseline"
  },
  "failure_injection": {
    "enabled": true,
    "point": "after_external_success_before_commit"
  },
  "metrics": {
    "duplicate_effect_count": 0,
    "recovery_latency_ms": 10.0
  },
  "result": {
    "status": "pass",
    "oracle": "no_duplicate_effect_and_recovery_completed"
  },
  "success": true,
  "limitations": [
    "fake external service",
    "offline deterministic fixture",
    "does not prove universal exactly-once semantics"
  ],
  "raw_records": []
}
```

Required fields:

| Field | Meaning |
| --- | --- |
| `benchmark_id` | Stable family/case id, for example `B1_fault_tolerance`. |
| `category` | Metric category such as `runtime_correctness`, `security_isolation`, or `micro_stress`. |
| `description` | Human-readable purpose. |
| `environment` | Top-level run environment, referenced by each case. |
| `fixture` | Deterministic fixture identity and version. |
| `runtime_version` | AgentKernel version under evaluation. |
| `metrics` | Numeric, boolean, or string metric values. |
| `result` | Status and success oracle. |
| `success` | Boolean pass/fail for automation. |
| `limitations` | What this case does not prove. |

Compatibility note:

Existing `benchmark`, `case`, `strategy`, and `metrics` records should be
preserved inside `raw_records` during migration. RuntimeBench should not discard
raw module output.

## 7. Benchmark Families

RuntimeBench V0.7 freezes eight benchmark families. B1 through B7 are the core
runtime-property suite. B8 is explicitly a micro/stress family.

### B1 Fault Tolerance

Research question:

```text
Can AgentKernel recover durable runtime state after named crash boundaries
without losing facts or creating duplicate action risk?
```

Mechanism under test:

- `Session` event log.
- Recovery analysis.
- Durable operation classification.
- Context compaction replay.
- Resource externalization replay.
- Process recovery mapping.

Baseline:

- B0 naive loop with in-memory state.
- Optional workflow baseline where a comparable recovery prefix can be modeled.

Failure injection:

- after user event;
- after model response;
- after tool prepare;
- after dispatch;
- before commit;
- during context compaction;
- during resource externalization;
- during scheduler WAITING/BLOCKED transition.

Metrics:

- `recovery_success_rate`;
- `lost_durable_fact_count`;
- `duplicate_effect_count`;
- `recovery_latency_ms`;
- `pending_operation_classification_accuracy`;
- `manual_intervention_rate`;
- `corrupted_replay_rejected`.

Expected failure mode:

The benchmark should expose runtimes that cannot distinguish safe retry,
reconcile-required, completed, interrupted, and corrupted states after restart.
It must not assume AgentKernel passes; it should measure classification and
continuation behavior.

### B2 Side Effect Safety

Research question:

```text
Does Kernel-managed WAL reduce duplicate side effects after crash around an
external mutation?
```

Mechanism under test:

- `DurableToolExecutor`.
- Stable `operation_id`.
- `tool/prepare`.
- dispatch authorization.
- external dispatch.
- commit/abort.
- reconcile.

Baseline:

- B0 naive direct tool call.
- Naive retry with a new implicit request id.
- Future optional idempotent API baseline.

Failure injection:

```text
prepare
dispatch
external success
kernel crash before local commit
restart
```

Metrics:

- `duplicate_effect_count`;
- `incorrect_success_count`;
- `reconciliation_success_rate`;
- `manual_required_rate`;
- `operation_identity_stable`;
- `recovery_latency_ms`.

Expected failure mode:

Naive retry may duplicate an external mutation or lose ambiguity after dispatch.
AgentKernel should be measured on whether it preserves the operation identity
and chooses retry, reconcile, completed, or manual-required correctly.

### B3 Context Efficiency + Truth Preservation

Research question:

```text
Can AgentKernel reduce model-visible context while preserving exact durable
facts needed for final correctness and recovery?
```

Mechanism under test:

- Context VM projection.
- Context pages.
- Working set selection.
- Compaction.
- Resource handles.
- Bounded `resource_read`.

Baseline:

- Full History.
- Naive truncation.
- Simple Summary.
- Replacement History.
- Future semantic summary baseline.
- Future retrieval baseline.

Failure injection:

- crash during compaction;
- crash during resource externalization;
- restart before final answer;
- context pressure beyond configured budget.

Metrics:

- `context_tokens`;
- `context_bytes`;
- `exact_recall`;
- `final_correctness`;
- `durable_bytes_preserved`;
- `resource_bytes`;
- `resource_read_latency_ms`;
- `context_overflow_count`;
- `compaction_cost`;
- `recovery_ability`.

Expected failure mode:

Full History may grow linearly. Truncation and simple summary may drop exact
facts. Replacement-style strategies may preserve final correctness but lack
replayable provenance. RuntimeBench should separate short context from durable
truth preservation.

### B4 Capability / Security Isolation

Research question:

```text
Does authority remain outside the LLM at Tool, Resource, and Durable mutation
boundaries?
```

Mechanism under test:

- `CapabilityGrant`.
- `CapabilityEvaluator`.
- `ToolRegistry` visibility and execution checks.
- `ResourceService` read/stat checks.
- Durable Tool authorization metadata and audit events.

Baseline:

- Naive tool loop that trusts model-selected tools.
- Naive resource tool that checks only handle syntax.

Failure injection:

- prompt injection requesting hidden tool;
- tool-output injection requesting hidden tool;
- unauthorized resource access;
- unauthorized durable mutation;
- attempt to forge capability-like arguments in model output.

Metrics:

- `unauthorized_execution_count`;
- `unauthorized_access_count`;
- `unauthorized_dispatch_count`;
- `privilege_amplification_count`;
- `false_deny_rate`;
- `audit_metadata_complete`;
- `legacy_compatibility_passed`.

Expected failure mode:

The benchmark should expose designs where the LLM, tool handler, or resource
store becomes the effective authority. V0.7 must not claim delegation,
revocation, namespace, RBAC, IAM, or production sandbox security.

### B5 Resource Governance

Research question:

```text
Can AgentKernel observe resource usage and block a process at cooperative safe
points when budget is exceeded?
```

Mechanism under test:

- `ProcessUsageSnapshot`.
- `UsageCollector`.
- `CooperativeScheduler.safe_point`.
- Agent budget checks.
- BLOCKED process state.

Baseline:

- Naive loop with no runtime budget enforcement.
- Naive post-run accounting that cannot stop execution.

Failure injection:

- token usage over budget;
- tool call count over budget;
- resource bytes over budget;
- wall-time budget exceeded at a safe point;
- restart after budget pause.

Metrics:

- `budget_overshoot`;
- `blocked_correctness`;
- `resource_usage_accuracy`;
- `usage_snapshot_accuracy`;
- `unblock_correctness`;
- `wall_time_observed`;
- `budget_recovery_success`.

Expected failure mode:

Without scheduler-owned safe points, a process may continue after budget breach
or only learn about overrun after completion. RuntimeBench should keep
accounting as observation, not durable billing truth.

### B6 Long-Horizon Runtime Stability

Research question:

```text
Do AgentKernel mechanisms compose under long-horizon pressure without breaking
Session truth, side-effect safety, context bounds, capability checks, or budget
governance?
```

Mechanism under test:

- Session replay.
- Durable Tool WAL.
- Context VM.
- Resource Handle.
- Capability enforcement.
- Process Scheduler.
- Usage Accounting.

Baseline:

- B0 naive loop.
- Naive + truncation.
- Naive + summary.
- AgentKernel ablations.

Failure injection:

- deterministic pseudo-random crash schedule;
- large resource steps;
- durable mutation steps;
- context compaction steps;
- budget pressure steps.

Metrics:

- `state_integrity`;
- `task_completion`;
- `exact_fact_recall`;
- `duplicate_effect_count`;
- `prompt_growth_rate`;
- `resource_growth_bytes`;
- `recovery_count`;
- `runtime_overhead_ms`;
- `final_durable_consistency`.

Expected failure mode:

An implementation may pass isolated tests but fail when mechanisms compose.
B6 is the first family that should support a scoped "runtime stability" claim.
In the current V0.7 release evidence, B6 exists for deterministic single-agent
profiles at 100, 500, and 1000 logical steps. Composition claims should remain
limited to those tested invariants.

### B7 Boundary Isolation

Research question:

```text
Do AgentKernel object boundaries remain stable under runtime operations?
```

Mechanism under test:

- Agent as capability principal.
- Process as runtime identity.
- Session as durable truth.
- Scheduler as mechanism.
- Accounting as observation.
- ResourceStore as storage, not authority.
- Context Page as projection, not authority.

Baseline:

- Agent framework where loop state, transcript state, and tool authority are
  not separated.
- Internal invariant checks.

Failure injection:

- process tries to become capability principal;
- accounting tries to mutate Session truth;
- ResourceStore receives an authorization decision;
- context projection is treated as durable fact;
- LLM-generated data tries to become grant or operation identity.

Metrics:

- `boundary_invariant_passed`;
- `authority_leak_count`;
- `durable_truth_mutation_count`;
- `object_identity_confusion_count`;
- `regression_count`.

Expected failure mode:

The benchmark should catch accidental boundary drift. It is not a speed test; it
is a kernel object-model regression suite.

### B8 Scheduler Scalability

Research question:

```text
What is the engineering overhead of the cooperative scheduler data structures
as process count grows?
```

Mechanism under test:

- READY queue.
- WAITING registry.
- BLOCKED registry.
- dispatch.
- yield.
- wake/unblock.

Baseline:

- Direct function loop.
- Simple FIFO list, if useful as an internal microbaseline.

Failure injection:

- 1, 10, 100, and 1000 synthetic processes;
- alternating READY/WAITING/BLOCKED transitions;
- exited processes removed from scheduling;
- cancellation and pause safe-point checks.

Metrics:

- `dispatch_throughput`;
- `queue_latency_ms`;
- `p95_scheduling_latency_ms`;
- `memory_overhead_bytes`;
- `starvation_indicator`;
- `exited_process_scheduled_count`.

Expected failure mode:

B8 may reveal queue overhead or starvation bugs. It must be labeled as
micro/stress evidence only. It is not a core AgentKernel research claim.

## 8. Fixture Design

All V0.7 fixtures should be deterministic and offline.

### Crash Fixture

Purpose:

Validate replay, operation classification, and recovery mapping across named
prefixes.

Crash points:

- after user event;
- after model response;
- after tool prepare;
- after dispatch;
- before commit;
- during context compaction;
- during resource externalization.

Required controls:

- fixed event sequence;
- fixed operation id;
- fixed fake external service state;
- fixed Session path in a temporary directory;
- no real API call.

### Large Resource Fixture

Purpose:

Validate context/resource behavior with large exact payloads.

Payload sizes:

- 10 MiB;
- 100 MiB;
- 500 MiB;
- optional 1 GiB stress case.

Marker placement:

- HEAD;
- MIDDLE;
- TAIL.

Required controls:

- deterministic payload generator;
- exact byte offsets for markers;
- bounded read sizes;
- restart read checks;
- optional binary-like payload variant.

### Long-Horizon Fixture

Purpose:

Validate composition under runtime pressure.

Step counts:

- 100;
- 500;
- 1000.

Mixed operations:

- ordinary tool calls;
- resource writes and reads;
- context pressure;
- durable mutations;
- deterministic pseudo-random crash schedule;
- budget pressure;
- recovery and continuation.

Required controls:

- scripted model behavior;
- fixed seed;
- stable task oracle;
- fixed budget profile;
- no network.

### Capability Attack Fixture

Purpose:

Validate that model text and tool output cannot manufacture authority.

Attack cases:

- prompt injection requests unauthorized tool;
- tool output injection requests unauthorized tool;
- unauthorized resource access;
- unauthorized mutation;
- forged capability-like JSON in arguments;
- forged operation id in model output.

Required controls:

- fixed grants;
- fixed denied actions;
- fixed denied resources;
- expected error code;
- audit metadata checks.

## 9. Metric Schema

RuntimeBench metrics should be standardized so cases are comparable across
families.

### Reliability

| Metric | Meaning |
| --- | --- |
| `recovery_success_rate` | Fraction of crash cases that recover to a legal state. |
| `lost_durable_fact_count` | Durable facts missing after replay. |
| `duplicate_effect_count` | External effects repeated after crash/retry. |
| `recovery_latency_ms` | Time to reload, replay, classify, and map recovery state. |
| `pending_operation_classification_accuracy` | Whether pending operation state matches the fixture oracle. |
| `manual_intervention_rate` | Fraction of ambiguous cases requiring manual handling. |

### Context

| Metric | Meaning |
| --- | --- |
| `context_tokens` | Estimated model-visible tokens. |
| `context_bytes` | Model-visible bytes. |
| `exact_recall` | Whether required exact marker/fact is available. |
| `durable_bytes_preserved` | Durable bytes still recoverable outside the prompt. |
| `context_overflow_count` | Number of times the strategy exceeds budget. |
| `compaction_cost` | Tokens or time spent compacting, depending on fixture. |

### Security

| Metric | Meaning |
| --- | --- |
| `unauthorized_execution_count` | Unauthorized tool executions that reached handler code. |
| `unauthorized_access_count` | Unauthorized resource accesses that reached store reads. |
| `unauthorized_dispatch_count` | Unauthorized durable dispatches that reached external effect code. |
| `privilege_amplification_count` | Any observed expansion beyond granted action/scope. |
| `false_deny_rate` | Authorized requests incorrectly denied. |
| `audit_metadata_complete` | Whether allow/deny records include required identity and scope metadata. |

### Governance

| Metric | Meaning |
| --- | --- |
| `budget_overshoot` | Amount by which usage exceeded configured budget before block. |
| `blocked_correctness` | Whether the process entered BLOCKED at the expected safe point. |
| `resource_usage_accuracy` | Whether observed resource metrics match fixture operations. |
| `usage_snapshot_accuracy` | Whether `ProcessUsageSnapshot` matches expected totals. |
| `unblock_correctness` | Whether a blocked process returns to READY/RUNNING only after explicit unblock. |

### Overhead

| Metric | Meaning |
| --- | --- |
| `latency_ms` | Wall-clock duration of the case or operation. |
| `cpu_time_ms` | CPU time where practical. |
| `memory_bytes` | Peak or sampled memory use where practical. |
| `disk_bytes` | Session plus resource plus WAL bytes written. |
| `storage_bytes` | Durable storage bytes retained after the run. |
| `token_overhead` | Extra tokens spent on handles, summaries, metadata, or recovery prompts. |

## 10. Baseline Strategy

RuntimeBench should compare properties, not whole products.

Generic baselines:

| Baseline | Meaning | V0.7 use |
| --- | --- | --- |
| B0 Naive ReAct/simple loop | In-memory loop with direct tool execution. | Side-effect, recovery, context, and budget comparisons. |
| B1 Naive + truncation | Simple context cutoff. | Context pressure baseline. |
| B2 Naive + summary | Rolling summary without durable page provenance. | Context and truth-preservation baseline. |

Dimension-specific future baselines:

| Dimension | Candidate baseline | When to use |
| --- | --- | --- |
| Stateful durability | LangGraph | Only when the task maps naturally to workflow persistence. |
| Multi-agent runtime | AutoGen | V0.8, after AgentKernel has Multi-Agent/IPC primitives. |
| Memory correctness | Letta | V0.9, after AgentKernel has Memory primitives. |
| Coding-agent runtime | OpenHands, Codex, Gemini CLI | Only for comparable coding-agent runtime tasks with controlled tools and prompts. |
| Harness architecture | DeepSeek Harness | For session/surface/compaction/capability seam comparisons. |

Control rule:

```text
The primary experimental variable should be Runtime.
```

When a baseline requires different prompts, tools, memory APIs, or checkpoint
semantics, RuntimeBench must record that limitation instead of hiding it behind
an aggregate score.

## 11. Ablation Strategy

Ablations should identify which Kernel mechanism contributes to which runtime
property. They should not pre-commit to positive results.

| Variant | Hypothesis | Metrics | Expected failure mode |
| --- | --- | --- | --- |
| Full AgentKernel | Combined mechanisms preserve V0.7 runtime invariants under deterministic pressure. | B1-B7 metrics plus overhead. | Boundary regression, excessive overhead, or failed mechanism composition. |
| minus WAL | Removing durable mutation protocol should affect side-effect safety after crash. | `duplicate_effect_count`, `reconciliation_success_rate`, `manual_intervention_rate`. | Blind retry, lost ambiguity, or duplicate external mutation. |
| minus Context VM | Removing projection/working-set control should affect prompt growth and recovery. | `context_tokens`, `context_overflow_count`, `final_correctness`, `recovery_ability`. | Full-history growth, lossy truncation, or unreplayable summary state. |
| minus Resource Handle | Removing handles should affect large exact-output handling. | `context_bytes`, `durable_bytes_preserved`, `resource_read_latency_ms`, `exact_recall`. | Payload enters prompt, exact bytes unavailable, or restart access fails. |
| minus Capability | Removing Kernel authorization should affect unauthorized action prevention. | `unauthorized_execution_count`, `unauthorized_access_count`, `false_deny_rate`. | LLM-generated or injected calls reach handlers or stores. |
| minus Scheduler Budget | Removing budget safe points should affect resource governance. | `budget_overshoot`, `blocked_correctness`, `wall_time_observed`, `usage_snapshot_accuracy`. | Process continues after quota breach or accounting is only post-hoc. |

RuntimeBench reports should distinguish "mechanism absent" from "policy changed."

## 12. V0.7 Scope

V0.7 RuntimeBench is single-agent only.

In scope:

- Session event replay.
- Durable Tool WAL/reconcile behavior.
- Context VM projection and compaction.
- Resource Handle storage and bounded reads.
- Capability enforcement at Tool, Resource, and Durable Tool boundaries.
- Process lifecycle.
- Cooperative Scheduler READY/WAITING/BLOCKED behavior.
- Usage accounting and budget blocking.
- Agent/Process/Session boundary isolation.

Out of scope:

- Process Tree.
- IPC.
- Multi-Agent.
- Delegation.
- Revocation.
- Namespace.
- RBAC/IAM.
- Memory.
- production sandbox security.
- real provider intelligence comparison.
- universal exactly-once side effects.

V0.7 success criterion:

```text
RuntimeBench V0.7 demonstrates single-agent runtime invariants with measured
overhead using deterministic offline fixtures.
```

## 13. Output Report Design

Human evidence report:

```text
docs/evaluation/V0.7_RUNTIMEBENCH_REVIEW.md
```

Future generated JSON:

```text
benchmarks/results/runtimebench_v0.7.json
```

Recommended report sections:

1. Environment.
2. Commit and runtime version.
3. Summary table.
4. B1 Fault Tolerance.
5. B2 Side Effect Safety.
6. B3 Context Efficiency + Truth Preservation.
7. B4 Capability / Security Isolation.
8. B5 Resource Governance.
9. B6 Long-Horizon Runtime Stability.
10. B7 Boundary Isolation.
11. B8 Scheduler Scalability.
12. Overhead.
13. Limitations.
14. Claims supported by this run.
15. Claims not supported by this run.

## 14. Future V0.8 Extension

V0.8 should extend RuntimeBench only after Process Tree, IPC, Multi-Agent, and
delegation primitives exist.

Expected new benchmark areas:

- parent creates child with reduced capability;
- child privilege cannot exceed parent privilege;
- child crash does not corrupt parent Session truth;
- IPC message cannot carry unauthorized authority;
- confused deputy attempt through shared tools/resources;
- per-child budget isolation;
- shared resource access rules;
- namespace or logical isolation view.

V0.8 should not reinterpret V0.7 results. It should add multi-agent dimensions
to the same RuntimeBench schema.

## 15. Future V0.9 Extension

V0.9 should extend RuntimeBench only after Memory primitives exist.

Expected new benchmark areas:

- memory write after durable fact;
- memory write denied without capability;
- memory provenance from Session/Tool/Resource facts;
- stale memory handling;
- poisoned memory handling;
- cross-session recall;
- forgetting or deletion semantics;
- memory projection into Context VM.

V0.9 should keep the same schema and add memory-specific fixture fields and
metrics rather than creating a separate memory benchmark format.

## 16. Risks and Limitations

Known risks:

- Synthetic fixtures may not represent real coding-agent workloads.
- Offline fake services model failure modes, not complete production APIs.
- Marker-based recall is narrower than human answer quality.
- Local latency and memory numbers are machine-specific.
- Existing benchmarks are mostly single-agent.
- Runtime accounting is observation, not durable billing truth.
- B8 scheduler scalability is useful engineering evidence but not a core
  research claim.

Mitigations:

- Keep raw JSON output.
- Record environment and commit.
- Keep network disabled by default.
- Report limitations per benchmark family.
- Use ablations as well as external baselines.
- Avoid product-ranking claims.

## 17. Final Freeze Decision

Decision:

```text
READY_FOR_IMPLEMENTATION
```

The V0.7 RuntimeBench design is stable enough to implement a unified runner in a
later task. The runner should wrap existing leaf benchmarks, normalize their
records into the schema above, and emit `benchmarks/results/runtimebench_v0.7.json`.

No V0.8 functionality is required for this design.
