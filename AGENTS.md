# AgentKernel V0.4 phase 1 agent guide

Read this file and `docs/IMPLEMENTATION_BLUEPRINT.md` before changing code. `docs/ARCHITECTURE.md` describes implemented V0.4 phase 1 behavior; code and tests are the final authority when prose drifts.

## Goal

AgentKernel is a small trusted runtime spine for tool-using agents. The LLM proposes decisions; Kernel-owned protocol, session, capability, budget, and tool boundaries control execution.

## Navigation

- `agentkernel/protocol.py`: provider-neutral messages, model requests/responses, tool schemas/calls/results, error codes.
- `agentkernel/events.py` and `agentkernel/session.py`: append-only event vocabulary and model-history projection.
- `agentkernel/persistence.py`: Session header, storage seam, InMemory and single-writer JSONL drivers.
- `agentkernel/recovery.py`: pure replay validation and recovery analysis; never recovery policy.
- `agentkernel/context/`: Context Page model, projection, token estimation, policy, working-set selection, pin/evict/page-in, and protocol validation.
- `agentkernel/tool_effects.py`: host-only Tool effect and reconciliation values.
- `agentkernel/durable_tools.py`: mutation authorization, operation identity, WAL boundaries, dispatch, retry, and reconciliation mechanisms.
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

## Context VM rules

- Never delete or rewrite Session events to satisfy a Context budget.
- Context projection must remain derived from durable Session truth plus the current host system prompt.
- Eviction removes a Page from one model working set, not from history or persistence.
- Pinned and explicitly requested Pages may not be silently evicted; fail with `ContextBudgetExceeded` when their atomic closure does not fit.
- Tool-call/result protocol validity must survive Context selection. Their atomic group is selected or evicted together.
- Selection may use priority and temperature, but final messages must return to causal Session order.
- Context policy remains outside `DefaultAgentLoop` and may change only selection metadata, never projected facts.
- Context Page IDs include Session identity; pin and page-in state must not alias across Sessions.
- Do not add Context working-set events to the durable log unless they become irreducible source-of-truth facts. Phase 1 metrics are reconstructable runtime values.
- Do not introduce Summary, compaction, RAG, semantic retrieval, or long-term memory into V0.4 phase 1.

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
python examples/real_llm_agent.py  # requires explicit AGENTKERNEL_LLM_* environment
python -m pytest
python -m compileall -q agentkernel examples tests
```

Run focused tests after changing a module, then the full suite before handoff.

## Prohibited in V0.4 phase 1

- Provider wire or SDK types outside `agentkernel/providers/`.
- Direct business actions in `loop.py`.
- A second mutable chat-history store beside Session events.
- Capability mutation by model or tool code.
- SQLite, distributed transactions, 2PC, distributed locks, Saga framework, LLM Summary, full DeepSeek-style compaction, VFS/artifact handles, scheduler, multi-agent, subagent, IPC, complex plugin runtime, UI, Gateway, MCP, RAG, embeddings, vector storage, long-term memory, or prompt-injection classification.
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

Current: V0.4 Context VM phase 1 on top of the V0.3 Durable Tool Execution release. Implemented mechanisms include deterministic Context Pages, configurable token estimation and input budget, replaceable policy, working-set selection, pin/evict/page-in, causal ordering, Tool atomicity, metrics, and JSONL replay consistency. Summary/compaction, VFS, long-term memory, RAG, and SQLite remain future decisions.
