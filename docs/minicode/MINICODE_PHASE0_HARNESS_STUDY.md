# MiniCode Phase 0 Harness Study

Final decision: `MINICODE_PHASE0_HARNESS_STUDY_COMPLETE`

## 1. Goal

This document studies mature local CodeAgent and harness implementations as design input for MiniCode v0 on top of AgentKernel V0.8.

Phase 0 is research only. It does not implement MiniCode, does not modify `agentkernel/`, and does not start V0.9. The goal is to decide what MiniCode should own as an application harness and what must remain AgentKernel runtime authority.

The target MiniCode v0 workload is intentionally small:

```text
minicode "fix the divide-by-zero bug in calculator.py and run tests"
```

The expected loop is:

```text
inspect repository
-> read relevant files
-> patch
-> run tests
-> inspect failure
-> patch again
-> report completion
```

## 2. Reference Repositories Inspected

The study focused on code paths that directly affect a minimal coding harness.

| Repository | Local references inspected | Why it mattered |
| --- | --- | --- |
| Codex | `../codex-main/codex-rs/core/src/tasks/regular.rs`; `../codex-main/codex-rs/core/src/tools/handlers/unified_exec/exec_command.rs`; `../codex-main/codex-rs/core/src/unified_exec/head_tail_buffer.rs`; `../codex-main/codex-rs/core/src/tools/handlers/apply_patch.rs`; `../codex-main/codex-rs/apply-patch/src/parser.rs`; `../codex-main/codex-rs/core/src/agents_md.rs` | Practical turn loop, shell tool boundary, head/tail output retention, mature `apply_patch` grammar, workspace instructions discovery. |
| Gemini CLI | `../gemini-cli-main/packages/core/src/core/turn.ts`; `../gemini-cli-main/packages/core/src/tools/tool-registry.ts`; `../gemini-cli-main/packages/core/src/tools/read-file.ts`; `../gemini-cli-main/packages/core/src/tools/grep.ts`; `../gemini-cli-main/packages/core/src/tools/glob.ts`; `../gemini-cli-main/packages/core/src/tools/edit.ts`; `../gemini-cli-main/packages/core/src/tools/shell.ts`; `../gemini-cli-main/packages/core/src/context/processors/toolMaskingProcessor.ts`; `../gemini-cli-main/packages/core/src/utils/workspaceContext.ts` | Declarative tool registry, defensive path handling, line-ranged reads, search/glob split, edit confirmation, shell timeout, large output masking. |
| DeepSeek Harness | `../deepseek-harness-master/packages/core/agent-loop/src/index.ts`; `../deepseek-harness-master/packages/core/agent-loop/src/tool-calls.ts`; `../deepseek-harness-master/packages/core/tools/src/index.ts`; `../deepseek-harness-master/packages/core/session/src/index.ts`; `../deepseek-harness-master/packages/core/session/src/surface.ts`; `../deepseek-harness-master/packages/fs/tool-fs/src/read.ts`; `../deepseek-harness-master/packages/fs/tool-fs/src/edit.ts`; `../deepseek-harness-master/packages/fs/tool-fs-search/src/grep.ts`; `../deepseek-harness-master/packages/shell/tool-bash/src/index.ts`; `../deepseek-harness-master/packages/compaction/compaction-tool-result-pruner/src/index.ts`; `../deepseek-harness-master/packages/subagent/subagent/src/index.ts` | Strong separation of session, surface, tools, filesystem backends, bash policy, deterministic tool-result pruning, subagent runtime direction. |
| OpenHands | `../OpenHands-main/src/hooks/query/use-workspace-files.ts`; `../OpenHands-main/src/hooks/query/use-workspace-file-content.ts`; `../OpenHands-main/src/components/features/chat/tool-visualizers/bash/bash.tsx`; `../OpenHands-main/src/components/features/chat/tool-visualizers/file-editor/file-editor.tsx` | Workspace/file UX and tool result visualization patterns, useful for later MiniCode UX but not core v0 runtime. |

Other sibling repositories were noted but not deeply used because the above projects supplied the closest code-level evidence for the MiniCode v0 questions.

## 3. Research Method

The study was problem-driven rather than repository-driven. For each MiniCode concern, it searched local source for the running path: loop execution, tool registration, filesystem reads, patch/edit application, shell execution, output truncation/masking, workspace root handling, session/resume, permission/sandbox hooks, and subagent wiring.

The conclusion is not that one reference is "best". The conclusion is which patterns are useful when AgentKernel remains the runtime kernel and MiniCode is only an application harness.

## 4. Problem-by-Problem Findings

### Agent Loop

Codex keeps the regular task focused on session turn execution and repeats while pending input exists. Gemini CLI organizes each turn around model output, tool calls, and tool responses. DeepSeek Harness creates/resumes session-backed agents and separates agent loop, session, and tool-call scheduling.

MiniCode should implement a small coding loop, but it should not become a second durable runtime. Its loop should prepare prompts, invoke a model adapter, normalize tool calls, submit them through AgentKernel tool boundaries, and decide when the coding task is complete.

Decision: ADAPT. MiniCode owns application turn policy; AgentKernel owns Session, Tool boundary, Process, Recovery, Context VM, Capability, WAL, IPC, and ResourceShare.

### Model Adapter

The references expose provider-specific complexity, but MiniCode v0 does not need a provider framework. It needs the smallest possible protocol:

```text
generate(messages, tools, *, budget, cancellation) -> model response
```

The response must expose assistant text, structured tool calls, finish reason, usage if available, and raw provider diagnostics for debugging. Provider retries are policy, not Kernel truth.

Decision: ADOPT a tiny protocol, DEFER broad provider abstraction.

### Tool Schema

Gemini CLI's tool registry and Codex's tool handlers show the value of model-visible schema separated from execution. DeepSeek Harness also separates tool definition, execution, and presentation.

MiniCode should define application tools as stable names with structured arguments and model-facing descriptions, but execution must pass through AgentKernel's `ToolRegistry` and capability enforcement when available.

Decision: ADAPT. MiniCode may own tool UX and argument normalization; AgentKernel remains authority for authorization and execution boundary events.

### Filesystem Reads

Gemini CLI and DeepSeek Harness both treat path validation, workspace-relative paths, line windows, binary/encoding handling, and truncation as first-class. DeepSeek's read tool also makes observed file state visible for safer edits.

MiniCode v0 should implement `read_file(path, start_line?, end_line?)` or equivalent and return line-numbered UTF-8 text with explicit truncation markers. It should reject paths outside the workspace before model-visible output is produced.

Decision: ADOPT line-ranged reads and defensive path checks; ADAPT observed-version tracking later if MiniCode edits need stale-write protection.

### List and Search Files

The minimal four-tool hypothesis includes `list_files` but not `search_files`. Gemini CLI and DeepSeek Harness both include search/grep as a core coding primitive. In practice, a code agent without search is forced to misuse shell for repository discovery.

MiniCode v0 can still keep a small catalog, but `search_files` is strongly recommended. If strict v0 must remain four tools, then `run_command` must temporarily cover `rg`-style search, with the downside that shell output policy and path safety become harder.

Decision: ADAPT. Recommended v0 tool set has five tools: `list_files`, `search_files`, `read_file`, `apply_patch`, `run_command`.

### Patch / Edit

Codex's `apply_patch` path is the strongest reference. It uses a compact grammar, parser verification, and execution after validation. Gemini and DeepSeek support editing tools, but a patch grammar is simpler to make auditable and durable in AgentKernel.

MiniCode should adopt a Codex-style `apply_patch` grammar instead of inventing a bespoke edit-block language. Patch parsing should happen before mutation; failed parse or failed hunk matching should be returned as a model-readable tool error.

Decision: ADOPT Codex-style `apply_patch` grammar at the MiniCode application level and route actual filesystem mutation through AgentKernel durable mutation/WAL boundaries where available.

### Shell / Run Command

Codex and Gemini both show shell execution must define cwd, timeout, exit code, stdout/stderr, environment, cancellation, and long-running behavior. DeepSeek's bash tool includes timeout, workdir, sandbox policy, and background execution hooks.

MiniCode v0 should keep `run_command` synchronous and bounded. It should require a workspace-relative or explicit cwd under the workspace, enforce a default timeout with a cap, return exit code plus bounded stdout/stderr preview, and expose cancellation through AgentKernel Process safe points.

Decision: ADAPT. Synchronous bounded shell first; DEFER background jobs.

### Large Output

Codex uses head/tail retention for command output. Gemini masks large tool output to files. DeepSeek has deterministic tool-result pruning. AgentKernel V0.5 already provides ResourceHandle and V0.4 provides Context VM separation.

MiniCode should return a bounded preview to the model and store complete large outputs in AgentKernel ResourceStore as a `ResourceHandle`. The handle is not permission; access still goes through Capability/ResourceService checks.

Decision: ADAPT external head/tail and masking patterns into AgentKernel ResourceHandle semantics.

### Workspace and Repository Context

Codex's `AGENTS.md` discovery and Gemini's workspace context show that repo root and instructions discovery matter. MiniCode v0 should find the nearest `.git` root from cwd, fallback to cwd when no git root exists, and discover `AGENTS.md` from root to cwd. It should not support every instruction format initially.

Decision: ADOPT nearest git root fallback; ADOPT `AGENTS.md` as the first instruction format; DEFER `CLAUDE.md`, `.cursor`, IDE metadata, and package-specific instruction synthesis.

### Session / Resume

DeepSeek and Codex both have session concepts in their harnesses. For MiniCode, this is exactly where AgentKernel must stay authoritative. MiniCode should not write its own event log, replay logic, or recovery semantics.

Decision: REJECT a MiniCode-owned durable session. MiniCode consumes AgentKernel Session and Recovery APIs.

### Permission / Sandbox

Codex and Gemini both combine tool permissions with OS/sandbox constraints. AgentKernel V0.8 supplies semantic Capability, delegation, ResourceShare, IPC, Process and integrated recovery invariants, but it is not a production OS sandbox.

MiniCode should combine semantic Capability with external/OS sandboxing where available. It must not claim Capability alone prevents arbitrary OS effects from a shell command.

Decision: ADAPT. Semantic authority belongs to AgentKernel; OS sandbox belongs to external execution environment.

### Durable Side Effects

For a coding harness, read-only operations are not durable mutations. `apply_patch`, file writes, some git operations, and external API calls are durable mutations. Test commands are usually ephemeral but may create local artifacts.

MiniCode should route file mutation through AgentKernel Durable Tool WAL where the mutation must be crash-safe. Shell is trickier: v0 should classify commands conservatively and may require host policy or explicit capability for write-capable shell use.

Decision: ADAPT. WAL for durable mutations; DEFER full shell side-effect classification.

### Context

The references prune, summarize, or mask tool output. AgentKernel already defines the stronger invariant: Session durable truth is not Context. MiniCode should format task prompt, instructions, current working set, and tool result previews, but Context VM should own projection/reclaim mechanisms.

Decision: REJECT a second MiniCode context manager; ADAPT formatting into AgentKernel Context VM pages.

### Subagent / Reviewer

DeepSeek's subagent runtime is the most relevant reference. AgentKernel V0.8 already distinguishes Agent Tree, Process Tree, IPC, ResourceShare, and Capability delegation. A future MiniCode reviewer should be a child Agent with read-only capability, explicit shared resources, IPC communication, and no write authority by default.

Decision: DEFER implementation. Direction is compatible with V0.8.

## 5. ADOPT / ADAPT / REJECT / DEFER Matrix

| Problem | Reference implementation(s) | Observed pattern | Decision | MiniCode direction | Reason |
| --- | --- | --- | --- | --- | --- |
| Agent loop | Codex `regular.rs`; Gemini `turn.ts`; DeepSeek `agent-loop` | Loop manages model/tool cycles and termination | ADAPT | MiniCode owns coding policy loop, not durable runtime | AgentKernel already owns durable runtime mechanisms. |
| Model adapter | Gemini/Codex provider paths | Provider complexity grows quickly | ADOPT | Minimal `generate(messages, tools, budget, cancellation)` protocol | Keeps v0 small and testable. |
| Tool schema | Gemini `tool-registry.ts`; DeepSeek `tools` | Declarative schema separate from execution | ADAPT | Register MiniCode tools through AgentKernel tool boundary | Model proposal must not become authority. |
| Read file | Gemini `read-file.ts`; DeepSeek `tool-fs/src/read.ts` | Path validation, line windows, truncation | ADOPT | `read_file` with ranges, line numbers, binary/large handling | Essential for coding tasks. |
| Search files | Gemini `grep.ts`/`glob.ts`; DeepSeek `tool-fs-search/src/grep.ts` | Search is core discovery, not luxury | ADAPT | Add `search_files` unless strict v0 forces shell fallback | Prevents shell misuse and reduces context waste. |
| Patch/edit | Codex `apply_patch` parser/handler | Compact verified patch grammar | ADOPT | Codex-style `apply_patch` grammar | Mature, auditable, and small. |
| Shell | Codex `exec_command.rs`; Gemini `shell.ts`; DeepSeek `tool-bash` | cwd, timeout, exit code, stdout/stderr, policy | ADAPT | Bounded synchronous `run_command` first | Background jobs and approvals can wait. |
| Large output | Codex `head_tail_buffer.rs`; Gemini `toolMaskingProcessor.ts`; DeepSeek pruner | Preview plus retained full output | ADAPT | Bounded preview plus ResourceHandle | Matches AgentKernel Resource != Context invariant. |
| Workspace discovery | Codex `agents_md.rs`; Gemini `workspaceContext.ts` | Discover root and scoped instructions | ADOPT | Nearest `.git`, fallback cwd, `AGENTS.md` root-to-cwd | Small and useful. |
| Tool errors | Codex/Gemini/DeepSeek tool result patterns | Model-readable structured failures | ADOPT | Error type, message, retryable flag, optional diagnostics handle | Makes self-correction possible. |
| Session/resume | DeepSeek session; Codex session task | Harnesses often own session | REJECT | Do not create MiniCode session log | AgentKernel Session is durable truth. |
| Durable mutations | Codex patch hooks; AgentKernel WAL | Mutations need audit/recovery | ADAPT | `apply_patch` and file writes use WAL boundary | Crash-safe coding edits matter. |
| Permissions | Codex/Gemini sandbox/approval; AgentKernel Capability | OS sandbox and semantic authority differ | ADAPT | Capability for semantic authorization; external sandbox for OS isolation | Avoid false security claims. |
| Budget/cancel | Gemini shell abort; AgentKernel Process | Runtime cancellation and budgets at safe points | ADAPT | Expose budget/cancel from AgentKernel Process to MiniCode loop and tools | Keeps Process semantics intact. |
| Subagent/reviewer | DeepSeek subagent; AgentKernel V0.8 | Child agent with explicit channels | DEFER | Future read-only reviewer child Agent | Not needed for minimal v0. |
| Testing | Existing AgentKernel RuntimeBench | Deterministic offline evidence | ADAPT | Add MiniCode IntegrationBench I1-I8 later | Measures application integration, not model intelligence. |

## 6. Recommended MiniCode Architecture

Recommended architecture:

```text
User
  |
  v
MiniCode CLI
  |
  v
Coding Agent / Policy
  |
  v
Model Adapter
  |
  v
AgentKernel Runtime
  |
  v
MiniCode Tool Drivers
  |-- filesystem read/list/search
  |-- patch mutation
  `-- shell command
       |
       v
External filesystem / subprocess / OS sandbox
```

MiniCode should be an application layer consuming AgentKernel V0.8. It should not copy the runtime ownership style of harnesses that combine session, replay, context, tools, and permissions in one application framework.

Rejected alternatives:

| Alternative | Decision | Reason |
| --- | --- | --- |
| MiniCode owns its own JSON session log | REJECT | Conflicts with AgentKernel Session durable truth. |
| MiniCode builds a separate capability layer | REJECT | Weakens `Model proposal != Kernel authority`. |
| MiniCode starts as a broad provider/plugin framework | REJECT for v0 | Not needed for minimal coding loop. |
| MiniCode shells out for all filesystem operations | REJECT | Makes path safety, context control, and WAL integration worse. |
| MiniCode requires subagents in v0 | DEFER | Useful reviewer pattern, not necessary for basic edit/test loop. |

## 7. Ownership Boundary

| Concern | MiniCode | AgentKernel | External/OS |
| --- | --- | --- | --- |
| CLI | Owns | None | Shell invokes CLI |
| Coding prompt and completion policy | Owns | None | None |
| Model choice and adapter | Owns minimal protocol | Observes usage if integrated | Provider API |
| Agent identity | Creates/chooses inputs | Owns Agent authority | None |
| Process runtime | Requests process creation/use | Owns Process, Scheduler, Accounting | None |
| Session | Consumes | Owns durable event log and recovery | Storage backend |
| WAL | Marks durable mutation intent | Owns prepare/dispatch/commit semantics | External side effect |
| Context | Formats app inputs | Owns Context VM projection/reclaim | None |
| Resource storage | Requests store/read | Owns ResourceService authority and handles | Filesystem/object backend |
| Capability authorization | Supplies intended grants/config | Owns evaluation/enforcement/delegation | OS sandbox may add hard limits |
| Workspace discovery | Owns root and instruction discovery | None | Filesystem/git |
| Filesystem read/list/search tools | Owns tool UX and path mapping | Authorizes Resource/workspace access if integrated | Filesystem |
| Patch generation/parsing | Owns parser integration and feedback | WAL/Tool boundary for mutation | Filesystem |
| Shell process | Owns command UX and output shaping | Process/budget/cancel safe points, capability boundary | Subprocess/OS sandbox |
| IPC/ResourceShare | Requests future reviewer flows | Owns IPC and share semantics | None |
| Integrated recovery | Consumes recovery result | Owns invariant checks | Storage backend |

## 8. Minimal Tool Set Recommendation

Recommended MiniCode v0 tools:

| Tool | Include in v0? | Notes |
| --- | --- | --- |
| `list_files` | Yes | Small directory/repo discovery; output bounded. |
| `search_files` | Strong yes | Reference evidence shows grep/search is a core coding primitive. |
| `read_file` | Yes | UTF-8 text, line numbers, line range, truncation marker. |
| `apply_patch` | Yes | Verified patch grammar; durable mutation path. |
| `run_command` | Yes | Bounded shell/test command execution. |

If v0 must remain exactly four tools, merge `list_files` and `search_files` under one `inspect_files` tool or defer `search_files` while allowing `run_command` for `rg`. The second option is less clean because it pushes discovery into shell policy.

## 9. Model Adapter Recommendation

MiniCode v0 should define a tiny model protocol:

```text
ModelRequest:
  messages
  tools
  temperature/options
  token_budget
  cancellation

ModelResponse:
  assistant_text
  tool_calls[]
  finish_reason
  usage
  raw_diagnostics
```

The adapter should not own retries that alter durable truth. Retry policy is host policy. Usage should flow into AgentKernel accounting when available.

## 10. Workspace / Filesystem Design

Recommended workspace rules:

1. Start from CLI cwd.
2. Find nearest `.git` root.
3. If no `.git` root exists, use cwd as workspace root.
4. Resolve all model-provided paths relative to workspace root or current task cwd.
5. Normalize paths and reject traversal outside workspace before reading or mutating.
6. Discover `AGENTS.md` from root to cwd as v0 project instructions.
7. Return line-numbered file content with explicit truncation.
8. Use ResourceHandle for large file or search outputs.

Deferred:

| Feature | Reason to defer |
| --- | --- |
| Multiple workspace roots | Adds path and capability complexity. |
| All instruction file formats | Useful later but not required for v0. |
| Binary file editing | MiniCode v0 is source/text oriented. |
| Stale observed-version edit guard | Valuable, but can follow initial patch path. |

## 11. Patch Design

MiniCode should adopt a Codex-style `apply_patch` format:

```text
*** Begin Patch
*** Update File: path/to/file.py
@@
-old line
+new line
*** End Patch
```

Design requirements:

| Requirement | Direction |
| --- | --- |
| Parse before mutate | Reject malformed patch without side effects. |
| Path normalization | Resolve paths inside workspace only. |
| Hunk verification | Failed hunk returns model-readable error. |
| Durable mutation | Patch application should enter WAL boundary when MiniCode integrates with AgentKernel Durable Tool. |
| Output | Return changed files, hunk count, and concise diff summary. |

Do not invent a complex edit-block language in v0.

## 12. Shell Design

Recommended `run_command` parameters:

```text
command: string
cwd?: string
timeout_ms?: int
```

Recommended result:

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

1. Default cwd is workspace root or current task cwd.
2. Relative cwd is resolved under workspace root.
3. Timeout has a default and maximum cap.
4. On timeout, terminate the process group where possible and return a structured timeout result.
5. Return output as head/tail preview plus ResourceHandle when large.
6. Treat cancellation as runtime control, not rollback.
7. Do not claim semantic Capability is an OS sandbox.

Deferred:

| Feature | Reason |
| --- | --- |
| Background jobs | Requires lifecycle and output polling UX. |
| Interactive shell | Hard to make deterministic and safe. |
| Full command intent classifier | Risky and policy-heavy for v0. |

## 13. Large-Output Design

Large output should follow this pattern:

```text
Tool execution
  -> full bytes stored as Resource
  -> bounded preview returned to model
  -> ResourceHandle recorded for later explicit reads
```

This adapts:

- Codex head/tail retention.
- Gemini output masking to files.
- DeepSeek deterministic pruning.
- AgentKernel ResourceHandle and Context VM invariants.

The model-visible preview must include:

| Field | Purpose |
| --- | --- |
| total bytes | Shows scale. |
| total lines when text | Helps navigation. |
| truncation marker | Prevents false belief that output is complete. |
| resource handle | Enables explicit later read. |
| read hint | Tells model how to fetch a range if needed. |

## 14. Session / Recovery Integration

MiniCode should not implement:

- Session persistence.
- Event replay.
- Recovery analysis.
- Integrated multi-agent recovery.

MiniCode should:

1. Create or resume an AgentKernel Session.
2. Append semantic task/tool events through AgentKernel APIs.
3. Use AgentKernel Recovery result after crash.
4. Reconstruct application UI/loop state from Kernel truth plus recoverable app configuration.
5. Treat Context as projection, not truth.

Potential friction: application-level loop state such as "current coding plan" may need either explicit Session events or a documented app metadata channel. Do not solve this in Phase 0.

## 15. WAL Integration

MiniCode should classify tools:

| Operation | WAL direction |
| --- | --- |
| `list_files` | No WAL; read-only. |
| `search_files` | No WAL; read-only. |
| `read_file` | No WAL; read-only. |
| `apply_patch` | WAL prepare/dispatch/commit around filesystem mutation. |
| `run_command` read-only test command | Usually no WAL, but still evented. |
| `run_command` with durable mutation | Host policy should either deny, require explicit authorization, or route through durable mutation path. |
| external API call | WAL required. |

The reference harnesses often treat shell as a broad capability. MiniCode on AgentKernel should be more explicit: shell is a tool boundary, not proof that side effects are safe or recoverable.

## 16. Capability Model

MiniCode should request or configure capabilities such as:

```text
workspace.read  artifact://workspace/**
workspace.write artifact://workspace/**
tool.execute    tool://minicode/read_file
tool.execute    tool://minicode/apply_patch
tool.execute    tool://minicode/run_command
shell.execute   shell://workspace/**
```

Directions:

1. Agent remains the authority principal.
2. Process remains runtime identity.
3. ResourceHandle remains data reference, not permission.
4. IPC payload remains data, not authority.
5. Child reviewer gets narrowed read-only grants.
6. OS sandbox remains external hard containment, not AgentKernel semantic authorization.

MiniCode must never let the LLM create or enlarge its own capability grants.

## 17. Context Integration

MiniCode should provide app-specific context inputs:

- User task.
- Project instructions.
- Workspace summary.
- Relevant file snippets.
- Recent tool result previews.
- Current plan/status if represented as durable/app state.

AgentKernel Context VM should decide projection and bounded working set. MiniCode should not store a parallel "truth" transcript or make summary the source of truth.

Large tool outputs should become ResourceHandles and bounded previews, not permanent full-context blobs.

## 18. Subagent Direction

Future MiniCode Reviewer direction:

```text
Coder Agent
  -> creates Reviewer child Agent
  -> delegates read-only capabilities
  -> shares selected resources explicitly
  -> communicates via Kernel IPC
  -> receives review findings
  -> Coder applies final patch
```

Reviewer non-authorities:

- No implicit write access.
- No capability from Process lineage.
- No resource access from URI alone.
- No authority from IPC message content alone.

This is compatible with AgentKernel V0.8, but implementation is DEFERRED.

## 19. MINICODE_API_FRICTION

| Severity | Friction | Why it matters | Possible direction |
| --- | --- | --- | --- |
| Medium | Agent/Session/Process setup may require too much Host glue | MiniCode wants a simple `start_task` path while preserving explicit Kernel objects | Add an application helper later, not a Kernel redesign. |
| Medium | Tool registration plus capability setup may be verbose | Five MiniCode tools require schema, authorization, execution, and legacy compatibility wiring | Provide a documented ToolBundle helper outside core authority. |
| Medium | Durable filesystem mutation fixture is low-level | `apply_patch` wants a clean WAL mutation path | Add MiniCode adapter utilities over existing DurableToolExecutor. |
| Medium | ResourceHandle for shell output needs a clean app-facing API | Large stdout/stderr should not leak full text into context | Provide a MiniCode output-capture adapter backed by ResourceService. |
| Low | Model usage accounting may differ by provider | Budget enforcement needs normalized usage | Model adapter should map usage into AgentKernel UsageCollector. |
| Low | Recovery API may require app-specific interpretation | MiniCode must resume task UX without inventing durable truth | Document recovery-to-loop handoff. |
| Low | Capability grants for shell are semantically coarse | Shell can perform many side effects | Keep v0 conservative; combine with OS sandbox/host policy. |
| Low | Context VM integration may need page types for code snippets | Coding context benefits from file/snippet semantics | Add app-level page metadata later if needed. |

No Kernel architecture blocker was found.

## 20. IntegrationBench Proposal

| Scenario | Application behavior tested | AgentKernel invariant exercised |
| --- | --- | --- |
| I1 basic edit | Read a file, apply one patch, verify final content | Tool boundary, WAL for mutation, Resource != Context. |
| I2 test-and-fix loop | Run failing tests, patch, rerun passing tests | Shell output preview, Process safe points, Context boundedness. |
| I3 crash / resume | Crash after patch prepare or after test output, resume | Session truth, Recovery != retry, WAL reconciliation. |
| I4 large stdout via ResourceHandle | Command emits large output and model reads selected range | Context != Resource, ResourceHandle != Permission. |
| I5 capability denial | Agent attempts write outside workspace or unauthorized shell | Model proposal != Kernel authority, Capability enforcement. |
| I6 budget exhaustion | Long loop hits token/tool/resource budget then pauses | Budget exceeded != failure, Process runtime accounting. |
| I7 durable mutation crash / recovery | Patch/external mutation succeeds then process crashes | Durable facts cannot be deleted; no duplicate side effect. |
| I8 optional reviewer child Agent | Reviewer reads shared files and reports findings without write access | Agent Tree != Process Tree, ResourceShare != Capability, IPC != Authority. |

MiniCode IntegrationBench should be deterministic and offline. It should measure integration correctness, not model intelligence.

## 21. Non-Goals

MiniCode v0 should not attempt:

- Full IDE.
- GUI.
- Browser agent.
- Production OS sandbox.
- Broad multi-provider framework.
- Persistent memory or V0.9 memory.
- Distributed execution.
- Remote workers.
- Full GitHub automation.
- Plugin marketplace.
- Large subagent swarm.
- Autonomous long-running daemon.
- General-purpose workflow engine.

## 22. Open Questions

Questions for MiniCode Architecture Freeze:

1. Should `search_files` be included as a fifth v0 tool, or merged with `list_files`?
2. Should `apply_patch` be the only v0 write path?
3. How should MiniCode classify shell commands that may mutate files?
4. What is the minimal user confirmation/host policy interface for shell and patch?
5. Should project instructions support only `AGENTS.md` in v0?
6. What application state, if any, deserves first-class Session events?
7. How should model adapter usage normalize token/cost fields?
8. What exact ResourceService API should shell output capture use?
9. Should MiniCode v0 expose child reviewer only behind a benchmark flag, or defer completely?

## 23. Architecture Freeze Recommendations

MiniCode Architecture Freeze should lock the following before implementation:

| Area | Recommendation |
| --- | --- |
| Runtime ownership | MiniCode is application layer; AgentKernel is runtime authority. |
| Tool set | Prefer five tools: `list_files`, `search_files`, `read_file`, `apply_patch`, `run_command`. |
| Patch | Adopt Codex-style `apply_patch`. |
| Shell | Bounded synchronous shell only in v0. |
| Workspace | Nearest `.git`, fallback cwd, `AGENTS.md` root-to-cwd discovery. |
| Large output | Bounded preview plus ResourceHandle. |
| Session/recovery | Consume AgentKernel Session/Recovery, no MiniCode event log. |
| WAL | Use for durable mutations, especially patch/file write. |
| Capability | Agent-owned semantic grants; no model-created authority. |
| Context | App formatting only; AgentKernel Context VM owns projection. |
| Subagent | DEFER; future reviewer is read-only child Agent with explicit IPC/share/delegation. |

## 24. Final Decision

`MINICODE_PHASE0_HARNESS_STUDY_COMPLETE`

AgentKernel V0.8 appears capable of supporting MiniCode v0 without Kernel architecture redesign.

This phase did not modify `agentkernel/`, did not implement MiniCode, and did not start V0.9.
