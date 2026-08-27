# MiniCode Phase 2A Workspace Core

Status: implemented.

Phase 2A establishes MiniCode as a separate application package while keeping
AgentKernel as the runtime authority. No `agentkernel/` code is modified in this
phase.

## Implemented Scope

- `minicode/` package import.
- `python -m minicode` CLI skeleton.
- Typed Phase 2A configuration.
- Structured application errors with `code`, `message`, and `retryable`.
- Workspace root discovery.
- Workspace-contained path normalization.
- `AGENTS.md` discovery from workspace root toward task cwd.
- Deterministic test fixtures for future MiniCode phases.

The CLI can parse the frozen command shape (`run`, `resume`, `trace`, `bench`)
and validate the workspace. It intentionally reports
`not_implemented_in_phase_2a` for command execution because coding tools, model
adapters, and the loop are later phases.

## Workspace Root Resolution

MiniCode v0 supports exactly one workspace root.

Resolution starts from `--workspace` when supplied; otherwise it starts from the
current working directory. MiniCode searches upward for the nearest `.git`
directory. If one is found, that directory becomes the workspace root. If no
`.git` directory is found, the starting directory itself becomes the workspace
root.

When an explicit task cwd is provided by tests or future callers, it must resolve
inside the selected workspace root.

## Path Containment

Relative paths resolve under the workspace root. Absolute paths are allowed only
when their resolved real path remains inside the workspace. Normalized
model-visible paths are workspace-relative and use `/` separators.

Escapes are rejected before future tools can read or write:

- `..` traversal outside the root;
- absolute paths outside the root;
- symlink/junction escapes when the platform exposes them through real-path
  resolution.

This is an application-level path boundary, not a production OS sandbox.

## AGENTS.md Discovery

MiniCode v0 implements only `AGENTS.md` instruction discovery.

Discovery walks from the workspace root toward the active task cwd and returns
sources in deterministic precedence/source order. The returned records include
the workspace-relative path and full file content for later Context VM input
construction.

The following remain deferred:

- `CLAUDE.md`;
- `.cursor`;
- IDE configuration;
- arbitrary instruction formats;
- model summarization of instruction files.

## Deliberately Not Implemented

Phase 2A does not implement:

- `list_files`, `search_files`, or `read_file` tools;
- Codex-style patch parsing;
- `apply_patch`;
- patch WAL integration;
- subprocess execution;
- shell authorization;
- `run_command`;
- ModelAdapter;
- coding loop;
- Context VM coding integration;
- Session resume/recovery behavior.

## MINICODE_IMPLEMENTATION_FRICTION

| Item | Classification | Disposition |
| --- | --- | --- |
| Windows symlink creation may require developer mode or privileges. | Expected platform limitation | Tests skip symlink escape coverage when the OS refuses symlink creation; path containment still uses resolved real paths when symlinks exist. |
| `python -m minicode` has command names before options in Phase 2A. | CLI ergonomics | Acceptable for skeleton; future phases may add global aliases without changing workspace semantics. |
| Workspace path containment is semantic application validation, not OS confinement. | Security boundary clarity | Documented explicitly; hard containment remains external to MiniCode. |

