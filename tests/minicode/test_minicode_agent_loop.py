from __future__ import annotations

import subprocess
import sys

from agentkernel import (
    AgentBudget,
    EventType,
    ModelUsage,
    OperationRecoveryClassification,
    ProcessState,
    ToolCall,
)
from minicode.config import MiniCodeConfig
from minicode.durable_patch import DurableApplyPatchAdapter
from minicode.loop import MiniCodeAgentLoop, MiniCodeRunStatus
from minicode.model import ScriptedModelAdapter, scripted_response
from minicode.patch import apply_mutation_plan
from minicode.testing import make_minicode_workspace
from minicode.tools import APPLY_PATCH_NAME, READ_FILE_NAME, RUN_COMMAND_NAME, SEARCH_FILES_NAME
from minicode.workspace import discover_workspace


def _pytest_command() -> str:
    return subprocess.list2cmdline([sys.executable, "-m", "pytest", "-q"])


def _tool(call_id: str, name: str, arguments: dict[str, object]) -> dict[str, object]:
    return {"call_id": call_id, "name": name, "arguments": arguments}


def _wrong_patch() -> str:
    return (
        "*** Begin Patch\n"
        "*** Update File: calculator.py\n"
        "@@\n"
        "-    return a / b\n"
        "+    return 0\n"
        "*** End Patch"
    )


def _correct_patch() -> str:
    return (
        "*** Begin Patch\n"
        "*** Update File: calculator.py\n"
        "@@\n"
        "-    if b == 0:\n"
        "-        raise ZeroDivisionError('division by zero')\n"
        "-    return 0\n"
        "+    if b == 0:\n"
        "+        return None\n"
        "+    return a / b\n"
        "*** End Patch"
    )


def _workspace_with_zero_division_test(tmp_path):
    fixture = make_minicode_workspace(tmp_path)
    (fixture.root / "tests" / "test_calculator.py").write_text(
        "from calculator import divide\n\n"
        "def test_divide():\n"
        "    assert divide(8, 2) == 4\n\n"
        "def test_zero_division_returns_none():\n"
        "    assert divide(1, 0) is None\n",
        encoding="utf-8",
    )
    return fixture, discover_workspace(cwd=fixture.root)


def test_scripted_loop_runs_patch_test_fix_trajectory(tmp_path):
    fixture, workspace = _workspace_with_zero_division_test(tmp_path)
    session_path = tmp_path / "session.jsonl"
    trace_path = tmp_path / "trace.jsonl"
    script = [
        scripted_response(tool_calls=(_tool("call-search", SEARCH_FILES_NAME, {"query": "divide"}),)),
        scripted_response(tool_calls=(_tool("call-read", READ_FILE_NAME, {"path": "calculator.py"}),)),
        scripted_response(tool_calls=(_tool("call-patch-wrong", APPLY_PATCH_NAME, {"patch": _wrong_patch()}),)),
        scripted_response(
            tool_calls=(
                _tool(
                    "call-test-fail",
                    RUN_COMMAND_NAME,
                    {"command": _pytest_command(), "mutation_intent": "read_only"},
                ),
            )
        ),
        scripted_response(tool_calls=(_tool("call-read-after-fail", READ_FILE_NAME, {"path": "calculator.py"}),)),
        scripted_response(tool_calls=(_tool("call-patch-correct", APPLY_PATCH_NAME, {"patch": _correct_patch()}),)),
        scripted_response(
            tool_calls=(
                _tool(
                    "call-test-pass",
                    RUN_COMMAND_NAME,
                    {"command": _pytest_command(), "mutation_intent": "read_only"},
                ),
            )
        ),
        scripted_response(text="fixed divide"),
    ]
    loop = MiniCodeAgentLoop(
        model=ScriptedModelAdapter(script),
        config=MiniCodeConfig(workspace=workspace.root, max_turns=12, trace_jsonl=trace_path),
        workspace=workspace,
        session_path=session_path,
    )

    result = loop.run("fix divide")

    assert result.status is MiniCodeRunStatus.COMPLETED
    assert "return None" in fixture.calculator.read_text(encoding="utf-8")
    events = [event.type for event in loop.session.events]
    assert EventType.TOOL_PREPARE in events
    assert EventType.TOOL_DISPATCH in events
    assert EventType.TOOL_COMMIT in events
    command_results = [
        event.data["output"]
        for event in loop.session.events
        if event.type is EventType.TOOL_RESULT and event.data["name"] == RUN_COMMAND_NAME
    ]
    assert [output["exit_code"] for output in command_results] == [1, 0]
    assert loop.usage.snapshot(loop.process_id).tool_calls >= 7
    assert loop.scheduler.manager.get(loop.process_id).state is ProcessState.EXITED
    assert trace_path.read_text(encoding="utf-8")


def test_context_request_includes_agents_md_and_projected_tool_schemas(tmp_path):
    fixture = make_minicode_workspace(tmp_path)
    workspace = discover_workspace(cwd=fixture.task_cwd)
    adapter = ScriptedModelAdapter([scripted_response(text="done")])
    loop = MiniCodeAgentLoop(
        model=adapter,
        config=MiniCodeConfig(workspace=workspace.root, task_cwd=fixture.task_cwd, max_turns=2),
        workspace=workspace,
    )

    result = loop.run("read instructions")

    request = adapter.requests[0]
    assert result.ok is True
    assert request.system_prompt is not None
    assert "Root instructions" in request.system_prompt
    assert "Nested instructions" in request.system_prompt
    assert {tool.name for tool in request.tools} >= {READ_FILE_NAME, APPLY_PATCH_NAME, RUN_COMMAND_NAME}


def test_resume_adds_new_turn_without_new_user_message(tmp_path):
    fixture = make_minicode_workspace(tmp_path)
    workspace = discover_workspace(cwd=fixture.root)
    session_path = tmp_path / "session.jsonl"
    first = MiniCodeAgentLoop(
        model=ScriptedModelAdapter([scripted_response(text="need another pass")]),
        config=MiniCodeConfig(workspace=workspace.root, max_turns=2),
        workspace=workspace,
        session_path=session_path,
    )
    first_result = first.run("initial task")
    assert first_result.ok is True

    resumed = MiniCodeAgentLoop.resume(
        model=ScriptedModelAdapter([scripted_response(text="resumed done")]),
        config=MiniCodeConfig(workspace=workspace.root, max_turns=2),
        workspace=workspace,
        session_path=session_path,
        session_id=first.session.session_id,
    )
    resumed_result = resumed.run(None)

    user_messages = [event for event in resumed.session.events if event.type is EventType.USER_MESSAGE]
    turns = [event.data["turn"] for event in resumed.session.events if event.type is EventType.TURN_START]
    assert resumed_result.ok is True
    assert len(user_messages) == 1
    assert turns == [1, 2]


def test_manual_recovery_stops_loop_before_new_turn(tmp_path):
    fixture = make_minicode_workspace(tmp_path)
    workspace = discover_workspace(cwd=fixture.root)
    loop = MiniCodeAgentLoop(
        model=ScriptedModelAdapter([scripted_response(text="should not run")]),
        config=MiniCodeConfig(workspace=workspace.root),
        workspace=workspace,
    )
    call = ToolCall("call-crash", APPLY_PATCH_NAME, {"patch": _wrong_patch()})
    prepared = DurableApplyPatchAdapter(loop.registry).prepare_call(
        workspace,
        call,
        loop.agent.control,
    )
    loop.session.append(EventType.TURN_START, {"turn": 1})
    loop.session.append(EventType.USER_MESSAGE, {"turn": 1, "content": "patch"})
    loop.session.append(EventType.STEP_START, {"turn": 1, "step": 1})
    loop.session.append(
        EventType.ASSISTANT_MESSAGE,
        {"turn": 1, "step": 1, "content": "", "tool_calls": [prepared.call.as_dict()]},
    )
    loop.session.append(EventType.TOOL_CALL, {"turn": 1, "step": 1, **prepared.call.as_dict()})
    loop.session.append(
        EventType.TOOL_PREPARE,
        {
            "turn": 1,
            "step": 1,
            "operation_id": prepared.operation_id,
            "tool_call_id": prepared.call.call_id,
            "tool_name": prepared.call.name,
            "effect_kind": "reconcilable_mutation",
        },
    )
    loop.session.append(
        EventType.TOOL_DISPATCH,
        {"turn": 1, "step": 1, "operation_id": prepared.operation_id, "attempt": 1},
    )
    loop.session.flush()
    apply_mutation_plan(prepared.plan)

    assert (
        loop.session.recovery_analysis.durable_operations[0].classification
        is OperationRecoveryClassification.RECONCILE_REQUIRED
    )

    result = loop.run("new task")

    assert result.status is MiniCodeRunStatus.RECOVERY_REQUIRED
    assert len(loop.model.requests) == 0


def test_cancel_and_budget_safe_points_return_structured_status(tmp_path):
    fixture = make_minicode_workspace(tmp_path)
    workspace = discover_workspace(cwd=fixture.root)
    cancelled = MiniCodeAgentLoop(
        model=ScriptedModelAdapter([scripted_response(text="nope")]),
        config=MiniCodeConfig(workspace=workspace.root),
        workspace=workspace,
    )
    cancelled.scheduler.request_cancel(cancelled.process_id, "host_cancelled")

    cancel_result = cancelled.run("cancel me")

    assert cancel_result.status is MiniCodeRunStatus.CANCELLED
    assert cancel_result.reason == "host_cancelled"

    budgeted = MiniCodeAgentLoop(
        model=ScriptedModelAdapter(
            [scripted_response(text="nope", usage=ModelUsage(input_tokens=1, output_tokens=0, total_tokens=1))]
        ),
        config=MiniCodeConfig(workspace=workspace.root),
        workspace=workspace,
    )
    budgeted.scheduler.update_process_budget(
        budgeted.process_id,
        AgentBudget(max_token_usage=0),
    )

    budget_result = budgeted.run("budget me")

    assert budget_result.status is MiniCodeRunStatus.BUDGET_EXCEEDED
