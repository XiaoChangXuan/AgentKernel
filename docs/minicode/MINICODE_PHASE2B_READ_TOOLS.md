# MiniCode Phase 2B Read-Only Tools

Decision: `MINICODE_PHASE2B_READ_TOOLS_COMPLETE`

## Scope

Phase 2B implements the read-only MiniCode tool bundle:

- `list_files`
- `search_files`
- `read_file`

It does not implement `apply_patch`, `run_command`, WAL mutation, shell
execution, the model loop, Context VM policy, or V0.9 memory.

AgentKernel remains the runtime authority. MiniCode defines the application
tool contracts and workspace semantics, while AgentKernel `ToolRegistry` owns
model-visible tool projection and the execution authorization boundary.

## ToolRegistry Integration

MiniCode exposes `read_only_tool_definitions(workspace)` and
`register_read_only_tools(registry, workspace)`.

Each tool is registered as an AgentKernel `ToolDefinition` with:

| Tool | Tool capability |
| --- | --- |
| `list_files` | `tool.execute tool://minicode/list_files` |
| `search_files` | `tool.execute tool://minicode/search_files` |
| `read_file` | `tool.execute tool://minicode/read_file` |

Each handler also checks workspace read authority through the
AgentKernel-provided `CapabilityEvaluator`:

```text
workspace.read workspace://<workspace-id>/**
```

This keeps the model unable to enlarge grants. A missing `tool.execute` grant
hides the tool from `ToolRegistry.model_schemas()` and denies direct execution.
A missing `workspace.read` grant denies handler execution with `EACCES`.

## Common Result Contract

Successful MiniCode application results use:

```json
{
  "ok": true
}
```

Structured application errors use:

```json
{
  "ok": false,
  "error": {
    "code": "path_not_found",
    "message": "...",
    "retryable": false
  }
}
```

Authorization failures at the Kernel tool boundary are AgentKernel
`ToolResult` failures, usually `EACCES`.

All tool outputs are JSON-safe and include minimal metadata suitable for later
observable trace wiring.

## `list_files`

Input:

```text
path?: string = "."
recursive?: bool = false
max_entries?: int = 100
include_hidden?: bool = false
```

Hard maximum: `1000` entries.

Result fields:

```text
root
path
entries[]
truncated
entry_count
metadata
```

Entry fields:

```text
path
type
size?   # files only
```

Semantics:

- path resolution uses Phase 2A workspace containment;
- returned paths are workspace-relative POSIX-style paths;
- non-recursive mode lists immediate children only;
- recursive mode walks descendants and sorts deterministically;
- no shell or git command is used;
- symlinks are displayed as `type="symlink"` and not followed for size/type;
- default hidden behavior excludes dot paths and common generated/cache
  directories:
  `.git`, `.mypy_cache`, `.pytest_cache`, `.ruff_cache`, `__pycache__`,
  `build`, `dist`, `node_modules`;
- `include_hidden=true` disables that default display filter, still under
  `max_entries`.

## `search_files`

Input:

```text
query: string
path?: string = "."
glob?: string
case_sensitive?: bool = false
max_matches?: int = 50
context_lines?: int = 0
```

Hard maximums:

- `max_matches`: `500`
- `context_lines`: `5`

Search semantics:

- literal substring search, not regex;
- case-insensitive by default using `casefold()`;
- deterministic file order by workspace-relative path;
- Python implementation, no subprocess or `rg` dependency;
- hidden/generated/cache paths use the same default filter as `list_files`;
- `glob` matches either the workspace-relative path or basename;
- UTF-8 text files are searched;
- binary and unsupported-encoding files are skipped and reported in
  `skipped_files`;
- each match contains file path, 1-based line number, bounded line text, and
  optional bounded context lines.

Result fields:

```text
searched_path
query
literal
case_sensitive
glob
matches[]
truncated
match_count
scanned_files
skipped_files
metadata
```

## `read_file`

Input:

```text
path: string
start_line?: int
end_line?: int
max_bytes?: int = 32000
```

Hard maximum: `64000` model-visible bytes.

Line contract:

- line numbers are 1-based;
- `start_line` and `end_line` are inclusive;
- omitted range reads the whole file;
- invalid ranges return structured `invalid_range`;
- `max_bytes` applies after line selection and line-number formatting;
- returned `end_line` is the last line included in the bounded preview;
- `requested_end_line` records the requested inclusive end.

Read semantics:

- file bytes are hashed before decoding;
- `sha256` is over exact file bytes;
- UTF-8 is the only v0 text encoding;
- binary files return structured `binary_file`;
- invalid UTF-8 returns structured `unsupported_encoding`;
- directories return structured `is_directory`;
- content is line-numbered for model use.

## Error Codes

Current MiniCode application error codes:

| Code | Meaning |
| --- | --- |
| `invalid_argument` | Input does not match the small JSON contract. |
| `outside_workspace` | Path normalization escaped the workspace root. |
| `path_not_found` | Required path does not exist. |
| `not_directory` | `list_files` target is not a directory. |
| `invalid_path` | Search target is neither file nor directory. |
| `invalid_range` | `read_file` line range is invalid. |
| `is_directory` | `read_file` target is a directory. |
| `binary_file` | `read_file` target appears binary. |
| `unsupported_encoding` | File cannot be decoded as UTF-8. |

## Implementation Choices

Phase 2B prioritizes deterministic portable correctness over performance:

- no shell dependency;
- no git dependency for listing/searching;
- bounded model-visible output only;
- no ResourceHandle creation yet;
- no DurableToolExecutor usage.

## Implementation Friction

`MINICODE_IMPLEMENTATION_FRICTION: read/search large-output ResourceHandle deferred to Phase 2E adapter`

The current read/search tools return bounded previews and explicit
`truncated=true` metadata. Full large-output capture belongs with the Phase 2E
output-capture adapter so read/search and shell output share one ResourceHandle
path.

## Non-Goals

Phase 2B does not implement:

- `apply_patch`
- patch grammar or filesystem mutation
- WAL prepare/dispatch/commit/reconcile
- `run_command`
- subprocess execution
- shell timeout handling
- ResourceHandle output capture
- ScriptedModelAdapter
- OpenAI-compatible adapter
- coding loop
- Context VM coding policy
- V0.9 memory

