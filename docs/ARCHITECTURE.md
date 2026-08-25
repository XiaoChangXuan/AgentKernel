# AgentKernel V0.4 phase 1 architecture

This document describes implemented behavior. The roadmap and long-term design constraints live in [`IMPLEMENTATION_BLUEPRINT.md`](IMPLEMENTATION_BLUEPRINT.md).

## Boundary

AgentKernel V0.4 phase 1 is a single-process, single-agent mechanism layer with an optionally durable Session log, a durable protocol for one Tool side-effect operation, and deterministic management of each model request's physical Context working set. The trusted code owns lifecycle state, capabilities, budgets, Session semantics, Context projection boundaries, model request assembly, operation identity, WAL transitions, and Tool dispatch. Storage, Model, Tool, token-estimation, and Context-policy implementations remain replaceable seams; they do not own Kernel truth.

## Modules

| Module | Responsibility |
|---|---|
| `protocol.py` | Provider-neutral `Message`, `ToolCall`, `ToolResult`, `ToolSchema`, `ModelRequest`, and `ModelResponse` values. |
| `events.py` | Closed event vocabulary, including Tool WAL events, and immutable `SessionEvent` envelope. |
| `session.py` | Append-only semantic log, persistence coordination, load, and `derive_messages()` projection. |
| `persistence.py` | Versioned header/record codec, storage errors, `SessionPersistence`, InMemory, and JSONL. |
| `recovery.py` | Pure sequence/lifecycle/WAL validation and operation-level `RecoveryAnalysis`. |
| `context/` | Context Page model, projection, token estimation, budget, default policy, working-set selection, pin/evict/page-in, Tool atomicity, and metrics. |
| `llm.py` | Abstract `LLMService.generate()` and deterministic `ScriptedLLM`. |
| `tool_effects.py` | Host-only effect classifications and reconciliation values. |
| `tools.py` | Runtime definitions, schema projection, capability enforcement, timeout, handler invocation, and failure normalization. |
| `durable_tools.py` | Mutation prepare/dispatch/commit protocol, stable operation IDs, explicit retry, and reconciliation. |
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
  ContextManager.build_working_set()
    Session events → Context Pages
    ContextPolicy → temperature / priority / pin
    budget + dependencies → Working Set
    causal projection → messages
  LLMService.generate(ModelRequest)
  append assistant/message

  if tool calls:
    for each call, sequentially:
      enforce tool-call budget
      append tool/call
      notify before_tool
      DurableToolExecutor.execute()
        resolve + capability check
        for mutation: prepare + flush, dispatch + flush
        invoke Tool handler
        for mutation: commit/abort + flush
      append tool/result
      notify after_tool
    append step/end(tool_calls)
    continue

  append step/end(completed)
  append turn/end(completed)
  return final text
```

`DefaultAgentLoop` never keeps a message list and contains no token threshold or eviction policy. Each model request receives a fresh `ContextWorkingSet` through the replaceable `ContextService` seam. `Session.derive_messages()` remains the complete V0.1–V0.3 compatibility projection; the Loop now uses the budgeted projection. Boundary, WAL, and `tool/call` events are log-only; `user/message`, `assistant/message`, and `tool/result` produce Context Pages.

## Context VM

```text
Session Event Log + current host system prompt
                  ↓
          ContextProjector
                  ↓
            Context Pages
                  ↓
          ContextPolicy
                  ↓
       ContextManager selection
                  ↓
       ContextWorkingSet + metrics
                  ↓
             ModelRequest
```

The key responsibility split is:

- **Session:** what actually happened? It remains the durable, append-only source of truth.
- **Context VM:** what should the current model Step see? It derives a disposable working set without editing history.

Context VM is not long-term memory. Long-term memory would extract and recall facts across Sessions. It is not RAG; a future retriever may become one page-in source. It is not compaction; future compaction is one pressure-reclaim strategy that may create a durable, provenance-carrying summary projection. Phase 1 only evicts and pages existing information back in.

### Context Page model

Each immutable `ContextPage` carries:

- a Session-qualified `page_id`;
- `kind`: `SYSTEM`, `USER_MESSAGE`, `ASSISTANT_MESSAGE`, `TOOL_RESULT`, or the reserved-but-not-produced `SUMMARY`;
- exact model-facing `content` and optional provider-neutral `Message`;
- estimated `token_cost`;
- policy fields `priority`, `temperature`, and `pinned`;
- origin metadata `created_seq` and `turn`;
- `trust_label`: `KERNEL`, `USER`, `TOOL`, or `EXTERNAL`;
- `dependencies` and an optional `atomic_group`.

There is no persisted `last_access`, VFS `source_uri`, `summary_of`, artifact handle, embedding, or mutable Page store in phase 1. Those fields would either create unused OS-shaped metadata or imply later subsystems that do not exist. Session-event sequence plus Session-qualified identity provides the implemented provenance.

Projection and policy are separate. `ContextProjector` deterministically maps current Session events to neutral Pages and never projects Turn/Step boundaries, Tool Calls, or `tool/prepare`, `tool/dispatch`, `tool/commit`, `tool/abort`, and `tool/reconcile`. `ContextPolicy` may change only priority, temperature, and pin status; the manager rejects a policy that changes content, identity, cost, trust, dependency, or origin.

### Budget and estimation

`ContextBudget` defines:

```text
available_input_tokens = max_tokens - reserved_output_tokens
```

The explicit output reservation prevents input selection from consuming the entire advertised model window. `TokenEstimator` is a provider-neutral protocol. The built-in `ApproximateTokenEstimator` uses deterministic Unicode-code-point length divided by a configurable characters-per-token ratio, with no `tiktoken` or Provider import. Its count is an estimate, not exact model billing; deployments can inject a Provider-specific estimator later.

Phase 1 prices system and message Pages. Tool schemas and Provider envelope overhead are not Pages yet, so deployments requiring a hard wire-level cap must reserve that overhead in the supplied budget.

### Default policy

`DefaultContextPolicy` is deterministic and configurable through `ContextPolicyConfig`:

- system prompt: `PINNED`;
- current user message: `PINNED` by default;
- other current-Turn Pages: `HOT`;
- Pages within `recent_turns`: `HOT`;
- ordinary older Pages: `WARM`;
- Tool groups whose result exceeds `large_tool_result_threshold_tokens`, or ages beyond `tool_result_cold_after_turns`: `COLD`.

Pin choice is policy; pin enforcement is mechanism. Manual `pin()` can add residency, `unpin()` removes only that manual pin, and policy-owned pins remain authoritative.

### Working-set selection

Selection proceeds as follows:

1. Reproject all available Pages from current truth.
2. Apply and validate policy-only selection metadata.
3. Apply manual pins and one-shot page-in requests.
4. Expand mandatory Pages through atomic groups and dependencies.
5. Fail with `ContextBudgetExceeded` if mandatory closure exceeds input budget.
6. If the complete projection fits, select everything without arbitrary eviction.
7. Otherwise consider remaining atomic units by temperature, priority, recency, and stable identity, admitting only units whose dependency closure fits.
8. Restore selected Pages to `created_seq` causal order and validate Tool protocol before building messages.

Evicted Pages remain in the returned metrics and Page projection and, more importantly, their source events remain untouched in Session. A later larger budget may select them naturally. `request_page(page_id)` makes an available Page plus its dependency/atomic closure mandatory for the next successful working set, then clears the request.

### Tool protocol atomicity

An assistant message containing Tool Calls and all corresponding Tool Results share one atomic group and mutual dependency closure. Selection includes or excludes that group as one unit. `ContextWorkingSet.to_messages()` independently validates that each selected Tool Result follows a selected assistant Tool Call and that no selected Tool Call is left without its Result. Final Session order is retained, so the OpenAI-compatible adapter never receives a priority-sorted or orphaned tool transcript.

### Metrics and durable-event decision

Every working set reports:

```text
projected_pages / projected_tokens
selected_pages / selected_tokens
evicted_pages / evicted_tokens
pinned_pages
budget_tokens
```

Phase 1 does not append `context/working-set` to Session. Selection and these metrics are deterministic projections of current events, system prompt, estimator, policy, page-in state, and budget; they are not irreducible facts about what happened. Persisting every step's Page IDs would grow the log and create a second lifecycle without improving recovery. A future reproducibility requirement may revisit this decision with an explicit request-envelope event.

## Tool boundary

`ToolDefinition` is host-only and contains its handler, required capability, timeout, concurrency classification, effect kind, and optional reconciliation callback. `ToolRegistry.model_schemas()` constructs detached `ToolSchema` values containing only name, description, and input schema. WAL metadata, capabilities, operation identity, timeout implementation, and reconciliation callbacks never enter the model request.

The registry resolves and authorizes again during execution, even when an unauthorized schema was hidden from the model. Direct registry execution is retained for `READ_ONLY` compatibility and rejects mutation Tools; mutation handlers can only be reached through `DurableToolExecutor`. Outcomes use stable codes:

- `ENOENT`: no registered tool.
- `EACCES`: missing effective capability.
- `EIO`: handler failure or non-JSON output.
- `ETIMEDOUT`: configured timeout elapsed.

`EINVAL` also reports an attempted direct mutation dispatch; `ECANCELED` remains reserved for later cancellation work.

## Durable Tool execution

```text
Model ToolCall
      ↓
Kernel resolve + capability check
      ↓
tool/prepare(operation_id, tool_call_id, tool_name, effect_kind)
      ↓ Session.flush()
tool/dispatch(operation_id, attempt)
      ↓ Session.flush()
external Tool handler(arguments, ToolExecutionContext)
      ↓
tool/commit(operation_id, output) / tool/abort(operation_id, error_code)
      ↓ Session.flush()
tool/result(call_id, semantic result)
```

The prepare durability boundary is the central invariant: failure to append or flush prepare prevents entry into the mutation handler. Dispatch is also made durable before the call so replay can distinguish a prepared-but-not-dispatched operation from one whose external outcome may be unknown. Capability denial occurs before both records and therefore creates neither durable intent nor external effect.

`tool_call_id` and `operation_id` serve different namespaces. The model/provider supplies the former to correlate a semantic Tool Call and Tool Result. The Kernel generates the latter to identify one real external operation across process restart, retry, and reconciliation. The operation ID is passed through `ToolExecutionContext`, never through model-owned arguments. A retry always retains the prepared operation ID and increments its dispatch attempt.

Host-side effect semantics are:

| Effect kind | External contract | Ambiguous post-dispatch recovery |
|---|---|---|
| `READ_ONLY` | No persistent external side effect. | Ordinary re-execution is safe; mutation WAL is unnecessary. |
| `IDEMPOTENT_MUTATION` | Repeating the same `operation_id` cannot duplicate the effect. | `IDEMPOTENT_RETRY_ALLOWED` with the same identity. |
| `RECONCILABLE_MUTATION` | The Tool can query external status by `operation_id`. | `RECONCILE_REQUIRED`; only a reliable `NOT_FOUND` permits dispatch. |
| `OPAQUE_MUTATION` | Neither idempotency nor reliable status lookup exists. | `MANUAL_REQUIRED`; executor retry is rejected. |

Reconciliation observations are `SUCCEEDED`, `FAILED`, `NOT_FOUND`, `IN_PROGRESS`, and `UNKNOWN`. A succeeded observation is durably committed without dispatching again. A failed observation is durably aborted as a known terminal failure. Not found changes the mechanism fact to safe-to-retry; in-progress and unknown remain reconcile-required.

Tool handler `EIO` and `ETIMEDOUT` results remain semantic execution errors, while uncertainty is represented independently in operation recovery classification. In particular, timeout after dispatch is classified according to effect kind rather than being treated as permission for a blind retry.

This protocol is a local WAL for a single external operation, not a distributed transaction. AgentKernel cannot provide universal exactly-once side effects without cooperation from the external system. Stable external idempotency or trustworthy reconciliation can provide effectively-once/recoverable behavior; opaque systems cannot.

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

`Session` depends only on the `SessionPersistence` protocol. It does not know paths, JSONL records, file handles, fsync, token budgets, or Page residency. The persistence implementation owns storage details and Context VM owns model visibility. `Session` retains a replayed in-process projection for normal access, but the durable Event Log remains the only stored conversation source of truth; full messages, Context Pages, working sets, metrics, and recovery facts are derived.

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

## Operation recovery analysis

Replay validates operation identity uniqueness, one prepare per mutation Tool Call, effect type, dispatch attempt ordering, legal retry conditions, commit/abort prerequisites, reconciliation status, and agreement between committed output and semantic Tool Result. It then returns a `DurableOperationRecovery` for each prepared operation:

- `SAFE_TO_RETRY`: prepare is durable but dispatch is absent, or reconciliation reliably returned `NOT_FOUND`.
- `IDEMPOTENT_RETRY_ALLOWED`: dispatch may have occurred and the Tool contract makes the stable operation ID idempotent.
- `RECONCILE_REQUIRED`: a reconcilable operation crossed dispatch without a terminal observation.
- `COMPLETED`: commit is durable or reconciliation found a terminal success/failure. A missing `tool/result` still leaves model-side work to recover, but the external effect is no longer ambiguous and must not be dispatched again.
- `MANUAL_REQUIRED`: an opaque operation crossed dispatch and automatic execution is unsafe.

These are reconstructed mechanism facts, not policy choices. `DurableToolExecutor.retry()` and `.reconcile()` are explicit host calls and reject classifications that do not authorize their mechanism. V0.3 recovery actions append only while the originally prepared Step remains open; it has no cross-Step repair/reopen protocol. The default loop does not silently resume interrupted operations.

## Deliberately deferred

V0.4 phase 1 has no Summary generation, semantic retrieval, RAG, embeddings, long-term memory, DeepSeek-style surface replacement, Tool Result pruning, VFS/artifact handles, infinite context, SQLite, multi-process writer/lease, repair API, checkpoint/snapshot optimization, distributed transaction/2PC/Saga coordinator, argument JSON-Schema validation, streaming, parallel Tool dispatch, external cancellation API, namespace, scheduler, child Agent, IPC, plugin runtime, Gateway, UI, MCP, prompt-injection classifier, or Provider-specific retry layer.
