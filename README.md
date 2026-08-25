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
  │     ├── ContextPressure + ReclaimPolicy
  │     ├── deterministic ToolResultPruner
  │     ├── durable ContextCompactor
  │     ├── budgeted Working Set ──> ModelRequest
  │     └── provider-aware request token accounting
  ├── LLMService / ScriptedLLM
  │     └── normalized context overflow ──> forced reclaim ──> retry once
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

## Context VM

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

### V0.4 phase 2: context reclamation

`ContextManager.prepare_working_set()` extends phase 1 with an ordered reclaim pipeline:

```text
full projection → working-set eviction → deterministic Tool Result pruning
                → durable semantic compaction → rebuilt Working Set
```

`ContextPressure` derives `NORMAL`, `PRESSURED`, `CRITICAL`, or `OVERFLOW` from projected/selected token estimates, the input budget, and reserved output. A replaceable reclaim policy chooses mechanisms; the default prefers cheap deterministic eviction, then head/omission/tail pruning, and invokes the existing provider-neutral `LLMService` for compaction only at overflow.

Pruning changes only the model-visible Tool Result Page and retains its source Page ID, original/retained cost, strategy, and error-rich tail. Compaction replaces an atomic-safe older Page range with one durable Summary Page while keeping a configurable recent tail verbatim. Every summary records its source event/Page identities, range, costs, fingerprint, timestamp, parent summary, and optional model/provider metadata. Completed summaries shadow their source Pages only in the model-visible projection; raw Session events and full Tool Results are never deleted or rewritten.

The summary lifecycle (`requested → started → summary created → completed`) is append-only and replay-validated. Only a completed summary becomes visible after restart. Rolling compaction can replace a prior checkpoint plus newer old history with one successor checkpoint.

This is bounded, lossy context reclamation—not infinite context, lossless summarization, perfect memory, RAG, or long-term memory.

Run the offline 200-turn comparison:

```bash
python -m examples.context_reclamation_benchmark
```

### V0.4 phase 3: provider-aware accounting and overflow recovery

`RequestTokenAccounting` estimates the complete `ModelRequest`, not only Page text. The deterministic fallback reports separate costs for system prompt, messages (including Tool Calls and Tool Results), Tool Schemas, and provider envelope. `ModelContextLimits` carries a lightweight provider/model context window, maximum output, and recommended output reserve without creating a model registry. Provider adapters may replace the fallback with an exact tokenizer; Context VM never imports a Provider SDK.

No estimator is guaranteed to equal every Provider's final billing or hidden envelope. A normalized Provider context overflow is therefore the final safety boundary:

```text
ModelRequest → Provider CONTEXT_OVERFLOW
             → ContextService.force_reclaim()
             → rebuild a measurably smaller request
             → retry exactly once
```

The adapter classifies overflow separately from rate limit, timeout, authentication, unavailable service, and ordinary invalid requests. Provider-specific strings stay at that boundary. The loop owns only the one-retry guard; eviction, pruning, compaction, safety target, and pinned-Page enforcement remain Context VM policy. Reclaim failure, no measurable reduction, or a second overflow raises `ContextOverflowRecoveryError` without a third call. Because retry completes before an Assistant event or Tool Call is appended, it cannot replay a durable Tool side effect.

Run the default network-free Phase 3 resource/quality benchmark:

```bash
python -m benchmarks.context_real_provider_benchmark
```

It compares Full History, Phase 1 eviction, and Phase 2/3 reclamation across early-constraint, middle-decision, and large-Tool-tail cases. Real API execution is never part of pytest and requires all three `AGENTKERNEL_LLM_*` variables plus `AGENTKERNEL_RUN_REAL_BENCHMARK=1`. API keys are neither committed nor printed. A small isolated coding fixture and runner seam are available through `python -m benchmarks.coding_fixture_runner`; full Shell Agent orchestration remains out of scope.

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

V0.4 phase 3 adds complete-request token accounting, optional model limits, Provider-boundary failure normalization, and one-shot overflow recovery on top of phase 2's pruning and durable compaction. Phase 1 working-set selection and V0.3 Durable Tool Execution remain intact underneath it. All tests and benchmarks are offline by default.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for implemented behavior, [`docs/CONTEXT_PROVIDER_RECOVERY_RESEARCH.md`](docs/CONTEXT_PROVIDER_RECOVERY_RESEARCH.md) for the phase 3 source study, and [`docs/IMPLEMENTATION_BLUEPRINT.md`](docs/IMPLEMENTATION_BLUEPRINT.md) for the longer roadmap.

## Next stage

The next decision is review and, if accepted, merge/tag V0.4. V0.5 virtual resources/artifact handles and a later SQLite persistence driver remain separate choices; RAG, long-term memory, and multi-agent execution are not part of this phase.
