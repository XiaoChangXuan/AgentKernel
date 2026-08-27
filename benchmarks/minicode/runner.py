"""Deterministic MiniCode Phase 2F validation runner."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentkernel import (
    Agent,
    AgentBudget,
    CapabilityEvaluator,
    CapabilityGrant,
    ErrorCode,
    EventType,
    JsonlSessionPersistence,
    LocalResourceStore,
    ModelUsage,
    OperationRecoveryClassification,
    ProcessState,
    RESOURCE_READ_ACTION,
    ReconcileStatus,
    ResourceAccessDenied,
    ResourceOwner,
    ResourceService,
    Session,
    TOOL_EXECUTE_ACTION,
    ToolCall,
    ToolRegistry,
)
from minicode.config import MiniCodeConfig
from minicode.durable_patch import DurableApplyPatchAdapter
from minicode.errors import MiniCodeError
from minicode.loop import MiniCodeAgentLoop, MiniCodeRunStatus
from minicode.model import ScriptedModelAdapter, scripted_response
from minicode.patch import apply_mutation_plan
from minicode.testing import make_minicode_workspace
from minicode.tools import (
    APPLY_PATCH_NAME,
    READ_FILE_NAME,
    RUN_COMMAND_NAME,
    SEARCH_FILES_NAME,
    SHELL_EXECUTE_ACTION,
    apply_patch_capability_grants,
    register_apply_patch_tool,
    run_command_capability_grants,
    tool_resource,
)
from minicode.tools.run_command import CommandCompleted
from minicode.trace import TraceRecorder
from minicode.workspace import discover_workspace


DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "results" / "minicode_phase2f_validation.json"
BENCHMARK_VERSION = "minicode.phase2f.validation.v0"
RUNTIME_VERSION = "AgentKernel V0.8 + MiniCode v0"


@dataclass(frozen=True, slots=True)
class MiniCodeValidationResult:
    check_id: str
    name: str
    status: str
    scenario: str
    fixture: str
    scripted_model_behavior: str
    actual_tools_used: tuple[str, ...]
    failure_injection: str | None
    invariant: str
    oracle: str
    forbidden_outcome: str
    evidence: Mapping[str, Any]

    @property
    def passed(self) -> bool:
        return self.status == "PASS"

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "check_id": self.check_id,
            "name": self.name,
            "status": self.status,
            "scenario": self.scenario,
            "fixture": self.fixture,
            "scripted_model_behavior": self.scripted_model_behavior,
            "actual_minicode_tools_used": list(self.actual_tools_used),
            "failure_injection": self.failure_injection,
            "agentkernel_invariant_exercised": self.invariant,
            "oracle": self.oracle,
            "forbidden_outcome": self.forbidden_outcome,
            "evidence": _stable_json(self.evidence),
        }
        return payload


@dataclass(frozen=True, slots=True)
class MiniCodePhase2FValidationDocument:
    checks: tuple[MiniCodeValidationResult, ...]
    runtime_version: str = RUNTIME_VERSION
    benchmark_version: str = BENCHMARK_VERSION

    def as_dict(self) -> dict[str, Any]:
        passed = sum(1 for check in self.checks if check.passed)
        failed = len(self.checks) - passed
        return {
            "benchmark_version": self.benchmark_version,
            "runtime_version": self.runtime_version,
            "suite": "minicode_phase2f_validation",
            "deterministic": True,
            "offline": True,
            "network": "not_used",
            "summary": {
                "total": len(self.checks),
                "passed": passed,
                "failed": failed,
                "decision": "PASS" if failed == 0 else "FAIL",
            },
            "checks": [check.as_dict() for check in self.checks],
            "future_integrationbench_contract": {
                "status": "not_claimed_by_phase2f_validation",
                "frozen_ids": [
                    "I1 Basic edit",
                    "I2 Test-and-fix loop",
                    "I3 Crash/resume",
                    "I4 Large stdout ResourceHandle",
                    "I5 Capability denial",
                    "I6 Budget exhaustion",
                    "I7 Durable mutation crash/recovery",
                    "I8 Reviewer child Agent - DEFERRED",
                ],
            },
            "limitations": [
                "Synthetic local fixtures only.",
                "ScriptedModelAdapter drives deterministic decisions.",
                "Phase 2F validation measures MiniCode CodeAgent integration behavior, not model intelligence.",
                "Real-model demo is opt-in and not a release oracle.",
                "This is not the frozen Phase 2G IntegrationBench release artifact.",
                "Reviewer child Agent remains deferred.",
            ],
        }


class CountingRunner:
    def __init__(
        self,
        *,
        exit_code: int | None = 0,
        timed_out: bool = False,
        stdout: bytes = b"",
        stderr: bytes = b"",
    ) -> None:
        self.exit_code = exit_code
        self.timed_out = timed_out
        self.stdout = stdout
        self.stderr = stderr
        self.dispatch_count = 0

    def run(
        self,
        command: str,
        *,
        cwd: Path,
        timeout_ms: int,
        max_capture_bytes: int,
    ) -> CommandCompleted:
        self.dispatch_count += 1
        return CommandCompleted(
            exit_code=self.exit_code,
            timed_out=self.timed_out,
            duration_ms=1,
            stdout=self.stdout,
            stderr=self.stderr,
        )


def run_phase2f_validation() -> MiniCodePhase2FValidationDocument:
    with tempfile.TemporaryDirectory(prefix="minicode-phase2f-validation-") as root:
        tmp = Path(root)
        checks = (
            _check_f1_workspace(tmp / "f1"),
            _check_f2_tool_visibility(tmp / "f2"),
            _check_f3_durable_patch_recovery(tmp / "f3"),
            _check_f4_resource_authority(tmp / "f4"),
            _check_f5_nonzero_command(tmp / "f5"),
            _check_f6_budget_exhaustion(tmp / "f6"),
            _check_f7_resume_handoff(tmp / "f7"),
            _check_f8_trace_redaction(tmp / "f8"),
        )
    return MiniCodePhase2FValidationDocument(checks=checks)


def write_phase2f_validation(
    document: MiniCodePhase2FValidationDocument,
    output: str | Path = DEFAULT_OUTPUT,
) -> Path:
    path = Path(output)
    if not path.is_absolute():
        path = DEFAULT_OUTPUT.parent / path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document.as_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def format_human_report(document: MiniCodePhase2FValidationDocument) -> str:
    names = {
        "F1": "Workspace",
        "F2": "Tool Visibility",
        "F3": "Durable Patch Recovery",
        "F4": "Resource Authority",
        "F5": "Nonzero Command",
        "F6": "Budget Block",
        "F7": "Resume / Handoff",
        "F8": "Trace Redaction",
    }
    lines = ["MiniCode Phase 2F Validation", ""]
    for check in document.checks:
        label = f"{check.check_id} {names.get(check.check_id, check.name)}"
        lines.append(f"{label:<36} {check.status}")
    summary = document.as_dict()["summary"]
    lines.extend(
        [
            "",
            "Phase 2F Validation:",
            f"{summary['passed']}/{summary['total']} PASS",
        ]
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run deterministic MiniCode Phase 2F validation.")
    parser.add_argument("--json", action="store_true", help="Print the structured JSON document.")
    parser.add_argument("--json-output", default=str(DEFAULT_OUTPUT), help="Write structured JSON evidence.")
    parser.add_argument("--no-write", action="store_true", help="Do not write the evidence artifact.")
    args = parser.parse_args(argv)

    document = run_phase2f_validation()
    print(json.dumps(document.as_dict(), ensure_ascii=False, indent=2, sort_keys=True) if args.json else format_human_report(document))
    if not args.no_write:
        write_phase2f_validation(document, args.json_output)
    return 0 if document.as_dict()["summary"]["decision"] == "PASS" else 1


def _check_f1_workspace(tmp_path: Path) -> MiniCodeValidationResult:
    tmp_path.mkdir(parents=True)
    fixture = make_minicode_workspace(tmp_path)
    workspace = discover_workspace(cwd=fixture.task_cwd)
    outside_denied = False
    try:
        workspace.normalize_path("../outside.txt")
    except MiniCodeError:
        outside_denied = True
    request = ScriptedModelAdapter([scripted_response(text="done")])
    loop = MiniCodeAgentLoop(
        model=request,
        config=MiniCodeConfig(workspace=workspace.root, task_cwd=fixture.task_cwd, max_turns=1),
        workspace=workspace,
        session_path=tmp_path / "session.jsonl",
    )
    result = loop.run("inspect workspace")
    first_request = request.requests[0]
    success = (
        result.status is MiniCodeRunStatus.COMPLETED
        and workspace.root == fixture.root.resolve()
        and workspace.task_cwd == fixture.task_cwd.resolve()
        and outside_denied
        and first_request.system_prompt is not None
        and "Root instructions" in first_request.system_prompt
        and "Nested instructions" in first_request.system_prompt
    )
    return MiniCodeValidationResult(
        "F1",
        "Workspace",
        _status(success),
        "MiniCode discovers a fixture workspace, constrains paths, and projects AGENTS.md instructions.",
        "make_minicode_workspace nested task cwd fixture",
        "single scripted final response after model request construction",
        (),
        None,
        "Workspace identity and instruction discovery integrate with Context VM without granting authority",
        "nearest fixture root found, task cwd preserved, path escape denied, AGENTS.md included",
        "Treating AGENTS.md or model text as authority, or permitting path escape",
        {
            "run_status": result.status.value,
            "workspace_root_matches_fixture": workspace.root == fixture.root.resolve(),
            "task_cwd_matches_fixture": workspace.task_cwd == fixture.task_cwd.resolve(),
            "outside_path_denied": outside_denied,
            "root_agents_md_seen": "Root instructions" in (first_request.system_prompt or ""),
            "nested_agents_md_seen": "Nested instructions" in (first_request.system_prompt or ""),
        },
    )


def _check_f2_tool_visibility(tmp_path: Path) -> MiniCodeValidationResult:
    tmp_path.mkdir(parents=True)
    fixture = make_minicode_workspace(tmp_path)
    workspace = discover_workspace(cwd=fixture.root)
    runner = CountingRunner(stdout=b"should not dispatch")
    loop = MiniCodeAgentLoop(
        model=ScriptedModelAdapter(
            [
                scripted_response(tool_calls=(_tool("f2-denied", RUN_COMMAND_NAME, {"command": "echo denied", "mutation_intent": "read_only"}),)),
                scripted_response(text="denial observed"),
            ]
        ),
        config=MiniCodeConfig(workspace=workspace.root, max_turns=4),
        workspace=workspace,
        session_path=tmp_path / "session.jsonl",
        command_runner=runner,
    )
    loop.agent = Agent.create(
        agent_id=loop.agent.control.agent_id,
        session=loop.session,
        capability_grants=(
            CapabilityGrant(loop.agent.control.agent_id, TOOL_EXECUTE_ACTION, tool_resource(RUN_COMMAND_NAME)),
        ),
    )

    schemas = loop.registry.model_schemas(loop.agent.control)
    result = loop.run("Attempt unauthorized shell.")
    denied = _first_failed_tool_result(loop.session, RUN_COMMAND_NAME)
    success = (
        result.status is MiniCodeRunStatus.COMPLETED
        and any(tool.name == RUN_COMMAND_NAME for tool in schemas)
        and denied is not None
        and denied.error is not None
        and denied.error.code is ErrorCode.EACCES
        and runner.dispatch_count == 0
    )
    return MiniCodeValidationResult(
        "F2",
        "Tool visibility",
        _status(success),
        "ToolRegistry exposes model schemas according to tool authority and still denies execution without shell authority.",
        "restricted Agent capability profile",
        "model sees run_command tool.execute grant, proposes shell command, execution denies missing shell.execute",
        (RUN_COMMAND_NAME,),
        "Missing shell.execute grant",
        "Tool visibility is not the same as complete execution authority",
        "run_command schema visible, EACCES result recorded, subprocess dispatch count is zero",
        "Model-visible tool schema bypasses execution-time capability check",
        {
            "run_status": result.status.value,
            "schema_visible": any(tool.name == RUN_COMMAND_NAME for tool in schemas),
            "denial_code": denied.error.code.value if denied and denied.error else None,
            "subprocess_dispatch_count": runner.dispatch_count,
            "model_observed_denial": len(loop.model.requests) >= 2,
        },
    )


def _check_f3_durable_patch_recovery(tmp_path: Path) -> MiniCodeValidationResult:
    result = _durable_patch_recovery(tmp_path, check_id="F3", name="Durable patch recovery")
    return result


def _check_f4_resource_authority(tmp_path: Path) -> MiniCodeValidationResult:
    tmp_path.mkdir(parents=True)
    fixture = make_minicode_workspace(tmp_path)
    workspace = discover_workspace(cwd=fixture.root)
    command = subprocess.list2cmdline([sys.executable, "-c", "import sys; sys.stdout.write('x'*5000)"])
    adapter = ScriptedModelAdapter(
        [
            scripted_response(tool_calls=(_tool("f4-large", RUN_COMMAND_NAME, {"command": command, "mutation_intent": "read_only"}),)),
            scripted_response(text="observed handle"),
        ]
    )
    loop = MiniCodeAgentLoop(
        model=adapter,
        config=MiniCodeConfig(workspace=workspace.root, max_turns=4),
        workspace=workspace,
        session_path=tmp_path / "session.jsonl",
    )

    result = loop.run("Capture large stdout.")
    output = _first_tool_output(loop.session, RUN_COMMAND_NAME)
    stdout = output.get("stdout") if isinstance(output, dict) else None
    handle = stdout.get("resource") if isinstance(stdout, dict) else None
    context_text = "\n".join(message.content for message in adapter.requests[-1].messages)
    authorized_read = None
    unauthorized_denied = False
    if isinstance(handle, Mapping):
        uri = str(handle["uri"])
        authorized_read = loop.resources.read(
            uri,
            owner=ResourceOwner(loop.agent.control.agent_id, loop.agent.control.session_id),
        )
        try:
            loop.resources.read(
                uri,
                owner=ResourceOwner(loop.agent.control.agent_id, loop.agent.control.session_id),
                capability_evaluator=CapabilityEvaluator(),
            )
        except ResourceAccessDenied:
            unauthorized_denied = True
    success = (
        result.status is MiniCodeRunStatus.COMPLETED
        and isinstance(stdout, Mapping)
        and stdout.get("bytes") == 5000
        and stdout.get("preview_bytes") == 4096
        and stdout.get("truncated") is True
        and isinstance(handle, Mapping)
        and authorized_read is not None
        and authorized_read.data == b"x" * 5000
        and unauthorized_denied
        and ("x" * 5000) not in context_text
    )
    return MiniCodeValidationResult(
        "F4",
        "Resource authority",
        _status(success),
        "A command emits deterministic large stdout and the next model turn sees only bounded preview plus handle.",
        "local workspace with Python subprocess emitting 5000 bytes",
        "run_command large stdout, then final after observing bounded context",
        (RUN_COMMAND_NAME,),
        None,
        "Resource != Context; Handle != Permission; large bytes are externalized through ResourceService",
        "stdout has bounded preview, ResourceHandle reads exact bytes with authority, unauthorized read is denied",
        "Stuffing full large output into model context or treating handle possession as permission",
        {
            "run_status": result.status.value,
            "stdout_bytes": stdout.get("bytes") if isinstance(stdout, Mapping) else None,
            "preview_bytes": stdout.get("preview_bytes") if isinstance(stdout, Mapping) else None,
            "truncated": stdout.get("truncated") if isinstance(stdout, Mapping) else None,
            "resource_handle_returned": isinstance(handle, Mapping),
            "authorized_resource_bytes": len(authorized_read.data) if authorized_read is not None else 0,
            "unauthorized_read_denied": unauthorized_denied,
            "full_output_absent_from_context": ("x" * 5000) not in context_text,
            "resources_created": loop.resources.metrics.resources_created,
        },
    )


def _check_f5_nonzero_command(tmp_path: Path) -> MiniCodeValidationResult:
    tmp_path.mkdir(parents=True)
    fixture = make_minicode_workspace(tmp_path)
    (fixture.root / "tests" / "test_calculator.py").write_text(
        "from calculator import divide\n\n"
        "def test_divide():\n"
        "    assert divide(8, 2) == 4\n\n"
        "def test_zero_division_returns_none():\n"
        "    assert divide(1, 0) is None\n",
        encoding="utf-8",
    )
    workspace = discover_workspace(cwd=fixture.root)
    script = [
        scripted_response(tool_calls=(_tool("f5-search", SEARCH_FILES_NAME, {"query": "divide"}),)),
        scripted_response(tool_calls=(_tool("f5-read", READ_FILE_NAME, {"path": "calculator.py"}),)),
        scripted_response(tool_calls=(_tool("f5-wrong", APPLY_PATCH_NAME, {"patch": _wrong_patch()}),)),
        scripted_response(tool_calls=(_tool("f5-test-fail", RUN_COMMAND_NAME, {"command": _pytest_command(), "mutation_intent": "read_only"}),)),
        scripted_response(tool_calls=(_tool("f5-read-fail", READ_FILE_NAME, {"path": "calculator.py"}),)),
        scripted_response(tool_calls=(_tool("f5-correct", APPLY_PATCH_NAME, {"patch": _correct_patch_after_wrong()}),)),
        scripted_response(tool_calls=(_tool("f5-test-pass", RUN_COMMAND_NAME, {"command": _pytest_command(), "mutation_intent": "read_only"}),)),
        scripted_response(text="fixed"),
    ]
    loop = MiniCodeAgentLoop(
        model=ScriptedModelAdapter(script),
        config=MiniCodeConfig(workspace=workspace.root, max_turns=12),
        workspace=workspace,
        session_path=tmp_path / "session.jsonl",
    )

    result = loop.run("Fix divide by zero and run tests.")
    exits = _command_exit_codes(loop.session)
    success = (
        result.status is MiniCodeRunStatus.COMPLETED
        and exits == [1, 0]
        and "return None" in fixture.calculator.read_text(encoding="utf-8")
    )
    return MiniCodeValidationResult(
        "F5",
        "Nonzero command",
        _status(success),
        "Scripted CodeAgent applies a wrong patch, observes pytest exit 1, repairs, then observes pytest exit 0.",
        "calculator.py plus pytest fixture with zero division expectation",
        "inspect, patch, pytest exit 1, inspect, patch, pytest exit 0, final",
        (SEARCH_FILES_NAME, READ_FILE_NAME, APPLY_PATCH_NAME, RUN_COMMAND_NAME),
        None,
        "Non-zero subprocess exit is structured observation, not Tool crash",
        "run_command exits are [1, 0]; final file has expected implementation; task completed",
        "Treating pytest exit 1 as a Tool crash or terminating the loop before repair",
        {
            "run_status": result.status.value,
            "pytest_exit_codes": exits,
            "final_contains_return_none": "return None" in fixture.calculator.read_text(encoding="utf-8"),
            "tool_result_count": _event_count(loop.session, EventType.TOOL_RESULT),
        },
    )


def _check_f6_budget_exhaustion(tmp_path: Path) -> MiniCodeValidationResult:
    tmp_path.mkdir(parents=True)
    fixture = make_minicode_workspace(tmp_path)
    workspace = discover_workspace(cwd=fixture.root)
    adapter = ScriptedModelAdapter(
        [
            scripted_response(
                text="would continue",
                usage=ModelUsage(input_tokens=1, output_tokens=0, total_tokens=1),
            ),
            scripted_response(text="must not dispatch"),
        ]
    )
    loop = MiniCodeAgentLoop(
        model=adapter,
        config=MiniCodeConfig(workspace=workspace.root, max_turns=4),
        workspace=workspace,
        session_path=tmp_path / "session.jsonl",
    )
    loop.scheduler.update_process_budget(loop.process_id, AgentBudget(max_token_usage=0))

    result = loop.run("Exhaust budget.")
    process_state = loop.scheduler.manager.get(loop.process_id).state
    success = (
        result.status is MiniCodeRunStatus.BUDGET_EXCEEDED
        and process_state is ProcessState.BLOCKED
        and len(adapter.requests) == 1
        and _event_count(loop.session, EventType.TOOL_CALL) == 0
    )
    return MiniCodeValidationResult(
        "F6",
        "Budget block",
        _status(success),
        "A low token budget is exceeded at a scheduler safe point.",
        "single-turn scripted model response with one token of usage",
        "first model response reports usage; scheduler blocks before further dispatch",
        (),
        "max_token_usage=0",
        "Budget exceeded is runtime blocking, not semantic task failure or durable truth rewrite",
        "process is BLOCKED; only one model dispatch; no tool dispatch; Session remains readable",
        "Continuing tool/model dispatch after budget block or marking durable task truth as failed",
        {
            "run_status": result.status.value,
            "process_state": process_state.value,
            "model_request_count": len(adapter.requests),
            "tool_call_count": _event_count(loop.session, EventType.TOOL_CALL),
            "session_events": len(loop.session.events),
        },
    )


def _check_f7_resume_handoff(tmp_path: Path) -> MiniCodeValidationResult:
    tmp_path.mkdir(parents=True)
    fixture = make_minicode_workspace(tmp_path)
    workspace = discover_workspace(cwd=fixture.root)
    session_path = tmp_path / "session.jsonl"
    first = MiniCodeAgentLoop(
        model=ScriptedModelAdapter(
            [scripted_response(tool_calls=(_tool("f7-patch", APPLY_PATCH_NAME, {"patch": _zero_division_patch()}),))]
        ),
        config=MiniCodeConfig(workspace=workspace.root, max_turns=1),
        workspace=workspace,
        session_path=session_path,
        process_id="minicode-proc-f7-before",
    )

    first_result = first.run("Patch calculator and stop before final answer.")
    old_process_id = first.process_id
    session_id = first.session.session_id
    first_event_count = len(first.session.events)
    first.session.close()

    resumed = MiniCodeAgentLoop.resume(
        model=ScriptedModelAdapter([scripted_response(text="resume completed")]),
        config=MiniCodeConfig(workspace=workspace.root, max_turns=2),
        workspace=workspace,
        session_path=session_path,
        session_id=session_id,
        process_id="minicode-proc-f7-after",
    )
    resumed_result = resumed.run(None)
    mutation_commits = _event_count(resumed.session, EventType.TOOL_COMMIT)
    success = (
        first_result.status is MiniCodeRunStatus.MAX_TURNS
        and resumed_result.status is MiniCodeRunStatus.COMPLETED
        and old_process_id != resumed.process_id
        and resumed.session.session_id == session_id
        and len(resumed.session.events) > first_event_count
        and mutation_commits == 1
        and "return None" in fixture.calculator.read_text(encoding="utf-8")
    )
    return MiniCodeValidationResult(
        "F7",
        "Resume / handoff",
        _status(success),
        "Runtime is interrupted after a durable patch turn, then recreated through MiniCode resume.",
        "persisted Session JSONL plus same workspace",
        "first runtime applies patch and stops; second runtime resumes and finalizes",
        (APPLY_PATCH_NAME,),
        "Runtime object destroyed after first turn before final answer",
        "Session is durable truth; Process is replaceable runtime identity; resume does not invent prior transcript",
        "new process id differs, session id is preserved, prior durable commit remains singular, task completes",
        "Duplicating the durable mutation or treating old Process identity as durable truth",
        {
            "first_status": first_result.status.value,
            "resumed_status": resumed_result.status.value,
            "same_session": resumed.session.session_id == session_id,
            "process_recreated": old_process_id != resumed.process_id,
            "tool_commit_count": mutation_commits,
            "events_before_resume": first_event_count,
            "events_after_resume": len(resumed.session.events),
        },
    )


def _check_f8_trace_redaction(tmp_path: Path) -> MiniCodeValidationResult:
    trace_path = tmp_path / "trace.jsonl"
    recorder = TraceRecorder(jsonl_path=trace_path)
    secret = "sk-test-secret-token"
    recorder.record(
        "model/request",
        {
            "api_key": secret,
            "Authorization": f"Bearer {secret}",
            "nested": {"secret_token": secret},
            "message": "public observation",
        },
    )
    text = trace_path.read_text(encoding="utf-8")
    human = recorder.human_text()
    success = (
        secret not in text
        and secret not in human
        and "<redacted>" in text
        and "public observation" in human
    )
    return MiniCodeValidationResult(
        "F8",
        "Trace redaction",
        _status(success),
        "Observable trace records runtime facts while redacting secret-shaped fields.",
        "synthetic TraceRecorder event with API key, Authorization header, and nested secret token",
        "record one trace event and render JSONL/human trace",
        (),
        None,
        "Trace is observable evidence, not hidden CoT, and it must not leak secrets",
        "secret token absent from JSONL and human text while non-secret fact remains visible",
        "Leaking API key, Authorization header, or hidden reasoning into public trace",
        {
            "jsonl_secret_absent": secret not in text,
            "human_secret_absent": secret not in human,
            "redaction_marker_present": "<redacted>" in text,
            "public_fact_present": "public observation" in human,
        },
    )


def _durable_patch_recovery(tmp_path: Path, *, check_id: str, name: str) -> MiniCodeValidationResult:
    tmp_path.mkdir(parents=True)
    fixture = make_minicode_workspace(tmp_path)
    workspace = discover_workspace(cwd=fixture.root)
    session_path = tmp_path / "session.jsonl"
    session = Session("i7-session", JsonlSessionPersistence(session_path))
    agent = Agent.create(
        agent_id="i7-agent",
        session=session,
        capability_grants=apply_patch_capability_grants(agent_id="i7-agent", workspace=workspace),
    )
    registry = register_apply_patch_tool(ToolRegistry(), workspace, session=session)
    prepared = DurableApplyPatchAdapter(registry).prepare_call(
        workspace,
        ToolCall("f3-patch", APPLY_PATCH_NAME, {"patch": _integer_division_patch()}),
        agent.control,
    )
    dispatch_count = 0
    _append_call_prefix(session, prepared.call)
    session.append(
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
    session.append(
        EventType.TOOL_DISPATCH,
        {"turn": 1, "step": 1, "operation_id": prepared.operation_id, "attempt": 1},
    )
    session.flush()
    apply_mutation_plan(prepared.plan)
    dispatch_count += 1
    session.close()

    restored = Session.load("i7-session", JsonlSessionPersistence(session_path))
    restored_agent = Agent.create(
        agent_id="i7-agent",
        session=restored,
        capability_grants=apply_patch_capability_grants(agent_id="i7-agent", workspace=workspace),
    )
    restored_registry = register_apply_patch_tool(ToolRegistry(), workspace, session=restored)
    operation = restored.recovery_analysis.durable_operations[0]
    observed = asyncio.run(
        DurableApplyPatchAdapter(restored_registry).reconcile(
            operation,
            restored_agent.control,
            restored,
        )
    )
    completed = restored.recovery_analysis.durable_operations[0]
    success = (
        operation.classification is OperationRecoveryClassification.RECONCILE_REQUIRED
        and observed.status is ReconcileStatus.SUCCEEDED
        and completed.classification is OperationRecoveryClassification.COMPLETED
        and dispatch_count == 1
        and "a // b" in fixture.calculator.read_text(encoding="utf-8")
    )
    return MiniCodeValidationResult(
        check_id,
        name,
        _status(success),
        "Patch mutation succeeds after DISPATCH and the process crashes before COMMIT.",
        "durable apply_patch operation with persisted Session JSONL",
        "prepare/dispatch, apply mutation, destroy runtime, reload Session, reconcile",
        (APPLY_PATCH_NAME,),
        "Crash after mutation before TOOL_COMMIT",
        "Recovery reconciles durable side effects; recovery is not blind retry",
        "postimage recognized, operation completed, dispatch count remains 1",
        "Executing the filesystem mutation a second time during recovery",
        {
            "pre_reconcile_classification": operation.classification.value,
            "reconcile_status": observed.status.value,
            "post_reconcile_classification": completed.classification.value,
            "dispatch_count": dispatch_count,
            "recognized_existing_mutation": (
                isinstance(observed.output, Mapping)
                and isinstance(observed.output.get("recovery"), Mapping)
                and observed.output["recovery"].get("action_taken") == "recognized_existing_mutation"
            ),
        },
    )


def assert_repeatable() -> tuple[MiniCodePhase2FValidationDocument, MiniCodePhase2FValidationDocument]:
    first = run_phase2f_validation()
    second = run_phase2f_validation()
    if first.as_dict() != second.as_dict():
        raise AssertionError("MiniCode Phase 2F validation stable fields are not repeatable")
    return first, second


def _tool(call_id: str, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    return {"call_id": call_id, "name": name, "arguments": dict(arguments)}


def _pytest_command() -> str:
    return subprocess.list2cmdline([sys.executable, "-m", "pytest", "-q"])


def _zero_division_patch() -> str:
    return (
        "*** Begin Patch\n"
        "*** Update File: calculator.py\n"
        "@@\n"
        "-        raise ZeroDivisionError('division by zero')\n"
        "+        return None\n"
        "*** End Patch"
    )


def _wrong_patch() -> str:
    return (
        "*** Begin Patch\n"
        "*** Update File: calculator.py\n"
        "@@\n"
        "-    return a / b\n"
        "+    return 0\n"
        "*** End Patch"
    )


def _correct_patch_after_wrong() -> str:
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


def _integer_division_patch() -> str:
    return (
        "*** Begin Patch\n"
        "*** Update File: calculator.py\n"
        "@@\n"
        "-    return a / b\n"
        "+    return a // b\n"
        "*** End Patch"
    )


def _append_call_prefix(session: Session, call: ToolCall) -> None:
    session.append(EventType.TURN_START, {"turn": 1})
    session.append(EventType.USER_MESSAGE, {"turn": 1, "content": "patch"})
    session.append(EventType.STEP_START, {"turn": 1, "step": 1})
    session.append(
        EventType.ASSISTANT_MESSAGE,
        {"turn": 1, "step": 1, "content": "", "tool_calls": [call.as_dict()]},
    )
    session.append(EventType.TOOL_CALL, {"turn": 1, "step": 1, **call.as_dict()})


def _workspace_hashes(root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if ".git" in path.relative_to(root).parts or ".minicode" in path.relative_to(root).parts:
            continue
        relative = path.relative_to(root).as_posix()
        hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def _has_events(session: Session, *types: EventType) -> bool:
    present = [event.type for event in session.events]
    return all(event_type in present for event_type in types)


def _event_count(session: Session, event_type: EventType) -> int:
    return sum(1 for event in session.events if event.type is event_type)


def _command_exit_codes(session: Session) -> list[int | None]:
    exits: list[int | None] = []
    for event in session.events:
        if event.type is not EventType.TOOL_RESULT or event.data.get("name") != RUN_COMMAND_NAME:
            continue
        output = event.data.get("output")
        if isinstance(output, Mapping):
            exit_code = output.get("exit_code")
            exits.append(exit_code if isinstance(exit_code, int) or exit_code is None else None)
    return exits


def _first_tool_output(session: Session, name: str) -> Mapping[str, Any]:
    for event in session.events:
        if event.type is EventType.TOOL_RESULT and event.data.get("name") == name:
            output = event.data.get("output")
            if isinstance(output, Mapping):
                return output
    return {}


def _first_failed_tool_result(session: Session, name: str):
    for event in session.events:
        if event.type is not EventType.TOOL_RESULT or event.data.get("name") != name:
            continue
        if event.data.get("ok") is False:
            from agentkernel import ToolResult

            return ToolResult.from_dict(event.data)
    return None


def _status(success: bool) -> str:
    return "PASS" if success else "FAIL"


def _stable_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _stable_json(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_stable_json(item) for item in value]
    if isinstance(value, Path):
        return value.as_posix()
    return value
