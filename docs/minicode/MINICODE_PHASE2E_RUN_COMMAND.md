# MiniCode Phase 2E Bounded Run Command

Decision: `MINICODE_PHASE2E_RUN_COMMAND_COMPLETE`

## Scope

Phase 2E adds a model-callable MiniCode `run_command` tool for bounded,
synchronous command execution inside a discovered MiniCode workspace.

Implemented boundary:

```text
model proposes command
-> ToolRegistry checks tool.execute tool://minicode/run_command
-> MiniCode validates cwd under workspace
-> MiniCode checks shell.execute shell://workspace/<workspace-id>/**
-> Host policy allow/deny/confirm
-> bounded subprocess dispatch
-> bounded stdout/stderr preview
-> optional AgentKernel ResourceHandle for large output
-> structured command result
```

AgentKernel owns Tool authorization, ResourceService authorization, and
ResourceHandle storage. MiniCode owns command argument validation, workspace cwd
normalization, host shell policy, subprocess capture, and result shaping.

## Tool Schema

`run_command` accepts:

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `command` | string | required | Shell command to execute. |
| `cwd` | string | `.` | Workspace-relative working directory. |
| `timeout_ms` | integer | `30000` | Finite command timeout, capped at `120000`. |
| `mutation_intent` | enum | `read_only` | `read_only` or `may_mutate`. |

Extra model arguments do not grant approval or authority.

## Windows Execution Semantics

Windows execution uses:

```text
powershell.exe -NoLogo -NoProfile -NonInteractive -Command <command>
```

MiniCode launches the shell with `shell=False`, `stdin=DEVNULL`, and the
workspace-contained cwd. The wrapper is non-interactive and does not expose a
background shell.

POSIX execution uses:

```text
/bin/sh -c <command>
```

Phase 2E is Windows-first because the repository is developed and validated on
Windows, including quoted Python one-liners and cwd-sensitive subprocess tests.

## Timeout Behavior

Every command has a finite timeout. On timeout, MiniCode kills the subprocess
and returns a normal structured command result:

```json
{
  "ok": true,
  "exit_code": null,
  "timed_out": true,
  "duration_ms": 50,
  "stdout": {"bytes": 0, "preview": "", "truncated": false},
  "stderr": {"bytes": 0, "preview": "", "truncated": false}
}
```

Timeout is not a Tool crash. It is observable command state for the future
coding loop to inspect.

## Non-Zero Exit Codes

Non-zero command exits are normal command results, not Tool failures.

Example:

```json
{
  "ok": true,
  "exit_code": 1,
  "timed_out": false,
  "stderr": {"preview": "failed\n"}
}
```

This keeps `python -m pytest` failures available to a future CodeAgent without
breaking the Tool execution boundary.

## Output Policy

Stdout and stderr are captured separately.

Model-visible output is bounded:

| Limit | Value |
| --- | --- |
| Default preview | `4096` bytes per stream |
| Maximum preview | `16384` bytes per stream |
| Capture cap | `8388608` bytes per stream |

If a stream is larger than the preview and a `ResourceService` is available,
MiniCode stores the full exact bytes as an AgentKernel artifact and returns a
bounded preview plus a `ResourceHandle`.

If a stream exceeds the capture cap, MiniCode returns a structured
`output_limit_exceeded` error payload. It does not stream unbounded output into
model context.

## ResourceHandle Integration

Large output follows:

```text
full exact bytes
-> AgentKernel ResourceService
-> bounded preview returned to model
-> ResourceHandle returned
```

The handle is not permission. Reading the artifact still requires ResourceService
authorization, such as `resource.read`, and ownership checks. Tests verify that
an agent with the handle but without resource read authority is denied.

## Capability Mapping

Phase 2E uses two separate capabilities:

| Capability | Resource |
| --- | --- |
| `tool.execute` | `tool://minicode/run_command` |
| `shell.execute` | `shell://workspace/<workspace-id>/**` |

The Tool boundary decides whether the model can see and invoke `run_command`.
The shell boundary decides whether this agent can execute a command for the
workspace logical shell scope.

## Host Policy

The default host policy is intentionally conservative:

| Situation | Default behavior |
| --- | --- |
| `read_only` local command | allow |
| `may_mutate` command | require host confirmation |
| external/network side effect | deny |
| background/interactive command | deny |

Host confirmation is separate from AgentKernel capability. A capable agent may
still be blocked by host policy, and a host confirmation cannot create Kernel
authority.

The model cannot approve itself. Arguments such as `"approved": true` are
ignored by the implementation and do not affect host policy.

## Mutation Semantics

`mutation_intent` is explicit:

- `read_only`: command is treated as local observation by default.
- `may_mutate`: command requires host confirmation by default.

Arbitrary shell mutation is not WAL-safe in Phase 2E. `run_command` is not
routed through `DurableToolExecutor` and does not claim exactly-once behavior.
`apply_patch` remains the preferred durable filesystem mutation path.

## Denial Semantics

Denied Kernel authority is reported as Tool execution denial:

- missing `tool.execute` returns `EACCES`;
- missing `shell.execute` returns `EACCES`;
- cwd escape is denied before subprocess dispatch.

Host policy denial returns a structured Tool payload with `ok: false` so the
future coding loop can inspect the denial reason without confusing it with a
Kernel crash.

## Not Implemented

Out of scope for Phase 2E:

- ScriptedModelAdapter;
- OpenAI-compatible adapter;
- coding Agent loop;
- automatic model -> tool -> model execution;
- Context VM coding orchestration;
- full CLI task execution/resume;
- Process cancellation integration;
- generic shell WAL / exactly-once mutation;
- V0.9 memory.

## Implementation Friction

`MINICODE_IMPLEMENTATION_FRICTION: Process cancellation integration deferred`

AgentKernel Process cancellation can be wired cleanly after the Phase 2F Agent
loop exists. Phase 2E therefore implements the truthful synchronous bounded
subprocess subset and does not fake cancellation support.

## Validation

Tests cover:

- command exits `0`;
- command exits non-zero as a normal result;
- timeout as a structured result;
- cwd escape denied before subprocess dispatch;
- missing `tool.execute` denied;
- missing `shell.execute` denied;
- large stdout ResourceHandle;
- large stderr ResourceHandle;
- authorized ResourceHandle read succeeds;
- unauthorized ResourceHandle read denied;
- mutation confirmation deny skips dispatch;
- mutation confirmation allow executes;
- model arguments cannot self-approve;
- Windows execution wrapper;
- existing Phase 2A-2D MiniCode regression through `tests/minicode`.

## Final Decision

`MINICODE_PHASE2E_RUN_COMMAND_COMPLETE`
