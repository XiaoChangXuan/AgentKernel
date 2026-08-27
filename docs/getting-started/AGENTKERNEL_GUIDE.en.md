# AgentKernel V0.8 Newcomer Guide

## First 30 Seconds

AgentKernel is a runtime kernel for tool-using LLM agents.

Core principle:

```text
Model proposes actions.
Kernel owns invariants.
```

The model may propose tool calls, messages, child agents, resource reads, and IPC payloads. A proposal is not authority, not durable truth, and not proof that an external side effect completed safely. AgentKernel puts trusted runtime mechanisms behind Kernel boundaries: Session, Tool boundary, WAL, Context VM, Resource Handle, Capability, Process, IPC, and Recovery.

```text
User
  |
  v
Agent / Model
  |
  | proposes actions
  v
AgentKernel
  |-- Session durable truth
  |-- Tool boundary
  |-- Durable Tool WAL
  |-- Context projection
  |-- Resource / Artifact Handle
  |-- Capability authority
  |-- Process / Scheduler / Accounting
  |-- Agent Tree / IPC / Resource Share
  `-- Recovery
  |
  v
External World
```

AgentKernel is not a general-purpose agent framework. Prompts, business policy, model choice, UI, plugin systems, and product memory strategies live above the Kernel. The Kernel focuses on mechanisms that must remain true when model output is wrong, incomplete, or hostile.

## Start With A Naive Agent Loop

A minimal agent loop often looks like this:

```python
messages = []
while True:
    response = model(messages)
    if response.tool_call:
        result = execute_tool(response.tool_call)
        messages.append(result)
    else:
        break
```

That loop is easy to understand, but it fails under real runtime pressure:

| Problem | Naive behavior | AgentKernel mechanism |
| --- | --- | --- |
| Process crash | In-memory messages and state disappear | Session Event Log records durable semantic facts |
| Crash after an external side effect | Blind retry may duplicate the effect | Durable Tool WAL binds `operation_id` and requires reconciliation |
| Unlimited message growth | Full history grows linearly | Context VM projects durable truth into a bounded working set |
| Huge tool output | Hundreds of MB enter model context | Resource / Artifact Handle keeps bytes outside context |
| Model asks for a tool | Request is treated as permission | CapabilityEvaluator checks authority at Kernel boundaries |
| Child agent creation | Child implicitly inherits all power | Agent Tree, Capability Delegation, and ResourceShare are explicit |
| IPC carries a URI | URI is mistaken for access | IPC transfers data, not authority |
| Cancellation | Cancellation is treated as rollback or revocation | Process cancellation controls runtime execution, not durable facts |

## V0.1 To V0.8 Evolution

### V0.1 Agent Spine

Problem: an ad-hoc model/tool loop has no structured runtime boundary.

Naive failure mode: model output, tool execution, and history updates live in one business loop, making recovery, authorization, and context management hard to add later.

Kernel mechanism: `Agent` owns an `AgentControlBlock`, `Session` records events, and `DefaultAgentLoop` writes structured turn, step, and tool-boundary events.

Key invariant: the model proposes a tool call; the Kernel resolves, authorizes, executes, and records the boundary.

Runnable evidence:

```bash
python examples/tutorials/v0_1_agent_spine.py
```

The tutorial uses `ScriptedLLM` and deterministic `math.add`. Its output includes `turn/start`, `tool/call`, `tool/result`, and `turn/end`.

Trade-off: callers must create explicit `Agent`, `Session`, and `ToolRegistry` objects instead of writing one bare while loop.

### V0.2 Persistence / Recovery

Problem: runtime memory is not durable semantic truth.

Naive failure mode: after a crash, in-memory messages, steps, and tool results disappear. The host can only guess from an incomplete transcript.

Kernel mechanism: `Session` appends JSON-safe events; `JsonlSessionPersistence` persists them; `Session.load` reloads and runs recovery analysis.

Key invariant: durable facts come from the Session Event Log, not from a live Python object.

Runnable evidence:

```bash
python examples/tutorials/v0_2_recovery.py
```

The tutorial runs once, discards the old runtime object, and reloads from JSONL Session persistence. The output shows `after_restart_status=completed` and `lost_durable_facts=False`.

RuntimeBench mapping: B1 Fault Tolerance covers crash-prefix replay, lost durable facts, duplicate tested effects, and recovery status oracles.

Trade-off: event schemas and persistence drivers must be strict; historical records cannot be casually repaired.

### V0.3 Durable Tool WAL

Problem: an external side effect may happen before the Kernel records local completion.

Naive failure mode: a payment API succeeds, the process crashes before ToolResult, and a restart blindly charges again.

Kernel mechanism: Durable Tool WAL uses PREPARE, DISPATCH, COMMIT, and stable `operation_id`. If a crash happens after dispatch but before commit, recovery requires reconciliation instead of direct retry.

Key invariant: recovery != retry; recovery classification != reconciliation itself.

Runnable evidence:

```bash
python examples/tutorials/v0_3_durable_side_effect.py
```

The tutorial simulates a fake payment:

```text
PREPARE -> DISPATCH -> external success -> crash before local completion
restart -> RECONCILE_REQUIRED -> reconcile -> committed
```

Its output includes `external_effect_count=1`, which means this deterministic fake fixture did not execute the same external effect twice.

What this demonstrates: stable operation identity, crash-after-dispatch classification, reconciliation obligation, and no duplicate fake external effect in the tested service.

What this does not demonstrate: universal exactly-once behavior, distributed transaction atomicity, or arbitrary external-system safety.

RuntimeBench mapping: B2 Side Effect Safety covers duplicate-execution oracle and recovery correctness for the fake service scenario.

Trade-off: hosts must provide reconcile handlers for reconcilable mutations. The Kernel cannot turn every external system into exactly-once infrastructure.

### V0.4 Context VM

Problem: model context is finite while durable Session truth keeps growing.

Naive failure mode: full history grows linearly; summaries may lose key facts; replacement history can confuse context with truth.

Kernel mechanism: Context VM projects events into Context Pages and then into a Working Set. Context is a model-visible projection, not durable truth.

Key invariant: Session durable truth != Model context.

Evidence: RuntimeBench B3 Context Efficiency / Truth Preservation checks bounded context, truth preservation, and deterministic correctness oracles.

Trade-off: projection and compaction add runtime cost, and the host still chooses context policy.

### V0.5 Resource / Artifact Handle

Problem: tool output may be far larger than model context.

Naive failure mode: a 100 MB or 1 GB result is pushed into messages, causing context and memory growth.

Kernel mechanism: the Resource Layer stores large bytes while context carries a handle, preview, or bounded marker.

Key invariant: Resource != Context; ResourceHandle != Permission.

Evidence: Resource RuntimeBench cases show Artifact Handle keeping context size stable while resource bytes remain in ResourceStore.

Trade-off: handle lifetime, read latency, resource cleanup, and host storage policy are explicit integration concerns.

### V0.6 Capability

Problem: a model-generated tool call is not permission to execute.

Naive failure mode: if the model writes `payment.charge`, the tool runner runs it.

Kernel mechanism: `CapabilityGrant`, `AuthorizationRequest`, `AuthorizationDecision`, and `CapabilityEvaluator`. Tool, Resource, and Durable operation boundaries re-check authorization.

Key invariant: Model proposal != Kernel authority.

Evidence: RuntimeBench B4 Capability Isolation covers unauthorized tool, resource read, payment dispatch denial, and legacy tool compatibility.

Trade-off: authority becomes explicit. Hosts must provide grants and policy input. V0.8 is still not RBAC, IAM, or complete namespace security.

### V0.7 Process Runtime

Problem: semantic Agent identity and runtime scheduling identity should not be the same object.

Naive failure mode: cancellation is mistaken for rollback; budget exceeded is mistaken for task failure; runtime state is mistaken for durable truth.

Kernel mechanism: `ProcessControlBlock`, cooperative scheduler, safe points, `UsageCollector`, and runtime budget blocking.

Key invariant: Agent != Process; Accounting != Authority; Budget exceeded != Semantic failure.

Evidence: RuntimeBench B5 Resource Governance and B7 Boundary Isolation cover budget safe-point blocking, unblock recovery, and Agent / Process / Session / Context / ResourceStore boundaries.

Trade-off: hosts must handle process lifecycle explicitly. V0.8 alpha does not include preemptive scheduling.

### V0.8 Multi-Agent Runtime

Problem: multi-agent runtime is not just more loops. Identity, delegation, IPC, resource sharing, process lineage, and recovery can all be confused.

Naive failure mode: a child agent inherits every parent capability; process lineage is treated as authority; a resource URI inside IPC becomes an access grant; recovery restores stale runtime-only authority.

Kernel mechanism: Agent Registry, Agent Tree, Process Tree, Capability Delegation, Kernel IPC, ResourceShare, runtime isolation, and integrated multi-agent recovery.

Key invariants:

- Agent Tree != Process Tree
- ResourceShare != Capability
- IPC data != Authority
- Process lineage != Authority inheritance
- Historical delegation != Current authority
- Persistent semantic facts != Live runtime state

Evidence: RuntimeBench B8 Multi-Agent Runtime covers M1-M10. M10 passes deterministic profiles at 100, 500, and 1000 logical steps.

Trade-off: multi-agent coordination is more explicit. Callers must handle more runtime objects and recovery obligations.

## RuntimeBench Evidence

Frozen artifact:

```text
benchmarks/results/runtimebench_v0.8.json
```

Release evidence:

```text
runtimebench_version = 0.8
runtime_version = AgentKernel V0.8
source commit = 813ca776428987e80bfb9396d4a3beb257ab7ccb
release tag = v0.8.0-alpha
B1-B8 = 8/8 PASS
B8 M1-M10 = 10/10 PASS
M10 horizons = 100 / 500 / 1000 PASS
```

Benchmark oracle summary:

| Benchmark | What PASS means in the tested fixture |
| --- | --- |
| B1 Fault Tolerance | crash prefixes replay without lost durable facts or duplicate tested effects |
| B2 Side Effect Safety | WAL + reconcile avoids duplicate fake external mutation in the tested scenario |
| B3 Context Efficiency / Truth Preservation | model-visible context is bounded while durable facts remain available |
| B4 Capability Isolation | unauthorized Tool, Resource, and Durable operations are denied |
| B5 Resource Governance | scheduler safe points block execution when configured budgets are exceeded |
| B6 Long-Horizon Runtime Stability | V0.1-V0.7 invariants compose over deterministic long-horizon fixtures |
| B7 Boundary Isolation | Agent, Process, Session, Context, Accounting, and ResourceStore boundaries stay distinct |
| B8 Multi-Agent Runtime | identity, delegation, IPC, resource sharing, cancellation, budget, fault, and recovery invariants pass in deterministic multi-agent fixtures |

RuntimeBench is deterministic, offline, local, and synthetic. It measures runtime invariants, not LLM intelligence.

## Design Space Compared With Other Runtimes

Companion document:

```text
docs/research/AGENT_RUNTIME_DESIGN_COMPARISON.md
```

That comparison is based on locally inspected READMEs, docs, tests, or source from reference repositories. It is not a superiority benchmark. AgentKernel does not claim to beat Codex, OpenHands, Gemini CLI, DeepSeek Harness, LangChain, Letta, or any other project.

High-level difference:

| Problem | Common system focus | AgentKernel focus |
| --- | --- | --- |
| Workspace safety | sandbox, approval, filesystem scoping | semantic capability principal and Kernel authorization boundary |
| Conversation resume | transcript, session storage, UI continuity | event-sourced durable semantic truth and recovery classification |
| Tool lifecycle | call / observation abstraction, approval, retry | WAL, operation_id, dispatch / commit / reconcile |
| Context management | compaction, summary, tool output trimming | Context VM projection that is not durable truth |
| Multi-agent orchestration | subagent UI, routing, server/process ownership | Agent Tree != Process Tree, delegation and IPC do not transfer authority implicitly |

## Design Benefits

- Model output is treated as an untrusted proposal.
- Durable truth, model context, and live runtime state are separate.
- External side effects have crash-aware WAL and reconciliation boundaries.
- Large tool results use Resource / Artifact Handle instead of polluting context.
- Authority, delegation, IPC, and resource sharing use explicit Kernel objects.
- Process lifecycle, budget, pause, and cancel use runtime mechanisms instead of prompt conventions.

## Costs And Limits

- There are more explicit runtime objects than in a simple agent loop.
- Host integration must provide policy, capability grants, resource storage, and reconcile handlers.
- Event sourcing, WAL, recovery classification, and multi-agent recovery increase implementation complexity.
- V0.8 alpha does not prove production sandbox security, distributed correctness, universal exactly-once behavior, semantic long-horizon reasoning, or superior model intelligence.
- V0.8 is not V0.9 memory and does not include complete namespace security, RBAC, IAM, or production SLA evidence.

## Recommended Reading Path

1. Read this guide.
2. Run the three tutorials:

```bash
python examples/tutorials/v0_1_agent_spine.py
python examples/tutorials/v0_2_recovery.py
python examples/tutorials/v0_3_durable_side_effect.py
```

3. Read `docs/ARCHITECTURE.md`.
4. Read `docs/evaluation/V0.8_MULTI_AGENT_RUNTIMEBENCH.md`.
5. Read `docs/releases/V0.8_RELEASE_REVIEW.md` and `docs/releases/V0.8_RELEASE_NOTES.md`.
6. For design-space context, read `docs/research/AGENT_RUNTIME_DESIGN_COMPARISON.md`.

## Onboarding Findings

The V0.1 and V0.2 tutorials are expressible through the public AgentKernel API. The V0.3 tutorial manually appends a WAL prefix to demonstrate the exact crash point after dispatch and before commit. That is acceptable for a teaching example at a low-level runtime boundary, but it also suggests a future MiniCode / Runtime API Review could add a smaller durable-side-effect fixture helper. This documentation phase does not modify the Kernel.
