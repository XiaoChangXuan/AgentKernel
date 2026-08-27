# MiniCode Phase 2F Workload Evaluation

Status: implemented and validated.

This document freezes the first MiniCode real-workload evaluation layer. The
goal is to test MiniCode as a small coding agent workload on top of
AgentKernel V0.1-V0.8 primitives, without claiming model intelligence,
production success rate, or V0.9 behavior.

The executable suite is:

```bash
python -m benchmarks.minicode
```

The machine-readable artifact is:

```text
benchmarks/results/minicode_phase2f_validation.json
```

The suite is deterministic, offline, and uses `ScriptedModelAdapter`. It does
not call real model providers and does not depend on network access.

## Evaluation Scope

MiniCode Phase 2F validation contains eight checks:

| Check | Name | Mechanism exercised |
| --- | --- | --- |
| F1 | Workspace | Workspace discovery, path containment, AGENTS.md instruction projection |
| F2 | Tool Visibility | Tool schema filtering plus execution-time capability denial |
| F3 | Durable Patch Recovery | Durable apply_patch prepare/dispatch/crash/reconcile |
| F4 | Resource Authority | Large command output externalization and Handle != Permission |
| F5 | Nonzero Command | pytest exit 1 as structured command observation, not Tool failure |
| F6 | Budget Block | Scheduler and usage budget blocking at a safe point |
| F7 | Resume / Handoff | Process recreation over the same durable Session truth |
| F8 | Trace Redaction | Observable trace with secret-shaped fields redacted |

Current result:

```text
F1 Workspace                         PASS
F2 Tool Visibility                   PASS
F3 Durable Patch Recovery            PASS
F4 Resource Authority                PASS
F5 Nonzero Command                   PASS
F6 Budget Block                      PASS
F7 Resume / Handoff                  PASS
F8 Trace Redaction                   PASS

Phase 2F Validation:
8/8 PASS
```

## V0.1-V0.8 Coverage Matrix

| AgentKernel version | Primitive | Covered by | Coverage level |
| --- | --- | --- | --- |
| V0.1 | Agent execution and Tool boundary | F2, F5 | Direct integration coverage |
| V0.2 | Session persistence and recovery | F3, F7 | Direct integration coverage for MiniCode resume/recovery paths |
| V0.3 | Durable Tool execution and WAL | F3, F7 | Direct coverage for `apply_patch` durable mutation |
| V0.4 | Context VM | F1, F4 | Indirect integration coverage through request construction and bounded output |
| V0.5 | ResourceHandle / ResourceService | F4 | Direct coverage for large stdout storage and authorized read |
| V0.6 | Capability enforcement | F2, F4 | Direct coverage for tool execution and resource read denial |
| V0.7 | Process runtime, scheduler, accounting | F6, F7 | Direct coverage for budget block and replaceable Process identity |
| V0.8 | Multi-agent runtime | None in Phase 2F | Not claimed; reviewer/subagent remains deferred for MiniCode v0 |

## Claims Supported

The current suite supports these claims:

- MiniCode can drive AgentKernel Tool execution through Kernel boundaries.
- Unauthorized tool execution is denied even when a tool schema is visible.
- Durable `apply_patch` recovery reconciles an already-applied mutation instead
  of blindly retrying it.
- Large shell output is kept out of model context and preserved behind a
  ResourceHandle.
- A ResourceHandle is not itself permission to read the stored bytes.
- Non-zero subprocess exits remain inspectable command results.
- Scheduler budget exhaustion blocks runtime progress without rewriting Session
  truth.
- MiniCode resume can continue with a new Process identity over the same
  durable Session.
- Trace output records observable runtime facts without exposing secret-shaped
  fields.

## Claims Not Supported

The current suite does not support these claims:

- MiniCode is better than another coding agent.
- A real LLM will solve arbitrary repositories.
- Long-horizon coding success rate is measured.
- V0.8 multi-agent behavior is covered by this MiniCode workload.
- Shell mutation is generally WAL-safe.
- Context summaries are a durable source of truth.
- ResourceHandle possession grants authority.
- Real-model provider behavior is deterministic or suitable as a CI oracle.

## Relationship To RuntimeBench

RuntimeBench remains the canonical AgentKernel release benchmark. MiniCode
Phase 2F validation is an application workload on top of the runtime, not a
replacement for RuntimeBench.

The Phase 2F suite is useful because it composes multiple runtime primitives in
one CodeAgent-like loop. RuntimeBench remains better for isolated runtime
claims such as crash injection, durable side-effect safety, context efficiency,
capability isolation, resource governance, and multi-agent recovery.

## Future IntegrationBench Boundary

The frozen future IntegrationBench contract remains separate:

| ID | Contract | Phase 2F status |
| --- | --- | --- |
| I1 | Basic edit | Partially represented by F3 and F5 |
| I2 | Test-and-fix loop | Represented by F5 |
| I3 | Crash/resume | Represented by F3 and F7 |
| I4 | Large stdout ResourceHandle | Represented by F4 |
| I5 | Capability denial | Represented by F2 and F4 |
| I6 | Budget exhaustion | Represented by F6 |
| I7 | Durable mutation crash/recovery | Represented by F3 |
| I8 | Reviewer child Agent | Deferred for MiniCode v0 |

Phase 2F therefore provides broad MiniCode workload evidence, but it should not
be renamed as the frozen Phase 2G IntegrationBench.

## Limitations

- The suite uses synthetic fixtures.
- The model is scripted.
- The benchmark is offline.
- It measures runtime integration behavior, not model intelligence.
- It does not measure latency, token economy, or coding task success rate.
- It does not cover reviewer/subagent workflows.
- It does not cover arbitrary shell mutation crash safety.
- It does not exercise real provider API behavior.

## Decision

MINICODE_PHASE2F_WORKLOAD_EVALUATION_COMPLETE
