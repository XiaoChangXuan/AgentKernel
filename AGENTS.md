# AgentKernel V0.5 agent guide

Read this file and `docs/IMPLEMENTATION_BLUEPRINT.md` before changing code. `docs/ARCHITECTURE.md` describes implemented V0.5 behavior; code and tests are the final authority when prose drifts.

## Goal

AgentKernel is a small trusted runtime spine for tool-using agents. The LLM proposes decisions; Kernel-owned protocol, session, capability, budget, and tool boundaries control execution.

## Navigation

- `agentkernel/protocol.py`: provider-neutral messages, model requests/responses, tool schemas/calls/results, error codes.
- `agentkernel/events.py` and `agentkernel/session.py`: append-only event vocabulary and model-history projection.
- `agentkernel/persistence.py`: Session header, storage seam, InMemory and single-writer JSONL drivers.
- `agentkernel/recovery.py`: pure replay validation and recovery analysis; never recovery policy.
- `agentkernel/token_accounting.py`: complete-request accounting, deterministic fallback, and lightweight model limits.
- `agentkernel/context/`: Context Pages, projection, policy, pressure, pruning, durable compaction, forced reclaim, working-set selection, and protocol validation.
- `agentkernel/tool_effects.py`: host-only Tool effect and reconciliation values.
- `agentkernel/durable_tools.py`: mutation authorization, operation identity, WAL boundaries, dispatch, retry, and reconciliation mechanisms.
- `agentkernel/resources/`: V0.5 Artifact identity, store/service separation, owner/range enforcement, externalization policy, metrics, and read/stat tools.
- `agentkernel/agent.py`: Agent, AgentControlBlock, state transitions, capabilities, bounding set, budgets.
- `agentkernel/tools.py`: runtime tool definitions, model schema projection, capability enforcement, and handler invocation.
- `agentkernel/prompt.py`, `agentkernel/llm.py`, `agentkernel/hooks.py`: replaceable seams used by the loop.
- `agentkernel/providers/`: wire-protocol adapters; Provider concerns must stay in this boundary.
- `agentkernel/loop.py`: thin default Turn/Step/LLM/Tool driver.
- `examples/`: deterministic runnable compositions.
- `tests/`: executable V0.1–V0.4 contracts, deterministic crash matrices, and synthetic Context pressure tests.

## Kernel invariants

1. The LLM is untrusted and is never the Kernel.
2. External side effects cross `DurableToolExecutor`; direct mutation dispatch through `ToolRegistry.execute()` is rejected.
3. The Session Event Log is the source of truth; model messages are derived projections.
4. Model-visible `ToolSchema` is separate from host `ToolDefinition` metadata and handler.
5. Kernel implements mechanisms; deployments choose policy.
6. Effective capabilities remain a subset of the AgentControlBlock bounding set.
7. `DefaultAgentLoop` contains orchestration only, never business-agent branches.
8. ResourceId and HandleId are Kernel-owned and remain distinct from tool_call_id, operation_id, and host paths.
9. ResourceHandle is a model projection; ResourceMetadata and ResourceStore paths remain host-only.
10. A Resource handle may be emitted only after its bytes and metadata are durably published.

## Resource rules

- The only V0.5 model URI is `artifact://<resource_id>`; do not expose LocalResourceStore paths.
- Every model read/stat crosses ResourceService and validates exact agent/session ownership plus hard range limits.
- Externalized raw bytes live in ResourceStore, not Session or Context Pages. Session records preview+handle.
- Tool Result externalization policy belongs in the ToolResultProcessor seam, never as a threshold branch in DefaultAgentLoop.
- ResourceStore owns durable bytes only; ResourceService owns identity, authorization, validation, and metrics.
- V0.4 Context pruning may further reduce a preview but must never load, rewrite, or delete its resource.
- Crash before store publication yields no handle. A store-committed resource without a Session reference is retained and identifiable; V0.5 has no automatic GC.
- Keep `resource_read` bounded and excluded from recursive result externalization by default.
- Do not add directory/mount/rename/delete/search/glob semantics, remote drivers, media parsers, or a general VFS in V0.5.

## Context VM rules

- Never delete or rewrite Session events to satisfy a Context budget.
- Never treat a Summary as authoritative over its source events.
- Context projection must remain derived from durable Session truth plus the current host system prompt.
- Eviction removes a Page from one model working set, not from history or persistence.
- Pruning rewrites only a model-visible Tool Result Page; the durable full result remains unchanged.
- Pinned and explicitly requested Pages may not be silently evicted; fail with `ContextBudgetExceeded` when their atomic closure does not fit.
- Never compact pinned Pages by default.
- Tool-call/result protocol validity must survive Context selection. Their atomic group is selected or evicted together.
- Never split a Tool Call / Tool Result atomic group during pruning or compaction.
- Selection may use priority and temperature, but final messages must return to causal Session order.
- Context policy remains outside `DefaultAgentLoop` and may change only selection metadata, never projected facts.
- Prefer deterministic reclaim before LLM-based compaction.
- Compaction belongs to Context VM, not `DefaultAgentLoop`; the loop only invokes the Context service seam.
- Every durable Summary must preserve its source range, Page/event identities, costs, fingerprint, and lifecycle.
- Only a fully completed durable Summary may shadow source Pages in the model-visible projection.
- Context Page IDs include Session identity; pin and page-in state must not alias across Sessions.
- Do not persist ordinary Context working sets or reconstructable metrics. Summary lifecycle records are durable because model generation is expensive and non-deterministic.
- Do not introduce RAG, Memory, or VFS in V0.4 phase 3.
- Never retry Provider context overflow indefinitely; automatic recovery may retry the model call at most once.
- Provider-specific error strings must not leak into `ContextManager` or reclaim policy.
- Context accounting must include system prompt, message/tool payloads, Tool Schemas, and protocol overhead—not only `message.content`.
- Exact Provider counting is optional; a deterministic offline fallback is required.
- Overflow recovery must never evict mandatory pinned Pages or rerun durable Tool side effects.
- Real API benchmarks must never run in default pytest, commit credentials, or print API keys.

## Durable Tool rules

- Never dispatch a mutation Tool before `tool/prepare` has crossed `Session.flush()`.
- Never automatically retry an ambiguous `OPAQUE_MUTATION`.
- `operation_id` is Kernel-owned and must never be accepted from, exposed to, or overridden by the model.
- `tool_call_id` is model-protocol identity; `operation_id` is external-operation identity. They are always distinct concepts.
- Resolve the Tool and enforce capability checks before creating `tool/prepare`.
- Persist `tool/dispatch` before entering the external handler so replay can distinguish not-dispatched from possibly-dispatched work.
- Recovery analysis reports mechanism facts. It must not silently choose deployment recovery policy.
- Reuse the original `operation_id` for permitted idempotent retries and Tool reconciliation.
- Do not describe V0.3 as universal exactly-once execution. Effectively-once behavior requires external idempotency or reconciliation support.

## Commands

```bash
python examples/basic_agent.py
python examples/persistent_session.py
python -m examples.context_reclamation_benchmark
python -m benchmarks.context_real_provider_benchmark
python -m benchmarks.coding_fixture_runner
python examples/resource_handles.py
python -m benchmarks.resource_handle_benchmark --sizes-mb 10,100
python examples/real_llm_agent.py  # requires explicit AGENTKERNEL_LLM_* environment
python -m pytest
python -m compileall -q agentkernel examples tests
```

Run focused tests after changing a module, then the full suite before handoff.

## Prohibited in V0.5

- Provider wire or SDK types outside `agentkernel/providers/`.
- Direct business actions in `loop.py`.
- A second mutable chat-history store beside Session events.
- Capability mutation by model or tool code.
- SQLite, distributed transactions, 2PC, distributed locks, Saga framework, full DeepSeek Surface cloning, full VFS semantics, resource deletion/GC/search, remote resource drivers, scheduler, multi-agent, subagent, IPC, complex plugin runtime, UI, Gateway, MCP, RAG, embeddings, vector storage, long-term memory, or prompt-injection classification.
- Large copied sections from reference repositories.

## Persistence and recovery constraints

- Historical Session events are immutable; continuing a restored Session only appends.
- Session owns semantics and must not know JSONL paths or file operations.
- Loading and analysis never repair, close, truncate, or synthesize historical events.
- A truncated final JSONL record is reported and the original artifact remains unchanged.
- Never auto-retry an operation merely because its Tool Call is pending. Follow its explicit `OperationRecoveryClassification` and deployment policy.
- JSONL is single-writer only. Do not imply multi-process safety or a lease protocol.

## References

- Primary runtime reference: `../deepseek-harness-master`, especially `packages/core/{session,tools,agent,agent-loop,system-prompt}` and `packages/llm/llm`.
- Repository-harness reference only: `../harness-engineering-main`.
- Future AgentOS reference only: `../openclaw-main`.
- `../harness-main/harness-main` is not an Agent runtime reference.

## Stage

Current: V0.5 Virtual Resource / Artifact Handle on top of V0.4 Context VM and V0.3 Durable Tool Execution. Implemented mechanisms include a replaceable ResourceStore, durable LocalResourceStore, Kernel-owned opaque handles, owner-checked bounded reads, restart resolution, large Tool Result externalization before Session commit, and Resource metrics. Full VFS, automatic GC, remote stores, search, richer media parsing, long-term memory, RAG, and SQLite remain future decisions.
