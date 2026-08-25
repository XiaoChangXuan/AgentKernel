# AgentKernel V0.2 agent guide

Read this file and `docs/IMPLEMENTATION_BLUEPRINT.md` before changing code. `docs/ARCHITECTURE.md` describes implemented V0.2 behavior; code and tests are the final authority when prose drifts.

## Goal

AgentKernel is a small trusted runtime spine for tool-using agents. The LLM proposes decisions; Kernel-owned protocol, session, capability, budget, and tool boundaries control execution.

## Navigation

- `agentkernel/protocol.py`: provider-neutral messages, model requests/responses, tool schemas/calls/results, error codes.
- `agentkernel/events.py` and `agentkernel/session.py`: append-only event vocabulary and model-history projection.
- `agentkernel/persistence.py`: Session header, storage seam, InMemory and single-writer JSONL drivers.
- `agentkernel/recovery.py`: pure replay validation and recovery analysis; never recovery policy.
- `agentkernel/agent.py`: Agent, AgentControlBlock, state transitions, capabilities, bounding set, budgets.
- `agentkernel/tools.py`: runtime tool definitions, model schema projection, capability enforcement, execution.
- `agentkernel/prompt.py`, `agentkernel/llm.py`, `agentkernel/hooks.py`: replaceable seams used by the loop.
- `agentkernel/providers/`: wire-protocol adapters; Provider concerns must stay in this boundary.
- `agentkernel/loop.py`: thin default Turn/Step/LLM/Tool driver.
- `examples/`: deterministic runnable compositions.
- `tests/`: executable V0.1 contracts.

## Kernel invariants

1. The LLM is untrusted and is never the Kernel.
2. External side effects cross `ToolRegistry.execute()`.
3. The Session Event Log is the source of truth; model messages are derived projections.
4. Model-visible `ToolSchema` is separate from host `ToolDefinition` metadata and handler.
5. Kernel implements mechanisms; deployments choose policy.
6. Effective capabilities remain a subset of the AgentControlBlock bounding set.
7. `DefaultAgentLoop` contains orchestration only, never business-agent branches.

## Commands

```bash
python examples/basic_agent.py
python examples/persistent_session.py
python examples/real_llm_agent.py  # requires explicit AGENTKERNEL_LLM_* environment
python -m pytest
python -m compileall -q agentkernel examples tests
```

Run focused tests after changing a module, then the full suite before handoff.

## Prohibited in V0.2 first phase

- Provider wire or SDK types outside `agentkernel/providers/`.
- Direct business actions in `loop.py`.
- A second mutable chat-history store beside Session events.
- Capability mutation by model or tool code.
- SQLite, Tool WAL, operation ids, side-effect reconciliation, Context VM, VFS, scheduler, multi-agent, subagent, IPC, complex plugin runtime, UI, Gateway, MCP, RAG, or vector storage.
- Large copied sections from reference repositories.

## Persistence and recovery constraints

- Historical Session events are immutable; continuing a restored Session only appends.
- Session owns semantics and must not know JSONL paths or file operations.
- Loading and analysis never repair, close, truncate, or synthesize historical events.
- A truncated final JSONL record is reported and the original artifact remains unchanged.
- Never auto-retry a pending Tool Call during recovery; its external side effect is ambiguous.
- Do not claim external side-effect recovery before the V0.3 WAL/reconciliation stage.
- JSONL is single-writer only. Do not imply multi-process safety or a lease protocol.

## References

- Primary runtime reference: `../deepseek-harness-master`, especially `packages/core/{session,tools,agent,agent-loop,system-prompt}` and `packages/llm/llm`.
- Repository-harness reference only: `../harness-engineering-main`.
- Future AgentOS reference only: `../openclaw-main`.
- `../harness-main/harness-main` is not an Agent runtime reference.

## Stage

Current: V0.2 Persistence + Recovery first phase: InMemory and JSONL storage, strict replay validation, and read-only recovery analysis. SQLite and V0.3 Tool WAL/reconciliation remain explicit future decisions.
