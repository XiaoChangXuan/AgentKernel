# V0.5 Resource Layer Research

## Scope and method

This note records local source evidence examined before implementing AgentKernel
V0.5. It focuses on oversized Tool Results, durable raw-output storage,
model-visible previews, retrieval, restart behavior, ownership, and cleanup. The
reference repositories are evidence, not code donors: V0.5 keeps AgentKernel's
small trusted-spine architecture and copies no implementation wholesale.

Repositories inspected:

- `../codex-main`
- `../gemini-cli-main`
- `../OpenHands-main`
- `../deepseek-harness-master`

## Codex

### Evidence

- `codex-rs/utils/pty/src/lib.rs` defines `DEFAULT_OUTPUT_BYTES_CAP` as 1 MiB.
- `codex-rs/core/src/exec.rs` applies that cap through
  `ExecCapturePolicy::ShellTool`; its comments explicitly describe bounded
  retained stdout/stderr/aggregate output as an OOM defense. Trusted helper
  paths can select `FullBuffer`, but that is not the ordinary shell-tool path.
- `codex-rs/core/src/context_manager/history.rs` normalizes function and custom
  tool outputs with `truncate_function_output_payload` before model history.
- `codex-rs/core/src/tools/context.rs` keeps host execution facts such as
  session/turn context, call identity, cancellation, wall time, and truncation
  policy outside the model-visible tool payload.
- `codex-rs/core/src/tools/handlers/mcp_resource/read_mcp_resource.rs` exposes
  MCP resource reading through an authorized tool handler, but it is specific
  to MCP resources rather than a general retained Tool Result layer.

### Assessment

Codex has strong acquisition and context bounds, and a clean trusted tool
boundary. For ordinary shell output, bytes beyond the capture cap are not a
general durable model-recoverable artifact. A command can deliberately write a
workspace file and later use file tools, but that is an explicit filesystem
workflow, not automatic Kernel-owned output identity. Resume preserves the
recorded rollout/history; it does not recreate raw bytes already discarded by
capture or history truncation. Filesystem and MCP access remain subject to their
own sandbox/authorization boundaries.

AgentKernel should reuse the separation of host invocation metadata from model
projection and the rule that limits are enforced at trusted boundaries. It
should not adopt irreversible truncation as the only handling of large output.

## Gemini CLI

### Evidence

- `packages/core/src/config/config.ts` defines the configurable Tool output
  truncation threshold (`DEFAULT_TRUNCATE_TOOL_OUTPUT_THRESHOLD`, currently
  40,000 in the inspected checkout).
- `packages/core/src/context/truncation.ts` implements proportional head/tail
  retention and adds `Full output saved to: <path>` to normalized function
  responses.
- `packages/core/src/context/toolDistillationService.ts` saves the raw
  stringified content before distillation through `saveTruncatedToolOutput`.
- `packages/core/src/utils/fileUtils.ts` writes the full text beneath
  `<project temp>/tool-outputs[/session-...]/...txt` and creates a first/last
  preview. The locator shown to the model is the actual file path.
- `packages/core/src/config/storage.ts` maps project temporary state beneath a
  stable project identifier in Gemini's global `tmp` area.
- `packages/core/src/tools/read-file.ts` is the ordinary path-based way for the
  model to read saved content again.
- `packages/core/src/services/chatRecordingService.ts` associates saved Tool
  output directories with session artifact cleanup.

### Assessment

Gemini CLI demonstrates the central UX V0.5 needs: preserve full output, keep a
bounded preview in context, and tell the model how to retrieve more. Its local
implementation exposes a host filesystem path and relies on file-tool/workspace
access semantics. The saved file is useful across a process restart while the
project temp tree remains, but the path is not a backend-neutral identity and
the model projection is coupled to local storage layout.

AgentKernel should adopt durable-before-reference storage and deterministic
head/tail previews, while replacing host paths with opaque `artifact://`
handles and routing every read through ResourceService.

## OpenHands local checkout

### Repository boundary

The inspected `../OpenHands-main` checkout identifies itself in `README.md` as
**Agent Canvas**, a TypeScript control center/front end. `AGENTS.md` and
`docs/DEVELOPMENT.md` explicitly assign agents, tools, observations,
conversations, workspaces, events, and the canonical Agent Server API to the
separate `OpenHands/software-agent-sdk` repository. This local checkout can
therefore support control-plane observations only; it is not sufficient source
evidence for the current SDK's Tool output retention internals.

### Evidence and assessment

- The Canvas consumes Agent Server observation events and addresses generated
  artifacts primarily through workspace paths (for example the files/diff
  panels and `tools/canvas_ui_tool.py`).
- Local and container deployment documentation makes workspace isolation and
  persistent mounts explicit.
- No general AgentKernel-like `ResourceStore`/opaque Tool Result handle/read
  service was found in this Canvas checkout.

The useful lesson is architectural: UI/control-plane artifact rendering and
workspace navigation should consume runtime contracts, not define Kernel
storage semantics. V0.5 will not claim behavior of the separate official SDK
that is absent from the inspected local sources.

## DeepSeek Harness

### Evidence

DeepSeek Harness has the closest locally available design:

- `packages/spill/spill/src/types.ts` and its README define `SpillStore` as a
  replaceable storage service. `SaveTextSpill` carries owner and source facts;
  `SpillRef` returns a locator, exact byte count, and retrieval guidance.
- `packages/spill/spill-local/src/store.ts` persists text in private,
  session-grouped files with sanitized names, unpredictable prefixes,
  exclusive creation, and owner-only POSIX permissions.
- `packages/spill/spill-policy` is a `tools/post-execute` transformer. It skips
  inappropriate results, stores oversized plain text, and replaces model
  content with a bounded head/tail preview plus locator notice.
- Preview mechanics are delegated to the separate output-retention utility,
  rather than embedded into the main agent loop.
- The spill README explicitly defers retrieval/deletion APIs and states that a
  storage owner namespace is not read authorization. The local backend exposes
  a real path and tells the model to use `read` with offset/limit or `grep`.
- `packages/core/session/src/surface.ts` derives the current model surface from
  an append-only event log; replacements shadow rather than rewrite the log.
- `packages/session-query` provides bounded search/read/trace over durable
  session events and distinguishes current, shadowed, and log-only surfaces.
- `packages/attachment/attachment-local/src/store.ts` provides a stronger
  content-addressed, atomic-publication pattern for image attachments, but that
  specialized image subsystem is not a general Tool Result resource service.

### Assessment

The split between storage service, local backend, result policy, and preview
utility is directly applicable. Its documented gaps are exactly where
AgentKernel V0.5 must go further: the locator must be opaque and backend-neutral;
reads must pass through a Kernel service; agent/session ownership must be
checked; metadata must survive restart; and bounded range reading must be a
first-class model tool. Search and deletion/GC remain deferred.

## Comparison

| Concern | Codex | Gemini CLI | OpenHands local Canvas | DeepSeek Harness | AgentKernel V0.5 choice |
|---|---|---|---|---|---|
| Huge Tool output | Capture/history caps | Save then distill/truncate | Runtime behavior lives in external SDK | Post-execute spill policy | Replaceable post-result externalization policy |
| Full raw storage | Not guaranteed for capped shell output | Project temp file | Workspace/control-plane artifacts | `SpillStore`, local private files | Durable `ResourceStore` backend |
| Model preview | Bounded normalized output | Proportional head/tail | Observation/workspace UI | Shared head/tail retainer | Shared deterministic head/marker/tail utility |
| Identity | Call/history/file/MCP identities | Host file path | Workspace path/event identity | Opaque type, local implementation is path | Kernel-generated ResourceId and HandleId; `artifact://<resource_id>` |
| Range read | Not a general retained-output API | `read_file` path access | External Agent Server/workspace APIs | `read offset/limit` on exposed path | `resource_read` with hard byte limit |
| Search | File/MCP-specific tools | File search tools | Workspace UI/API | `grep` on exposed path; session-query for events | Deferred beyond V0.5 |
| Persistence | Recorded history; discarded bytes stay discarded | Temp tree can outlive process | Persistent workspace/server mounts | Configured spill root; no lifecycle deletion | Explicit durable local ResourceStore root |
| Cleanup | Feature-specific | Session artifact cleanup exists | Deployment/workspace lifecycle | External cleanup deferred | Orphans retained and identifiable; GC deferred |
| Permission | Sandbox/tool authorization | Filesystem/workspace boundary | Backend/workspace isolation | Private writes, but no read authorization | ResourceService checks owner agent + session |
| Restart | Replays retained history | Saved path works while file survives | Depends on external runtime/workspace | Configured root survives; locator is path | New service instance resolves durable handle metadata |

## V0.5 design decisions

1. **Small extension, not a VFS.** V0.5 supports only durable artifact resources
   addressed by `artifact://<resource_id>`. It adds no mounts, directories,
   rename, delete, glob, search, PDF parsing, remote stores, or distributed
   coordination.
2. **Three identities remain distinct.** Tool call identity describes model
   protocol; operation identity describes durable side effects; ResourceId and
   HandleId describe retained bytes and their model reference.
3. **Projection is not metadata.** The model receives a `ResourceHandle` with
   URI and safe descriptive facts. Store path, owner, source operation, and
   durability metadata remain host-only.
4. **ResourceService is the security and validation boundary.** It creates
   identity, validates URI/offset/limit/size, checks agent and session owner,
   delegates bytes to `ResourceStore`, and records metrics.
5. **Store before reference.** A handle is emitted only after an atomic local
   store commit. Temporary/incomplete objects are ignored. A committed object
   not yet referenced by a Tool Result is retained and can be identified as an
   orphan; V0.5 does not implement GC.
6. **Policy stays outside the loop.** A Tool Result processor between Tool
   invocation and durable Tool Result/commit events decides whether to
   externalize. The default policy is replaceable and excludes bounded resource
   read/stat tools to prevent read-spill-read recursion.
7. **Session stores the handle, never the raw externalized payload.** Context
   projection consequently sees only the durable preview and handle. Context VM
   pruning may still bound that already-small projection but cannot mutate the
   resource bytes.
8. **Text/blob first.** String results are stored verbatim as UTF-8 text; other
   JSON results use deterministic UTF-8 JSON. Model reads are bounded textual
   projections. Media parsing and structured document extraction are future
   work.
9. **Hard limits and metrics are mechanisms.** Maximum resource size and read
   size are enforced. The Kernel counts resources, stored/read bytes,
   externalized results, preview bytes, and estimated model-visible bytes saved;
   deployments choose thresholds.

## Crash contract

| Boundary | Required V0.5 result |
|---|---|
| Crash/failure before resource commit | No final object and no committed handle |
| Resource committed before Tool Result event | Durable orphan remains and is identifiable from owner/source metadata versus committed `tool/result` references |
| Tool Result containing handle committed, then restart | A new ResourceService over the same store can stat/read the handle |
| Context preview/pruning after commit | Stored bytes and checksum remain unchanged |

These decisions intentionally stop at the minimum Resource abstraction required
for V0.5 and leave VFS, search, remote storage, lifecycle deletion, and richer
artifact types to later versions.
