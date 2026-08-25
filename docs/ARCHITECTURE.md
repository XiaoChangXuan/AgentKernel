# AgentKernel V0.2 architecture

This document describes implemented behavior. The roadmap and long-term design constraints live in [`IMPLEMENTATION_BLUEPRINT.md`](IMPLEMENTATION_BLUEPRINT.md).

## Boundary

AgentKernel V0.2 is a single-process, single-agent mechanism layer with an optionally durable Session log. The trusted code owns lifecycle state, capabilities, budgets, Session semantics, model request assembly, and Tool dispatch. Storage, Model, and Tool implementations are replaceable callers of those mechanisms; they do not own Kernel state.

## Modules

| Module | Responsibility |
|---|---|
| `protocol.py` | Provider-neutral `Message`, `ToolCall`, `ToolResult`, `ToolSchema`, `ModelRequest`, and `ModelResponse` values. |
| `events.py` | Closed V0.1 event names and immutable `SessionEvent` envelope. |
| `session.py` | Append-only semantic log, persistence coordination, load, and `derive_messages()` projection. |
| `persistence.py` | Versioned header/record codec, storage errors, `SessionPersistence`, InMemory, and JSONL. |
| `recovery.py` | Pure sequence/lifecycle/Tool validation and `RecoveryAnalysis`. |
| `llm.py` | Abstract `LLMService.generate()` and deterministic `ScriptedLLM`. |
| `tools.py` | Runtime definitions, schema projection, capability enforcement, timeout, execution, and failure normalization. |
| `prompt.py` | Fresh system-prompt and authorized-tool projection for each step. |
| `agent.py` | AgentControlBlock identities, states, immutable capability sets, bounding invariant, and budgets. |
| `hooks.py` | Ordered notification seam for `before_step`, `before_tool`, and `after_tool`. |
| `loop.py` | Turn and Step orchestration only. |
| `providers/openai_compatible.py` | Non-streaming OpenAI-compatible Chat Completions wire translation and HTTP transport. |

## Turn data flow

```text
append turn/start
append user/message

repeat:
  append step/start
  notify before_step
  PromptService.assemble()
  Session.derive_messages()
  LLMService.generate(ModelRequest)
  append assistant/message

  if tool calls:
    for each call, sequentially:
      enforce tool-call budget
      append tool/call
      notify before_tool
      ToolRegistry.execute()
      append tool/result
      notify after_tool
    append step/end(tool_calls)
    continue

  append step/end(completed)
  append turn/end(completed)
  return final text
```

`DefaultAgentLoop` never keeps a message list. Each model request receives a new tuple projected from Session events. Boundary events and `tool/call` are log-only; `user/message`, `assistant/message`, and `tool/result` produce model history.

## Tool boundary

`ToolDefinition` is host-only and contains its handler, required capability, timeout, and reserved concurrency classification. `ToolRegistry.model_schemas()` constructs detached `ToolSchema` values containing only name, description, and input schema.

The registry resolves and authorizes again during execution, even when an unauthorized schema was hidden from the model. Outcomes use stable codes:

- `ENOENT`: no registered tool.
- `EACCES`: missing effective capability.
- `EIO`: handler failure or non-JSON output.
- `ETIMEDOUT`: configured timeout elapsed.

`EINVAL` and `ECANCELED` are reserved in the protocol for later argument-validation and cancellation work.

## Agent process model

The AgentControlBlock defines `NEW`, `READY`, `RUNNING`, `WAITING`, `PAUSED`, `FAILED`, and `EXITED`. V0.1 actively uses `READY`, `RUNNING`, `WAITING`, and `FAILED`; the others reserve the lifecycle vocabulary needed by later process management.

Both `capabilities` and `capability_bounding_set` are immutable. Construction rejects any effective capability outside the bounding set. Model requests and Tool handlers never receive the AgentControlBlock.

## Budgets and failure closure

`max_steps_per_turn` is checked before opening another Step. `max_tool_calls_per_turn` is checked before dispatching another Tool. Exhaustion appends a closing `step/end` when needed and a `turn/end` with `reason=budget_exceeded`, transitions the Agent to `FAILED`, and raises `LoopBudgetExceeded`.

Unexpected LLM, hook, or Kernel failures close open Step and Turn brackets, transition the Agent to `FAILED`, and propagate the exception. Tool handler failures are normal Tool results and remain visible to the next model Step.

## OpenAI-compatible Provider boundary

`OpenAICompatibleLLM` implements the existing `LLMService.generate()` interface without changing Kernel modules. It uses only `AGENTKERNEL_LLM_BASE_URL`, `AGENTKERNEL_LLM_MODEL`, and the optional `AGENTKERNEL_LLM_API_KEY`; there is no default endpoint or credential discovery.

Outbound conversion supports:

- system and user messages;
- assistant content and one or more function Tool Calls;
- tool messages with their required `tool_call_id`;
- function Tool Schemas containing only name, description, and parameters;
- `tool_choice=auto` when tools are present.

Inbound conversion requires a non-streaming Chat Completions response. Function arguments must be a JSON string that decodes to an object. Missing identities, invalid arguments, unsupported message content, and inconsistent finish reasons raise `OpenAICompatibleProtocolError` before the response reaches the loop. HTTP, transport, configuration, and protocol failures remain distinct, and HTTP diagnostics redact the configured API key.

The current AgentKernel Protocol already preserves the semantic information required for basic Tool Calling: assistant Tool Calls carry stable call IDs, and each Tool Result becomes a tool message carrying the same ID. Successful Provider diagnostics such as request ID, model echo, and token usage are deliberately not added to the semantic protocol yet because V0.1 does not consume them.

## Durable Session

```text
Session.append(event)
        │
        ├── validate strict JSON semantics
        ▼
SessionPersistence.append(event)
        │
        ├── InMemory
        └── JSONL (single writer)

process restart
        ↓
Session.load()
        ↓
header + format validation
        ↓
event replay / consistency checking
        ↓
Session reconstruction + RecoveryAnalysis
```

`Session` depends only on the `SessionPersistence` protocol. It does not know paths, JSONL records, file handles, or fsync. The persistence implementation owns those details. `Session` retains a replayed in-process projection for normal loop access, but the durable Event Log remains the only stored source of truth; messages and recovery facts are derived.

The on-disk format version is `1`. A JSONL artifact starts with an explicit header record:

```json
{"created_at":"2026-08-25T00:00:00Z","format_version":1,"record_type":"session/header","session_id":"session-123"}
{"data":{"turn":1},"record_type":"session/event","seq":1,"time":1787600000.0,"type":"turn/start"}
```

Header and Event records are distinguished by `record_type`, never an implicit sequence value. A runtime refuses every unsupported format version and every unknown required event type. Serialization is deterministic UTF-8 JSON with finite, lossless JSON values; pickle and automatic string coercion are prohibited.

For JSONL, `append()` writes and flushes the Python stream so the record has entered the driver/OS boundary. `Session.flush()` is the explicit durability checkpoint and calls `fsync`; `close()` performs the same checkpoint and is idempotent. This design intentionally omits background batching and sync policies.

## Recovery validation and states

Replay enforces:

- event sequence exactly `1..N`;
- non-nested Turn and Step lifecycles, with Step enclosed by Turn;
- matching Turn/Step identifiers and contiguous per-session Turn/per-Turn Step numbers;
- Tool Calls declared by the same Step's assistant message;
- unique Tool Call IDs and exactly one matching Tool Result;
- no Step or Turn closure while a dispatched Tool Call is pending.

Valid replay produces:

- `COMPLETED` when no Turn, Step, or Tool Call remains open. The last Turn reason is reported separately, so structural completion does not erase an error/budget outcome.
- `INTERRUPTED` when the prefix is valid but work remains open, including a pending Tool Call or truncated final JSONL record.
- `CORRUPTED` when bytes, sequence, identifiers, or lifecycle relationships are invalid. Semantic corruption raises `SessionCorruptionError` with a `CORRUPTED` analysis.

V0.2 does not use an `ACTIVE` persisted state because single-process storage has no durable owner lease and cannot infer whether another process is alive. A live in-process open prefix and a crashed prefix are byte-identical; recovery reports facts rather than inventing liveness.

An invalid final unterminated JSONL record is treated as a recognized crash tail: replay stops at the last complete record, returns `INTERRUPTED`, and records a warning. Loading never edits the artifact, and continuation is blocked until an explicit repair API exists. Malformed records elsewhere are corruption.

## Recovery is not side-effect reconciliation

A durable `tool/call` without `tool/result` means only that dispatch intent was logged and no result is durable. The Tool may not have run, may have failed, or may have completed an external side effect immediately before the crash. `RecoveryAnalysis.pending_tool_calls` therefore marks ambiguous outcomes; the Kernel never retries them automatically.

Resolving that ambiguity requires V0.3 operation IDs, idempotency, prepare/commit records, and Tool-specific reconciliation. Those mechanisms are deliberately absent from V0.2.

## Deliberately deferred

V0.2 first phase has no SQLite, multi-process writer/lease, repair API, checkpoint/snapshot optimization, operation id, side-effect reconciliation, argument JSON-Schema validation, streaming, parallel Tool dispatch, external cancellation API, context compaction, VFS, namespace, scheduler, child Agent, IPC, plugin runtime, Gateway, UI, MCP, memory store, RAG, or Provider-specific retry layer.
