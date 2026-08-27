# AgentKernel Evaluation Strategy

Status: strategy review updated after V0.8 Multi-Agent RuntimeBench evidence.

Decision:

```text
V0.8_MULTI_AGENT_RUNTIMEBENCH_EVIDENCE_COMPLETE
```

Scope:

- Define AgentKernel's research positioning after V0.1 through V0.7.
- Separate implemented claims from future goals.
- Define one versioned RuntimeBench strategy for V0.7, V0.8, V0.9, and V1.0.
- Review existing benchmarks as KEEP / EXTEND / REWRITE / DEPRECATE.
- Do not implement V0.8, Process Tree, IPC, Multi-Agent, or Memory.

## 1. Executive Summary

AgentKernel is not primarily an Agent Framework. It is a small trusted runtime
kernel for executing non-deterministic, failure-prone, tool-using LLM agents
while preserving system-level invariants.

The evaluation strategy should therefore not focus on model intelligence,
prompt quality, product breadth, integration catalogs, or whether AgentKernel
"beats" Codex, Gemini CLI, OpenHands, LangChain, Letta, Hermes, or DeepSeek
Harness. Those systems have different design centers and many mature mechanisms
AgentKernel should learn from.

The central evaluation question is narrower:

```text
Without improving the model itself, can a small trusted runtime kernel improve
long-horizon agent reliability, recoverability, side-effect correctness,
context efficiency, resource governance, authority isolation, and runtime
stability?
```

The current V0.1-V0.7 implementation gives real evidence for several runtime
claims:

- durable Session truth is separated from model-visible Context projection;
- durable side effects can be WAL/reconcile controlled;
- large exact resources can stay outside prompt context behind handles;
- authority checks can be enforced outside the LLM at Tool, Resource, and
  Durable Tool boundaries;
- single-agent execution can be represented as a schedulable runtime Process
  with cooperative safe points and observation-based budget blocking;
- implemented V0.1-V0.7 runtime mechanisms preserve tested invariants across
  deterministic single-agent long-horizon fixtures up to 1000 logical steps;
- implemented V0.8 multi-agent runtime primitives preserve tested identity,
  delegation, IPC, resource-sharing, budget, fault-isolation, and recovery
  invariants across deterministic offline fixtures, including 100, 500, and
  1000 step multi-agent profiles.

The current implementation does not yet justify claims about complete
revocation, namespace security, persistent memory correctness, production
sandboxing, universal exactly-once side effects, distributed multi-agent
runtime correctness, or superior task intelligence.

The next evaluation step should be RuntimeBench: a versioned benchmark suite
organized around runtime properties, not ad hoc per-version tests.

## 2. What AgentKernel Is

AgentKernel is a trusted mechanism layer below agent policy and above drivers.
The current object split is:

```text
Agent   = capability principal
Process = runtime identity and schedulable execution state
Session = durable journal and replay source of truth
```

Kernel-owned invariants:

1. LLM is never Kernel.
2. All external side effects cross a trusted Tool/syscall boundary.
3. Session Event Log is the durable source of truth.
4. Model-visible context is only a projection of durable truth.
5. Mechanism and Policy stay separate.
6. Resource is not the same object as Handle or Preview.
7. Agent identity is not Process identity.
8. Agent remains the capability principal.
9. Scheduler owns runtime mechanism, not policy.
10. Durable side effects remain WAL/reconciliation controlled.
11. Runtime accounting is observation, not a durable ledger.

AgentKernel's design target is the small set of mechanisms that must continue
to hold when model output is wrong, incomplete, malicious, or interrupted by a
crash.

## 3. What AgentKernel Is Not

AgentKernel is not:

- a general agent application framework;
- a workflow graph framework;
- a coding-agent product UI;
- a sandbox product;
- a plugin marketplace;
- a complete memory product;
- a broad model/provider integration catalog;
- an automatic human approval system;
- a universal exactly-once transaction coordinator;
- evidence that any model is smarter or solves tasks better.

These are valid product or framework concerns. AgentKernel may expose primitives
that such systems can use, but the evaluation must not claim those product
surfaces as current Kernel achievements.

## 4. Comparison With Existing Systems

This review uses local source snapshots under
`D:/Users/changxuan.xiao/Desktop/Github` plus existing AgentKernel comparison
documents. It is not a ranking. The goal is to identify design centers and avoid
false novelty claims.

| System | Primary design center | Runtime mechanisms visible from local review | AgentKernel interpretation |
| --- | --- | --- | --- |
| Codex | Product-grade coding-agent runtime and user experience. | Sandbox/approval profiles, task/thread runtime, context management, rollout/task continuation, multi-agent/task coordination surfaces. | Important reference and baseline. AgentKernel should not claim sandboxing, approvals, or context management as unique ideas. AgentKernel's narrower claim is explicit kernel invariants and embeddable mechanism boundaries. |
| DeepSeek Harness | Plugin-composed harness/runtime with services, surfaces, compaction, session, sandbox, and subagent seams. | Append-only session truth, derived model-visible surface, compaction/session/capability seams, subagent seam. | Strong architectural reference. AgentKernel should not claim session/surface separation as unique. Its distinction is the smaller trusted core and explicit WAL/resource/capability/process object model. |
| Gemini CLI | Terminal-first coding agent. | Context handling, checkpointing, sandbox/policy engine, tool output management, subagents, memory extraction. | Useful product and CLI baseline. AgentKernel should not claim checkpointing, sandboxing, policy hooks, or subagents as unique. Use it as a baseline for context and tool-output behavior where fair. |
| OpenHands | Full agent application/workspace stack. | Workspace isolation, backend orchestration, frontend/server product surface, automation and agent runtime integration. | Relevant for workspace isolation and product/runtime separation. AgentKernel should stay below application orchestration and define the trusted kernel boundary a host could use. |
| LangChain/LangGraph | Broad LLM application framework and stateful orchestration runtime. | Agent construction, tools, middleware, checkpointers, stores, summarization middleware, tool call limit middleware, graph-based execution. | Strong framework baseline. AgentKernel should not compete on integration breadth. Its claims should focus on trusted enforcement and crash semantics that cannot be optional middleware. |
| Letta | Memory-first stateful agents. | The local `letta-main` snapshot is a landing page that points to current Letta Code; README-level evidence confirms memory, identity, conversations, desktop/web/channel surfaces. | Relevant to V0.9 Memory only. The local snapshot does not support detailed runtime claims, so comparisons must stay high-level unless the current source is added locally. |
| Hermes Agent | Product runtime for a self-improving, multi-surface agent. | SQLite session state, FTS search, cross-platform gateway, persistent memory/skills, approvals, terminal backends, delegation/subagent rows, budget/run controls, recovery utilities. | Important product/runtime reference. AgentKernel should not claim memory, skills, delegation, or cross-platform runtime as unique. Its narrower value is the explicitly factored kernel object model and benchmarkable invariants. |

Comparison conclusion:

AgentKernel should be evaluated as a trusted runtime substrate, not as a full
agent host. Many neighboring projects solve more user-facing or broader
framework problems. AgentKernel's research claim is whether a small, explicit
kernel boundary can make core runtime properties testable and composable.

## 5. Core Research Claims

Candidate claims after V0.7:

| Claim | Status | Current public claim allowed |
| --- | --- | --- |
| A. Durable Truth != Model Context | IMPLEMENTED | AgentKernel separates durable Session truth from model-visible Context projection and can reclaim prompt context without rewriting history. |
| B. External Side Effects are Kernel-managed | IMPLEMENTED_WITH_SCOPE | AgentKernel can WAL/reconcile durable tools with stable operation identity. It cannot guarantee universal exactly-once behavior without external idempotency or reconciliation. |
| C. Large Resources do not need to live in prompt/context | IMPLEMENTED_WITH_SCOPE | AgentKernel can externalize large local artifact bytes behind opaque handles and bounded reads. It is not a full VFS, search layer, or GC system. |
| D. Authority does not belong to the LLM | IMPLEMENTED_WITH_SCOPE | AgentKernel enforces current Tool, Resource, and Durable Tool authorization outside model output. It does not yet implement delegation, revocation, namespace, RBAC, or IAM. |
| E. Agent Execution is governable as a runtime process | PARTIALLY_IMPLEMENTED | AgentKernel has single-agent cooperative Process/Scheduler/Accounting primitives. It does not yet have Process Tree, IPC, preemption, or multi-agent isolation. |
| F. Kernel mechanisms compose without destroying previous invariants | IMPLEMENTED_WITH_SCOPE | AgentKernel V0.1-V0.7 mechanisms preserved the tested runtime invariants across deterministic single-agent long-horizon fixtures up to 1000 logical steps. |
| G. Multi-Agent primitives preserve authority and recovery boundaries | IMPLEMENTED_WITH_SCOPE | AgentKernel V0.8 RuntimeBench B8 validates M1-M10 deterministic multi-agent identity, delegation, IPC, resource sharing, budget, fault, and recovery invariants, including 100/500/1000 step M10 profiles. |

These claims should be stated as runtime mechanism claims, not product-quality
or model-quality claims.

## 6. Claim / Mechanism / Evidence Matrix

| Claim | Mechanisms | Current evidence | Status | Not allowed yet |
| --- | --- | --- | --- | --- |
| Durable Truth != Model Context | `Session`, append-only events, Context VM projection, Page/Working Set, pruning, compaction provenance. | `benchmarks/results/context_vm.json`: Full History used 298,891 tokens; AgentKernel Context VM used 2,091 tokens with final correctness and recovery ability in the 1000-turn fixture. | IMPLEMENTED | Do not claim perfect memory, lossless summarization, or general task-success improvement. |
| External Side Effects are Kernel-managed | `DurableToolExecutor`, `operation_id`, prepare, dispatch, commit/abort, reconcile, WAL recovery analysis. | `benchmarks/results/durable_tool.json`: ordinary retry executed fake payment twice; AgentKernel WAL executed once and recovered successfully. | IMPLEMENTED_WITH_SCOPE | Do not claim universal exactly-once, distributed transactions, or safety for opaque external APIs. |
| Large Resources do not need to live in prompt/context | `ResourceStore`, `ResourceService`, `ResourceHandle`, `artifact://`, bounded `resource_read`. | Resource benchmark: full context grew from 10.5 MB to 524.3 MB; Artifact Handle context stayed about 12.8 KB while preserving 10 MiB, 100 MiB, and 500 MiB resources and restart reads. | IMPLEMENTED_WITH_SCOPE | Do not claim general filesystem, semantic retrieval, garbage collection, remote object storage, or RAG. |
| Authority does not belong to the LLM | `CapabilityGrant`, `CapabilityEvaluator`, `ToolRegistry`, `ResourceService`, Durable Tool authorization audit. | `benchmarks/results/capability_runtime.json`: unauthorized tool/resource/payment dispatch denied; hidden tool was not model-visible; legacy `required_capability` still works. | IMPLEMENTED_WITH_SCOPE | Do not claim delegation, revocation, namespace normalization, RBAC, IAM, or complete sandboxing. |
| Agent Execution is governable as a runtime process | `ProcessControlBlock`, `ProcessManager`, `CooperativeScheduler`, safe points, `UsageCollector`, budget checks. | `benchmarks/results/v0.7_runtime.json`: lifecycle, WAITING/BLOCKED/READY, budget blocking, usage accounting, process recovery, and Agent/Process/Session boundary cases passed. | PARTIALLY_IMPLEMENTED | Do not claim preemption, multi-agent scheduling, process tree semantics, IPC, durable accounting ledger, or production fairness. |
| Kernel mechanisms compose | Scheduler + WAL, Accounting + Capability, Context VM + Resource, Recovery + Process mapping. | `benchmarks/results/runtimebench_v0.7.json`: B6 passes 100, 500, and 1000 step deterministic profiles with 9684 recovered events, 0 duplicate external effects, 0 unauthorized effects, and 3 budget block/recovery cycles. | IMPLEMENTED_WITH_SCOPE | Do not claim production reliability, semantic long-horizon reasoning, multi-agent stability, or scheduler scalability. |
| Multi-Agent runtime invariants | AgentRegistry, ProcessManager, CooperativeScheduler, delegation narrowing, KernelIPC, ResourceShareRegistry, ResourceService authorization, multi-agent recovery, WAL recovery classification. | `benchmarks/results/runtimebench_v0.8.json`: B8 passes M1-M10, with M10 passing 100, 500, and 1000 step deterministic profiles and zero unauthorized effects, duplicate effects, cross-agent leaks, authority escalations, lost durable facts, recovery corruptions, or unresolved mandatory WAL obligations. | IMPLEMENTED_WITH_SCOPE | Do not claim production sandboxing, distributed runtime correctness, complete revocation, namespace security, RBAC, IAM, memory correctness, or model intelligence improvement. |

## 7. RuntimeBench Design

RuntimeBench should be organized by runtime property. Pytest cases and queue
throughput tests remain useful correctness and microbenchmark tools, but they
are not the core research benchmark.

### B1 Fault Tolerance

Purpose: measure whether durable truth and recovery analysis preserve facts
through interrupted execution.

Crash points:

- after user event;
- after model response;
- after tool prepare;
- after dispatch;
- before commit;
- during context compaction;
- during resource externalization;
- during scheduler WAITING/BLOCKED transition.

Metrics:

- recovery success rate;
- lost durable facts;
- duplicate side effects;
- recovery latency;
- pending operation classification accuracy;
- manual intervention rate.

### B2 Side Effect Safety

Purpose: measure whether external mutations are retried, reconciled, or blocked
according to durable WAL facts.

Scenarios:

- payment;
- shipment;
- email;
- database mutation.

Important case:

```text
external effect succeeded
Kernel crashed before commit
restart
```

Metrics:

- `duplicate_effect_count`;
- `incorrect_success_count`;
- `reconciliation_success_rate`;
- `manual_required_rate`;
- operation identity stability;
- recovery latency.

### B3 Context Efficiency + Truth Preservation

Purpose: measure prompt pressure handling without conflating short prompts with
truth preservation.

Workloads:

- 10 MB, 100 MB, 500 MB, and later 1 GB tool results;
- exact markers at head, middle, and tail;
- 100, 500, and 1000 step synthetic conversations;
- durable compaction interruption and replay.

Baselines:

- Full History;
- truncation;
- simple summary;
- replacement history;
- semantic summary baseline;
- retrieval baseline;
- AgentKernel Context VM;
- AgentKernel Resource Handle.

Metrics:

- prompt tokens;
- context bytes;
- exact recall;
- final correctness;
- recovery ability;
- durable bytes preserved;
- retrieval/read latency;
- context overflow rate;
- compaction cost.

### B4 Capability / Security Isolation

Purpose: measure whether authority stays outside the LLM.

Cases:

- prompt injection requests hidden tool;
- tool-output injection requests hidden tool;
- unauthorized resource read;
- unauthorized tool execution;
- unauthorized durable mutation;
- privilege escalation attempt.

Future V0.8 cases:

- delegation amplification;
- confused deputy;
- malicious child process or subagent;
- cross-agent resource access.

Metrics:

- unauthorized tool execution count;
- unauthorized resource access count;
- unauthorized durable dispatch count;
- privilege amplification count;
- false deny rate;
- audit metadata completeness.

### B5 Resource Governance

Purpose: measure budget enforcement as runtime mechanism.

Resources:

- tokens;
- model cost;
- tool calls;
- resource reads;
- resource bytes;
- wall time.

Metrics:

- budget enforcement correctness;
- budget overshoot;
- blocked process correctness;
- unblock/recovery correctness;
- isolation between Agent, Process, Session, Resource, and Accounting state.

### B6 Long-Horizon Runtime Stability

Purpose: combine mechanisms under pressure instead of validating each one in
isolation.

Workload:

- 100, 500, and 1000 steps;
- mixed tool calls;
- large resources;
- context pressure;
- random crashes;
- durable mutations;
- budget pressure;
- recovery and continuation.

Metrics:

- state integrity;
- task completion;
- exact fact recall;
- duplicate effects;
- prompt growth;
- resource growth;
- recovery count;
- runtime overhead;
- final durable consistency.

### B7 Boundary Isolation

Purpose: verify kernel object ownership, not traditional speed.

Invariants:

- Agent is principal.
- Process is runtime.
- Session is durable truth.
- Scheduler does not own capability.
- Accounting does not mutate Session truth.
- ResourceStore does not authorize.
- Context Page does not carry authority.
- LLM cannot create grants or operation identity.

Metrics:

- invariant pass/fail;
- unauthorized ownership mutation attempts;
- boundary regression count.

### B8 Multi-Agent Runtime

Purpose: verify that V0.8 multi-agent runtime primitives preserve Kernel
object boundaries under local deterministic pressure.

Cases:

- Agent / Process identity isolation;
- Agent tree / Process tree separation;
- Capability delegation narrowing;
- IPC delivery / authority isolation;
- Resource sharing isolation;
- hierarchical budget isolation;
- fault / cancellation isolation;
- integrated multi-agent recovery;
- authority shrink after restart;
- long-horizon multi-agent composition at 100, 500, and 1000 logical steps.

Metrics:

- scenario pass count;
- horizon pass count;
- unauthorized effects;
- unsafe duplicate effects;
- cross-agent resource leaks;
- authority escalations;
- lost durable facts;
- recovery corruptions;
- unresolved mandatory WAL obligations.

### B8b Scheduler Scalability

Purpose: micro/stress benchmark for scheduler data structures.

Scale:

- 1, 10, 100, 1000 processes.

Metrics:

- dispatch throughput;
- queue latency;
- P95 scheduling latency;
- memory overhead;
- starvation indicators.

This is a useful engineering benchmark, but not a central AgentKernel research
claim by itself.

### Existing Benchmark Review

| Existing benchmark | Decision | Reason | Next action |
| --- | --- | --- | --- |
| Resource Handle benchmark | KEEP + EXTEND | It already demonstrates context growth versus handle stability with real bytes and restart reads. | Add head/middle/tail marker placement, semantic retrieval baseline, 1 GB optional stress, and overhead reporting. |
| Durable Tool benchmark | KEEP + EXTEND | It demonstrates duplicate baseline retry versus WAL/reconcile behavior. | Add no-WAL baseline, idempotent API baseline, opaque mutation case, shipment/email/database mutation variants. |
| Recovery benchmark | KEEP + EXTEND | Six crash points validate replay prefixes and pending operation classification. | Add partial write, corrupted event, compaction crash, resource externalization crash, replay scalability. |
| Context VM benchmark | KEEP + EXTEND/REWRITE | Current fixture demonstrates tokens/correctness/recovery, but the future benchmark should separate context efficiency from truth preservation more explicitly. | Fold into B3 and B6 with truncation, summary, semantic summary, retrieval, replacement, Context VM, and Resource Handle variants. |
| Capability benchmark | KEEP + EXTEND | Current runtime denial cases validate Kernel authority at Tool, Resource, and Durable boundaries. | Add prompt/tool-output injection fixtures and false-deny measurement. Defer delegation/confused-deputy cases to V0.8. |
| V0.7 scheduler/accounting benchmark | KEEP | It validates lifecycle, registries, budget blocking, usage snapshots, recovery mapping, and boundary isolation. | Treat scheduler scalability as micro/stress only. Extend B5/B7/B6 for runtime governance. |
| Queue throughput / 1000 process scheduling | MICROBENCH_ONLY | It can show scheduler data structure cost, but not AgentKernel's core runtime value. | Keep as a future scheduler microbenchmark, not as headline research evidence. |

## 8. Metrics

RuntimeBench should report metrics in groups:

| Metric group | Examples |
| --- | --- |
| Reliability | recovery success rate, lost durable facts, corrupted replay count, interrupted operation classification accuracy. |
| Side-effect correctness | duplicate effect count, incorrect success count, reconcile success rate, manual-required rate. |
| Context efficiency | prompt tokens, context bytes, overflow count, compaction cost, reclaim tokens. |
| Truth preservation | exact recall, durable bytes preserved, final state correctness, source provenance availability. |
| Authority isolation | unauthorized execution/access/dispatch count, privilege amplification count, false deny rate. |
| Resource governance | budget overshoot, blocked process correctness, usage snapshot accuracy, unblock correctness. |
| Stability | completion rate, recovery count, final durable consistency, long-horizon state integrity. |
| Overhead | wall-clock latency, CPU time where practical, memory, disk usage, token overhead, storage overhead. |

Every headline reliability or security improvement should be paired with an
overhead measurement.

## 9. Baselines

Use a layered baseline strategy. Do not force every framework into every
benchmark.

Minimum generic baselines:

- B0: Naive ReAct/simple agent loop.
- B1: Naive loop + truncation.
- B2: Naive loop + simple summary.

Dimension-specific baselines:

| Dimension | Candidate baseline |
| --- | --- |
| Stateful durability / workflow recovery | LangGraph where the task maps naturally to graph persistence. |
| Multi-agent runtime | AutoGen in V0.8, once AgentKernel has Multi-Agent/IPC primitives to compare. |
| Memory correctness | Letta in V0.9, once AgentKernel has Memory primitives. |
| Coding-agent sandbox/runtime behavior | Codex, Gemini CLI, OpenHands, and Hermes only on tasks where their product surfaces can be fairly driven. |
| Harness composability | DeepSeek Harness for session/surface/compaction/capability seam comparisons. |

Baseline rule:

```text
Compare along the property under test, not across entire products.
```

For example, a WAL side-effect benchmark does not need a full coding-agent
product baseline unless that product exposes a comparable durable mutation
protocol.

## 10. Controlled Variables

External runtime comparisons should control as much as possible:

- same model;
- same prompt;
- same tools;
- same input data;
- same task;
- same temperature and deterministic provider settings;
- same fake external services;
- same crash injection schedule;
- same resource payloads;
- same success oracle.

The main experimental variable should be:

```text
Runtime
```

If a framework requires a different prompt, tool shape, checkpoint mechanism,
or memory interface, the report must state that limitation rather than hiding
it behind aggregate scores.

Networked API tests should be optional. The core RuntimeBench should remain
deterministic and offline.

## 11. Ablations

Ablations should measure what each Kernel mechanism contributes without
assuming results in advance.

| Variant | Hypothesis | Measurement | Expected failure mode to check |
| --- | --- | --- | --- |
| AgentKernel Full | Combined mechanisms preserve runtime invariants under pressure. | All B1-B7 property metrics plus overhead. | Any invariant regression, excessive overhead, or failure to compose mechanisms. |
| minus WAL | Removing durable mutation protocol should affect side-effect safety after crash. | Duplicate effect count, reconcile success, manual intervention. | Blind retry or lost ambiguity after dispatch. |
| minus Context VM | Removing budgeted projection should affect prompt growth and overflow. | Prompt tokens, overflow count, final correctness, exact recall. | Full-history growth or lossy truncation. |
| minus Resource Handle | Removing artifact handles should affect large exact-output handling. | Context bytes, durable bytes preserved, read latency, restart access. | Large payload enters context or exact bytes become unavailable. |
| minus Capability | Removing Kernel authorization should affect unauthorized action prevention. | Unauthorized tool/resource/dispatch count, false deny rate. | LLM-generated or injected calls reach handlers. |
| minus Scheduler Budget | Removing budget safe points should affect resource governance. | Budget overshoot, blocked correctness, wall time, model cost. | Process continues after quota breach. |

Ablation reports should distinguish "mechanism absent" from "policy configured
differently."

## 12. Runtime Overhead

RuntimeBench must answer what AgentKernel costs in exchange for reliability and
isolation.

Measure where practical:

- wall-clock latency;
- CPU time;
- peak memory;
- disk bytes;
- Session log bytes;
- ResourceStore bytes;
- WAL record count;
- authorization decision count and latency;
- scheduler safe-point count and latency;
- token overhead for handles, summaries, and metadata;
- compaction model-call cost.

Overhead should be reported per workload and per mechanism. A slower result can
still be acceptable if it prevents duplicate side effects or preserves durable
truth, but the tradeoff must be explicit.

## 13. V0.7 Evaluation Plan

V0.7 should prove single-agent runtime invariants.

In scope:

- Session recovery;
- Durable Tool WAL;
- Context VM;
- Resource Handle;
- Capability enforcement;
- Process lifecycle;
- Cooperative Scheduler;
- Usage Accounting;
- budget blocking;
- Agent/Process/Session boundary isolation.

Recommended V0.7 RuntimeBench subset:

| Family | V0.7 target |
| --- | --- |
| B1 Fault Tolerance | Add compaction/resource crash points and replay scalability. |
| B2 Side Effect Safety | Add idempotent/no-WAL/opaque baselines. |
| B3 Context Efficiency + Truth Preservation | Rewrite as unified context/resource/recall suite. |
| B4 Capability / Security Isolation | Add prompt/tool-output injection and false deny rate. |
| B5 Resource Governance | Extend V0.7 budget blocking to mixed token/tool/resource budgets. |
| B6 Long-Horizon Runtime Stability | Build the first 100/500/1000 step combined workload. |
| B7 Boundary Isolation | Convert current invariant tests into stable benchmark records. |
| B8 Scheduler Scalability | Keep as micro/stress only. |

Success criterion:

```text
V0.7 demonstrates single-agent runtime invariants with measured overhead.
```

## 14. V0.8 Evaluation Extension

V0.8 should extend RuntimeBench to Multi-Agent Isolation only after AgentKernel
implements the required runtime primitives.

Expected mechanisms:

- process tree or spawn model;
- IPC;
- delegation;
- capability inheritance/restriction;
- fault isolation;
- budget isolation;
- resource sharing rules;
- namespace or logical isolation view.

Implemented RuntimeBench B8 cases:

- parent creates child with reduced privilege;
- child cannot amplify action/scope/constraint;
- child crash does not corrupt parent Session truth;
- IPC message cannot carry unauthorized authority;
- budget overrun in one child does not consume sibling budget;
- shared resource read/write rules remain enforced.

Current evidence:

- `benchmarks/results/runtimebench_v0.8.json`
- B8 passes M1-M10.
- M10 passes 100, 500, and 1000 logical step profiles.
- Required invariant counters remain zero.

V0.8 should compare against multi-agent baselines such as AutoGen only where the
same task, model, tool, and failure scenario can be made fair.

## 15. V0.9 Evaluation Extension

V0.9 should extend RuntimeBench to Persistent Memory Correctness.

Expected mechanisms:

- memory identity and ownership;
- memory read/write capabilities;
- provenance from Session/Resource/Tool facts;
- staleness handling;
- poisoning defense;
- cross-session recall;
- forgetting/deletion semantics;
- memory projection into Context VM.

New benchmark cases:

- memory write after successful durable fact;
- memory write rejected without capability;
- stale memory detected or scoped;
- poisoned tool output cannot become trusted memory without policy;
- cross-session recall includes provenance;
- forgotten memory no longer appears in Context projection;
- shared memory access remains scoped.

Letta should be considered a relevant memory-first baseline in V0.9, but only
with current source or documented behavior available for a fair comparison.

## 16. Threats to Validity

Known threats:

- Synthetic fixtures may not represent real coding-agent workloads.
- Offline fake services model failure modes but not full production APIs.
- Marker-based exact recall is narrower than human answer quality.
- Latency depends on local hardware and should not be generalized without
  repeated runs.
- Current benchmarks do not measure model intelligence.
- Current benchmarks do not evaluate real sandbox escape resistance.
- Local reference snapshots may be incomplete or older than upstream projects.
- Some baselines require different abstractions, making strict
  apples-to-apples comparison difficult.
- AgentKernel's current tests are mostly single-process and single-agent.
- Runtime accounting is observation, not a durable billing ledger.

Mitigation:

- Keep raw result JSON committed.
- Record environment metadata.
- Report limitations per benchmark.
- Use ablations in addition to external baselines.
- Avoid ranking products when design centers differ.
- Extend beyond deterministic single-agent long-horizon workloads before making
  production, semantic, multi-agent, or scalability claims.

## 17. Claims We Must Not Make Yet

Do not claim:

- AgentKernel is better than Codex, Gemini CLI, OpenHands, Letta, Hermes,
  DeepSeek Harness, LangChain, or LangGraph.
- AgentKernel has unique context management, sandboxing, checkpointing,
  approvals, memory, subagents, or workflow orchestration.
- AgentKernel improves model intelligence.
- AgentKernel provides universal exactly-once side effects.
- AgentKernel provides production sandbox security.
- AgentKernel has complete namespaces, delegation, revocation, RBAC, IAM, or a
  full policy engine.
- AgentKernel has complete multi-agent isolation beyond the V0.8 deterministic
  local RuntimeBench invariants.
- AgentKernel has persistent memory correctness.
- AgentKernel's Context VM is infinite context or lossless memory.
- AgentKernel's Resource Layer is a full VFS, RAG system, search engine, or GC
  system.
- AgentKernel's scheduler is preemptive, fair under production workloads, or
  multi-agent ready.
- Runtime accounting is a durable billing ledger.

Allowed current framing:

```text
AgentKernel currently demonstrates a small trusted runtime kernel whose
single-agent and scoped multi-agent mechanisms can preserve selected runtime
invariants under deterministic offline crash, context, resource, capability,
scheduling, IPC, delegation, and recovery fixtures.
```

## 18. Final Evaluation Strategy Decision

Decision:

```text
V0.8_MULTI_AGENT_RUNTIMEBENCH_EVIDENCE_COMPLETE
```

Rationale:

- The core AgentKernel positioning is coherent: small trusted runtime kernel,
  not general Agent Framework.
- V0.1-V0.8 already provide implemented mechanisms and real offline evidence
  for several runtime claims.
- Current evidence is strong enough to support scoped V0.8 single-agent and
  deterministic local multi-agent RuntimeBench claims, but not production,
  semantic long-horizon reasoning, distributed runtime, memory, or cross-product
  superiority claims.
- RuntimeBench should be versioned by runtime property families B1 through B8,
  with V0.7 proving single-agent invariants, V0.8 adding multi-agent isolation,
  V0.9 adding persistent memory correctness, and V1.0 combining them into a
  complete AgentKernel RuntimeBench.

Immediate next step:

```text
Complete V0.8 evidence freeze without entering Phase 9 implementation.
```
