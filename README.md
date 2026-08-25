# AgentKernel

AgentKernel is a minimal trusted runtime spine for tool-using agents. It owns the protocol, event log, process metadata, capability checks, budgets, and tool execution boundary; an LLM only proposes tool calls and answers.

It is not a general-purpose Agent Framework. Framework-level prompts, business tools, workflows, memory products, UIs, and provider integrations belong above or outside the Kernel. AgentKernel concentrates on the small set of mechanisms that must remain enforceable even when model output is incorrect or hostile.

## Kernel and user boundary

```text
Untrusted / policy layer                 Trusted mechanism layer

User input ───────────────┐
LLM decisions             ├──> DefaultAgentLoop
Business Tool handlers ───┘         │
                                    ├── AgentControlBlock + budgets
                                    ├── Session append-only Event Log
                                    └── ToolRegistry capability boundary
```

The model receives provider-neutral messages and `ToolSchema` values. It never receives Tool handlers, credentials, capabilities, timeouts, or mutable Session state.

## Architecture

```text
User
  ↓
Agent + AgentControlBlock
  ↓
DefaultAgentLoop
  ├── PromptService
  ├── Session ──> append-only Event Log ──> derive_messages()
  ├── LLMService / ScriptedLLM
  └── ToolRegistry
        ├── model schema projection
        ├── capability check
        ├── host execution
        └── structured ToolResult

Session
  ├── append-only semantic Event Log
  ├── derive_messages()
  └── SessionPersistence
        ├── InMemorySessionPersistence
        └── JsonlSessionPersistence
```

The deterministic reference flow is:

```text
User → ScriptedLLM → math.add → ToolResult(42) → ScriptedLLM → Final Answer
```

## Run the example

Python 3.11 or newer is required. The runtime has no third-party dependencies.

```bash
python examples/basic_agent.py
```

The command prints `The result is 42.` followed by the complete Session Event Log.

## Durable sessions and recovery

V0.2 keeps the default `Session("id")` process-local and zero-configuration. Durable callers explicitly supply a storage driver:

```python
from agentkernel import JsonlSessionPersistence, Session

session = Session("session-123", JsonlSessionPersistence("sessions/session-123.jsonl"))
# Run the Agent, then establish the explicit fsync boundary.
session.flush()
session.close()

restored = Session.load(
    "session-123",
    JsonlSessionPersistence("sessions/session-123.jsonl"),
)
print(restored.recovery_analysis.status)
restored.close()
```

The first JSONL line is a versioned `session/header`; every later line is a `session/event`. Loading validates the format, requested Session ID, contiguous sequence numbers, Turn/Step nesting, and Tool Call/Result relationships before reconstructing model history.

Recovery results are:

- `COMPLETED`: the durable prefix has no open lifecycle. This describes structural closure; `last_turn_reason` preserves the actual Turn outcome.
- `INTERRUPTED`: the prefix is valid but has an open Turn/Step, a pending Tool Call, or a reported truncated tail.
- `CORRUPTED`: bytes or semantic relationships are invalid; loading raises `SessionCorruptionError` carrying a corrupted analysis when replay reached the semantic validator.

A final incomplete JSONL record is ignored only for analysis, with `tail_truncated` and a warning. The file is not modified and the loaded Session cannot append until an explicit future repair operation exists. A pending Tool Call is always ambiguous: V0.2 cannot know whether an external side effect occurred and never retries it automatically.

Run the offline persistence/restart example:

```bash
python examples/persistent_session.py
```

The JSONL driver is single-writer only. SQLite, multi-process leases, checkpoints, automatic repair, and external side-effect reconciliation are not implemented.

## Run against an OpenAI-compatible API

The optional `OpenAICompatibleLLM` adapter uses the Python standard library and the non-streaming Chat Completions endpoint. It has no default public service and does not read unrelated OpenAI credentials.

Set the endpoint and model explicitly:

```text
AGENTKERNEL_LLM_BASE_URL=http://127.0.0.1:8000/v1
AGENTKERNEL_LLM_MODEL=your-model-name
AGENTKERNEL_LLM_API_KEY=your-key-if-required
```

`AGENTKERNEL_LLM_API_KEY` may be empty for a local service that does not require authorization. `.env` files are ignored and are not loaded automatically; [`.env.example`](.env.example) contains only blank variable names.

Run the real Tool Calling example:

```bash
python examples/real_llm_agent.py
```

Missing endpoint or model configuration produces a clear error. The adapter never falls back to `api.openai.com` or another endpoint.

## Run the tests

Install the test extra once, then run pytest:

```bash
python -m pip install -e ".[test]"
python -m pytest
```

## Current stage

V0.2 first phase adds versioned InMemory/JSONL persistence, deterministic replay, strict consistency checking, crash-position analysis, and explicit durability boundaries to the V0.1 Agent Spine. The optional standard-library OpenAI-compatible adapter remains independent of persistence and adds no mandatory network dependency.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for implemented behavior and [`docs/IMPLEMENTATION_BLUEPRINT.md`](docs/IMPLEMENTATION_BLUEPRINT.md) for the longer roadmap.

## Next stage

After the JSONL semantics are accepted, the next decision is either a V0.2 SQLite driver implementing the same seam or V0.3 Tool WAL/idempotency/reconciliation. Neither is part of this phase.
