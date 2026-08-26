# Agent Runtime Comparison

## 1. Scope

This document compares AgentKernel with selected agent runtimes and frameworks
only on the requested questions:

- what problem they solve;
- whether AgentKernel needs the same mechanism;
- how AgentKernel should position itself differently.

This is not a ranking and not a "who is best" document.

Research basis checked on 2026-08-25:

| Project | Primary references used |
|---|---|
| Codex | [Running Codex safely at OpenAI](https://openai.com/index/running-codex-safely/), [Building a safe Codex Windows sandbox](https://openai.com/index/building-codex-windows-sandbox/), [Codex sandbox docs](https://github.com/openai/codex/blob/main/docs/sandbox.md). |
| DeepSeek Harness | [Developer preview](https://www.deepseek.com/harness/en/), [compaction docs](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/compaction.md). |
| Gemini CLI | [Google Cloud Gemini CLI docs](https://docs.cloud.google.com/gemini/docs/codeassist/gemini-cli), [checkpointing docs](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/checkpointing.md), [Gemini CLI docs index](https://geminicli.com/docs/). |
| OpenHands | [Docker sandbox agent server](https://docs.openhands.dev/sdk/guides/agent-server/docker-sandbox), [Docker sandbox usage](https://docs.openhands.dev/openhands/usage/sandboxes/docker). |
| Letta | [MemFS](https://docs.letta.com/concepts/memfs), [shared memory](https://docs.letta.com/concepts/shared-memory), [shared memory SDK management](https://docs.letta.com/agent-sdk/repositories). |
| LangChain / LangGraph | [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence), [human-in-the-loop](https://docs.langchain.com/oss/python/langchain/human-in-the-loop), [middleware overview](https://docs.langchain.com/oss/python/langchain/middleware/overview). |

## 2. Positioning Summary

AgentKernel is not trying to be a full product, IDE agent, workflow framework,
memory product, or plugin marketplace.

AgentKernel's intended position is narrower:

```text
small trusted runtime kernel
  - agent identity
  - lifecycle
  - event log
  - recovery
  - capability enforcement
  - durable tool execution
  - context VM
  - virtual resources
```

Frameworks and products can sit above AgentKernel. Drivers, stores, providers,
and tool handlers can sit below it. The Kernel should own the invariants that
must hold even when the model, plugin, prompt, or tool policy is wrong.

## 3. Codex

Targeted areas:

- context manager;
- sandbox;
- rollout / task continuation.

What Codex solves:

- product-grade coding-agent operation across local, IDE, and cloud surfaces;
- sandbox and approval policies around filesystem, network, and command
  execution;
- task/thread lifecycle, streaming, approval requests, and app/server protocol;
- context and tool integration for software engineering workflows;
- cloud tasks in isolated environments.

Does AgentKernel need it?

| Codex mechanism | AgentKernel need |
|---|---|
| Sandbox + approvals | Yes, but as future driver/process isolation and policy integration, not as prompt logic. |
| Product task/thread UI | No. AgentKernel should expose runtime state but not own a product UI. |
| Context replacement / rollout ideas | Yes as benchmark baselines and design references, but AgentKernel's Context VM must keep durable provenance and replay semantics. |
| Cloud environment orchestration | Not core now. V0.7+ can define process/scheduler seams that cloud runners can implement. |

Positioning difference:

Codex is a complete coding-agent product/runtime with user-facing controls.
AgentKernel should remain an embeddable kernel layer that other products can
use to enforce durable runtime invariants.

## 4. DeepSeek Harness

Targeted areas:

- session;
- surface;
- compaction;
- capability boundary.

What DeepSeek Harness solves:

- a plugin-composed harness where models, tools, sessions, sandboxes, storage,
  loops, scheduling, and UI can be swapped or recomposed;
- append-only session/event sourcing with derived model-visible surfaces;
- compaction as a capability seam, with log-only compaction events and surface
  replacement;
- subagent capability family for delegated child work.

Does AgentKernel need it?

| DeepSeek Harness mechanism | AgentKernel need |
|---|---|
| Everything-as-plugin composition | Partly. AgentKernel needs replaceable drivers/policies, but not every kernel invariant should become a peer plugin. |
| Session log -> surface projection | Yes. AgentKernel already follows this with Session Event Log -> Context Projection. |
| Compaction seam | Yes. AgentKernel already has durable compaction; future work should keep compaction policy replaceable. |
| Subagent family | Yes later, but V0.8 should build it on ACB, delegation, resource grants, and IPC rather than copying plugin shape wholesale. |
| Capability seams | Yes as a design reference for clean ownership boundaries. |

Positioning difference:

DeepSeek Harness optimizes for composable harness extensibility. AgentKernel
should optimize for a smaller trusted core: plugins may provide policies and
drivers, but they should not bypass event integrity, WAL, capability checks, or
resource authorization.

## 5. Gemini CLI

Targeted areas:

- context handling;
- tool output management;
- checkpointing/session management.

What Gemini CLI solves:

- a practical command-line coding agent with ReAct-style loop, tools, MCP, and
  project context;
- checkpointing before file modifications so a user can restore workspace state;
- GEMINI.md and memory features for persistent project/user context;
- tool-output masking/management to avoid runaway context growth.

Does AgentKernel need it?

| Gemini CLI mechanism | AgentKernel need |
|---|---|
| Workspace checkpoint before edits | Useful benchmark baseline, but AgentKernel's primary primitive is durable event/WAL recovery, not git restore UX. |
| Project memory files | Policy/user-space input to Context VM; not Kernel truth by itself. |
| Tool output masking | Yes as a baseline. AgentKernel V0.5 Resource handles are the stronger form for large exact payloads. |
| Session resume UX | Product layer. Kernel should provide recoverable state and classification. |

Positioning difference:

Gemini CLI is a developer tool. AgentKernel is a runtime substrate. The CLI may
choose user-facing restore and memory workflows; AgentKernel should provide the
mechanism that lets such workflows be durable, bounded, and auditable.

## 6. OpenHands

Targeted area:

- workspace isolation.

What OpenHands solves:

- isolated execution environments for coding agents through Docker, process, or
  remote sandboxes;
- a workspace abstraction for command execution and file operations;
- product/runtime architecture for local, remote, and hosted agent execution.

Does AgentKernel need it?

| OpenHands mechanism | AgentKernel need |
|---|---|
| Docker/remote sandbox | Yes eventually as a driver/process isolation option. |
| Workspace API | Possibly as a Resource/Tool driver family, not as the core permission model. |
| Product orchestration/UI | No. Keep above Kernel. |
| Host isolation lessons | Yes. V0.7 scheduler and V0.6 capability should make sandbox boundaries explicit. |

Positioning difference:

OpenHands provides a broad coding-agent workspace/runtime. AgentKernel should
define the narrow kernel-level authority model that can run on top of local,
container, or remote workspace drivers.

## 7. Letta

Targeted area:

- memory model.

What Letta solves:

- stateful agents with persistent memory;
- MemFS / context repositories: git-backed memory projected into a filesystem
  that agents can read and edit with ordinary file tools;
- in-context memory blocks for core memory in the v1 SDK / legacy API surface;
- shared memory repositories across agents, with older shared memory blocks
  retained as a legacy pattern;
- archival memory as searchable long-term storage accessed through tools in the
  v1 memory model;
- a context hierarchy that distinguishes always-visible/system memory,
  out-of-context files, archival retrieval, and external RAG.

Does AgentKernel need it?

| Letta mechanism | AgentKernel need |
|---|---|
| MemFS / context repositories | Yes as V0.9 design input for file-like memory, but AgentKernel should model it through Resource/Memory services plus Context VM projection rather than making Git the kernel primitive. |
| In-context/core memory blocks | Yes as design input, but as Context VM sources with provenance and capabilities, not necessarily as Letta-compatible block objects. |
| Archival memory | Yes later as a Memory service/store, likely backed by resource-like handles, indexes, and retrieval tools. |
| Agent-editable memory | Yes, but writes must be capability-checked and journaled as durable effects. |
| Shared memory repositories / shared blocks | Yes for multi-agent systems, but requires delegation, revocation, consistency semantics, and audit. |

Positioning difference:

Letta is memory-first. AgentKernel should not turn V0.9 Memory into the whole
runtime. Memory should plug into the existing Kernel boundaries:

```text
Memory Store truth
  -> Memory retrieval/write tools
  -> Context VM projection
  -> Session provenance / audit links
```

## 8. LangChain / LangGraph

Targeted area:

- agent abstraction limitations.

What they solve:

- LangChain provides high-level model, tool, middleware, and agent abstractions;
- LangGraph provides lower-level stateful graph orchestration with durable
  execution, persistence, streaming, and human-in-the-loop;
- middleware gives developers hooks around model calls, tool calls, context, and
  runtime state.

Does AgentKernel need it?

| LangChain/LangGraph mechanism | AgentKernel need |
|---|---|
| Broad integration ecosystem | No. AgentKernel should interoperate rather than replace it. |
| Durable execution/persistence | Yes, but AgentKernel's durable unit is the session/WAL/kernel event boundary, not a general workflow graph. |
| Middleware hooks | Partly. AgentKernel has hooks and policies, but security-critical checks must not become optional middleware. |
| Human-in-the-loop approval | Yes later as policy around capability/tool/resource actions. |
| Graph workflow abstraction | No for Kernel core. A framework above AgentKernel can own graph shape. |

Positioning difference:

LangChain is a framework and LangGraph is an orchestration runtime. AgentKernel
is a smaller kernel-style runtime layer. It should be usable underneath or
beside framework abstractions when a product needs explicit recovery,
capability, context, and side-effect invariants.

## 9. Cross-Project Lessons for AgentKernel

| Lesson | Source pattern | AgentKernel decision |
|---|---|---|
| Sandbox and approval are runtime boundaries, not prompt instructions. | Codex, OpenHands, Gemini CLI | Put future sandbox/process isolation below Kernel services and expose approval as policy. |
| Durable truth and model-visible surface should be separate. | DeepSeek Harness, AgentKernel current code, Letta message/memory separation | Keep Session/Event truth separate from Context Pages and Memory projections. |
| Large tool output needs a bounded model-visible representation. | Gemini CLI masking, Codex/Gemini context management, AgentKernel V0.5 | Prefer Resource handles plus range reads when exact bytes matter. |
| Memory is a source with permissions and lifecycle, not just a summary. | Letta | Build V0.9 as Memory Store + tools + Context VM projection + capability checks. |
| Framework hooks are useful but cannot be the trusted boundary. | LangChain middleware, DeepSeek plugin seams | Keep plugin/policy hooks outside core invariants. |
| Multi-agent support needs delegation and revocation. | DeepSeek subagents, Letta shared blocks, Codex parallel tasks | Extend ACB and Resource/Capability models before V0.8. |

## 10. Final Position

AgentKernel should not compete by offering the largest product surface. Its
value is that the following remain explicit and testable:

- what happened;
- what can safely be retried;
- what the model is allowed to call;
- what resource a handle refers to;
- what bytes stay outside context;
- what subset of history enters a model request;
- what must survive a crash.

This differentiates AgentKernel from ordinary agent frameworks while still
leaving room for those frameworks, CLIs, IDE agents, and memory systems to use
AgentKernel as a lower-level runtime spine.
