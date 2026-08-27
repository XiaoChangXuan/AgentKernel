# MiniCode Phase 2F Agent Loop

Status: implemented.

Phase 2F adds MiniCode's coding harness loop without changing AgentKernel core. MiniCode owns model adapter wiring, tool orchestration, trace rendering, and CLI entry points. AgentKernel remains the owner of Session truth, Capability evaluation, Context VM projection, durable Tool WAL, Scheduler safe points, ResourceService, and recovery analysis.

## Implemented Scope

- `ModelAdapter` protocol with deterministic `ScriptedModelAdapter`.
- Minimal opt-in `OpenAICompatibleAdapter` for OpenAI-compatible chat completion APIs.
- `MiniCodeAgentLoop` over AgentKernel `Agent`, `Session`, `ToolRegistry`, `ContextManager`, `CooperativeScheduler`, `UsageCollector`, and `ResourceService`.
- Observable `TraceRecorder` JSONL stream with secret redaction.
- CLI `run`, `resume`, `trace`, and explicit `bench --suite phase2f`
  validation commands.
- `bench --suite integration` remains a Phase 2G stub.

## Adapter Behavior

`ScriptedModelAdapter` is the deterministic CI adapter. It records every `MiniCodeModelRequest` and returns queued `MiniCodeModelResponse` values or callback-generated responses. Exhaustion is reported as `ModelAdapterError("script_exhausted")`.

`OpenAICompatibleAdapter` is opt-in. It requires either explicit `enabled=True` configuration or an injected client. Real-model calls are not used as deterministic test or CI oracles. API keys are not exposed through public diagnostics.

## Loop State Machine

For a new task the loop writes:

1. `turn/start`
2. optional `user/message`
3. repeated `step/start`
4. model request through Context VM working set
5. `assistant/message`
6. zero or more `tool/call`
7. tool execution and `tool/result`
8. `step/end`
9. `turn/end`

If the model returns no tool calls, the assistant text is the final answer. If the configured step limit is reached, the run returns `MAX_TURNS`.

## Tool Boundary

Tool schemas come from `ToolRegistry.model_schemas(agent.control)`, so unauthorized tools remain hidden from the model. Execution re-enters the Kernel boundary:

- `apply_patch` uses `DurableApplyPatchAdapter` and AgentKernel `DurableToolExecutor`.
- `run_command`, `list_files`, `search_files`, and `read_file` use `ToolRegistry.execute`.

`run_command` remains non-durable. Non-zero exit codes are structured command results, not MiniCode loop failures. Arbitrary shell mutation is still not claimed WAL-safe.

## Context And Instructions

The loop builds every request through `ContextManager.build_working_set`. `AGENTS.md` files are discovered from workspace root to task cwd and placed into the system prompt as instructions only. They are not authority and cannot grant capabilities, host confirmation, or OS sandbox permission.

## Runtime Identity

- Agent: capability principal.
- Process: runtime scheduling identity.
- Session: durable event journal.
- Context: model-visible projection, not durable truth.
- Trace: observable execution facts for humans and tests, not a replay source and not hidden chain-of-thought.

## Capabilities And Host Policy

MiniCode installs the Phase 2E capability grants for its five tools and workspace shell scope. Capability checks are Kernel mechanism. Host confirmation remains separate from capability and from OS sandboxing. The non-interactive CLI maps `--approve always` to allow mutating shell commands, and `--approve never` / `on-mutation` to deny mutation confirmation by default.

## Resume And Recovery

`resume` loads an AgentKernel JSONL session and appends a new turn without creating a new user message. Before a new run, the loop checks `session.recovery_analysis`; unresolved durable operations classified as `RECONCILE_REQUIRED` or `MANUAL_REQUIRED` stop the loop with `RECOVERY_REQUIRED` instead of blindly retrying.

## Cancellation And Budget

The loop checks Scheduler safe points before turns, steps, model calls, tool calls, and durable dispatch boundaries. Cancellation, pause, and budget exceeded outcomes are reported as structured `MiniCodeRunStatus` values. Cancellation during an already-running synchronous subprocess remains future work because Phase 2E command execution is bounded but synchronous.

## CLI

- `minicode run --script-json script.json --workspace PATH TASK...`
- `minicode resume --session-path session.jsonl --script-json script.json SESSION_ID`
- `minicode trace --session-path session.jsonl SESSION_ID`
- `minicode bench --suite phase2f`

The CLI only enables scripted deterministic model runs in Phase 2F.

`bench --suite phase2f` runs MiniCode Phase 2F validation checks and writes
`benchmarks/results/minicode_phase2f_validation.json` unless `--no-write` is
provided. It does not claim the frozen Phase 2G IntegrationBench contract.
`bench --suite integration` remains explicitly not implemented until Phase 2G.

## Phase 2F Validation Evidence

The Phase 2F validation runner is available through:

```bash
python -m benchmarks.minicode
python -m minicode.cli bench --suite phase2f
```

It runs these deterministic offline checks:

| Check | Purpose |
| --- | --- |
| F1 Workspace | Validates workspace discovery, containment, and AGENTS.md projection. |
| F2 Tool Visibility | Validates `ToolRegistry.model_schemas(agent.control)` and execution-time capability denial. |
| F3 Durable Patch Recovery | Validates durable `apply_patch` prepare/dispatch/crash/reconcile behavior. |
| F4 Resource Authority | Validates large command output externalization and Handle != Permission. |
| F5 Nonzero Command | Validates pytest exit 1 as a structured command observation followed by repair and pytest exit 0. |
| F6 Budget Block | Validates scheduler budget blocking at a safe point without rewriting Session truth. |
| F7 Resume / Handoff | Validates runtime identity replacement while preserving durable Session facts. |
| F8 Trace Redaction | Validates observable trace redaction for secret-shaped fields. |

These checks are Phase 2F integration checks, not the frozen Phase 2G
IntegrationBench IDs. The frozen future contract remains:

| ID | Future IntegrationBench contract |
| --- | --- |
| I1 | Basic edit |
| I2 | Test-and-fix loop |
| I3 | Crash/resume |
| I4 | Large stdout ResourceHandle |
| I5 | Capability denial |
| I6 | Budget exhaustion |
| I7 | Durable mutation crash/recovery |
| I8 | Reviewer child Agent - deferred |

## MINICODE_IMPLEMENTATION_FRICTION

- Process cancellation is cooperative at loop safe points; it does not interrupt a subprocess already running inside the Phase 2E synchronous command runner.
- The CLI has no interactive confirmation UI in Phase 2F; `on-mutation` denies mutating shell commands by default.
- Real-model runs are adapter-supported but intentionally excluded from deterministic CI.

## Not Implemented

- Phase 2G IntegrationBench.
- automatic long-horizon coding policies.
- reviewer/subagent flow.
- V0.9 memory work.
- AgentKernel core redesign.
