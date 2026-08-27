from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from minicode.errors import MiniCodeError
from minicode.workspace import WorkspaceIdentity

from .parser import AddFile, DeleteFile, Hunk, ParsedPatch, PatchError, UpdateFile, parse_patch


ABSENT_HASH = None


@dataclass(frozen=True, slots=True)
class PatchApplyResult:
    applied: bool
    changed_files: tuple[str, ...]
    hunk_count: int
    summary: tuple[dict[str, object], ...]
    preimage_hashes: dict[str, str | None]
    postimage_hashes: dict[str, str | None]

    def to_dict(self) -> dict[str, object]:
        return {
            "applied": self.applied,
            "changed_files": list(self.changed_files),
            "hunk_count": self.hunk_count,
            "summary": list(self.summary),
            "preimage_hashes": dict(self.preimage_hashes),
            "postimage_hashes": dict(self.postimage_hashes),
        }


@dataclass(frozen=True, slots=True)
class PatchFilePlan:
    path: Path
    relative_path: str
    operation: str
    output: bytes | None
    preimage_hash: str | None
    postimage_hash: str | None
    hunk_count: int

    def to_durable_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "path": self.relative_path,
            "operation": self.operation,
            "preimage_hash": self.preimage_hash,
            "postimage_hash": self.postimage_hash,
            "hunk_count": self.hunk_count,
        }
        if self.output is not None:
            payload["postimage_text"] = self.output.decode("utf-8")
        return payload


@dataclass(frozen=True, slots=True)
class PatchMutationPlan:
    files: tuple[PatchFilePlan, ...]

    @property
    def changed_files(self) -> tuple[str, ...]:
        return tuple(plan.relative_path for plan in self.files)

    @property
    def hunk_count(self) -> int:
        return sum(plan.hunk_count for plan in self.files)

    def to_result(self) -> PatchApplyResult:
        return PatchApplyResult(
            applied=True,
            changed_files=self.changed_files,
            hunk_count=self.hunk_count,
            summary=tuple(
                {
                    "path": plan.relative_path,
                    "operation": plan.operation,
                    "hunks": plan.hunk_count,
                }
                for plan in self.files
            ),
            preimage_hashes={
                plan.relative_path: plan.preimage_hash for plan in self.files
            },
            postimage_hashes={
                plan.relative_path: plan.postimage_hash for plan in self.files
            },
        )

    def to_durable_dict(self) -> dict[str, object]:
        return {
            "changed_files": list(self.changed_files),
            "hunk_count": self.hunk_count,
            "files": [plan.to_durable_dict() for plan in self.files],
        }

    @classmethod
    def from_durable_dict(
        cls,
        workspace: WorkspaceIdentity,
        payload: object,
    ) -> "PatchMutationPlan":
        if not isinstance(payload, dict):
            raise PatchError(
                "durable_state_invalid",
                "durable patch plan must be an object",
                False,
            )
        raw_files = payload.get("files")
        if not isinstance(raw_files, list) or not raw_files:
            raise PatchError(
                "durable_state_invalid",
                "durable patch plan must contain files",
                False,
            )
        plans: list[PatchFilePlan] = []
        for item in raw_files:
            if not isinstance(item, dict):
                raise PatchError(
                    "durable_state_invalid",
                    "durable patch file plan must be an object",
                    False,
                )
            relative_path = _required_string(item, "path")
            operation = _required_string(item, "operation")
            if operation not in {"add", "update", "delete"}:
                raise PatchError(
                    "durable_state_invalid",
                    f"unsupported durable patch operation: {operation}",
                    False,
                    {"file": relative_path},
                )
            normalized = workspace.normalize_path(relative_path, must_exist=False)
            output = None
            postimage_text = item.get("postimage_text")
            if operation != "delete":
                if not isinstance(postimage_text, str):
                    raise PatchError(
                        "durable_state_invalid",
                        "durable patch postimage_text is required for add/update",
                        False,
                        {"file": relative_path},
                    )
                output = postimage_text.encode("utf-8")
            elif postimage_text is not None:
                raise PatchError(
                    "durable_state_invalid",
                    "delete durable patch plan cannot contain postimage_text",
                    False,
                    {"file": relative_path},
                )
            hunk_count = item.get("hunk_count")
            if isinstance(hunk_count, bool) or not isinstance(hunk_count, int) or hunk_count < 0:
                raise PatchError(
                    "durable_state_invalid",
                    "durable patch hunk_count must be a non-negative integer",
                    False,
                    {"file": relative_path},
                )
            preimage_hash = _optional_hash(item, "preimage_hash")
            postimage_hash = _optional_hash(item, "postimage_hash")
            plans.append(
                PatchFilePlan(
                    path=normalized.absolute_path,
                    relative_path=normalized.relative_path,
                    operation=operation,
                    output=output,
                    preimage_hash=preimage_hash,
                    postimage_hash=postimage_hash,
                    hunk_count=hunk_count,
                )
            )
        return cls(tuple(plans))


@dataclass(frozen=True, slots=True)
class _Line:
    text: str
    newline: str


def apply_patch_text(workspace: WorkspaceIdentity, patch: str) -> PatchApplyResult:
    return apply_parsed_patch(workspace, parse_patch(patch))


def apply_parsed_patch(workspace: WorkspaceIdentity, parsed: ParsedPatch) -> PatchApplyResult:
    """Validate every operation and hunk before any filesystem write."""

    return apply_mutation_plan(plan_parsed_patch(workspace, parsed))


def plan_parsed_patch(
    workspace: WorkspaceIdentity,
    parsed: ParsedPatch,
) -> PatchMutationPlan:
    """Build an exact, durable-safe mutation plan without writing files."""

    return PatchMutationPlan(_build_plans(workspace, parsed))


def apply_mutation_plan(plan: PatchMutationPlan) -> PatchApplyResult:
    """Apply an already validated mutation plan."""

    try:
        for file_plan in plan.files:
            current_hash = hash_file_or_absent(file_plan.path)
            if current_hash != file_plan.preimage_hash:
                raise PatchError(
                    "workspace_state_changed",
                    f"file state changed before patch dispatch: {file_plan.relative_path}",
                    False,
                    {
                        "file": file_plan.relative_path,
                        "expected_preimage_hash": file_plan.preimage_hash,
                        "current_hash": current_hash,
                    },
                )
        for file_plan in plan.files:
            if file_plan.output is None:
                file_plan.path.unlink()
            else:
                file_plan.path.parent.mkdir(parents=True, exist_ok=True)
                file_plan.path.write_bytes(file_plan.output)
    except OSError as exc:
        raise PatchError(
            "write_failed",
            f"failed to write patch result: {exc}",
            True,
        ) from exc

    actual = {
        file_plan.relative_path: hash_file_or_absent(file_plan.path)
        for file_plan in plan.files
    }
    for file_plan in plan.files:
        if actual[file_plan.relative_path] != file_plan.postimage_hash:
            raise PatchError(
                "postimage_verification_failed",
                f"patch postimage verification failed: {file_plan.relative_path}",
                False,
                {
                    "file": file_plan.relative_path,
                    "expected_postimage_hash": file_plan.postimage_hash,
                    "actual_hash": actual[file_plan.relative_path],
                },
            )
    return plan.to_result()


def hash_file_or_absent(path: Path) -> str | None:
    if not path.exists():
        return ABSENT_HASH
    if path.is_dir():
        raise PatchError(
            "is_directory",
            f"path is a directory: {path}",
            False,
        )
    return _sha256(path.read_bytes())


def _build_plans(workspace: WorkspaceIdentity, parsed: ParsedPatch) -> tuple[PatchFilePlan, ...]:
    seen: set[str] = set()
    plans: list[_Plan] = []
    for operation in parsed.operations:
        try:
            normalized = workspace.normalize_path(operation.path, must_exist=False)
        except MiniCodeError as exc:
            raise PatchError(
                exc.code,
                exc.message,
                exc.retryable,
                {"file": operation.path},
            ) from exc
        relative_path = normalized.relative_path
        if relative_path in seen:
            raise PatchError(
                "invalid_patch",
                f"multiple patch operations target the same file: {relative_path}",
                False,
                {"file": relative_path},
            )
        seen.add(relative_path)

        if isinstance(operation, AddFile):
            plans.append(_plan_add(operation, normalized.absolute_path, relative_path))
        elif isinstance(operation, UpdateFile):
            plans.append(_plan_update(operation, normalized.absolute_path, relative_path))
        elif isinstance(operation, DeleteFile):
            plans.append(_plan_delete(normalized.absolute_path, relative_path))
        else:
            raise AssertionError(f"unexpected patch operation: {operation!r}")
    return tuple(plans)


def _plan_add(operation: AddFile, path: Path, relative_path: str) -> PatchFilePlan:
    if path.exists():
        raise PatchError("file_already_exists", f"file already exists: {relative_path}", False, {"file": relative_path})
    payload = "".join(f"{line}\n" for line in operation.lines).encode("utf-8")
    return PatchFilePlan(
        path=path,
        relative_path=relative_path,
        operation="add",
        output=payload,
        preimage_hash=ABSENT_HASH,
        postimage_hash=_sha256(payload),
        hunk_count=0,
    )


def _plan_update(operation: UpdateFile, path: Path, relative_path: str) -> PatchFilePlan:
    raw = _read_existing_text_bytes(path, relative_path)
    lines = _decode_lines(raw, relative_path)
    default_newline = _default_newline(lines)
    current = list(lines)
    cursor = 0
    for hunk_index, hunk in enumerate(operation.hunks, start=1):
        match_index = _find_unique_hunk_match(
            current,
            hunk,
            start=cursor,
            relative_path=relative_path,
            hunk_index=hunk_index,
        )
        replacement = [_Line(text=line, newline=default_newline) for line in hunk.new_lines]
        old_len = len(hunk.old_lines)
        current = current[:match_index] + replacement + current[match_index + old_len :]
        cursor = match_index + len(replacement)
    output = _encode_lines(current)
    return PatchFilePlan(
        path=path,
        relative_path=relative_path,
        operation="update",
        output=output,
        preimage_hash=_sha256(raw),
        postimage_hash=_sha256(output),
        hunk_count=len(operation.hunks),
    )


def _plan_delete(path: Path, relative_path: str) -> PatchFilePlan:
    raw = _read_existing_text_bytes(path, relative_path)
    return PatchFilePlan(
        path=path,
        relative_path=relative_path,
        operation="delete",
        output=None,
        preimage_hash=_sha256(raw),
        postimage_hash=ABSENT_HASH,
        hunk_count=0,
    )


def _read_existing_text_bytes(path: Path, relative_path: str) -> bytes:
    if not path.exists():
        raise PatchError("file_not_found", f"file does not exist: {relative_path}", False, {"file": relative_path})
    if path.is_dir():
        raise PatchError("is_directory", f"path is a directory: {relative_path}", False, {"file": relative_path})
    raw = path.read_bytes()
    if b"\x00" in raw[:4096]:
        raise PatchError("binary_file", f"binary edits are not supported: {relative_path}", False, {"file": relative_path})
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PatchError(
            "encoding_error",
            f"file is not valid UTF-8: {relative_path}",
            False,
            {"file": relative_path},
        ) from exc
    return raw


def _decode_lines(raw: bytes, relative_path: str) -> list[_Line]:
    text = raw.decode("utf-8")
    lines: list[_Line] = []
    start = 0
    while start < len(text):
        newline_at = _next_newline(text, start)
        if newline_at is None:
            lines.append(_Line(text[start:], ""))
            break
        line_end, newline = newline_at
        lines.append(_Line(text[start:line_end], newline))
        start = line_end + len(newline)
    if not lines and text:
        raise PatchError("encoding_error", f"could not decode text lines: {relative_path}", False)
    return lines


def _next_newline(text: str, start: int) -> tuple[int, str] | None:
    index = start
    while index < len(text):
        char = text[index]
        if char == "\n":
            return index, "\n"
        if char == "\r":
            if index + 1 < len(text) and text[index + 1] == "\n":
                return index, "\r\n"
            return index, "\r"
        index += 1
    return None


def _default_newline(lines: list[_Line]) -> str:
    for line in lines:
        if line.newline:
            return line.newline
    return "\n"


def _encode_lines(lines: list[_Line]) -> bytes:
    return "".join(f"{line.text}{line.newline}" for line in lines).encode("utf-8")


def _find_unique_hunk_match(
    lines: list[_Line],
    hunk: Hunk,
    *,
    start: int,
    relative_path: str,
    hunk_index: int,
) -> int:
    old_lines = hunk.old_lines
    if not old_lines:
        raise PatchError(
            "hunk_ambiguous",
            "hunk has no old/context lines to anchor the update",
            False,
            {"file": relative_path, "hunk": hunk_index},
        )
    matches: list[int] = []
    max_start = len(lines) - len(old_lines)
    for index in range(start, max_start + 1):
        if tuple(line.text for line in lines[index : index + len(old_lines)]) == old_lines:
            matches.append(index)
            if len(matches) > 1:
                break
    if not matches:
        raise PatchError(
            "hunk_not_found",
            f"expected hunk context not found in {relative_path}",
            False,
            {
                "file": relative_path,
                "hunk": hunk_index,
                "expected": list(old_lines[:12]),
                "observed_near_cursor": [line.text for line in lines[start : start + 12]],
            },
        )
    if len(matches) > 1:
        raise PatchError(
            "hunk_ambiguous",
            f"hunk context matches more than one location in {relative_path}",
            False,
            {
                "file": relative_path,
                "hunk": hunk_index,
                "expected": list(old_lines[:12]),
            },
        )
    return matches[0]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _required_string(data: dict[str, object], name: str) -> str:
    value = data.get(name)
    if not isinstance(value, str) or not value:
        raise PatchError(
            "durable_state_invalid",
            f"durable patch {name} must be a non-empty string",
            False,
        )
    return value


def _optional_hash(data: dict[str, object], name: str) -> str | None:
    value = data.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or len(value) != 64:
        raise PatchError(
            "durable_state_invalid",
            f"durable patch {name} must be a sha256 hex string or null",
            False,
        )
    return value
