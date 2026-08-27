# MiniCode Phase 2C Apply Patch Core

Decision: `MINICODE_PHASE2C_APPLY_PATCH_CORE_COMPLETE`

## Scope

Phase 2C implements MiniCode's only direct filesystem write primitive:

```text
apply_patch
```

This phase adds a small Python-native Codex-style patch parser, a
workspace-safe applier, preimage/postimage hashes, structured patch errors, and
AgentKernel `ToolRegistry` integration.

Phase 2C does not implement Durable Tool WAL integration. Phase 2D owns
`TOOL_PREPARE`, `TOOL_DISPATCH`, `TOOL_COMMIT`, `operation_id` reconciliation,
and crash recovery for patch mutation.

## Supported Grammar Subset

MiniCode v0 supports this explicit subset:

```text
*** Begin Patch
*** Add File: path
+line
*** Update File: path
@@
-old line
+new line
*** Delete File: path
*** End Patch
```

Supported operations:

| Operation | Behavior |
| --- | --- |
| `*** Add File: path` | Creates a new UTF-8 text file. Existing targets are denied. |
| `*** Update File: path` | Applies one or more exact-match hunks to an existing UTF-8 text file. |
| `*** Delete File: path` | Deletes an existing UTF-8 text file. Directories and binary files are denied. |

Supported hunk lines:

| Prefix | Meaning |
| --- | --- |
| space | Context line, preserved. |
| `-` | Expected old line, removed. |
| `+` | New line, inserted. |

The parser rejects unsupported directives such as `*** Move to:` and
`*** End of File` in Phase 2C. Move support is intentionally deferred.

## Parsing Model

The full patch is parsed before any filesystem mutation occurs.

Malformed patches return structured errors such as:

- `invalid_patch`
- `unsupported_patch_operation`
- `malformed_hunk`

Parser tests cover valid add/update/delete patches, multi-file patches,
multiple hunks, malformed begin/end markers, unsupported directives, missing
paths, and deterministic parsed representation.

## Workspace Safety

All affected paths are normalized with the Phase 2A workspace path helper.

Denied cases include:

- `../` traversal outside the workspace;
- absolute paths outside the workspace;
- symlink escape when the platform permits symlink creation;
- unsupported binary or non-UTF-8 targets.

The patch parser does not duplicate path policy. Workspace containment remains
centralized in `WorkspaceIdentity.normalize_path`.

## Validate Before Mutate

Phase 2C computes every operation in memory before writing:

```text
parse full patch
-> normalize all paths
-> validate file preconditions
-> read preimages
-> verify every hunk
-> compute all postimages
-> write/delete files
```

If validation fails before the final write phase, no file is mutated. This is
application-level atomicity, not filesystem transactionality and not WAL
durability.

## Hunk Semantics

Hunks are exact-match and deterministic:

- expected old/context lines must match file content exactly, excluding line
  ending bytes;
- ambiguous hunk matches fail with `hunk_ambiguous`;
- missing matches fail with `hunk_not_found`;
- hunks are applied in order, and later hunks search after the previous applied
  location;
- v0 update hunks must include at least one removed line to avoid unanchored
  insert-only mutations.

MiniCode prefers explicit correction over fuzzy matching.

## Hash Semantics

Every affected file records SHA-256 hashes:

| Operation | Preimage | Postimage |
| --- | --- | --- |
| Add | `null` | SHA-256 of created bytes |
| Update | SHA-256 of original bytes | SHA-256 of patched bytes |
| Delete | SHA-256 of original bytes | `null` |

These hashes are intentionally present in Phase 2C so Phase 2D WAL
reconciliation can bind patch intent to observed filesystem state.

## Newline and Encoding Behavior

MiniCode v0 is source/text oriented:

- UTF-8 text is supported;
- binary files are denied by NUL-byte detection;
- invalid UTF-8 is denied with `encoding_error`;
- existing line ending style is preserved for untouched lines;
- replacement and inserted lines use the first newline style observed in the
  target file, defaulting to LF for new files.

CRLF fixtures verify changed lines remain CRLF when the target file uses CRLF.

## ToolRegistry Integration

MiniCode registers `apply_patch` through AgentKernel `ToolRegistry`.

Capability mapping:

```text
tool.execute     tool://minicode/apply_patch
workspace.write  workspace://<workspace-id>/**
```

The model-visible schema requires:

```json
{
  "patch": "..."
}
```

Tool projection requires `tool.execute`. Handler execution separately checks
`workspace.write` for every normalized affected path before mutation. Missing
workspace write authority returns AgentKernel `EACCES` and leaves the
filesystem unchanged.

Successful results are JSON-safe:

```json
{
  "ok": true,
  "applied": true,
  "changed_files": ["calculator.py"],
  "hunk_count": 1,
  "summary": [
    {"path": "calculator.py", "operation": "update", "hunks": 1}
  ],
  "preimage_hashes": {"calculator.py": "..."},
  "postimage_hashes": {"calculator.py": "..."}
}
```

Structured patch failures return:

```json
{
  "ok": false,
  "applied": false,
  "error": {
    "code": "hunk_not_found",
    "message": "...",
    "retryable": false,
    "diagnostics": {}
  }
}
```

## Error Codes

Phase 2C adds these patch-oriented error codes:

| Code | Meaning |
| --- | --- |
| `invalid_patch` | Patch envelope or operation structure is invalid. |
| `unsupported_patch_operation` | Directive is outside the Phase 2C subset. |
| `malformed_hunk` | Hunk syntax is invalid or not anchored. |
| `outside_workspace` | A target path escapes the workspace root. |
| `file_not_found` | Update/delete target is missing. |
| `file_already_exists` | Add target already exists. |
| `is_directory` | Target is a directory where a file is required. |
| `binary_file` | Target appears binary and is denied. |
| `encoding_error` | Target is not valid UTF-8. |
| `hunk_not_found` | Expected old/context lines were not found. |
| `hunk_ambiguous` | Expected old/context lines matched multiple locations. |
| `write_failed` | Final filesystem write/delete failed. |

## Explicit Non-Goals

Phase 2C does not implement:

- Durable Tool WAL;
- patch reconciliation after crash;
- shell or `run_command`;
- host confirmation UI;
- model loop;
- Context VM coding policy;
- V0.9 memory;
- arbitrary fuzzy patching;
- binary patching.

## Implementation Friction

`MINICODE_IMPLEMENTATION_FRICTION: apply_patch WAL durability deferred to Phase 2D`

The Phase 2C mutation core is deliberately separable from DurableToolExecutor.
It already exposes preimage/postimage hashes and deterministic structured
results, but exactly-once crash recovery is not claimed until Phase 2D wraps it
in AgentKernel WAL semantics.
