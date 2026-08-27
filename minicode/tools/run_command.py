from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from agentkernel.capabilities import AuthorizationRequest
from agentkernel.protocol import ErrorCode, JsonValue
from agentkernel.resources import ResourceOwner, ResourceService
from agentkernel.tools import ToolExecutionContext, ToolExecutionError

from minicode.errors import MiniCodeError
from minicode.workspace import WorkspaceIdentity

from .common import argument_string, optional_int
from .schemas import (
    DEFAULT_COMMAND_CAPTURE_BYTES,
    DEFAULT_COMMAND_PREVIEW_BYTES,
    DEFAULT_COMMAND_TIMEOUT_MS,
    MAX_COMMAND_PREVIEW_BYTES,
    MAX_COMMAND_TIMEOUT_MS,
    RUN_COMMAND_NAME,
    SHELL_EXECUTE_ACTION,
    error_result,
    shell_resource,
    shell_scope,
    success_result,
)


MutationIntent = Literal["read_only", "may_mutate"]
HostPolicyDecision = Literal["allow", "deny", "confirm"]


@dataclass(frozen=True, slots=True)
class ShellPolicyRequest:
    agent_id: str
    session_id: str
    tool_name: str
    action: str
    resource_scope: str
    workspace_root: str
    cwd: str
    command: str
    mutation_intent: MutationIntent
    risk_class: str


@dataclass(frozen=True, slots=True)
class ShellPolicyResult:
    decision: HostPolicyDecision
    reason: str


class ShellHostPolicy(Protocol):
    def decide(self, request: ShellPolicyRequest) -> ShellPolicyResult: ...
    def confirm(self, request: ShellPolicyRequest) -> bool: ...


@dataclass(frozen=True, slots=True)
class DefaultShellHostPolicy:
    """Conservative Phase 2E host policy; model arguments cannot alter it."""

    allow_network: bool = False
    confirm_mutation: Callable[[ShellPolicyRequest], bool] | None = None

    def decide(self, request: ShellPolicyRequest) -> ShellPolicyResult:
        if request.risk_class == "external_side_effect" and not self.allow_network:
            return ShellPolicyResult("deny", "external_side_effect_denied")
        if request.risk_class == "background_or_interactive":
            return ShellPolicyResult("deny", "background_or_interactive_denied")
        if request.mutation_intent == "may_mutate":
            return ShellPolicyResult("confirm", "mutation_requires_host_confirmation")
        return ShellPolicyResult("allow", "allowed")

    def confirm(self, request: ShellPolicyRequest) -> bool:
        if self.confirm_mutation is None:
            return False
        return bool(self.confirm_mutation(request))


@dataclass(frozen=True, slots=True)
class CommandCompleted:
    exit_code: int | None
    timed_out: bool
    duration_ms: int
    stdout: bytes
    stderr: bytes
    output_limit_exceeded: bool = False


class CommandRunner(Protocol):
    def run(
        self,
        command: str,
        *,
        cwd: Path,
        timeout_ms: int,
        max_capture_bytes: int,
    ) -> CommandCompleted: ...


class SubprocessCommandRunner:
    """Synchronous subprocess runner with finite timeout and finite capture."""

    def run(
        self,
        command: str,
        *,
        cwd: Path,
        timeout_ms: int,
        max_capture_bytes: int,
    ) -> CommandCompleted:
        argv = platform_shell_argv(command)
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="minicode-command-") as temp:
            stdout_path = Path(temp) / "stdout.bin"
            stderr_path = Path(temp) / "stderr.bin"
            with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
                process = subprocess.Popen(
                    argv,
                    cwd=str(cwd),
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    shell=False,
                )
                timed_out = False
                try:
                    process.wait(timeout=timeout_ms / 1000)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    process.kill()
                    process.wait()
            duration_ms = max(0, int((time.monotonic() - started) * 1000))
            stdout_data, stdout_limited = _read_capped(stdout_path, max_capture_bytes)
            stderr_data, stderr_limited = _read_capped(stderr_path, max_capture_bytes)
        return CommandCompleted(
            exit_code=None if timed_out else int(process.returncode),
            timed_out=timed_out,
            duration_ms=duration_ms,
            stdout=stdout_data,
            stderr=stderr_data,
            output_limit_exceeded=stdout_limited or stderr_limited,
        )


def run_command(
    workspace: WorkspaceIdentity,
    arguments: Mapping[str, JsonValue],
    *,
    context: ToolExecutionContext,
    resources: ResourceService | None = None,
    policy: ShellHostPolicy | None = None,
    runner: CommandRunner | None = None,
    preview_bytes: int = DEFAULT_COMMAND_PREVIEW_BYTES,
    max_capture_bytes: int = DEFAULT_COMMAND_CAPTURE_BYTES,
) -> dict[str, JsonValue]:
    args = dict(arguments)
    command = argument_string(args, "command")
    cwd_value = args.get("cwd", ".")
    if not isinstance(cwd_value, str) or not cwd_value:
        raise MiniCodeError("invalid_argument", "cwd must be a non-empty string")
    mutation_intent = args.get("mutation_intent", "read_only")
    if mutation_intent not in {"read_only", "may_mutate"}:
        raise MiniCodeError(
            "invalid_argument",
            "mutation_intent must be read_only or may_mutate",
        )
    timeout_ms = optional_int(
        args,
        "timeout_ms",
        DEFAULT_COMMAND_TIMEOUT_MS,
        minimum=1,
        maximum=MAX_COMMAND_TIMEOUT_MS,
    )
    if isinstance(preview_bytes, bool) or preview_bytes < 1:
        raise ValueError("preview_bytes must be positive")
    preview_bytes = min(preview_bytes, MAX_COMMAND_PREVIEW_BYTES)
    normalized = workspace.normalize_path(cwd_value, must_exist=True)
    if not normalized.is_dir:
        raise MiniCodeError("invalid_argument", "cwd must be a directory")
    require_shell_execute(
        workspace=workspace,
        relative_path=normalized.relative_path,
        context=context,
    )
    risk_class = classify_command_risk(command)
    request = ShellPolicyRequest(
        agent_id=context.agent_id,
        session_id=context.session_id,
        tool_name=RUN_COMMAND_NAME,
        action=SHELL_EXECUTE_ACTION,
        resource_scope=shell_scope(workspace.workspace_id),
        workspace_root=str(workspace.root),
        cwd=normalized.relative_path,
        command=command,
        mutation_intent=mutation_intent,  # type: ignore[arg-type]
        risk_class=risk_class,
    )
    host_policy = policy or DefaultShellHostPolicy()
    decision = host_policy.decide(request)
    if decision.decision == "deny":
        return error_result("host_policy_denied", decision.reason, retryable=False)
    if decision.decision == "confirm" and not host_policy.confirm(request):
        return error_result("host_confirmation_required", decision.reason, retryable=False)

    completed = (runner or SubprocessCommandRunner()).run(
        command,
        cwd=normalized.absolute_path,
        timeout_ms=timeout_ms,
        max_capture_bytes=max_capture_bytes,
    )
    if completed.output_limit_exceeded:
        return error_result(
            "output_limit_exceeded",
            f"stdout/stderr exceeded {max_capture_bytes} byte capture limit",
            retryable=False,
        )
    stdout = _stream_result(
        "stdout",
        completed.stdout,
        context=context,
        resources=resources,
        preview_bytes=preview_bytes,
    )
    stderr = _stream_result(
        "stderr",
        completed.stderr,
        context=context,
        resources=resources,
        preview_bytes=preview_bytes,
    )
    return success_result(
        {
            "exit_code": completed.exit_code,
            "timed_out": completed.timed_out,
            "duration_ms": completed.duration_ms,
            "cwd": normalized.relative_path,
            "stdout": stdout,
            "stderr": stderr,
        }
    )


async def run_command_handler(
    workspace: WorkspaceIdentity,
    arguments: Mapping[str, JsonValue],
    context: ToolExecutionContext,
    *,
    resources: ResourceService | None = None,
    policy: ShellHostPolicy | None = None,
    runner: CommandRunner | None = None,
    preview_bytes: int = DEFAULT_COMMAND_PREVIEW_BYTES,
    max_capture_bytes: int = DEFAULT_COMMAND_CAPTURE_BYTES,
) -> JsonValue:
    try:
        return run_command(
            workspace,
            arguments,
            context=context,
            resources=resources,
            policy=policy,
            runner=runner,
            preview_bytes=preview_bytes,
            max_capture_bytes=max_capture_bytes,
        )
    except MiniCodeError as error:
        raise _tool_error_from_minicode(error) from error


def require_shell_execute(
    *,
    workspace: WorkspaceIdentity,
    relative_path: str,
    context: ToolExecutionContext,
) -> None:
    evaluator = context.capability_evaluator
    if evaluator is None:
        raise ToolExecutionError(ErrorCode.EACCES, "shell authorization unavailable")
    decision = evaluator.authorize(
        AuthorizationRequest(
            agent_id=context.agent_id,
            action=SHELL_EXECUTE_ACTION,
            resource=shell_resource(workspace.workspace_id, relative_path),
        )
    )
    if not decision.allowed:
        raise ToolExecutionError(ErrorCode.EACCES, decision.reason)


def platform_shell_argv(command: str) -> list[str]:
    if os.name == "nt":
        return [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            command,
        ]
    return ["/bin/sh", "-c", command]


def classify_command_risk(command: str) -> str:
    normalized = command.strip()
    lowered = normalized.lower()
    if _looks_background_or_interactive(lowered):
        return "background_or_interactive"
    if _looks_external_side_effect(lowered):
        return "external_side_effect"
    return "local"


def _stream_result(
    name: str,
    data: bytes,
    *,
    context: ToolExecutionContext,
    resources: ResourceService | None,
    preview_bytes: int,
) -> dict[str, JsonValue]:
    preview_data = data[:preview_bytes]
    result: dict[str, JsonValue] = {
        "bytes": len(data),
        "preview": preview_data.decode("utf-8", errors="replace"),
        "preview_bytes": len(preview_data),
        "truncated": len(data) > len(preview_data),
    }
    if len(data) > preview_bytes:
        if resources is None:
            result["resource_unavailable"] = True
        else:
            handle = resources.create_artifact(
                data,
                owner=ResourceOwner(context.agent_id, context.session_id),
                media_type="text/plain",
                encoding="utf-8",
                source_tool_name=RUN_COMMAND_NAME,
                source_tool_call_id=context.tool_call_id,
                source_operation_id=f"{context.operation_id}:{name}",
            )
            result["resource"] = handle.as_dict()
    return result


def _read_capped(path: Path, limit: int) -> tuple[bytes, bool]:
    for attempt in range(6):
        try:
            with path.open("rb") as source:
                data = source.read(limit + 1)
            break
        except OSError:
            if attempt == 5:
                raise
            time.sleep(0.05)
    if len(data) > limit:
        return data[:limit], True
    return data, False


def _looks_background_or_interactive(command: str) -> bool:
    if command.endswith("&"):
        return True
    tokens = _tokens(command)
    return any(token in {"start", "pause", "read", "ssh", "cmd", "powershell"} for token in tokens)


def _looks_external_side_effect(command: str) -> bool:
    tokens = _tokens(command)
    blocked = {
        "curl",
        "wget",
        "scp",
        "ftp",
        "sftp",
        "telnet",
        "nc",
        "netcat",
        "ping",
        "iwr",
        "irm",
        "invoke-webrequest",
        "invoke-restmethod",
    }
    if tokens & blocked:
        return True
    return bool(re.search(r"\b(?:pip|npm|pnpm|yarn|cargo|go)\s+install\b", command))


def _tokens(command: str) -> set[str]:
    try:
        return {token.lower() for token in shlex.split(command, posix=os.name != "nt")}
    except ValueError:
        return {part.lower() for part in re.split(r"\s+", command) if part}


def _tool_error_from_minicode(error: MiniCodeError) -> ToolExecutionError:
    code = ErrorCode.EACCES if error.code == "outside_workspace" else ErrorCode.EINVAL
    if error.code == "path_not_found":
        code = ErrorCode.ENOENT
    return ToolExecutionError(code, error.message)
