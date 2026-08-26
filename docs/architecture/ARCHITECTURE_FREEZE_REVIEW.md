# AgentKernel Architecture Freeze Review

## 1. Scope

This document freezes the current AgentKernel V0.1-V0.5 architecture before
starting V0.6 Capability / Namespace work.

This is a code-based review, not a roadmap-only document. The review reads the
current implementation under `agentkernel/`, especially:

- `agent.py`
- `loop.py`
- `session.py`
- `events.py`
- `context/`
- `resources/`
- `durable_tools.py`
- `tools.py`
- `persistence.py`
- `recovery.py`

No V0.6 feature is implemented here. The question is whether the current Kernel
boundary is stable enough for V0.6-V0.9, and whether anything must be changed
now to avoid overturning public or kernel-facing interfaces later.

Conclusion: **the current boundary is stable enough to freeze.** No core code
change is required before V0.6 design starts. The future work is mostly medium
refactoring around structured capability, process persistence, delegation,
resource-scoped authorization, and multi-source context projection.

## 2. Current Real Architecture

The requested chain is valid:

```text
LLM
  -> Agent Loop
  -> Kernel Services
  -> Drivers
  -> External World
```

The more precise current implementation is:

```text
LLM / Provider
  -> DefaultAgentLoop
     -> PromptService
     -> ContextService / ContextManager
     -> Session + RecoveryAnalysis
     -> ToolRegistry
     -> DurableToolExecutor
     -> ResourceService
  -> Drivers / adapters
     -> LLMService implementation
     -> SessionPersistence implementation
     -> ResourceStore implementation
     -> Tool handler implementation
  -> External world
```

Code-level module map:

| Module | Current responsibility |
|---|---|
| `agent.py` | Agent identity seed, lifecycle state, parent pointer, exact string capabilities, bounding set, hard turn budgets. |
| `loop.py` | Trusted reference loop for one turn: lifecycle transitions, step/tool budget enforcement, prompt assembly, Context VM preparation, LLM calls, one overflow retry, sequential tool execution, failure closure. |
| `session.py` | Append-only per-session semantic event log; persistence boundary; full-history message projection. |
| `events.py` | Closed durable event vocabulary and immutable event envelope validation. |
| `recovery.py` | Pure replay validator for turn/step nesting, tool-call lifecycle, durable tool WAL, and context compaction lifecycle. |
| `tools.py` | Tool/syscall registry, model schema projection, exact capability authorization, handler invocation, timeout/error normalization. |
| `durable_tools.py` | Durable mutation protocol: operation identity, prepare/dispatch/commit/abort/reconcile WAL, retry/reconcile classification enforcement. |
| `persistence.py` | Replaceable single-session persistence seam plus in-memory and JSONL drivers. |
| `context/` | Context VM: session projection, context pages, policy classification, working-set selection, pressure, pruning, compaction, overflow reclaim. |
| `resources/` | Virtual Resource layer: opaque handles, host metadata, owner/range/size checks, resource store seam, local store, large tool-result externalization, model-facing read/stat tools. |

The important fact is that the LLM never owns the authoritative state. It
receives messages and tool schemas. The Kernel owns event ordering, recovery,
authorization checks, durable operation identity, context working-set validity,
resource handle resolution, and hard budgets.

## 3. Kernel Boundary Matrix

| Component | Kernel/Policy | Reason |
|---|---|---|
| Agent identity | Kernel | `AgentControlBlock` is the principal-like identity used by tools, resources, sessions, and future scheduler decisions. |
| Agent lifecycle | Kernel | State transitions must remain enforceable even if the model asks for invalid work. |
| Parent/child relation | Kernel | `parent_agent_id` already exists as metadata; future delegation and revocation cannot be prompt policy. |
| Effective capabilities | Kernel | Capability enforcement is a security boundary, not a model preference. |
| Capability naming strategy | Policy | Names and grants may be configured by product/runtime policy, as long as Kernel validates them. |
| Capability bounding set | Kernel | The "effective <= bounding" invariant is a kernel-owned least-privilege constraint. |
| Tool registry / syscall table | Kernel | The registry controls which host operations are visible and callable. |
| Tool schemas | Kernel projection | Schemas are model-visible projections of trusted host definitions; handlers and credentials stay hidden. |
| Tool authorization | Kernel | `ToolRegistry.resolve_for_execution()` is the current enforced boundary. |
| Tool handler body | Policy / driver | Business logic executes outside the minimal Kernel; it should receive constrained context. |
| Durable Tool WAL | Kernel | Prepare/dispatch/commit/abort/reconcile ordering is the side-effect recovery contract. |
| Operation identity | Kernel | `operation_id` must be stable, unique, and independent from model-generated `tool_call_id`. |
| Event ordering | Kernel | Contiguous sequence numbers and legal lifecycle transitions are recovery-critical. |
| Session Event Log | Kernel | It is the durable truth for one agent conversation and tool recovery. |
| Session persistence driver | Driver | JSONL/in-memory/SQLite are replaceable storage implementations. |
| Recovery analysis | Kernel | Replay validation determines whether a durable prefix is valid, interrupted, or corrupted. |
| Prompt text | Policy | Product, task, role, and style prompts should stay outside the trusted mechanism. |
| Model choice | Policy / driver | Model selection changes quality/cost, not kernel invariants. |
| LLM provider adapter | Driver | Provider wire protocol and error mapping are replaceable. |
| Context VM mechanism | Kernel service | Budget, mandatory closure, tool protocol validity, and overflow recovery are enforceable runtime invariants. |
| Context selection/reclaim strategy | Policy | Priority, temperature, pruning thresholds, and compaction choices should remain replaceable. |
| Context budget enforcement | Kernel | Requests must not silently exceed hard physical context limits. |
| Context compaction model/prompt | Policy | The summarizer and summary prompt are quality choices; provenance and lifecycle are Kernel. |
| Resource handle validation | Kernel | Handles are authority-bearing references and must not expose store paths. |
| Resource access checks | Kernel | Owner/range/size checks are trusted runtime enforcement. |
| Resource store | Driver | Local filesystem, object store, database, or memory backends should not decide authorization. |
| Resource retention / GC | Policy plus Kernel lifecycle facts | Policy decides when to retain/delete; Kernel must record enough facts before destructive operations exist. |
| Retry policy | Policy plus Kernel guardrails | Policy may choose retries, but Kernel prevents unsafe duplicate side effects. |
| Memory strategy | Policy / future service | Retrieval/ranking/forgetting are product choices; future memory writes and permissions need Kernel seams. |
| Plugin system | Policy / user space | Plugins may provide tools, drivers, prompts, or policies; they must not bypass Kernel enforcement. |
| Future scheduler | Kernel service plus policy | Runnable state, accounting, and isolation are Kernel; priority/fairness choices are policy. |

## 4. Session Event Log Stability

Current state:

- `SessionEvent` is an immutable, JSON-compatible envelope with contiguous
  sequence numbers.
- `Session` is append-only and rejects appends after a truncated tail.
- `analyze_recovery()` reconstructs turn/step state, pending tool calls,
  durable operations, completed operations, and context compaction state.
- `Session.derive_messages()` is a projection, not a second source of truth.

Judgment: **keep the Session Event Log local to an agent/session.**

It should continue to store the durable semantic facts needed to reconstruct one
conversation and its model-visible history. It should not become one global
Kernel event log for Process, Resource, IPC, Memory, Scheduler, and Audit.

Future shape:

```text
Session Journal
  - conversation facts
  - turn/step facts
  - assistant/tool facts
  - durable tool operation facts
  - context compaction facts that affect this session

Process Journal / Process Table
  - runnable state
  - waiting reason
  - exit status
  - scheduler metadata

Resource Journal / Metadata Store
  - create/delete/retain/share/revoke lifecycle
  - owner and grant changes
  - storage metadata

IPC Journal / Mailbox
  - message send/receive/ack facts
  - child result availability

Memory Store / Journal
  - memory write/delete/provenance
  - retrieval metadata

Optional Global Audit Stream
  - append-only correlation view
  - not the canonical state store for every subsystem
```

This is **Local Journal + optional Global Audit**, not "one log to rule the
Kernel". The current `SessionEvent` envelope can remain; the future refactor is
to modularize validators before V0.7/V0.8 so process and IPC events are not
forced into the current monolithic turn/step parser.

No code change is required now.

## 5. Context VM Stability

Current Context VM:

```text
Event
  -> Projection
  -> Context Page
  -> Working Set
  -> ModelRequest
```

`ContextPage` is stronger than a raw message: it carries page identity, kind,
token cost, priority, temperature, pin state, trust label, dependencies,
atomic group, pruning provenance, and summary provenance.

Judgment: **Context Page should remain a Context-owned object for now.**

It should not be promoted to a generic Kernel object yet because it is still
model-request specific:

- most page kinds are tied to provider-neutral messages;
- `to_messages()` is the primary rendering seam;
- `created_seq` and default ordering assume session event sequence;
- summary provenance is context-compaction provenance, not a universal object
  lifecycle record;
- pin/page-in state currently lives inside `ContextManager` instances.

Future Memory, Resource, Rule, Skill, and IPC projections can become Context
Pages, but that requires a later composite projection seam:

- source-qualified page identity;
- stable cross-source ordering;
- source-specific policy metadata;
- explicit renderers from page to model message or prompt fragment;
- ownership of pin/page-in state for concurrent agents.

That is a medium refactor before V0.9 Memory or broad multi-source projection.
It does not require changing Context Page before V0.6.

## 6. Resource Layer Stability

Current Resource Layer:

- `ResourceHandle` is the safe model-facing projection.
- `ResourceMetadata` is host-only durable metadata.
- `ResourceStore` is the replaceable byte-store seam.
- `ResourceService` owns identity generation, validation, owner checks, size
  limits, read ranges, and metrics.
- `ToolResultExternalizer` stores large raw outputs before the result is
  projected into Session and Context.
- `resource_stat` and `resource_read` expose bounded access through tools.

Judgment: **the Resource abstraction is directionally stable.**

It already separates:

```text
raw bytes / metadata truth
  != model-visible preview
  != context page
  != store path
```

It can support future Memory, Artifact, Dataset, File, and Model Output by
expanding kind/scheme semantics and authorization, not by replacing the handle
model.

Current limitations:

- `ResourceKind` only has `ARTIFACT`.
- URI validation only accepts `artifact://res_...`.
- ownership is exact `agent_id + session_id` equality.
- no delegation, transfer, sharing, revocation, delete, retention, tombstone, or
  resource lifecycle journal exists yet.
- `orphaned_resources()` scans model-visible `TOOL_RESULT` references and is not
  sufficient for future automatic cleanup.

Required future change: add a resource-scoped authorization layer at
`ResourceService`, not in drivers. Drivers should continue to implement storage
I/O only.

No code change is required now because V0.5 only exposes retained, owned,
append-only artifacts with bounded reads.

## 7. Agent Identity Stability

Current `AgentControlBlock` contains:

- `agent_id`
- `session_id`
- lifecycle `state`
- `parent_agent_id`
- `capabilities`
- `capability_bounding_set`
- per-turn `budget`

Judgment: **ACB is a good seed, but it is not yet a full process control block.**

It is sufficient for V0.1-V0.6 design because it already centralizes identity,
state, capabilities, bounds, parent identity, and budgets. It will need a medium
refactor before V0.7/V0.8:

- durable ACB or process table persistence;
- process/run generation or epoch for stale handle detection;
- scheduler-owned runnable/waiting state;
- durable pending I/O or wait reason;
- parent/child grant records;
- delegation and revocation metadata;
- resource usage snapshot;
- cancellation/deadline fields;
- mailbox or IPC endpoint identity.

This does not overturn the current API. It extends the ACB from "live runtime
control block" into "durable process metadata".

## 8. Future Version Compatibility

| Version | Rating | Reason |
|---|---|---|
| V0.6 Capability / Namespace | **MEDIUM REFACTOR** | Exact string capability and one `required_capability` per tool are enough for current tool authorization, but future policy needs principal + action + resource scope + constraints + delegation. Existing enforcement points are right, so this is not a rewrite. |
| V0.7 Process / Scheduler | **MEDIUM REFACTOR** | ACB state and budget exist, but loop-local counters, no durable process table, no scheduler queue, and no unified usage snapshot mean scheduler support needs structural work around the loop. |
| V0.8 IPC / Multi Agent | **MEDIUM REFACTOR** | `parent_agent_id`, per-session journals, tool/result boundaries, and resource handles provide seeds. Missing pieces are mailbox, child lifecycle, grant propagation, resource transfer, revocation, and cross-agent audit. |
| V0.9 Memory | **MEDIUM REFACTOR** | Context VM and Resource Layer can host memory projections and blobs, but real memory needs a store/index, write/retrieve capabilities, provenance, retention policy, and multi-source context projection. |

No future item is currently a **MAJOR REWRITE** if the current boundaries are
kept: provider-neutral protocol, append-only session truth, Tool WAL, Context
VM projection, Resource handles, and service/driver separation can remain.

## 9. Architecture Freeze Decision

Freeze decision: **approved for design freeze before V0.6.**

Do not modify core code now. Do not implement Capability / Namespace yet.

The next architecture work should be design-only V0.6:

- principal model;
- structured capability object;
- action/resource/scope/constraint matching;
- delegation and revocation;
- relation to ToolRegistry and ResourceService;
- compatibility path for existing string capabilities.

The current code can support that work without an immediate interface-breaking
change.
