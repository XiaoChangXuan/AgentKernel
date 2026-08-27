from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Mapping

from agentkernel.agent import AgentControlBlock
from agentkernel.durable_tools import DurableToolExecutor
from agentkernel.protocol import ErrorCode, JsonValue, ToolCall, ToolResult
from agentkernel.recovery import DurableOperationRecovery
from agentkernel.session import Session
from agentkernel.tool_effects import ReconcileResult, ReconcileStatus
from agentkernel.tools import ToolExecutionContext, ToolExecutionError, ToolRegistry

from minicode.patch import (
    AddFile,
    DeleteFile,
    Hunk,
    PatchError,
    PatchMutationPlan,
    UpdateFile,
    apply_mutation_plan,
    hash_file_or_absent,
    parse_patch,
    plan_parsed_patch,
)
from minicode.workspace import WorkspaceIdentity

from .tools.common import argument_string, require_workspace_write
from .tools.schemas import (
    APPLY_PATCH_NAME,
    WORKSPACE_WRITE_ACTION,
    success_result,
    workspace_scope,
)

DURABLE_PLAN_ARGUMENT = "__minicode_durable_plan"
DURABLE_PLAN_VERSION = 1


@dataclass(frozen=True, slots=True)
class DurablePatchPreparation:
    call: ToolCall
    operation_id: str
    patch_digest: str
    changed_files: tuple[str, ...]
    plan: PatchMutationPlan


@dataclass(frozen=True, slots=True)
class PatchReconciliation:
    operation_id: str
    state: str
    action_taken: str
    changed_files: tuple[str, ...]
    current_hashes: dict[str, str | None]
    expected_preimages: dict[str, str | None]
    expected_postimages: dict[str, str | None]
    manual_reason: str | None = None

    def to_output(
        self,
        *,
        patch_digest: str,
        hunk_count: int,
        summary: tuple[dict[str, object], ...],
    ) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = success_result(
            {
                "applied": True,
                "changed_files": list(self.changed_files),
                "hunk_count": hunk_count,
                "summary": list(summary),  # type: ignore[arg-type]
                "preimage_hashes": self.expected_preimages,
                "postimage_hashes": self.expected_postimages,
                "patch_digest": patch_digest,
                "operation_id": self.operation_id,
                "recovered": True,
                "recovery": {
                    "state": self.state,
                    "action_taken": self.action_taken,
                    "current_hashes": self.current_hashes,
                    "expected_preimages": self.expected_preimages,
                    "expected_postimages": self.expected_postimages,
                    "manual_reason": self.manual_reason,
                },
            }
        )
        return payload


class DurableApplyPatchAdapter:
    """MiniCode-side orchestration around AgentKernel durable Tool execution."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def prepare_call(
        self,
        workspace: WorkspaceIdentity,
        call: ToolCall,
        agent: AgentControlBlock,
    ) -> DurablePatchPreparation:
        return prepare_durable_patch_call(workspace, call, agent)

    async def execute(
        self,
        workspace: WorkspaceIdentity,
        call: ToolCall,
        agent: AgentControlBlock,
        session: Session,
        *,
        turn: int,
        step: int,
    ) -> ToolResult:
        prepared = self.prepare_call(workspace, call, agent)
        executor = DurableToolExecutor(
            self._registry,
            operation_id_factory=lambda: prepared.operation_id,
        )
        return await executor.execute(prepared.call, agent, session, turn=turn, step=step)

    async def reconcile(
        self,
        operation: DurableOperationRecovery,
        agent: AgentControlBlock,
        session: Session,
    ) -> ReconcileResult:
        return await DurableToolExecutor(self._registry).reconcile(
            operation,
            agent,
            session,
        )


def prepare_durable_patch_call(
    workspace: WorkspaceIdentity,
    call: ToolCall,
    agent: AgentControlBlock,
) -> DurablePatchPreparation:
    if call.name != APPLY_PATCH_NAME:
        raise ValueError(f"durable patch adapter cannot prepare tool: {call.name}")
    patch_text = argument_string(dict(call.arguments), "patch")
    parsed = parse_patch(patch_text)
    preauth_context = ToolExecutionContext(
        agent_id=agent.agent_id,
        session_id=agent.session_id,
        tool_call_id=call.call_id,
        operation_id="minicode-preplan-authorization",
        capability_evaluator=ToolRegistry().evaluator_for_agent(agent),
    )
    for path in parsed.paths:
        normalized = workspace.normalize_path(path, must_exist=False)
        require_workspace_write(
            workspace=workspace,
            relative_path=normalized.relative_path,
            context=preauth_context,
        )
    plan = plan_parsed_patch(workspace, parsed)
    patch_digest = canonical_patch_digest(parsed)
    operation_id = stable_patch_operation_id(
        agent_id=agent.agent_id,
        tool_call_id=call.call_id,
        patch_digest=patch_digest,
        workspace_id=workspace.workspace_id,
        changed_files=plan.changed_files,
    )
    durable_payload = {
        "version": DURABLE_PLAN_VERSION,
        "operation_id": operation_id,
        "patch_digest": patch_digest,
        "workspace_id": workspace.workspace_id,
        "workspace_scope": workspace_scope(workspace.workspace_id),
        "capability_action": WORKSPACE_WRITE_ACTION,
        "capability_resource_scope": workspace_scope(workspace.workspace_id),
        "plan": plan.to_durable_dict(),
    }
    arguments = dict(call.arguments)
    arguments[DURABLE_PLAN_ARGUMENT] = durable_payload
    prepared = ToolCall(call.call_id, call.name, arguments)
    return DurablePatchPreparation(
        call=prepared,
        operation_id=operation_id,
        patch_digest=patch_digest,
        changed_files=plan.changed_files,
        plan=plan,
    )


async def durable_apply_patch_handler(
    workspace: WorkspaceIdentity,
    arguments: Mapping[str, JsonValue],
    context: ToolExecutionContext,
) -> JsonValue:
    metadata = _metadata_from_arguments(arguments)
    plan = _plan_from_metadata(workspace, metadata)
    _verify_operation_context(metadata, context)
    for file_plan in plan.files:
        require_workspace_write(
            workspace=workspace,
            relative_path=file_plan.relative_path,
            context=context,
        )
    try:
        result = apply_mutation_plan(plan)
    except PatchError as error:
        raise ToolExecutionError(_error_code_for_patch(error), error.message) from error
    payload = result.to_dict()
    payload.update(
        {
            "ok": True,
            "patch_digest": str(metadata["patch_digest"]),
            "operation_id": context.operation_id,
            "durable": True,
            "recovered": False,
        }
    )
    return payload  # type: ignore[return-value]


async def durable_apply_patch_reconcile_handler(
    workspace: WorkspaceIdentity,
    session: Session,
    context: ToolExecutionContext,
) -> ReconcileResult:
    metadata = durable_metadata_for_operation(session, context.operation_id)
    plan = _plan_from_metadata(workspace, metadata)
    _verify_operation_context(metadata, context)
    reconciliation = reconcile_patch_plan(context.operation_id, plan)
    if reconciliation.state == "completed":
        output = reconciliation.to_output(
            patch_digest=str(metadata["patch_digest"]),
            hunk_count=plan.hunk_count,
            summary=plan.to_result().summary,
        )
        return ReconcileResult(
            ReconcileStatus.SUCCEEDED,
            output=output,
            message="durable patch postimage already present",
        )
    if reconciliation.state == "not_dispatched":
        return ReconcileResult(
            ReconcileStatus.NOT_FOUND,
            message="workspace still matches durable patch preimage",
        )
    return ReconcileResult(
        ReconcileStatus.UNKNOWN,
        message=reconciliation.manual_reason
        or "manual_intervention_required: workspace diverged from durable patch facts",
    )


def reconcile_patch_plan(
    operation_id: str,
    plan: PatchMutationPlan,
) -> PatchReconciliation:
    current = {
        file_plan.relative_path: hash_file_or_absent(file_plan.path)
        for file_plan in plan.files
    }
    preimages = {
        file_plan.relative_path: file_plan.preimage_hash for file_plan in plan.files
    }
    postimages = {
        file_plan.relative_path: file_plan.postimage_hash for file_plan in plan.files
    }
    all_post = all(
        current[file_plan.relative_path] == file_plan.postimage_hash
        for file_plan in plan.files
    )
    all_pre = all(
        current[file_plan.relative_path] == file_plan.preimage_hash
        for file_plan in plan.files
    )
    if all_post:
        return PatchReconciliation(
            operation_id=operation_id,
            state="completed",
            action_taken="recognized_existing_mutation",
            changed_files=plan.changed_files,
            current_hashes=current,
            expected_preimages=preimages,
            expected_postimages=postimages,
        )
    if all_pre:
        return PatchReconciliation(
            operation_id=operation_id,
            state="not_dispatched",
            action_taken="no_mutation_performed",
            changed_files=plan.changed_files,
            current_hashes=current,
            expected_preimages=preimages,
            expected_postimages=postimages,
        )
    return PatchReconciliation(
        operation_id=operation_id,
        state="manual_required",
        action_taken="no_blind_retry",
        changed_files=plan.changed_files,
        current_hashes=current,
        expected_preimages=preimages,
        expected_postimages=postimages,
        manual_reason=(
            "manual_intervention_required: at least one patch path is neither "
            "the recorded preimage nor the expected postimage"
        ),
    )


def durable_metadata_for_operation(
    session: Session,
    operation_id: str,
) -> dict[str, JsonValue]:
    for event in session.events:
        if event.type.value != "tool/call":
            continue
        raw = event.data.get("arguments")
        if not isinstance(raw, Mapping):
            continue
        metadata = raw.get(DURABLE_PLAN_ARGUMENT)
        if not isinstance(metadata, Mapping):
            continue
        if metadata.get("operation_id") == operation_id:
            return dict(metadata)
    raise ToolExecutionError(
        ErrorCode.EIO,
        f"durable patch metadata not found for operation: {operation_id}",
    )


def canonical_patch_digest(parsed: object) -> str:
    payload = _canonical_patch_payload(parsed)
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def stable_patch_operation_id(
    *,
    agent_id: str,
    tool_call_id: str,
    patch_digest: str,
    workspace_id: str,
    changed_files: tuple[str, ...],
) -> str:
    payload = {
        "agent_id": agent_id,
        "tool_call_id": tool_call_id,
        "tool": APPLY_PATCH_NAME,
        "patch_digest": patch_digest,
        "workspace_id": workspace_id,
        "changed_files": list(changed_files),
        "capability_action": WORKSPACE_WRITE_ACTION,
        "capability_resource_scope": workspace_scope(workspace_id),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"minicode_patch_{digest[:32]}"


def _metadata_from_arguments(arguments: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    metadata = arguments.get(DURABLE_PLAN_ARGUMENT)
    if not isinstance(metadata, Mapping):
        raise ToolExecutionError(
            ErrorCode.EINVAL,
            "durable apply_patch requires prepared MiniCode patch metadata",
        )
    return dict(metadata)


def _plan_from_metadata(
    workspace: WorkspaceIdentity,
    metadata: Mapping[str, JsonValue],
) -> PatchMutationPlan:
    if metadata.get("version") != DURABLE_PLAN_VERSION:
        raise ToolExecutionError(ErrorCode.EINVAL, "unsupported durable patch metadata")
    if metadata.get("workspace_id") != workspace.workspace_id:
        raise ToolExecutionError(
            ErrorCode.EACCES,
            "durable patch workspace identity does not match current workspace",
        )
    try:
        return PatchMutationPlan.from_durable_dict(workspace, metadata.get("plan"))
    except PatchError as error:
        raise ToolExecutionError(ErrorCode.EINVAL, error.message) from error


def _verify_operation_context(
    metadata: Mapping[str, JsonValue],
    context: ToolExecutionContext,
) -> None:
    if metadata.get("operation_id") != context.operation_id:
        raise ToolExecutionError(
            ErrorCode.EINVAL,
            "durable patch operation identity does not match execution context",
        )


def _canonical_patch_payload(parsed: object) -> dict[str, object]:
    operations: list[dict[str, object]] = []
    for operation in getattr(parsed, "operations"):
        if isinstance(operation, AddFile):
            operations.append(
                {
                    "kind": "add",
                    "path": operation.path,
                    "lines": list(operation.lines),
                }
            )
        elif isinstance(operation, DeleteFile):
            operations.append({"kind": "delete", "path": operation.path})
        elif isinstance(operation, UpdateFile):
            operations.append(
                {
                    "kind": "update",
                    "path": operation.path,
                    "hunks": [_canonical_hunk(hunk) for hunk in operation.hunks],
                }
            )
        else:
            raise TypeError(f"unsupported patch operation: {operation!r}")
    return {"operations": operations}


def _canonical_hunk(hunk: Hunk) -> dict[str, object]:
    return {
        "header": hunk.header,
        "lines": [
            {"kind": line.kind, "text": line.text}
            for line in hunk.lines
        ],
    }


def _error_code_for_patch(error: PatchError) -> ErrorCode:
    if error.code in {"outside_workspace", "workspace_state_changed"}:
        return ErrorCode.EACCES
    if error.code == "file_not_found":
        return ErrorCode.ENOENT
    return ErrorCode.EINVAL
