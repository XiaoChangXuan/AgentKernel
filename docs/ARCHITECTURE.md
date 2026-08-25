# AgentKernel V0.1 architecture

This document describes implemented behavior. The roadmap and long-term design constraints live in [`IMPLEMENTATION_BLUEPRINT.md`](IMPLEMENTATION_BLUEPRINT.md).

## Boundary

AgentKernel V0.1 is a single-process, single-agent, in-memory mechanism layer. The trusted code owns lifecycle state, capabilities, budgets, the Session log, model request assembly, and Tool dispatch. Model and Tool implementations are replaceable callers of those mechanisms; they do not own Kernel state.

## Modules

| Module | Responsibility |
|---|---|
| `protocol.py` | Provider-neutral `Message`, `ToolCall`, `ToolResult`, `ToolSchema`, `ModelRequest`, and `ModelResponse` values. |
| `events.py` | Closed V0.1 event names and immutable `SessionEvent` envelope. |
| `session.py` | Append-only JSON event storage and `derive_messages()` projection. |
| `llm.py` | Abstract `LLMService.generate()` and deterministic `ScriptedLLM`. |
| `tools.py` | Runtime definitions, schema projection, capability enforcement, timeout, execution, and failure normalization. |
| `prompt.py` | Fresh system-prompt and authorized-tool projection for each step. |
| `agent.py` | AgentControlBlock identities, states, immutable capability sets, bounding invariant, and budgets. |
| `hooks.py` | Ordered notification seam for `before_step`, `before_tool`, and `after_tool`. |
| `loop.py` | Turn and Step orchestration only. |

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

## Deliberately deferred

V0.1 has no persistence, replay after process restart, operation id, side-effect reconciliation, argument JSON-Schema validation, streaming, parallel Tool dispatch, external cancellation API, context compaction, VFS, namespace, scheduler, child Agent, IPC, plugin runtime, Gateway, UI, MCP, memory store, RAG, or model Provider adapter.

