# Agent Runtime Design Comparison

This document is a problem-driven comparison, not a superiority benchmark.

It uses local reference repositories to explain where AgentKernel sits in the agent runtime design space. It does not claim that AgentKernel beats Codex, OpenHands, Gemini CLI, DeepSeek Harness, LangChain, Letta, or any other project. RuntimeBench does not benchmark those systems.

## Inspected Local Sources

The following local files or source areas were inspected for this phase:

| Repository | Inspected evidence |
| --- | --- |
| `deepseek-harness-master` | `AGENTS.md`, `docs/architecture.md`, `docs/agent-lifecycle.md`, `docs/capability-seams.md`, `packages/test-support/acp-snapshot/README.md` |
| `OpenHands-main` | `README.md`, `docs/architecture.md`, `docs/ACP_AGENTS.md`, `docs/SELF_HOSTING.md`, `docs/DEVELOPMENT.md` |
| `gemini-cli-main` | `CONTRIBUTING.md`, `packages/cli/src/ui/hooks/useToolScheduler.ts`, `packages/cli/src/ui/hooks/useToolScheduler.test.ts`, changelog docs mentioning sandbox, approval, workspace, and policy behavior |
| `langchain-master` | `libs/core/langchain_core/agents.py` |
| `codex-main` | local public docs around sandboxing were available; deeper closed runtime internals are not established from inspected implementation |
| `letta-main` | local README did not establish enough current runtime internals for strong claims in this comparison |

Where a behavior was not established from inspected local implementation, this document says so instead of guessing.

## Session / Resume

Problem: after an agent run is interrupted, what can be reconstructed without trusting live memory?

Observed approaches:

- DeepSeek Harness documents a durable `session/event` stream and separates durable replay facts from live `agent/*` coordination. It also states that model-visible inputs must be reconstructable from the session log.
- OpenHands Agent Canvas positions itself as a frontend and orchestration layer over Agent Server APIs. In the inspected repository, the canonical SDK/server internals are owned by a sibling repository, so AgentKernel-level durable event semantics are not established from this repo alone.
- LangChain core exposes `AgentAction`, `AgentStep`, and `AgentFinish`, which model action/observation flow. That abstraction is useful for agent execution, but the inspected file is not a durable recovery log.

AgentKernel design:

- `Session` is the durable semantic truth.
- `Session.load` validates and replays persisted events.
- Recovery analysis classifies interrupted state without pretending to choose product policy.

Trade-off:

- Stronger durable semantics require strict event schemas and persistence behavior.
- AgentKernel is smaller and lower-level than a full product harness, so Host code owns UI, policy, and provider integration.

## Tool Lifecycle And Side Effects

Problem: a tool may produce an external side effect, then the runtime may crash before recording local completion.

Observed approaches:

- LangChain's inspected `AgentAction` / `AgentStep` model captures action and observation but does not itself establish WAL, dispatch, commit, or reconciliation semantics.
- Gemini CLI exposes a tool scheduler path and approval-oriented tool lifecycle in inspected UI hook tests, but the inspected snippets are not a durable side-effect WAL.
- DeepSeek Harness has tool-owned events and session fixtures in inspected docs and test support, but AgentKernel-style operation WAL semantics should not be inferred unless implemented evidence is inspected.

AgentKernel design:

- Durable Tool Execution models PREPARE, DISPATCH, COMMIT, stable `operation_id`, and reconciliation.
- Crash after dispatch before commit is classified as reconcile required.
- Authorization metadata is bound to the durable operation boundary.

Trade-off:

- Hosts must supply reconcile handlers for reconcilable external mutations.
- AgentKernel does not claim universal exactly-once behavior or distributed transaction safety.

## Context Management

Problem: long runs exceed model context, while durable truth must remain available.

Observed approaches:

- DeepSeek Harness documents compaction, tool-result pruning, and surface replacement generation tied to session events.
- Gemini CLI changelog and code areas reference context and tool output handling, but the inspected local evidence is broad and does not establish a single durable Context VM equivalent.
- LangChain's inspected core action classes do not own context compaction semantics.

AgentKernel design:

- Context VM projects Session events into Context Pages and a model-visible Working Set.
- Context is explicitly not durable truth.
- RuntimeBench B3 tests bounded context plus truth preservation in deterministic fixtures.

Trade-off:

- Projection and compaction add complexity.
- Context policy remains a Host-level choice.

## Large Tool Output / Resource Management

Problem: large tool results do not fit model context.

Observed approaches:

- OpenHands and Gemini CLI both operate in workspace/tool environments where filesystem and sandbox boundaries matter, but AgentKernel should not infer a semantic ResourceHandle model from those inspected docs alone.
- DeepSeek Harness docs include storage, session query, projection, and tool-result pruning seams.

AgentKernel design:

- Resource Layer stores bytes outside context.
- ResourceHandle is a reference, not a permission.
- ResourceStore is storage, not the authorization boundary.
- ResourceShare and Capability remain distinct in V0.8.

Trade-off:

- Host code must manage storage policy, cleanup, read latency, and access patterns.

## Permission, Approval, Sandbox, And Capability

Problem: who decides whether a proposed action is allowed?

Observed approaches:

- Codex public docs inspected locally emphasize sandbox and approval concepts. Internal authorization details are not established from inspected local implementation.
- Gemini CLI inspected docs and changelogs discuss sandboxing, approval, workspace trust, path safety, and policy evolution. This is focused on CLI/workspace safety and tool execution governance.
- OpenHands docs warn that local operation can give agents filesystem access and recommend Docker sandbox mode for laptop usage.
- DeepSeek Harness describes capability seams as service definition/provider/consumer roles. That is a plugin architecture concept, not necessarily AgentKernel's semantic `CapabilityGrant`.

AgentKernel design:

- Agent is the capability principal.
- Kernel checks `CapabilityGrant` through `CapabilityEvaluator`.
- Tool, Resource, and Durable operation boundaries re-check authorization.
- Capability is separate from prompt text, IPC payload, ResourceHandle, and ResourceShare.

Trade-off:

- AgentKernel's capability model is explicit but not a production sandbox, RBAC, IAM, or full namespace system.
- Host policy still decides what grants should exist.

## Multi-Agent Runtime

Problem: subagents, child processes, IPC, and resource sharing can easily smuggle authority.

Observed approaches:

- Gemini CLI inspected UI scheduler code tracks root and subagent tool calls by scheduler id and filters UI display for non-root scheduler activity. This is a UI/runtime presentation concern and should not be overread as a Kernel authority model.
- OpenHands Agent Canvas can work with multiple agents/backends through Agent Server and ACP, but the inspected repository positions the deeper server/runtime behavior in sibling repositories.
- DeepSeek Harness docs and package map mention subagent capabilities and snapshot fixtures with parent/child logs, which is relevant design-space evidence for multi-agent session surfaces.

AgentKernel design:

- Agent Tree and Process Tree are distinct.
- Process lineage does not imply authority inheritance.
- Delegation produces explicit child grants with provenance.
- IPC transfers data but not authority.
- ResourceShare grants access only when current capability authorization and active share rules both permit it.
- Integrated recovery reconstructs durable semantic facts and fresh runtime mechanisms without restoring stale runtime-only authority.

Trade-off:

- Multi-agent coordination requires more explicit objects.
- V0.8 is still local, deterministic, and synthetic in its benchmark evidence.

## Cancellation, Budget, And Runtime Accounting

Problem: runtime control decisions are often confused with semantic task results.

Observed approaches:

- Gemini CLI inspected scheduler hooks and tests include tool scheduling, cancellation, and subagent UI activity surfaces.
- OpenHands and DeepSeek Harness both expose larger product/runtime orchestration surfaces, but exact equivalence to AgentKernel Process Accounting is not established from the inspected files.

AgentKernel design:

- Process is the schedulable runtime identity.
- Agent remains the capability principal.
- Accounting observes token/tool/resource usage; it is not authority and not a durable billing ledger.
- Budget exceeded can block or pause execution at safe points without marking the semantic task as failed.

Trade-off:

- Cooperative safe points are explicit; V0.8 does not implement preemptive scheduling.

## Design Positioning

AgentKernel sits below product frameworks and above raw OS primitives:

```text
Product agent / UI / workflow policy
  |
  v
AgentKernel runtime invariants
  |
  v
Drivers, filesystem, APIs, model providers, external services
```

Its design target is not "more features than a framework." Its design target is a small trusted runtime boundary for:

- durable semantic truth,
- crash-aware side effects,
- bounded context projection,
- explicit resource handles,
- semantic capability authority,
- process scheduling and accounting,
- multi-agent identity, IPC, sharing, and recovery invariants.

The cost is that AgentKernel requires Host integration. The benefit is that runtime invariants are not hidden inside prompts, transcripts, or ordinary retry loops.

## What This Comparison Does Not Establish

- It does not benchmark any external project.
- It does not claim other systems lack private or unpublished mechanisms.
- It does not prove AgentKernel is more secure, more reliable, or more intelligent.
- It does not establish production readiness for AgentKernel.
- It does not replace each project's own architecture documentation.
