# AgentKernel V0.8 Architecture

This document describes the current implemented architecture at the V0.8 alpha
release candidate. Historical architecture notes remain under
`docs/architecture/`, `docs/implementation/`, and `docs/research/`.

## Boundary

AgentKernel is a trusted mechanism layer for tool-using LLM agents.

```text
LLM / host policy
    |
    v
Agent Loop
    |
    v
Kernel Services
    |
    v
Drivers
    |
    v
External World
```

The LLM is an untrusted proposer. The Kernel owns runtime invariants:
authorization, lifecycle validation, durable truth, recovery classification,
resource access checks, IPC delivery state, and durable side-effect boundaries.

Host policy remains outside the Kernel. Prompt strategy, model choice, business
workflow, human approval, retry policy, restart policy, memory strategy, and
product UI are policy-layer concerns.

## Object Model

```text
Agent      = capability principal and semantic actor
Process    = schedulable runtime identity
Session    = durable single-writer semantic journal
Context    = model-visible projection over durable truth
Resource   = durable bytes/metadata behind a Kernel handle
IPC        = Kernel-owned local communication mechanism
Scheduler  = cooperative Process mechanism
Accounting = runtime observation and budget input
```

Important separations:

- Agent != Process.
- Agent Tree != Process Tree.
- Process lineage does not imply authority inheritance.
- Session is durable truth; Context is a projection.
- ResourceHandle is a reference, not permission.
- ResourceStore is storage, not authorization.
- IPC payload is data, not authority.
- Runtime accounting is not a durable billing ledger.

## Runtime Stack

| Version | Implemented layer |
| --- | --- |
| V0.1 | Agent execution loop and tool boundary. |
| V0.2 | Session persistence, replay, and recovery analysis. |
| V0.3 | Durable Tool WAL and reconciliation classifications. |
| V0.4 | Context VM, Context Pages, working sets, pruning, and compaction. |
| V0.5 | Resource/Artifact Handle layer for large tool results. |
| V0.6 | Structured Capability core and Tool/Resource/Durable enforcement. |
| V0.7 | Process runtime, cooperative Scheduler, and resource accounting. |
| V0.8 | Agent Registry, Process Tree, Capability Delegation, Kernel IPC, Resource Sharing, runtime isolation, integrated multi-agent recovery, and Multi-Agent RuntimeBench. |

## Core Modules

| Module | Responsibility |
| --- | --- |
| `agent.py` | `AgentControlBlock`, Agent identity, Agent Registry, Agent Tree, budgets, and Agent-owned capability principal state. |
| `process.py` | `ProcessControlBlock`, process lifecycle states, capability snapshots for attribution, and process runtime identity. |
| `scheduler.py` | `ProcessManager`, Process Tree metadata, cooperative scheduling queues, safe points, budget blocking, pause/cancel, and fault notification. |
| `session.py` | Append-only semantic log, persistence coordination, and replayed in-process event projection. |
| `events.py` | Closed event vocabulary for Session, Tool WAL, Context, Agent, Process, IPC audit, Resource sharing, and authorization audit facts. |
| `persistence.py` | Versioned Session JSONL/in-memory persistence and corruption handling. |
| `recovery.py` | Replay validation and durable operation recovery classification. |
| `multi_agent_recovery.py` | Integrated multi-agent reconstruction over Agent, Process, ResourceShare, IPC, Session, and WAL facts. |
| `capabilities.py` | `CapabilityGrant`, authorization requests/decisions, evaluator, delegation narrowing, provenance, and scope helpers. |
| `tools.py` | Tool schema projection, legacy and structured authorization, execution resolution, and direct execution compatibility checks. |
| `durable_tools.py` | WAL prepare/dispatch/commit/abort/reconcile, operation identity, authorization metadata, and side-effect recovery boundaries. |
| `resources/` | Resource metadata, handles, local store, owner checks, capability checks, Resource sharing, result externalization, and bounded reads. |
| `ipc.py` | Kernel IPC channels, durable envelopes, at-least-once delivery until ack, per-channel FIFO, backpressure, and reconstruction. |
| `context/` | Context Pages, projection, token estimation, pressure/reclaim policy, deterministic pruning, durable compaction, and working-set selection. |
| `accounting.py` | Runtime usage snapshots, aggregation, budget comparisons, and Host/Agent/Process budget helpers. |
| `loop.py` | Default Agent loop orchestration and optional scheduler/accounting safe-point integration. |
| `llm.py` | Provider-neutral `LLMService` and deterministic `ScriptedLLM`. |
| `providers/openai_compatible.py` | Optional OpenAI-compatible Chat Completions adapter with explicit endpoint configuration only. |

## Durable vs Runtime-Only State

| Durable semantic state | Runtime-only state |
| --- | --- |
| Agent identity/tree facts. | Scheduler READY/RUNNING/WAITING/BLOCKED queues. |
| Session events. | Live fault queues and supervision notifications. |
| Process identity/tree creation facts. | UsageCollector counters. |
| Capability delegation provenance. | Runtime Context working set. |
| IPC persistence state. | Temporary runtime indexes. |
| Resource metadata and ownership. | Python object identity. |
| ResourceShare facts. | Current in-memory queue structures. |
| Durable Tool WAL facts. | Live admission state after restart. |

Recovery uses:

```text
validated durable semantic facts
+ current Host configuration
+ fresh runtime mechanisms
```

It does not serialize or restore arbitrary pre-crash runtime state as current
authority.

## Authorization Model

Agent remains the authorization principal.

Capability evaluation is Kernel mechanism:

```text
AuthorizationRequest(agent_id, action, resource)
    -> CapabilityEvaluator
    -> AuthorizationDecision
```

Structured `CapabilityGrant` values coexist with legacy capability strings for
backward compatibility.

Delegation is explicit and narrowed:

```text
child effective authority
<= delegated authority
<= current parent effective authority
```

Delegation provenance is durable, but historical delegation facts are not the
same as current effective authority. A child Agent starts deny-by-default.

The implementation does not provide RBAC, IAM, namespace security, or complete
revocation semantics.

## Tool and Durable Side Effects

Tool schemas shown to the model are projected from current authorization.
Execution rechecks authorization, so a fabricated hidden tool call is denied.

Durable mutations follow:

```text
Authorization
  -> WAL prepare
  -> dispatch authorization
  -> external effect
  -> commit / abort / reconcile
```

Recovery classifications remain explicit:

- `SAFE_TO_RETRY`
- `IDEMPOTENT_RETRY_ALLOWED`
- `RECONCILE_REQUIRED`
- `COMPLETED`
- `MANUAL_REQUIRED`

Recovery is not blind retry. Already-dispatched durable obligations can survive
process cancellation, process failure, restart, or later authority shrink for
new work.

## Resource Model

`ResourceStore` stores bytes and metadata. It is not an authorization authority.

`ResourceService` authorizes:

- owner access;
- capability checks;
- cross-Agent ResourceShare checks;
- range validation;
- store reads.

Cross-Agent access requires:

```text
current Capability authorization
AND
active ResourceShare
```

A handle, URI, IPC `resource_refs` field, or grant-like JSON payload does not
grant access by itself.

## IPC Model

`KernelIPC` owns local point-to-point channels and durable message envelopes.

Message states:

```text
PENDING -> DELIVERED -> ACKED
```

V0.8 implements at-least-once observable delivery until ack. A delivered but
unacked message may redeliver after restart. Per-channel FIFO is implemented
for the tested point-to-point channel; no global total order is claimed.

IPC transfers structured data only. It does not grant capabilities, Resource
shares, tool permission, namespace access, or ownership.

## Scheduler, Budget, Fault, and Cancellation

The Scheduler schedules Processes, not Agents. Safe points are cooperative and
exist around turn, step, LLM, Tool, and durable-dispatch boundaries.

Budget hierarchy:

```text
Host
  -> Agent
      -> Process
```

Budget exhaustion blocks a process at a safe point. It is a runtime condition,
not a semantic task failure.

Child process fault does not automatically become parent process fault.
Cancellation is not rollback and is not revocation. Durable WAL facts and IPC
persistence survive cancellation.

## Context VM

The Session event log is durable truth. The Context VM answers what the next
model request should see.

```text
Session events
  -> Context projection
  -> Context Pages
  -> policy and working set
  -> ModelRequest
```

Eviction, pruning, and compaction alter model-visible projection only. They do
not delete raw Session facts.

## RuntimeBench Evidence

V0.8 evidence is frozen in:

```text
benchmarks/results/runtimebench_v0.8.json
```

Summary:

```text
B1-B8 = 8/8 PASS
B8 M1-M10 = 10/10 PASS
M10 horizons 100, 500, 1000 = PASS
```

RuntimeBench is deterministic, offline, local, and synthetic. It measures
runtime invariants, not model intelligence or production readiness.

## Public API Surface

The package exports current runtime primitives from `agentkernel/__init__.py`,
including Agent Registry, Process Manager/Scheduler, Capability and Delegation
objects, Kernel IPC, Resource Sharing, Durable Tool, Context VM, and integrated
multi-agent recovery entrypoints.

The V0.8 release audit found no duplicated `__all__` entries and no obvious
test-only internals exported as public release surface.

## Deliberately Deferred

V0.8 does not implement:

- V0.9 Persistent Memory.
- Namespace security.
- Complete revocation semantics.
- RBAC or IAM.
- Production sandbox security.
- Distributed runtime or consensus.
- Preemptive scheduling.
- Production SLA.
- Universal exactly-once side effects.
- Arbitrary external system atomicity.
- Claims of superiority over other agent frameworks or products.
