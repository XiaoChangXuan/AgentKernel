# MiniCode Phase 2D Durable Patch WAL

Decision: `MINICODE_PHASE2D_DURABLE_PATCH_COMPLETE`

## Scope

Phase 2D routes MiniCode `apply_patch` through AgentKernel durable Tool
execution. It makes the Phase 2C patch mutation core crash-aware without
creating a MiniCode-owned WAL.

Implemented boundary:

```text
parse patch
-> normalize and authorize affected paths
-> compute preimage and expected postimage hashes
-> AgentKernel TOOL_PREPARE
-> AgentKernel TOOL_DISPATCH
-> apply filesystem mutation
-> verify postimage hashes
-> AgentKernel TOOL_COMMIT
```

MiniCode still owns patch parsing, workspace path normalization, and filesystem
mutation. AgentKernel owns durable Session truth, WAL event ordering,
authorization at the Tool boundary, recovery analysis, retry eligibility, and
reconciliation dispatch.

## Durable Adapter

The integration lives in `minicode.durable_patch`.

Key objects:

| Object | Role |
| --- | --- |
| `DurableApplyPatchAdapter` | MiniCode adapter around `DurableToolExecutor`. |
| `DurablePatchPreparation` | Prepared `ToolCall`, stable `operation_id`, patch digest, changed files, and mutation plan. |
| `PatchMutationPlan` | Phase 2C validated plan with preimage/postimage hashes. |
| `PatchReconciliation` | MiniCode-readable interpretation of filesystem state after an interrupted durable operation. |

The low-level parser and hunk matcher remain independent from AgentKernel
Session/WAL logic.

## Operation Identity

`operation_id` is deterministic for a proposed patch operation. It hashes a
canonical JSON payload containing:

- `agent_id`;
- `tool_call_id`;
- tool name: `apply_patch`;
- canonical patch digest;
- workspace identity;
- normalized changed file paths;
- capability action: `workspace.write`;
- capability resource scope: `workspace://<workspace-id>/**`.

The model never supplies `operation_id`. MiniCode computes it before calling
`DurableToolExecutor`, and the executor rejects operation id collisions in the
Session.

## Patch Digest

`patch_digest` is SHA-256 over a canonical parsed patch representation:

- operation kind: add, update, delete;
- normalized patch path as parsed;
- add-file lines;
- update-file hunk headers and hunk lines;
- delete-file target.

The digest excludes timestamps, temporary paths, object ids, process ids, and
absolute workspace location. It is stable across fresh runtime recovery for the
same proposed patch.

## Persisted Durable Facts

AgentKernel `TOOL_PREPARE` keeps Kernel WAL facts:

- `operation_id`;
- `tool_call_id`;
- `tool_name`;
- `effect_kind`;
- Tool authorization context.

MiniCode durable patch facts are attached to the prepared `TOOL_CALL`
arguments under `__minicode_durable_plan` so they are available after Session
replay without changing Kernel WAL schema:

- metadata version;
- `operation_id`;
- `patch_digest`;
- workspace identity;
- workspace capability action/resource scope;
- changed file paths;
- operation kind per file;
- preimage hashes;
- expected postimage hashes;
- deterministic postimage text for add/update operations.

No hidden model reasoning is persisted.

## Dispatch Boundary

`TOOL_DISPATCH` means the filesystem mutation may have been sent/performed.
After dispatch, MiniCode invokes the Phase 2C validated `PatchMutationPlan`.

The dispatched mutation does not reparse or reinterpret the patch. It checks
that each current file still matches the recorded preimage, applies the planned
bytes/deletions, then verifies actual postimage hashes before the Kernel writes
`TOOL_COMMIT`.

## Flagship Recovery Scenario

Scenario: MiniCode changes `calculator.py`, then the runtime crashes after the
file is changed but before `TOOL_COMMIT`.

Observed sequence:

```text
TOOL_CALL contains durable patch plan
TOOL_PREPARE records durable intent
TOOL_DISPATCH records side-effect boundary
calculator.py reaches expected postimage
CRASH before TOOL_COMMIT
fresh runtime loads Session JSONL
RecoveryAnalysis classifies operation as RECONCILE_REQUIRED
reconcile compares current file hash to expected postimage
existing mutation is recognized
TOOL_RECONCILE records observation
TOOL_COMMIT records recovered success
```

The patch is not applied a second time. The duplicate mutation oracle test
keeps dispatch count at `1` across recovery.

## Reconciliation Algorithm

For every affected path, MiniCode compares:

```text
recorded preimage hash
recorded expected postimage hash
current filesystem hash
```

| Current state | Interpretation | Reconciliation status |
| --- | --- | --- |
| Every path equals expected postimage | Mutation already happened. Commit recovered result. | `SUCCEEDED` |
| Every path equals preimage | Mutation did not happen, or state returned to pre-dispatch state. | `NOT_FOUND` |
| Any path is neither preimage nor postimage, or a multi-file patch is mixed | Filesystem diverged or partial state is ambiguous. | `UNKNOWN` |

`UNKNOWN` is surfaced as manual intervention required. MiniCode does not guess
or patch over third-state content.

## Crash Matrix

| Crash point | Durable facts | Filesystem state | Recovery behavior |
| --- | --- | --- | --- |
| Before `TOOL_PREPARE` | No durable operation | Unchanged | No durable mutation obligation. |
| After `TOOL_PREPARE`, before `TOOL_DISPATCH` | Prepared operation | Unchanged | Kernel classifies `SAFE_TO_RETRY`; future retry requires current authorization. |
| After `TOOL_DISPATCH`, before mutation | Dispatched operation | Preimage | Reconcile returns `NOT_FOUND`; retry may be policy-allowed after current authorization. |
| After mutation, before `TOOL_COMMIT` | Dispatched operation | Expected postimage | Reconcile succeeds and commits existing result without reapplying. |
| After `TOOL_COMMIT` | Committed operation | Expected postimage | Kernel treats operation as completed fact. |

## Add, Update, Delete Semantics

Absence is represented as JSON `null` in preimage/postimage hash maps.

| Operation | Preimage | Postimage |
| --- | --- | --- |
| Add | `null` | SHA-256 of created file bytes |
| Update | SHA-256 of original bytes | SHA-256 of patched bytes |
| Delete | SHA-256 of original bytes | `null` |

Tests cover add-file recovery, update recovery, delete recovery, successful
multi-file recovery, and mixed multi-file states that require manual
intervention.

## Authority on Recovery

Historical durable facts remain facts even if current authority changes. A
recovery process may inspect and reconcile a historical dispatched operation.

New mutation work still requires current authority:

- `reconcile` observes current filesystem state through the Tool recovery
  boundary;
- if reconciliation reports `NOT_FOUND`, the operation becomes retryable;
- retry invokes the current Tool handler and rechecks `workspace.write`;
- if write authority was removed, retry fails with `EACCES` and does not mutate
  files.

This keeps the distinction between historical obligation and current
permission.

## Recovery Is Not Retry

Phase 2D explicitly separates recovery from retry.

For:

```text
filesystem mutation succeeded
-> crash before COMMIT
```

the correct behavior is:

```text
DISPATCH without COMMIT
-> reconcile filesystem hashes
-> recognize existing postimage
-> COMMIT recovered result
```

It is not:

```text
COMMIT missing
-> apply patch again blindly
```

## Observable Result

Recovered success includes JSON-safe observable fields:

```json
{
  "ok": true,
  "applied": true,
  "changed_files": ["calculator.py"],
  "hunk_count": 1,
  "preimage_hashes": {"calculator.py": "..."},
  "postimage_hashes": {"calculator.py": "..."},
  "patch_digest": "...",
  "operation_id": "...",
  "recovered": true,
  "recovery": {
    "state": "completed",
    "action_taken": "recognized_existing_mutation",
    "current_hashes": {"calculator.py": "..."},
    "expected_preimages": {"calculator.py": "..."},
    "expected_postimages": {"calculator.py": "..."},
    "manual_reason": null
  }
}
```

Manual paths return `UNKNOWN` with a `manual_intervention_required` message.

## Limitations

Phase 2D does not claim universal exactly-once filesystem mutation.

It provides tested durable detection and reconciliation for the covered crash
windows using AgentKernel WAL facts plus pre/post filesystem hashes. It does
not provide a distributed transaction, fsync every target file/directory, undo
arbitrary external interference, or make shell mutations WAL-safe.

Out of scope:

- `run_command`;
- subprocess timeout/capture;
- model adapter;
- coding loop;
- Context orchestration;
- reviewer/subagent;
- V0.9 memory.

## Validation

Phase 2D tests verify:

- successful durable patch;
- crash before prepare;
- crash after prepare before dispatch;
- crash after mutation before commit;
- crash after commit;
- current preimage retry path with authority shrink;
- hash conflict/manual-required behavior;
- add-file recovery;
- delete-file recovery;
- multi-file recovery;
- mixed multi-file manual-required behavior;
- fresh runtime recovery from `JsonlSessionPersistence`;
- duplicate mutation oracle dispatch count remains `1`.

## Implementation Friction

`MINICODE_IMPLEMENTATION_FRICTION: no AgentKernel core changes required`

AgentKernel `TOOL_PREPARE` does not currently expose an app-specific metadata
field. MiniCode therefore stores the durable patch plan in the already durable
`TOOL_CALL.arguments` payload. This keeps AgentKernel WAL schema unchanged
while preserving replayable patch facts.

## Final Decision

`MINICODE_PHASE2D_DURABLE_PATCH_COMPLETE`
