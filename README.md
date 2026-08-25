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
  ├── Session ──> append-only Event Log
  ├── ContextManager
  │     ├── ContextProjector ──> Context Pages
  │     ├── ContextPolicy
  │     └── budgeted Working Set ──> ModelRequest
  ├── LLMService / ScriptedLLM
  └── ToolRegistry
        ├── model schema projection
        ├── capability check
        └── DurableToolExecutor
              ├── operation identity + mutation WAL
              ├── host execution
              ├── idempotent retry / reconciliation
              └── structured ToolResult

Session
  ├── append-only semantic Event Log
  ├── derive_messages() full-history compatibility projection
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

A final incomplete JSONL record is ignored only for analysis, with `tail_truncated` and a warning. The file is not modified and the loaded Session cannot append until an explicit future repair operation exists. V0.3 additionally reconstructs each prepared mutation as `SAFE_TO_RETRY`, `IDEMPOTENT_RETRY_ALLOWED`, `RECONCILE_REQUIRED`, `COMPLETED`, or `MANUAL_REQUIRED`; analysis reports the mechanism fact and does not silently select deployment policy.

Run the offline persistence/restart example:

```bash
python examples/persistent_session.py
```

The JSONL driver is single-writer only. SQLite, multi-process leases, checkpoints, and automatic repair are not implemented.

## Durable Tool execution

Host code classifies each Tool without exposing the classification to the model:

```python
from agentkernel import ToolDefinition, ToolEffectKind

definition = ToolDefinition(
    schema=tool_schema,
    handler=create_order,
    required_capability="orders.create",
    effect_kind=ToolEffectKind.IDEMPOTENT_MUTATION,
)
```

`READ_ONLY` is the compatibility default and runs without mutation WAL. Every mutation follows this path:

```text
Model ToolCall
    → capability check
    → tool/prepare + flush
    → tool/dispatch + flush
    → external handler(operation_id)
    → tool/commit or tool/abort + flush
    → ToolResult
```

The Kernel generates `operation_id` separately from the model's `tool_call_id` and passes it only through `ToolExecutionContext`. An idempotent external API can use it as its idempotency key. A reconcilable Tool can map it to `SUCCEEDED`, `FAILED`, `NOT_FOUND`, `IN_PROGRESS`, or `UNKNOWN`. An ambiguous opaque mutation becomes `MANUAL_REQUIRED` and the executor refuses an automatic retry.

This is durable, recoverable side-effect execution—not a universal exactly-once guarantee. Effectively-once behavior is possible only when the external system honors stable idempotency or provides reliable reconciliation.

## Context VM phase 1

V0.4 introduces deterministic working-set based context selection. The durable Session still answers “what happened”; Context VM answers “what should this model Step see now?”

```text
Session Event Log
    → ContextProjector
    → Context Pages
    → ContextPolicy
    → budgeted Working Set
    → ModelRequest
```

`Session.derive_messages()` remains the complete history projection. `ContextManager.build_working_set()` creates a separate, replaceable physical-context projection with an explicit input budget:

```python
from agentkernel import ContextBudget, ContextManager

context = ContextManager()
working_set = context.build_working_set(
    session,
    current_turn=3,
    budget=ContextBudget(
        max_tokens=128_000,
        reserved_output_tokens=16_000,
    ),
    system_prompt="Use tools carefully.",
)
```

The default policy pins the system prompt and current user input, favors current/recent Turns, cools older history, and makes old or oversized Tool groups first-class eviction candidates. Tool-call assistant messages and their Tool Results form an atomic group, so budget pressure cannot create an orphan OpenAI-compatible tool message. Selection happens by pin/temperature/priority/recency; output messages return to causal Session order.

Eviction is not deletion. Original events and full Tool Results remain in Session. `request_page(page_id)` performs an explicit one-working-set page-in, while `pin()` and `unpin()` provide mechanism-level residency controls. If mandatory Pages and their dependencies exceed the input budget, selection raises `ContextBudgetExceeded` instead of silently dropping them.

Phase 1 performs no summarization, pruning, semantic retrieval, RAG, or long-term memory. It does not provide infinite context.

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

V0.4 phase 1 adds deterministic Context Page projection, configurable token estimation, reserved-output-aware input budgets, replaceable policy, working-set selection, pin/evict/page-in, Tool protocol atomicity, causal ordering, structured metrics, and JSONL restart consistency. V0.3 Durable Tool Execution remains unchanged underneath it. All tests and examples are offline by default; the optional OpenAI-compatible adapter remains independent and adds no mandatory network dependency.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for implemented behavior and [`docs/IMPLEMENTATION_BLUEPRINT.md`](docs/IMPLEMENTATION_BLUEPRINT.md) for the longer roadmap.

## Next stage

The next decision is among V0.4 phase 2 compaction/summary/tool-result pruning, V0.5 virtual resources and artifact handles, or a SQLite persistence driver. None is part of phase 1.
