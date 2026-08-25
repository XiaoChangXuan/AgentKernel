# Agent Runtime Positioning Review

Status: architecture positioning only. This document is not a ranking and does
not evaluate project quality.

Scope:

- Use local source snapshots under `D:/Users/changxuan.xiao/Desktop/Github`.
- Answer what each project is trying to solve.
- Identify whether AgentKernel needs the same idea now.
- Keep AgentKernel positioned as a small trusted runtime kernel, not a full
  product, CLI, memory platform, or integration framework.

## AgentKernel Current Position

After V0.5 and Runtime Benchmark v0.1, AgentKernel's distinct design target is:

- A minimal trusted runtime boundary for agents.
- Durable session event log and replay analysis.
- Crash-aware durable tool execution for external side effects.
- Context VM for model-visible working sets.
- Resource handles for exact large outputs outside prompt context.
- Small replaceable drivers around trusted services.
- Benchmarks that measure runtime mechanisms using offline fixtures.

AgentKernel is not currently:

- A full coding product UI.
- A CLI host with user approval UX.
- A plugin marketplace.
- A complete workflow orchestrator.
- A long-term memory product.
- A broad model/tool integration catalog.

## Comparison Matrix

| Project | What it solves | AgentKernel relevance | Positioning difference |
|---|---|---|---|
| Codex | Coding-agent product/runtime with context management rules, rollout/session resume, sandbox and approval profiles, tool execution UX, and multi-task coordination. | Useful reference for hard context caps, sandbox/approval concepts, and rollout-style resumability. | Codex is a product runtime and user-facing coding environment. AgentKernel is an embeddable kernel library focused on durable runtime invariants. |
| DeepSeek Harness | A composable harness with service/provider/consumer capability seams, session and surface separation, sandbox and permission plugins, compaction, subagent, workflow, and SDK projection. | Strong reference for capability seam vocabulary and the invariant that model-visible input should be reconstructable from session state. | DeepSeek Harness is a plugin-composed agent host and SDK surface. AgentKernel is smaller and keeps fewer concepts inside the trusted core. |
| Gemini CLI | CLI agent host with context compression, hooks, tool output management, sandbox/workspace controls, and subagent-style isolated context flows. | Useful reference for tool output pressure, policy hooks, and practical context compression baselines. | Gemini CLI is an end-user command-line agent. AgentKernel is a runtime substrate intended to be embedded behind many possible hosts. |
| OpenHands | Full agent application stack around workspace isolation, server/frontend workflow, and developer-facing task execution. The local `OpenHands-main` snapshot is primarily product/frontend oriented. | Workspace isolation is relevant as a future policy/driver layer around resources and tools. | OpenHands is an application workspace product. AgentKernel is the lower-level kernel piece that could sit under a product surface. |
| Letta | Stateful agents with memory, identity, and conversations available across computers. The local snapshot contains policy/readme files but not the full runtime source tree, so this review only relies on the locally visible README-level claim. | Relevant to V0.9 Memory as a product-level memory target: durable memory must be explicit, scoped, and recoverable. | Letta is centered on long-lived memory and agent state as a product capability. AgentKernel should provide primitives that a memory system can use, not become a memory product in V0.6. |
| LangChain | Broad framework for LLM applications and agents, with integrations, `create_agent`, middleware, tool/runtime hooks, state schemas, checkpointers, stores, retrievers, and LangGraph-based orchestration. | Useful reference for policy extensibility, middleware shapes, retrieval baselines, and integration ergonomics. | LangChain optimizes for composable application development and provider/tool breadth. AgentKernel optimizes for trusted runtime boundaries, durability, and crash semantics. |

## Project Notes

### Codex

Local evidence sampled:

- Context rules require bounded injected fragments and incremental model-visible
  context.
- Rollout/session artifacts appear as the resumability layer.
- Sandbox and approval profiles are first-class product controls.
- TUI snapshots and server protocol code show permissions and context status as
  user-facing product state.

AgentKernel should take from this:

- Context admission should stay bounded and explicit.
- Capability work should separate sandbox profile policy from kernel authority.
- Rollout-like resume is conceptually close to session replay, but AgentKernel
  should keep the storage contract smaller and library-oriented.

AgentKernel does not need now:

- Product approval UI.
- Full task/sidebar/thread coordination.
- A Codex-specific sandbox profile matrix.

### DeepSeek Harness

Local evidence sampled:

- The repository documents capability seams as Service Definition / Service
  Provider / Consumer roles.
- It states that model-visible input must be reconstructable from session log
  state.
- Its composition includes session persistence/projection, sandbox, permission,
  compaction, tool-result pruning, subagent, workflow, and SDK-facing surfaces.

AgentKernel should take from this:

- Capability should be a full seam, not just a string label.
- Model-visible state should remain replayable or handle-addressable.
- Session and surface may need clearer separation before multi-agent IPC.

AgentKernel does not need now:

- A full plugin composition system.
- Parallel TypeScript/Python SDK projection contracts.
- Workflow worker infrastructure.

### Gemini CLI

Local evidence sampled:

- The codebase includes context compression and tool output handling concerns.
- Hooks and sandbox/workspace controls are visible in the local snapshot.
- Subagent-style flows use isolated context as a practical runtime pattern.

AgentKernel should take from this:

- Tool outputs need active management before they hit the model.
- Policy hooks are valuable when they stay outside trusted kernel invariants.
- Subagents need context and permission boundaries from the beginning.

AgentKernel does not need now:

- CLI-specific UX.
- Host-specific command dispatch behavior.
- Product-level configuration surface.

### OpenHands

Local evidence sampled:

- The local snapshot is oriented around product and frontend/application
  workspace concerns rather than a small reusable kernel.
- The useful theme for this review is workspace isolation and the separation of
  user-facing product surface from agent execution.

AgentKernel should take from this:

- Workspace isolation belongs near resource and tool policy.
- A future host may need stronger boundaries than ResourceOwner alone.

AgentKernel does not need now:

- A product UI/server architecture.
- A browser or workspace task product model.

### Letta

Local evidence sampled:

- The local README describes stateful agents with memory that can learn and
  improve over time.
- It also mentions keeping agent memory, identity, and conversations available
  across computers.
- The local folder does not include the expected runtime source tree, so this
  review avoids deeper implementation claims.

AgentKernel should take from this:

- V0.9 Memory should be designed as explicit durable state, not hidden prompt
  decoration.
- Agent identity matters before memory, because memory ownership and scope need
  a subject.
- Memory should probably use Session Events, Resource Handles, and Context VM
  projection instead of bypassing them.

AgentKernel does not need now:

- A complete long-term memory product.
- Learning or preference-update workflows.
- Cross-device memory service assumptions.

### LangChain

Local evidence sampled:

- `create_agent` exposes middleware, state schema, context schema,
  checkpointer, store, tool registration, and structured output options.
- Middleware includes summarization, file search, human-in-the-loop, retry,
  model fallback, PII, shell tool, tool selection, and tool call limits.
- Tool runtime concepts are imported from LangGraph prebuilt primitives.

AgentKernel should take from this:

- Policy extensibility should be easy and composable.
- Retrieval and semantic summary are important benchmark baselines.
- Tool/runtime injection is useful, but trusted enforcement should stay inside
  kernel services.

AgentKernel does not need now:

- A broad integration catalog.
- General graph orchestration as the kernel core.
- Middleware owning crash-recovery truth.

## AgentKernel Independent Value After V0.5

AgentKernel's current value is not breadth. It is the small set of runtime
mechanisms that remain hard to bolt on after the fact:

- A durable event log that every higher-level behavior can replay.
- Recovery analysis that classifies partial execution instead of treating all
  restarts as fresh retries.
- WAL-backed tool execution for side effects that may have succeeded before a
  crash.
- Context VM projection that treats prompt construction as a bounded runtime
  service.
- Resource handles that keep exact large data out of model context while
  preserving restartable access.
- A developing trusted-kernel boundary where future Capability can enforce
  identity, action, scope, and constraints.

This means AgentKernel should stay narrow in V0.6:

- Add a principled Capability / Namespace model.
- Keep product policy, prompt behavior, memory strategy, retrieval strategy, and
  host UX replaceable.
- Avoid copying full platform features until the kernel invariant that requires
  them is clear.

## What AgentKernel Should Not Copy Yet

- Full UI approval flows.
- Full sandbox profile products.
- Plugin marketplace or composition framework.
- Long-term memory service behavior.
- Full workflow orchestration.
- Provider integration breadth.

These are valid host concerns, but they are not required to define the V0.6
trusted capability boundary.

## Readiness For V0.6 Positioning

AgentKernel should start V0.6 as an architecture design step, not as code.

The design should answer:

- Who is the subject? Agent, parent agent, child agent, tool, or host?
- What is the action? Read, write, execute, delegate, revoke, summarize,
  externalize, reconcile?
- What is the object scope? Tool name, resource URI, context page, memory
  namespace, durable operation, session?
- What constraints apply? Byte limits, time limits, network limits, one-shot
  grants, delegation depth, expiry, human approval, replay-only access?
- Where is enforcement? Tool registry, resource service, durable executor,
  context service, scheduler, or host policy?

The current exact-string capability model can remain for V0.5 behavior while
V0.6 is designed. It should not be expanded into a larger raw-string permission
language without first defining structure, matching, audit, delegation, and
revocation.
