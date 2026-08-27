from __future__ import annotations

import asyncio

import pytest

from agentkernel import (
    Agent,
    CapabilityGrant,
    DurableToolExecutionError,
    DurableToolExecutor,
    ErrorCode,
    EventType,
    JsonlSessionPersistence,
    OperationRecoveryClassification,
    ReconcileStatus,
    Session,
    TOOL_EXECUTE_ACTION,
    ToolCall,
    ToolEffectKind,
    ToolRegistry,
)
from minicode.durable_patch import (
    DURABLE_PLAN_ARGUMENT,
    DurableApplyPatchAdapter,
)
from minicode.patch import apply_mutation_plan
from minicode.testing import make_minicode_workspace
from minicode.tools import (
    APPLY_PATCH_NAME,
    apply_patch_capability_grants,
    register_apply_patch_tool,
    tool_resource,
)
from minicode.workspace import discover_workspace


TURN = 1
STEP = 1


def _update_patch() -> str:
    return (
        "*** Begin Patch\n"
        "*** Update File: calculator.py\n"
        "@@\n"
        "-    return a / b\n"
        "+    return a // b\n"
        "*** End Patch"
    )


def _add_patch(path: str = "created.txt") -> str:
    return (
        "*** Begin Patch\n"
        f"*** Add File: {path}\n"
        "+created\n"
        "*** End Patch"
    )


def _delete_patch(path: str = "nested/notes.txt") -> str:
    return (
        "*** Begin Patch\n"
        f"*** Delete File: {path}\n"
        "*** End Patch"
    )


def _multi_file_patch() -> str:
    return (
        "*** Begin Patch\n"
        "*** Update File: calculator.py\n"
        "@@\n"
        "-    return a / b\n"
        "+    return a // b\n"
        "*** Add File: src/new_helper.py\n"
        "+def created():\n"
        "+    return True\n"
        "*** End Patch"
    )


def _workspace(tmp_path):
    fixture = make_minicode_workspace(tmp_path)
    workspace = discover_workspace(cwd=fixture.root)
    return fixture, workspace


def _agent(session: Session, workspace, *, workspace_write: bool = True) -> Agent:
    grants: tuple[CapabilityGrant, ...]
    if workspace_write:
        grants = apply_patch_capability_grants(
            agent_id="agent-1",
            workspace=workspace,
        )
    else:
        grants = (
            CapabilityGrant(
                "agent-1",
                TOOL_EXECUTE_ACTION,
                tool_resource(APPLY_PATCH_NAME),
            ),
        )
    return Agent.create(
        agent_id="agent-1",
        session=session,
        capability_grants=grants,
    )


def _registry(workspace, *, session: Session | None = None) -> ToolRegistry:
    return register_apply_patch_tool(ToolRegistry(), workspace, session=session)


def _prepared(workspace, registry: ToolRegistry, agent: Agent, patch: str):
    adapter = DurableApplyPatchAdapter(registry)
    return adapter.prepare_call(
        workspace,
        ToolCall("call-patch", APPLY_PATCH_NAME, {"patch": patch}),
        agent.control,
    )


def _append_call_prefix(session: Session, call: ToolCall) -> None:
    session.append(EventType.TURN_START, {"turn": TURN})
    session.append(EventType.USER_MESSAGE, {"turn": TURN, "content": "Patch it."})
    session.append(EventType.STEP_START, {"turn": TURN, "step": STEP})
    session.append(
        EventType.ASSISTANT_MESSAGE,
        {
            "turn": TURN,
            "step": STEP,
            "content": "",
            "tool_calls": [call.as_dict()],
        },
    )
    session.append(EventType.TOOL_CALL, {"turn": TURN, "step": STEP, **call.as_dict()})


def _append_prepare(session: Session, prepared) -> None:
    session.append(
        EventType.TOOL_PREPARE,
        {
            "turn": TURN,
            "step": STEP,
            "operation_id": prepared.operation_id,
            "tool_call_id": prepared.call.call_id,
            "tool_name": prepared.call.name,
            "effect_kind": ToolEffectKind.RECONCILABLE_MUTATION.value,
        },
    )
    session.flush()


def _append_dispatch(session: Session, operation_id: str) -> None:
    session.append(
        EventType.TOOL_DISPATCH,
        {
            "turn": TURN,
            "step": STEP,
            "operation_id": operation_id,
            "attempt": 1,
        },
    )
    session.flush()


def _commit_output(prepared) -> dict[str, object]:
    output = prepared.plan.to_result().to_dict()
    output.update(
        {
            "ok": True,
            "patch_digest": prepared.patch_digest,
            "operation_id": prepared.operation_id,
            "durable": True,
            "recovered": False,
        }
    )
    return output


def _append_commit(session: Session, prepared) -> None:
    session.append(
        EventType.TOOL_COMMIT,
        {
            "turn": TURN,
            "step": STEP,
            "operation_id": prepared.operation_id,
            "output": _commit_output(prepared),
        },
    )
    session.flush()


def _operation(session: Session):
    return session.recovery_analysis.durable_operations[0]


def _prepare_prefix(session: Session, prepared, *, dispatch: bool = False) -> None:
    _append_call_prefix(session, prepared.call)
    _append_prepare(session, prepared)
    if dispatch:
        _append_dispatch(session, prepared.operation_id)


def _apply_once(prepared, counter: dict[str, int]) -> None:
    apply_mutation_plan(prepared.plan)
    counter["dispatch_count"] += 1


def test_successful_durable_patch_records_prepare_dispatch_commit(tmp_path):
    fixture, workspace = _workspace(tmp_path)
    session = Session("session-1")
    agent = _agent(session, workspace)
    registry = _registry(workspace, session=session)
    prepared = _prepared(workspace, registry, agent, _update_patch())
    _append_call_prefix(session, prepared.call)

    result = asyncio.run(
        DurableToolExecutor(
            registry,
            operation_id_factory=lambda: prepared.operation_id,
        ).execute(
            prepared.call,
            agent.control,
            session,
            turn=TURN,
            step=STEP,
        )
    )

    assert result.ok is True
    assert "a // b" in fixture.calculator.read_text(encoding="utf-8")
    operation = _operation(session)
    assert operation.classification is OperationRecoveryClassification.COMPLETED
    assert operation.operation_id == prepared.operation_id
    events = [
        event.type
        for event in session.events
        if event.type
        in {EventType.TOOL_PREPARE, EventType.TOOL_DISPATCH, EventType.TOOL_COMMIT}
    ]
    assert events == [
        EventType.TOOL_PREPARE,
        EventType.TOOL_DISPATCH,
        EventType.TOOL_COMMIT,
    ]
    metadata = prepared.call.arguments[DURABLE_PLAN_ARGUMENT]
    assert metadata["operation_id"] == prepared.operation_id  # type: ignore[index]
    assert metadata["patch_digest"] == prepared.patch_digest  # type: ignore[index]
    assert metadata["plan"]["changed_files"] == ["calculator.py"]  # type: ignore[index]


def test_crash_before_prepare_has_no_durable_mutation_obligation(tmp_path):
    fixture, workspace = _workspace(tmp_path)
    session = Session("session-1")
    agent = _agent(session, workspace)
    registry = _registry(workspace, session=session)
    prepared = _prepared(workspace, registry, agent, _update_patch())

    _append_call_prefix(session, prepared.call)

    assert session.recovery_analysis.durable_operations == ()
    assert "a / b" in fixture.calculator.read_text(encoding="utf-8")


def test_crash_after_prepare_before_dispatch_is_safe_to_retry(tmp_path):
    fixture, workspace = _workspace(tmp_path)
    session = Session("session-1")
    agent = _agent(session, workspace)
    registry = _registry(workspace, session=session)
    prepared = _prepared(workspace, registry, agent, _update_patch())
    _prepare_prefix(session, prepared)

    operation = _operation(session)

    assert operation.operation_id == prepared.operation_id
    assert operation.classification is OperationRecoveryClassification.SAFE_TO_RETRY
    assert operation.dispatch_attempts == 0
    assert "a / b" in fixture.calculator.read_text(encoding="utf-8")


def test_crash_after_mutation_before_commit_reconciles_without_duplicate_dispatch(
    tmp_path,
):
    fixture, workspace = _workspace(tmp_path)
    path = tmp_path / "session.jsonl"
    session = Session("session-1", JsonlSessionPersistence(path))
    agent = _agent(session, workspace)
    registry_a = _registry(workspace, session=session)
    prepared = _prepared(workspace, registry_a, agent, _update_patch())
    counter = {"dispatch_count": 0}
    _prepare_prefix(session, prepared, dispatch=True)
    _apply_once(prepared, counter)
    session.close()

    restored = Session.load("session-1", JsonlSessionPersistence(path))
    restored_agent = _agent(restored, workspace)
    registry_b = _registry(workspace, session=restored)

    assert restored is not session
    operation = _operation(restored)
    assert operation.classification is OperationRecoveryClassification.RECONCILE_REQUIRED

    observed = asyncio.run(
        DurableApplyPatchAdapter(registry_b).reconcile(
            operation,
            restored_agent.control,
            restored,
        )
    )

    assert observed.status is ReconcileStatus.SUCCEEDED
    assert observed.output["recovered"] is True  # type: ignore[index]
    assert observed.output["recovery"]["action_taken"] == "recognized_existing_mutation"  # type: ignore[index]
    assert counter["dispatch_count"] == 1
    assert "a // b" in fixture.calculator.read_text(encoding="utf-8")
    recovered = _operation(restored)
    assert recovered.classification is OperationRecoveryClassification.COMPLETED
    assert [
        event.type
        for event in restored.events
        if event.type in {EventType.TOOL_RECONCILE, EventType.TOOL_COMMIT}
    ] == [EventType.TOOL_RECONCILE, EventType.TOOL_COMMIT]
    restored.close()


def test_crash_after_commit_is_completed_and_not_reconciled(tmp_path):
    fixture, workspace = _workspace(tmp_path)
    session = Session("session-1")
    agent = _agent(session, workspace)
    registry = _registry(workspace, session=session)
    prepared = _prepared(workspace, registry, agent, _update_patch())
    counter = {"dispatch_count": 0}
    _prepare_prefix(session, prepared, dispatch=True)
    _apply_once(prepared, counter)
    _append_commit(session, prepared)
    operation = _operation(session)

    assert operation.classification is OperationRecoveryClassification.COMPLETED
    assert counter["dispatch_count"] == 1
    assert "a // b" in fixture.calculator.read_text(encoding="utf-8")
    with pytest.raises(DurableToolExecutionError, match="does not require reconciliation"):
        asyncio.run(DurableApplyPatchAdapter(registry).reconcile(operation, agent.control, session))
    assert counter["dispatch_count"] == 1


def test_current_preimage_after_dispatch_reconciles_not_found_then_retry_needs_current_write_authority(
    tmp_path,
):
    fixture, workspace = _workspace(tmp_path)
    session = Session("session-1")
    agent = _agent(session, workspace)
    registry = _registry(workspace, session=session)
    prepared = _prepared(workspace, registry, agent, _update_patch())
    _prepare_prefix(session, prepared, dispatch=True)

    observed = asyncio.run(
        DurableApplyPatchAdapter(registry).reconcile(
            _operation(session),
            agent.control,
            session,
        )
    )
    assert observed.status is ReconcileStatus.NOT_FOUND
    assert _operation(session).classification is OperationRecoveryClassification.SAFE_TO_RETRY
    assert "a / b" in fixture.calculator.read_text(encoding="utf-8")

    reduced_agent = _agent(session, workspace, workspace_write=False)
    result = asyncio.run(
        DurableToolExecutor(registry).retry(_operation(session), reduced_agent.control, session)
    )

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ErrorCode.EACCES
    assert "a / b" in fixture.calculator.read_text(encoding="utf-8")


def test_hash_conflict_returns_manual_required_without_blind_mutation(tmp_path):
    fixture, workspace = _workspace(tmp_path)
    session = Session("session-1")
    agent = _agent(session, workspace)
    registry = _registry(workspace, session=session)
    prepared = _prepared(workspace, registry, agent, _update_patch())
    _prepare_prefix(session, prepared, dispatch=True)
    fixture.calculator.write_text("third state\n", encoding="utf-8")

    observed = asyncio.run(
        DurableApplyPatchAdapter(registry).reconcile(
            _operation(session),
            agent.control,
            session,
        )
    )

    assert observed.status is ReconcileStatus.UNKNOWN
    assert "manual_intervention_required" in (observed.message or "")
    assert fixture.calculator.read_text(encoding="utf-8") == "third state\n"


def test_add_file_recovery_recognizes_absent_preimage_and_postimage(tmp_path):
    fixture, workspace = _workspace(tmp_path)
    session = Session("session-1")
    agent = _agent(session, workspace)
    registry = _registry(workspace, session=session)
    prepared = _prepared(workspace, registry, agent, _add_patch())
    counter = {"dispatch_count": 0}
    _prepare_prefix(session, prepared, dispatch=True)
    _apply_once(prepared, counter)

    observed = asyncio.run(
        DurableApplyPatchAdapter(registry).reconcile(
            _operation(session),
            agent.control,
            session,
        )
    )

    assert observed.status is ReconcileStatus.SUCCEEDED
    assert observed.output["preimage_hashes"]["created.txt"] is None  # type: ignore[index]
    assert observed.output["postimage_hashes"]["created.txt"] is not None  # type: ignore[index]
    assert (fixture.root / "created.txt").read_text(encoding="utf-8") == "created\n"
    assert counter["dispatch_count"] == 1


def test_delete_file_recovery_recognizes_absent_postimage(tmp_path):
    fixture, workspace = _workspace(tmp_path)
    session = Session("session-1")
    agent = _agent(session, workspace)
    registry = _registry(workspace, session=session)
    prepared = _prepared(workspace, registry, agent, _delete_patch())
    counter = {"dispatch_count": 0}
    _prepare_prefix(session, prepared, dispatch=True)
    _apply_once(prepared, counter)

    observed = asyncio.run(
        DurableApplyPatchAdapter(registry).reconcile(
            _operation(session),
            agent.control,
            session,
        )
    )

    assert observed.status is ReconcileStatus.SUCCEEDED
    assert observed.output["preimage_hashes"]["nested/notes.txt"] is not None  # type: ignore[index]
    assert observed.output["postimage_hashes"]["nested/notes.txt"] is None  # type: ignore[index]
    assert not (fixture.root / "nested" / "notes.txt").exists()
    assert counter["dispatch_count"] == 1


def test_multi_file_recovery_requires_every_path_to_match_postimage(tmp_path):
    fixture, workspace = _workspace(tmp_path)
    session = Session("session-1")
    agent = _agent(session, workspace)
    registry = _registry(workspace, session=session)
    prepared = _prepared(workspace, registry, agent, _multi_file_patch())
    counter = {"dispatch_count": 0}
    _prepare_prefix(session, prepared, dispatch=True)
    _apply_once(prepared, counter)

    observed = asyncio.run(
        DurableApplyPatchAdapter(registry).reconcile(
            _operation(session),
            agent.control,
            session,
        )
    )

    assert observed.status is ReconcileStatus.SUCCEEDED
    assert sorted(observed.output["changed_files"]) == ["calculator.py", "src/new_helper.py"]  # type: ignore[index]
    assert "a // b" in fixture.calculator.read_text(encoding="utf-8")
    assert (fixture.root / "src" / "new_helper.py").exists()
    assert counter["dispatch_count"] == 1


def test_multi_file_mixed_state_requires_manual_intervention(tmp_path):
    fixture, workspace = _workspace(tmp_path)
    session = Session("session-1")
    agent = _agent(session, workspace)
    registry = _registry(workspace, session=session)
    prepared = _prepared(workspace, registry, agent, _multi_file_patch())
    _prepare_prefix(session, prepared, dispatch=True)
    fixture.calculator.write_text(
        fixture.calculator.read_text(encoding="utf-8").replace("a / b", "a // b"),
        encoding="utf-8",
    )

    observed = asyncio.run(
        DurableApplyPatchAdapter(registry).reconcile(
            _operation(session),
            agent.control,
            session,
        )
    )

    assert observed.status is ReconcileStatus.UNKNOWN
    assert "manual_intervention_required" in (observed.message or "")
    assert not (fixture.root / "src" / "new_helper.py").exists()
