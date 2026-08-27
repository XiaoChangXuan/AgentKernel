# MiniCode Phase 1 Architecture Freeze

Final decision: `MINICODE_PHASE1_ARCHITECTURE_FROZEN`

## 1. Scope

This document freezes the MiniCode v0 implementation contract from
`MINICODE_PHASE0_HARNESS_STUDY.md`.

Phase 1 is design only. It does not implement MiniCode, does not modify
`agentkernel/`, and does not start V0.9.

MiniCode v0 is a small local coding harness on top of AgentKernel V0.8. Its
target workload remains:

```text
minicode "fix the divide-by-zero bug in calculator.py and run tests"
```

The v0 loop is:

```text
inspect repository
-> read relevant files
-> patch
-> run tests
-> inspect failure
-> patch again if needed
-> report completion
```

## 2. Normative Language

This freeze uses these terms:

| Term | Meaning |
| --- | --- |
| MUST | Required for MiniCode v0 implementation. |
| SHOULD | Expected default unless implementation evidence forces a narrower path. |
| MAY | Permitted but not required in v0. |
| DEFERRED | Explicitly outside v0 implementation. |
| FORBIDDEN | Must not be implemented as part of MiniCode v0. |

## 3. Ownership Boundary

MiniCode is an application harness. AgentKernel remains the runtime authority.

| Concern | MiniCode ownership | AgentKernel ownership | External ownership |
| --- | --- | --- | --- |
| CLI and user options | MUST own | None | Invoked by shell |
| Coding task prompt/policy | MUST own | None | None |
| Model selection | MUST own | MAY observe usage through accounting | Provider API |
| Model adapter | MUST own tiny protocol | None | Provider SDK/API |
| Agent identity | MAY request/create via Kernel API | MUST own authority principal | None |
| Process identity | MAY request/create via Kernel API | MUST own ProcessControlBlock, scheduling, accounting | None |
| Session | MUST consume | MUST own durable event log and recovery | Persistence backend |
| Tool schema UX | MUST define MiniCode tools | MUST enforce tool execution boundary | None |
| Capability | MUST request/configure grants | MUST evaluate/enforce/delegate | OS sandbox may add hard limits |
| WAL | MUST route durable mutations through it | MUST own prepare/dispatch/commit/reconcile semantics | Filesystem/subprocess side effects |
| Context | MUST format app inputs | MUST own projection/reclaim/working set | None |
| ResourceHandle | MUST use for large outputs | MUST own ResourceService authority and byte storage | ResourceStore backend |
| Workspace discovery | MUST own | None | Filesystem/git |
| Path normalization | MUST own before tool call | MAY recheck as Kernel/resource boundary | Filesystem |
| Shell process | MUST shape command UX | MUST provide capability/process/budget safe points | Subprocess/OS sandbox |
| Trace presentation | MUST own observable UX | MUST supply observable durable/runtime facts | None |
| Reviewer/subagent | DEFERRED | V0.8 compatible, not required | None |

Forbidden in MiniCode v0:

- a MiniCode-owned durable Session log;
- a MiniCode-owned capability evaluator;
- a MiniCode-owned Context VM;
- a MiniCode-owned WAL;
- a MiniCode-owned recovery engine;
- a claim that semantic Capability is an OS sandbox;
- hidden chain-of-thought in trace output.

## 4. Package and Module Layout

MiniCode v0 MUST be a separate application package. It MUST NOT live under
`agentkernel/`.

Frozen package layout:

```text
minicode/
  __init__.py
  __main__.py
  cli.py
  config.py
  workspace.py
  instructions.py
  model.py
  loop.py
  state.py
  trace.py
  policy.py
  context.py
  capabilities.py
  tools/
    __init__.py
    schemas.py
    list_files.py
    search_files.py
    read_file.py
    apply_patch.py
    run_command.py
    output_capture.py
  patch/
    __init__.py
    grammar.py
    parser.py
    applier.py
  adapters/
    __init__.py
    scripted.py
    openai_compatible.py
  testing/
    __init__.py
    fixtures.py
    integrationbench.py
```

Allowed support files:

```text
examples/minicode/
docs/minicode/
tests/minicode/
benchmarks/minicode/
```

Layout rationale:

- `minicode.tools` owns application tool argument/result contracts.
- `minicode.patch` owns Codex-style patch parsing and filesystem application.
- `minicode.adapters` owns model provider adapters.
- `minicode.testing` owns deterministic IntegrationBench fixtures.
- AgentKernel remains imported as a dependency, not copied or wrapped as a
  second runtime.

## 5. Five-Tool Contract

MiniCode v0 MUST expose exactly five model-callable tools:

```text
list_files
search_files
read_file
apply_patch
run_command
```

No additional model-callable tool is allowed in v0 without a new architecture
freeze. Internal helper functions are allowed, but the model-visible tool
surface remains five tools.

### 5.1 Common Tool Rules

Each tool MUST have:

- stable model-visible name;
- JSON input schema;
- JSON result schema;
- bounded model-visible output;
- structured error result with `code`, `message`, and `retryable`;
- path normalization before filesystem access;
- AgentKernel ToolRegistry registration;
- capability requirement;
- observable trace entry.

Every tool result MUST be lossless JSON.

### 5.2 `list_files`

Purpose: bounded workspace discovery.

Input:

```text
path?: string
recursive?: bool = false
max_entries?: int
include_hidden?: bool = false
```

Result:

```text
root
path
entries[]
truncated
entry_count
```

Rules:

- MUST resolve `path` inside workspace root.
- MUST return paths relative to workspace root.
- MUST sort deterministically.
- MUST omit ignored/build/cache directories by default where the repository
  policy defines them.
- MUST NOT use shell.

Capability:

```text
tool.execute    tool://minicode/list_files
workspace.read  workspace://<workspace-id>/**
```

WAL: none. This is read-only.

### 5.3 `search_files`

Purpose: search as a first-class coding primitive, avoiding shell misuse for
ordinary repository discovery.

Input:

```text
query: string
path?: string
glob?: string
case_sensitive?: bool = false
max_matches?: int
context_lines?: int = 0
```

Result:

```text
matches[]
truncated
match_count
searched_path
```

Rules:

- MUST resolve paths inside workspace root.
- SHOULD use Python implementation or `rg`-compatible semantics behind the
  tool, but the model-visible contract must not depend on shell.
- MUST include file path and line number for each match.
- MUST bound output and create a ResourceHandle for large search results.

Capability:

```text
tool.execute    tool://minicode/search_files
workspace.read  workspace://<workspace-id>/**
```

WAL: none. This is read-only.

### 5.4 `read_file`

Purpose: safe, line-ranged source reading.

Input:

```text
path: string
start_line?: int
end_line?: int
max_bytes?: int
```

Result:

```text
path
start_line
end_line
total_lines
content
truncated
encoding
sha256
```

Rules:

- MUST reject paths outside the workspace.
- MUST default to UTF-8 text.
- MUST return line-numbered content.
- MUST return structured errors for binary/unsupported encoding.
- MUST bound model-visible bytes.
- MAY create a ResourceHandle for large file content, but the preview remains
  the model-visible result.

Capability:

```text
tool.execute    tool://minicode/read_file
workspace.read  workspace://<workspace-id>/**
```

WAL: none. This is read-only.

### 5.5 `apply_patch`

Purpose: the only direct filesystem write tool in MiniCode v0.

Input:

```text
patch: string
```

Result:

```text
applied
changed_files[]
hunk_count
summary
preimage_hashes
postimage_hashes
```

Rules:

- MUST use Codex-style `apply_patch` grammar.
- MUST parse and validate the entire patch before mutation.
- MUST reject malformed patches without side effects.
- MUST reject paths outside the workspace.
- MUST reject binary edits in v0.
- MUST be the only direct filesystem write path in v0.
- MUST enter AgentKernel durable mutation/WAL boundary.

Capability:

```text
tool.execute     tool://minicode/apply_patch
workspace.write  workspace://<workspace-id>/**
```

WAL: required.

### 5.6 `run_command`

Purpose: bounded synchronous command execution, primarily tests and inspection.

Input:

```text
command: string
cwd?: string
timeout_ms?: int
mutation_intent?: "read_only" | "may_mutate"
```

Result:

```text
exit_code
timed_out
duration_ms
stdout_preview
stderr_preview
stdout_resource?: ResourceHandle
stderr_resource?: ResourceHandle
```

Rules:

- MUST be synchronous and bounded in v0.
- MUST enforce default timeout and maximum timeout.
- MUST resolve cwd under workspace root.
- MUST terminate the subprocess on timeout where the platform permits.
- MUST return exit code, timeout flag, duration, stdout preview, stderr preview.
- MUST store large stdout/stderr as ResourceHandle.
- MUST require explicit semantic capability.
- MUST NOT claim arbitrary shell mutation is WAL-safe.
- MUST NOT provide background jobs or interactive shell in v0.

Capability:

```text
tool.execute   tool://minicode/run_command
shell.execute  shell://workspace/<workspace-id>/**
```

WAL:

- read-only command: no WAL, but evented and traced;
- mutating command: host confirmation required; not claimed WAL-safe unless
  routed through a durable mutation adapter;
- external side effect command: deny by default in v0 unless explicitly
  configured by host policy.

## 6. Workspace and Path Semantics

Frozen workspace root algorithm:

1. Start from CLI cwd unless `--workspace` is provided.
2. Walk upward to nearest `.git` directory.
3. If found, workspace root is that git root.
4. If not found, workspace root is cwd or `--workspace`.
5. Normalize all model-provided paths relative to workspace root or current
   task cwd.
6. Reject traversal outside workspace before tool execution.
7. Return model-visible paths relative to workspace root.

MiniCode v0 MUST support only one workspace root.

Instruction discovery:

1. `AGENTS.md` is the only instruction file format supported in v0.
2. Discover from workspace root to current task cwd.
3. Later files may refine earlier files, but the exact merge policy is host
   policy and must be shown in trace.
4. `CLAUDE.md`, `.cursor`, IDE settings, package-manager metadata, and
   multi-root instruction synthesis are DEFERRED.

Path classes:

| Class | Rule |
| --- | --- |
| Relative path | Resolve under workspace root or task cwd. |
| Absolute path inside workspace | Normalize to workspace-relative path. |
| Absolute path outside workspace | Deny. |
| Symlink escaping workspace | Deny after realpath resolution. |
| Binary file | Read may return metadata/error; patch is denied. |
| Generated cache/build output | May be hidden from `list_files` by default. |

## 7. `apply_patch` and WAL Semantics

MiniCode v0 freezes `apply_patch` as a reconciliable durable mutation.

Required order:

```text
parse patch
-> normalize and authorize paths
-> compute preimage hashes
-> WAL PREPARE with operation_id and patch digest
-> DISPATCH authorization
-> apply filesystem mutation
-> compute postimage hashes
-> WAL COMMIT with patch result
```

Crash semantics:

| Crash point | Required recovery behavior |
| --- | --- |
| Before PREPARE | No durable mutation exists; model may propose again. |
| After PREPARE, before mutation | Recovery may safely abort or retry after reauthorization. |
| After mutation, before COMMIT | Recovery MUST reconcile file state before retry. |
| After COMMIT | Recovery treats patch as completed fact. |

Reconciliation rule:

- If postimage hashes match expected patch output, commit existing result.
- If preimage hashes still match and no mutation occurred, retry may be allowed.
- If neither preimage nor postimage matches, surface manual intervention.

`operation_id` MUST bind:

- agent_id;
- tool_call_id;
- patch digest;
- workspace root identity;
- normalized changed paths;
- capability action/resource scope.

MiniCode MUST NOT retry a patch blindly after a crash.

## 8. `run_command` Semantics

MiniCode v0 freezes command execution as synchronous bounded execution.

Required lifecycle:

```text
authorize shell intent
-> host policy decision if needed
-> dispatch subprocess
-> capture stdout/stderr
-> bound model-visible preview
-> externalize large output as ResourceHandle
-> return structured result
```

Timeout:

- default timeout MUST be finite;
- host MAY configure a lower default;
- model-provided timeout MUST be capped;
- timeout returns a structured result, not an exception leak.

Output:

- stdout and stderr previews MUST be bounded;
- full large output MUST be stored through ResourceService when available;
- ResourceHandle is a reference, not permission;
- later reads require `resource.read`/`resource.stat` and ResourceService checks.

Mutation:

| Command class | v0 decision |
| --- | --- |
| Read-only inspection/test | Allow with capability and policy. |
| Local mutation | Require host confirmation; not WAL-safe by default. |
| External/network side effect | Deny by default unless explicitly configured. |
| Long-running/background | DEFERRED. |
| Interactive | DEFERRED. |

MiniCode MUST NOT classify arbitrary shell mutation as exactly-once or
crash-safe in v0.

## 9. Minimal Host Confirmation Policy

MiniCode v0 MUST include a small host policy interface. It is policy, not
Kernel mechanism.

Decision values:

```text
allow
deny
confirm
```

Request fields:

```text
agent_id
process_id
tool_name
action
resource_scope
workspace_root
summary
risk_class
```

Default v0 policy:

| Operation | Default |
| --- | --- |
| `list_files` | allow after capability check |
| `search_files` | allow after capability check |
| `read_file` | allow after capability check |
| `apply_patch` | confirm unless `--yes` or test policy allows |
| `run_command` read-only | allow after capability check |
| `run_command` may mutate | confirm |
| outside workspace | deny |
| missing capability | deny |
| external/network side effect | deny |

The model MUST NOT be able to change policy mode or grant itself authority.

## 10. Tiny ModelAdapter Protocol

MiniCode v0 freezes a minimal model adapter protocol.

Request:

```text
messages
tools
tool_choice?
temperature?
max_output_tokens?
metadata
cancellation
```

Response:

```text
assistant_text
tool_calls[]
finish_reason
usage
raw_diagnostics
```

Tool call:

```text
call_id
name
arguments
```

Usage:

```text
input_tokens?
output_tokens?
total_tokens?
model_cost?
provider_latency_ms?
```

Required adapters:

| Adapter | v0 decision |
| --- | --- |
| ScriptedModelAdapter | MUST implement for deterministic tests. |
| OpenAI-compatible adapter | MUST implement one minimal opt-in real-model adapter. |
| Broad provider framework | DEFERRED. |

Provider retries are policy. They MUST NOT rewrite durable Kernel truth.

## 11. Coding Agent Loop State Machine

MiniCode v0 freezes this application state machine:

```text
NEW_TASK
-> LOAD_WORKSPACE
-> BUILD_CONTEXT
-> MODEL_REQUEST
-> MODEL_RESPONSE
-> TOOL_PROPOSED
-> TOOL_AUTHORIZED
-> TOOL_EXECUTED
-> OBSERVE_RESULT
-> BUILD_CONTEXT
```

Terminal states:

```text
COMPLETED
FAILED
PAUSED
BLOCKED
```

Recovery state:

```text
RECOVERING
-> RECONCILE_REQUIRED | READY_TO_RESUME | MANUAL_REQUIRED
```

Rules:

- MiniCode state is application policy, not durable truth.
- Session events are durable truth.
- Process state is runtime lifecycle.
- Context is projection.
- A model response may propose tools, but ToolRegistry/Capability decides
  whether they are visible/executable.
- If the process is paused/blocked/cancelled at a safe point, MiniCode MUST
  surface that runtime state rather than continuing the loop.

Loop safe points:

- before model call;
- after model response;
- before tool authorization;
- before tool execution;
- after tool result;
- before run_command subprocess dispatch;
- before durable mutation dispatch;
- after recovery analysis.

## 12. Session, New-Task, Resume, and Handoff

New task flow:

```text
CLI request
-> resolve workspace
-> load AGENTS.md instructions
-> create AgentKernel Agent principal
-> create AgentKernel Session
-> create ProcessControlBlock
-> enter MiniCode loop
```

Resume flow:

```text
CLI resume session_id
-> load AgentKernel Session
-> run RecoveryAnalysis
-> reconstruct ProcessControlBlock from recovery
-> rebuild MiniCode app view from Session plus config
-> continue only if recovery state permits
```

Handoff contract:

```text
session_id
agent_id
process_id
workspace_root
task_summary
last_observable_trajectory
pending_recovery_action?
resource_handles[]
changed_files[]
```

Rules:

- MiniCode MUST NOT write a separate replay log.
- MiniCode MAY store non-authoritative UI/config metadata.
- Any state required for correctness MUST be recoverable from AgentKernel
  Session, ResourceStore, WAL/recovery facts, and workspace files.
- Handoff must contain observable runtime facts only.

## 13. Capability Vocabulary

MiniCode v0 freezes this vocabulary.

Tool execution:

```text
tool.execute tool://minicode/list_files
tool.execute tool://minicode/search_files
tool.execute tool://minicode/read_file
tool.execute tool://minicode/apply_patch
tool.execute tool://minicode/run_command
```

Workspace access:

```text
workspace.read  workspace://<workspace-id>/**
workspace.write workspace://<workspace-id>/**
```

ResourceHandle access:

```text
resource.stat artifact://**
resource.read artifact://**
```

Shell:

```text
shell.execute shell://workspace/<workspace-id>/**
```

Optional future reviewer:

```text
workspace.read workspace://<workspace-id>/<selected-path>/**
resource.read  artifact://<selected-resource>
```

Rules:

- Agent remains capability principal.
- Process remains runtime identity.
- Capability grants are supplied by host/user config, not model output.
- Child/reviewer delegation is DEFERRED for v0 implementation.
- ResourceHandle, IPC message, or file path string never grants authority.
- OS sandboxing is external hard containment and must not be confused with
  AgentKernel semantic authorization.

## 14. Context VM Integration

MiniCode v0 MUST use AgentKernel Context VM as the context projection layer.

MiniCode may contribute app inputs:

- user task;
- AGENTS.md instructions;
- workspace summary;
- relevant file snippets;
- recent tool result previews;
- patch summary;
- test output preview;
- current observable trajectory.

MiniCode MUST NOT treat any summary as durable truth.

Large outputs:

```text
full output bytes -> ResourceService
bounded preview -> Context
ResourceHandle -> model-visible reference
```

Deferred:

- new Kernel page types for code snippets;
- semantic retrieval memory;
- persistent project memory;
- V0.9 memory integration.

## 15. Observable Trace and Trajectory Contract

MiniCode v0 MUST produce an observable execution trace. It MUST NOT expose or
require hidden chain-of-thought.

Trace event fields:

```text
seq
time
phase
agent_id
process_id
session_id
turn
step
tool_call_id?
operation_id?
tool_name?
action?
resource_scope?
authorization?
cwd?
command?
exit_code?
duration_ms?
result_preview?
resource_handles[]
changed_files[]
error?
```

Allowed phases:

```text
task/start
workspace/resolved
instructions/loaded
model/request
model/response
tool/proposed
authorization/granted
authorization/denied
tool/prepare
tool/dispatch
tool/result
tool/error
resource/created
process/blocked
process/paused
recovery/analyzed
recovery/reconciled
task/completed
task/failed
```

Trajectory summary MUST be compact and human-readable, for example:

```text
User task
-> workspace resolved
-> model proposed apply_patch
-> Kernel authorized tool.execute + workspace.write
-> WAL prepared patch
-> patch applied
-> tests run
-> Session recorded result
```

Trace must preserve observable facts and must not include API keys,
authorization headers, full hidden prompts, or hidden reasoning.

## 16. CLI Contract

MiniCode v0 CLI contract:

```text
python -m minicode run "task"
python -m minicode resume <session-id>
python -m minicode trace <session-id>
python -m minicode bench
```

Required options:

```text
--workspace PATH
--model PROVIDER_OR_SCRIPT
--max-turns N
--timeout-ms N
--approve never|on-mutation|always
--trace-jsonl PATH
--no-network
```

Exit codes:

| Code | Meaning |
| --- | --- |
| 0 | Completed successfully. |
| 1 | Task failed. |
| 2 | Blocked by host confirmation/policy. |
| 3 | Capability denied. |
| 4 | Recovery/manual intervention required. |
| 5 | Invalid CLI/configuration. |

Defaults:

- `--approve on-mutation`;
- real provider calls opt-in only;
- deterministic scripted mode for tests;
- no network during mandatory CI.

## 17. Deterministic vs Real-Model Testing Boundary

Mandatory CI:

- deterministic;
- offline;
- scripted model adapter;
- temporary workspace fixtures;
- no API keys;
- no network;
- stable assertions over filesystem/session/trace/bench output.

Real-model testing:

- opt-in only;
- explicit provider configuration;
- not a deterministic oracle;
- not mandatory CI;
- may demonstrate tool-call integration and trace shape, not model
  intelligence or production reliability.

MiniCode tests MUST distinguish:

| Test kind | Measures | Does not measure |
| --- | --- | --- |
| Unit tests | parser/path/policy/result contracts | model intelligence |
| IntegrationBench | AgentKernel/MiniCode runtime invariants | production success rate |
| Real-model demo | provider adapter path and observable trace | deterministic correctness |

## 18. IntegrationBench I1-I8 Contracts

IntegrationBench MUST be deterministic and offline unless explicitly marked
real-model optional. It measures integration correctness, not model
intelligence.

| ID | Scenario | Required fixture | Oracle | v0 status |
| --- | --- | --- | --- | --- |
| I1 | Basic edit | One bug file and one targeted patch | final content changed only by `apply_patch`; Session records tool boundary; no outside-workspace write | FROZEN |
| I2 | Test-and-fix loop | Failing test, patch, passing test | run_command captures failing then passing output; large output bounded; loop terminates completed | FROZEN |
| I3 | Crash/resume | Crash after patch prepare or after test output | Session truth survives; recovery does not invent MiniCode state; no blind patch retry | FROZEN |
| I4 | Large stdout ResourceHandle | Command emits large deterministic output | context preview bounded; full bytes in ResourceHandle; selected range can be read with authority | FROZEN |
| I5 | Capability denial | Unauthorized write/shell attempt | model proposal denied; no mutation; trace shows EACCES/denial | FROZEN |
| I6 | Budget exhaustion | Scripted loop exceeds token/tool/resource budget | Process blocks/pauses at safe point; resume/unblock preserves Session truth | FROZEN |
| I7 | Durable mutation recovery | Patch mutation succeeds then crash before commit | recovery reconciles file state; no duplicate mutation; manual required on hash conflict | FROZEN |
| I8 | Reviewer child Agent | Child reviewer reads shared files and cannot write | Agent Tree != Process Tree; IPC != Authority; ResourceShare != Capability | DEFERRED |

I8 remains a frozen future contract, but implementation is DEFERRED because
reviewer/subagent is outside MiniCode v0.

## 19. Phase 0 Open Question Dispositions

| Phase 0 question | Phase 1 disposition | Decision |
| --- | --- | --- |
| Should `search_files` be included as a fifth v0 tool, or merged with `list_files`? | Include it. MiniCode v0 has five tools. | FROZEN |
| Should `apply_patch` be the only v0 write path? | Yes. It is the only direct filesystem write tool. | FROZEN |
| How should MiniCode classify shell commands that may mutate files? | `mutation_intent` plus host policy. Arbitrary shell mutation is not WAL-safe. | FROZEN |
| What is the minimal user confirmation/host policy interface for shell and patch? | `allow`, `deny`, `confirm` with default confirm on mutation. | FROZEN |
| Should project instructions support only `AGENTS.md` in v0? | Yes. Other instruction formats are deferred. | FROZEN |
| What application state, if any, deserves first-class Session events? | None in v0 beyond existing AgentKernel events. App metadata is non-authoritative. | FROZEN |
| How should model adapter usage normalize token/cost fields? | Use optional `input_tokens`, `output_tokens`, `total_tokens`, `model_cost`, `provider_latency_ms`. | FROZEN |
| What exact ResourceService API should shell output capture use? | Store full stdout/stderr bytes with ResourceService-backed ResourceHandle and return bounded preview. Adapter utility may wrap this. | FROZEN |
| Should MiniCode v0 expose child reviewer only behind a benchmark flag, or defer completely? | Defer completely for v0; keep I8 as future contract. | DEFERRED |

## 20. MINICODE_API_FRICTION Dispositions

| Friction from Phase 0 | Disposition | Kernel change? |
| --- | --- | --- |
| Agent/Session/Process setup may require too much Host glue | Build a MiniCode application helper in Phase 2E/F. Do not redesign Kernel. | No |
| Tool registration plus capability setup may be verbose | Build a MiniCode `ToolBundle` helper outside Kernel authority. | No |
| Durable filesystem mutation fixture is low-level | Build MiniCode `apply_patch` durable adapter over existing DurableToolExecutor. | No |
| ResourceHandle for shell output needs a clean app-facing API | Build MiniCode output-capture adapter backed by ResourceService. | No |
| Model usage accounting may differ by provider | Normalize in ModelAdapter usage object and feed AgentKernel accounting when available. | No |
| Recovery API may require app-specific interpretation | Document and implement recovery-to-loop handoff in MiniCode. | No |
| Capability grants for shell are semantically coarse | Keep conservative capabilities plus host policy and external OS sandbox. | No |
| Context VM integration may need page types for code snippets | Use existing context inputs/metadata in v0; defer Kernel page-type improvements. | No |

No `MINICODE_API_FRICTION` item blocks MiniCode v0.

## 21. Phase 2A-2G Implementation Order

Phase 2 MUST proceed in this order.

### Phase 2A - Package Skeleton and Workspace Core

Deliver:

- `minicode/` package skeleton;
- CLI stub;
- workspace root resolver;
- path normalization and escape rejection;
- `AGENTS.md` discovery;
- deterministic workspace fixtures.

Validation:

- path traversal tests;
- git-root/fallback-cwd tests;
- instruction discovery tests.

### Phase 2B - Read-Only Tool Bundle

Deliver:

- `list_files`;
- `search_files`;
- `read_file`;
- schemas and structured errors;
- capability requirements;
- bounded output.

Validation:

- deterministic file listing/search/read tests;
- denied outside-workspace reads;
- tool schema visibility under capability grants.

### Phase 2C - Codex-Style `apply_patch`

Deliver:

- patch grammar/parser;
- hunk verification;
- workspace-safe applier;
- preimage/postimage hash capture;
- model-readable patch errors.

Validation:

- malformed patch has no side effects;
- failed hunk has no side effects;
- patch changes only intended files.

### Phase 2D - Durable Patch WAL Integration

Deliver:

- `apply_patch` as DurableToolExecutor mutation;
- operation_id binding;
- prepare/dispatch/commit trace;
- reconcile after crash.

Validation:

- crash before dispatch;
- crash after mutation before commit;
- hash mismatch manual-required path.

### Phase 2E - Bounded `run_command` and Output Capture

Deliver:

- synchronous subprocess runner;
- timeout and cancellation handling;
- stdout/stderr preview;
- ResourceHandle externalization;
- shell capability and host policy.

Validation:

- passing/failing test command;
- timeout;
- large stdout ResourceHandle;
- mutating shell confirmation/denial.

### Phase 2F - ModelAdapter, Loop, Trace, CLI Resume

Deliver:

- ScriptedModelAdapter;
- minimal OpenAI-compatible adapter;
- coding loop state machine;
- trace/trajectory output;
- new task and resume;
- handoff summary.

Validation:

- scripted end-to-end fix loop;
- resume after interrupted session;
- trace contains observable facts and no secrets.

### Phase 2G - IntegrationBench and Documentation

Deliver:

- IntegrationBench I1-I7 executable offline;
- I8 recorded as deferred contract;
- docs and examples;
- real-model opt-in demo;
- no-network CI boundary.

Validation:

- `pytest -q`;
- deterministic IntegrationBench pass;
- real-model demo skips without opt-in.

## 22. Deferred Items

The following are explicitly outside MiniCode v0:

- reviewer/subagent implementation;
- background jobs;
- interactive shell;
- broad provider/plugin framework;
- multiple workspace roots;
- non-`AGENTS.md` instruction formats;
- namespace/RBAC/IAM redesign;
- V0.9 memory;
- production OS sandbox;
- distributed workers;
- GUI/IDE frontend;
- automatic GitHub PR workflow;
- arbitrary shell side-effect reconciliation.

## 23. Frozen Summary

| Area | Frozen decision |
| --- | --- |
| Runtime ownership | MiniCode is app harness; AgentKernel is runtime authority. |
| Tool set | Five tools: `list_files`, `search_files`, `read_file`, `apply_patch`, `run_command`. |
| Write path | `apply_patch` is the only direct filesystem write tool in v0. |
| Patch grammar | Codex-style `apply_patch`. |
| Workspace | Nearest `.git` root, fallback cwd. |
| Instructions | `AGENTS.md` only in v0. |
| Shell | Synchronous bounded `run_command`. |
| Shell authority | Requires `shell.execute`; arbitrary mutation is not WAL-safe. |
| Large output | Bounded preview plus ResourceHandle. |
| Session/recovery | AgentKernel-owned. |
| WAL | AgentKernel-owned; MiniCode routes durable mutation through it. |
| Capability | Agent-owned semantic grants; model cannot create authority. |
| Context | AgentKernel Context VM; MiniCode formats inputs only. |
| Trace | Observable facts only; no hidden chain-of-thought. |
| CLI | `run`, `resume`, `trace`, `bench`. |
| Testing | Deterministic CI; real-model opt-in only. |
| IntegrationBench | I1-I7 v0 executable, I8 deferred contract. |
| Reviewer/subagent | DEFERRED. |

## 24. Final Decision

`MINICODE_PHASE1_ARCHITECTURE_FROZEN`

MiniCode v0 can proceed to Phase 2 implementation without AgentKernel core
changes. The implementation contract is intentionally small: a five-tool coding
harness, a tiny model adapter, Kernel-owned authority/recovery/context/WAL, and
deterministic IntegrationBench evidence.
