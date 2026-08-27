from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from .errors import MiniCodeError


@dataclass(frozen=True)
class NormalizedPath:
    """Workspace-contained path identity."""

    absolute_path: Path
    relative_path: str
    exists: bool
    is_dir: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "absolute_path": str(self.absolute_path),
            "relative_path": self.relative_path,
            "exists": self.exists,
            "is_dir": self.is_dir,
        }


@dataclass(frozen=True)
class WorkspaceIdentity:
    """MiniCode v0 single-root workspace identity."""

    root: Path
    task_cwd: Path
    workspace_id: str

    def normalize_path(self, path: str | Path = ".", *, must_exist: bool = False) -> NormalizedPath:
        return normalize_workspace_path(self, path, must_exist=must_exist)

    def to_dict(self) -> dict[str, str]:
        return {
            "root": str(self.root),
            "task_cwd": str(self.task_cwd),
            "workspace_id": self.workspace_id,
        }


def discover_workspace(
    *,
    explicit_workspace: str | Path | None = None,
    cwd: str | Path | None = None,
    task_cwd: str | Path | None = None,
) -> WorkspaceIdentity:
    """Resolve the MiniCode v0 workspace root.

    The search starts at ``explicit_workspace`` when provided, otherwise at
    ``cwd``. The nearest ancestor containing ``.git`` wins; without one the
    starting directory itself is the single workspace root.
    """

    start = Path(explicit_workspace) if explicit_workspace is not None else Path(cwd or Path.cwd())
    start = _require_directory(start, "invalid_workspace")
    root = _find_nearest_git_root(start) or start

    requested_task_cwd = Path(task_cwd) if task_cwd is not None else start
    requested_task_cwd = _require_directory(requested_task_cwd, "invalid_workspace")
    task = _resolve_existing(requested_task_cwd)
    if not _contains(root, task):
        raise MiniCodeError(
            code="outside_workspace",
            message=f"Task cwd is outside workspace: {requested_task_cwd}",
            retryable=False,
        )

    return WorkspaceIdentity(root=root, task_cwd=task, workspace_id=_workspace_id(root))


def normalize_workspace_path(
    workspace: WorkspaceIdentity,
    path: str | Path = ".",
    *,
    must_exist: bool = False,
) -> NormalizedPath:
    """Resolve a path and reject any escape from the workspace root."""

    raw = Path(path)
    candidate = raw if raw.is_absolute() else workspace.root / raw
    resolved = candidate.expanduser().resolve(strict=False)

    if not _contains(workspace.root, resolved):
        raise MiniCodeError(
            code="outside_workspace",
            message=f"Path escapes workspace: {path}",
            retryable=False,
        )

    if must_exist and not resolved.exists():
        raise MiniCodeError(
            code="path_not_found",
            message=f"Path does not exist: {relative_display_path(workspace.root, resolved)}",
            retryable=False,
        )

    return NormalizedPath(
        absolute_path=resolved,
        relative_path=relative_display_path(workspace.root, resolved),
        exists=resolved.exists(),
        is_dir=resolved.is_dir(),
    )


def relative_display_path(root: Path, path: Path) -> str:
    relative = os.path.relpath(path, root)
    if relative == ".":
        return "."
    return relative.replace(os.sep, "/")


def _find_nearest_git_root(start: Path) -> Path | None:
    current = _resolve_existing(start)
    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists():
            return candidate
    return None


def _require_directory(path: Path, code: str) -> Path:
    expanded = path.expanduser()
    try:
        resolved = expanded.resolve(strict=True)
    except OSError as exc:
        raise MiniCodeError(
            code=code,
            message=f"Directory does not exist: {path}",
            retryable=False,
        ) from exc
    if not resolved.is_dir():
        raise MiniCodeError(
            code=code,
            message=f"Path is not a directory: {path}",
            retryable=False,
        )
    return resolved


def _resolve_existing(path: Path) -> Path:
    return path.expanduser().resolve(strict=True)


def _contains(root: Path, candidate: Path) -> bool:
    try:
        common = os.path.commonpath([_casefold_path(root), _casefold_path(candidate)])
    except ValueError:
        return False
    return common == _casefold_path(root)


def _casefold_path(path: Path) -> str:
    return os.path.normcase(str(path))


def _workspace_id(root: Path) -> str:
    digest = hashlib.sha256(_casefold_path(root).encode("utf-8")).hexdigest()[:12]
    return f"workspace-{digest}"
