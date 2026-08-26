# AgentKernel Architecture Review After V0.5

Status: review only. This document does not define or implement V0.6
Capability.

Scope:

- Read the real `agentkernel/` code after V0.5 Resource Handle and Runtime
  Benchmark v0.1.
- Review the trusted kernel boundary before Capability / Namespace work.
- Review benchmark results from `benchmarks/results/`.
- Identify readiness items before V0.6.

## Git Baseline

Observed baseline at review time:

| Item | State |
|---|---|
| Current branch | `review/runtime-architecture-after-v0.5` |
| Current HEAD | `611b2c2 benchmark: add runtime benchmark results` |
| `main` | `2167a03 merge: add v0.5 kernel readiness review` |
| Benchmark branch | `feat/runtime-benchmark-results` points to `611b2c2` |
| V0.5 branch | `feat/v0.5-virtual-resource` points to `a032c45` |
| Working tree | Only pre-existing untracked review docs were present before this review |

Interpretation:

- V0.5 Resource Handle is present in branch history.
- Runtime Benchmark v0.1 results are present at the current branch HEAD.
- This review intentionally ignores the pre-existing untracked docs that are not
  part of the requested output.
- No `agentkernel/` core code changes are required for this review.

## Real Architecture Confirmation

The requested architecture still holds:

```text
LLM
  |
Agent Loop
  |
Kernel Services
  |
Drivers
  |
External World
```

The code-level mapping is:

| Layer | Current code | Notes |
|---|---|---|
| LLM | `LLMService`, `ModelRequest`, `ModelResponse` | Provider is injected behind a protocol. |
| Agent Loop | `DefaultAgentLoop` | Owns turn/step orchestration, lifecycle transitions, budget closure, model calls, context preparation, tool execution, and event emission order. |
| Kernel Services | `AgentControlBlock`, `Session`, `ToolRegistry`, `DurableToolExecutor`, `RecoveryAnalysis`, `ContextManager`, `ResourceService` | These services hold the durable and security-relevant invariants. |
| Drivers | `JsonlSessionPersistence`, `LocalResourceStore`, LLM providers, tool handlers, reconcile handlers | Drivers are replaceable but must obey kernel contracts. |
| External World | Filesystem, local resource bytes, model provider, tool side effects, fake or real APIs | External behavior is normalized through kernel services before it becomes durable state. |

The important caveat is that `DefaultAgentLoop` is both an implementation and a
reference boundary. It can be replaced later, but any replacement must preserve
the kernel invariants around lifecycle, event ordering, authorization, durable
tool state, context budgets, and recovery-visible event emission.

## Kernel Boundary Matrix

| Component | Kernel/Policy | Reason |
|---|---|---|
| Agent identity (`agent_id`, `session_id`) | Kernel | Every authorization, ownership, recovery, and future delegation decision needs stable identity. |
| Agent lifecycle state | Kernel | `AgentControlBlock` constrains valid transitions and prevents ambiguous running/waiting/failed states. |
| Parent agent id | Kernel seed, incomplete model | It is already part of the ACB and should become the root of future delegation, but current semantics are not yet enough for V0.8. |
| Agent turn/tool budgets | Kernel | Budgets are hard safety limits enforced by the loop and closed durably with events. |
| Prompt text and prompt assembly policy | Policy | Prompt content can vary by product without changing replay, authorization, or durable state. |
| Model/provider choice | Policy/driver | Provider selection is replaceable behind `LLMService`; provider errors are normalized by kernel-facing logic. |
| Token accounting implementation | Policy with kernel contract | Different estimators can be used, but the loop relies on them to enforce context bounds. |
| Event vocabulary and sequence ordering | Kernel | Recovery, benchmark reproducibility, and future audit depend on contiguous ordered events. |
| Session append and flush semantics | Kernel contract, driver implementation | `Session` owns validation; `SessionPersistence` drivers provide storage durability. |
| JSONL persistence driver | Driver | Replaceable if it preserves single-writer append, load, flush, and corruption semantics. |
| Recovery analysis | Kernel | It reconstructs active turn/step, pending tool calls, durable operation state, compaction state, and corruption status. |
| Tool schema exposure | Kernel/Policy boundary | Tools are policy-provided, but only authorized schemas should be exposed to the model. |
| Tool authorization | Kernel | `ToolRegistry` checks exact `required_capability` against ACB capabilities before execution. |
| Tool implementation | Policy/driver | The handler is application code; kernel normalizes invocation and durable result recording. |
| Durable tool WAL | Kernel | `prepare`, `dispatch`, `commit`, `abort`, and `reconcile` classify external side-effect state after crash. |
| Reconcile handler behavior | Driver with kernel contract | The handler speaks to the external system, but must return kernel-understood recovery status. |
| Context page projection invariants | Kernel | Model-visible context must be reconstructable from session events and compaction events. |
| Context reclaim strategy | Policy | Selection and summarization can change, provided mandatory pages and durable compaction invariants hold. |
| Context budget enforcement | Kernel | Prevents unbounded model-visible state and provider overflow loops. |
| Context compaction prompt/summary content | Policy with kernel validation | The summary can vary, but source fingerprint and lifecycle events are kernel concerns. |
| Resource handle identity and URI parsing | Kernel | Handles are durable references to bytes outside context and must not become raw storage paths. |
| Resource owner check | Kernel | `ResourceService` enforces agent/session ownership before stat/read. |
| Resource byte store | Driver | `ResourceStore` can be local, remote, or object storage if it preserves write/read/stat semantics. |
| Resource read limits and ranges | Kernel | Prevents unbounded context re-entry and invalid access patterns. |
| Tool result externalization threshold | Policy | The decision to externalize is product/runtime policy; committed resources use kernel services. |
| Hooks and plugins | Policy | Extension points may steer behavior, but cannot own trusted ordering or authorization. |
| Retry policy | Policy | Retries are workload-specific, except durable side-effect retry must respect WAL classification. |
| Memory strategy | Policy on kernel primitives | Future memory can use events, context pages, and resources, but should not be a hidden source of model-visible state. |

## Trusted Kernel Boundary

Trusted Kernel responsibilities after V0.5:

- Stable agent identity, session identity, parent identity seed, lifecycle, and
  hard budgets.
- Ordered session event log, append validation, durable flush boundary, load
  validation, and tail-truncation reporting.
- Recovery replay and classification of interrupted turn, step, tool, durable
  operation, and compaction state.
- Tool authorization before model-visible schema exposure and before execution.
- Durable Tool WAL state machine for side effects that can outlive process
  crashes.
- Context VM projection and working-set budget enforcement.
- Resource handle creation, owner checks, URI resolution, range validation, and
  bounded reads.

Policy responsibilities after V0.5:

- Prompt design and prompt assembly content.
- Model/provider selection.
- Context reclaim heuristics and summarization quality.
- Tool implementation details.
- Reconcile implementation details for each external service.
- Resource externalization threshold and preview behavior.
- Memory strategy, retrieval strategy, and plugin composition.

Replaceable components:

- `LLMService` providers.
- `PromptService`.
- `HookManager` listeners.
- `SessionPersistence` drivers.
- `ContextService` implementations, if they preserve Context VM invariants.
- `ResourceStore` implementations.
- Tool handlers, durable tool handlers, and reconcile handlers.
- Tool result processors such as `ToolResultExternalizer`.

Design changes likely to break existing architecture if done carelessly:

- Replacing the append-only session log with mutable transcript state.
- Allowing non-contiguous event sequences or unscoped concurrent writers.
- Moving tool authorization out of the kernel-visible `ToolRegistry` path.
- Treating resource URIs as direct filesystem/object-store paths rather than
  kernel-resolved handles.
- Letting model-visible context include data that cannot be reconstructed from
  logged session state or resource handles.
- Changing durable operation identity so crash recovery cannot reconcile
  `prepare -> dispatch -> external success -> crash`.
- Adding child agents without scoped event ownership, delegation rules, and
  revocation semantics.

## V0.1-V0.5 OS Mapping Review

The OS analogy is useful only when it names a runtime invariant. It should not
turn into Linux-shaped vocabulary without matching kernel semantics.

| Version | Analogy | Accuracy | Limit |
|---|---|---|---|
| V0.1 Execution / Tool Boundary | Process + syscall | Useful. The agent has identity, lifecycle, budgets, and invokes tools through a controlled boundary. | It is not a real OS process model: no address space isolation, scheduler, signal model, or syscall ABI yet. |
| V0.2 Event Log / Recovery | Journal + replay | Accurate for single-session durability. The log is the source of truth for replay and recovery classification. | It is not yet a multi-process journal with concurrency, snapshots, or global audit separation. |
| V0.3 Durable Tool | WAL + transaction intent | Accurate for external side effects. `prepare` and `dispatch` create recoverable intent and reconcile state. | It is not a full ACID transaction manager and cannot make an external API atomic by itself. |
| V0.4 Context VM | Memory management / working set | Accurate for model-visible memory: projection, pages, budgets, reclaim, and compaction. | It is not a virtual memory system: no address translation, page faults, or protection domains. |
| V0.5 Resource Handle | Filesystem / object storage handle | Accurate for exact bytes stored outside prompt context and re-entered through bounded reads. | It is not POSIX: no directories, leases, ownership groups, namespaces, or full permission model yet. |

The stronger framing is: AgentKernel is adopting OS-like invariants where they
solve agent runtime problems, not copying an OS surface.

## Benchmark Review

Source data: `benchmarks/results/all.json` and `docs/benchmark/RESULTS.md`.

### Resource Handle

Observed result:

| Payload | Full context bytes | Pruning context bytes | Artifact context bytes | Artifact resource bytes | Artifact restart |
|---|---:|---:|---:|---:|---|
| 10 MiB | 10,485,868 | 12,430 | 12,806 | 10,485,760 | true |
| 100 MiB | 104,857,708 | 12,431 | 12,809 | 104,857,600 | true |
| 500 MiB | 524,288,108 | 12,431 | 12,809 | 524,288,000 | true |

Fairness:

- The benchmark is fair for the narrow question: does prompt-facing context grow
  with large tool results?
- Full Result keeps exact data in context and therefore grows linearly.
- V0.4 pruning keeps context small but failed the marker-retention requirement
  in all three large-result cases.
- Artifact Handle keeps context stable and preserves exact range access after
  restart.

Answer:

Artifact Handle does solve context growth for large exact tool outputs. It is
also storage offloading. It does not reduce total bytes in the system; it
changes where bytes live and how exact data re-enters the model through bounded
resource reads.

The report should remain explicit that Full Result `restart_recovery=false`
means it lacks the handle/store recovery path measured by this fixture, not that
the full bytes are unrecoverable in every possible framework.

### Durable Tool

Observed result:

| Strategy | execution_count | duplicate_execution | recovery_status | success |
|---|---:|---|---|---|
| ordinary_tool | 2 | true | `retried_with_new_request_id` | false |
| agentkernel_wal | 1 | false | `succeeded` | true |

Fairness:

- The baseline is reasonable as a plain retry baseline.
- It demonstrates the specific failure mode where a crash after external
  success causes the next run to issue a new external request.

Recommended future baselines:

- No-WAL baseline with a stable operation id but no durable state machine.
- Idempotent API baseline where the external service itself deduplicates stable
  request ids.
- WAL without reconcile baseline to isolate how much value comes from durable
  intent versus external reconciliation.

### Recovery

Observed crash points:

| Crash point | Last event | Pending calls | Durable classification | Lost events | Success |
|---|---|---:|---|---:|---|
| after_user_message | `user/message` | 0 |  | 0 | true |
| after_step_start | `step/start` | 0 |  | 0 | true |
| after_tool_call | `tool/call` | 1 |  | 0 | true |
| after_tool_dispatch | `tool/dispatch` | 1 | `reconcile_required` | 0 | true |
| before_commit | `tool/dispatch` | 1 | `reconcile_required` | 0 | true |
| after_result | `tool/result` | 0 | `completed` | 0 | true |

Fairness:

- The six crash points cover the main single-tool lifecycle transitions.
- They validate replay classification rather than broad storage-failure
  resilience.

Recommended future cases:

- Partial final write / truncated JSONL tail.
- Corrupted committed event.
- Replay scalability with long sessions.
- Multiple pending or overlapping durable operations.
- Crash during context compaction lifecycle.

### Context VM

Observed result:

| Strategy | context_tokens | reclaim_tokens | compaction_cost | final_correctness | recovery_ability | success |
|---|---:|---:|---:|---|---|---|
| Full History | 298,891 | 0 | 0 | true | true | true |
| Simple Summary | 618 | 298,273 | 0 | false | false | false |
| Replacement History | 661 | 298,230 | 0 | true | false | true |
| AgentKernel Context VM | 2,091 | 284,791 | 284,925 | true | true | true |

Fairness:

- The benchmark is fair for distinguishing raw history, lossy summary, local
  replacement, and durable page/projection behavior.
- It is not yet a full memory benchmark.
- AgentKernel pays a measured compaction cost in this fixture; the benchmark
  should keep reporting that cost rather than only reporting final context size.

Recommended future baselines:

- Semantic summary with source-aware prompting.
- Retrieval baseline over the 1000-turn fixture.
- Summary with provenance or citation handles.
- Context VM with different page-priority policies.

## V0.6 Readiness

### SHOULD FIX

- Write a structured Capability design before implementation. The exact-string
  capability model is enough for current tool gating, but not enough for
  `Agent Identity + Action + Resource Scope + Constraint`.
- Decide the enforcement loci:
  - `ToolRegistry` for `execute tool.*`.
  - `ResourceService` for `read/write/stat resource://...`.
  - `DurableToolExecutor` for durable side-effect operation authority.
  - Context services for model-visible data admission and future memory reads.
- Define a resource scope grammar before adding more resource classes. Future
  examples such as `read artifact://project/**`, `write database://xxx`, and
  `execute tool.xxx` need deterministic matching and escaping rules.
- Define delegation semantics around the existing `parent_agent_id` and
  `capability_bounding_set`: inheritance, narrowing, revocation, audit event,
  and child-session ownership.
- Decide whether capability denials and grants become session events. If future
  recovery or audit needs them, they should be logged from the start.
- Keep ACB as the identity root, but do not overload raw strings with resource
  scopes and constraints. Add a structured capability record in the design.

### NICE TO HAVE

- Study Local Journal + Global Audit before V0.7/V0.8 multi-agent process work.
  The current session log is stable for one agent/session; multi-agent IPC may
  need per-agent local journals plus an ordered audit stream.
- Add the benchmark baselines listed above before claiming broader runtime
  coverage.
- Add corrupted-event and replay-scale tests before increasing log complexity.
- Clarify whether Context Page is only a Context VM object or a general kernel
  object. Current evidence favors keeping it Context-owned until IPC or Memory
  proves a shared object model is needed.

### NOT REQUIRED

- Do not implement V0.6 Capability as part of this review.
- Do not rewrite `agentkernel/` before V0.6 design.
- Do not replace the session event log before there is a concrete multi-agent
  concurrency requirement.
- Do not change `ResourceHandle`, `ResourceService`, or Durable Tool APIs unless
  the V0.6 design finds an interface that would otherwise force a V0.7 rewrite.
- Do not model full Linux namespaces or POSIX permissions for V0.6.

## Final Recommendation

AgentKernel is ready for V0.6 Capability architecture design, but not for
immediate implementation.

The current V0.1-V0.5 boundary is coherent:

- Event log and recovery form the durable truth.
- Durable Tool handles crash-safe external side effects.
- Context VM manages model-visible working sets.
- Resource Handle prevents large exact tool outputs from becoming prompt state.

The main pre-V0.6 risk is not missing code. It is under-specifying capability as
raw strings after the runtime already has Agent identity, Tool execution,
Resource ownership, Durable operations, Context admission, and future child
agents. The next step should be a structured Capability / Namespace design
document, not core implementation.
