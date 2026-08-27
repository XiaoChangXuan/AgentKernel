from __future__ import annotations

import asyncio
import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agentkernel import (
    Agent,
    CapabilityEvaluator,
    CapabilityGrant,
    ErrorCode,
    LocalResourceStore,
    RESOURCE_READ_ACTION,
    RESOURCE_STAT_ACTION,
    ResourceAccessDenied,
    ResourceOwner,
    ResourceService,
    Session,
    TOOL_EXECUTE_ACTION,
    ToolCall,
    ToolRegistry,
    resource_tool_definitions,
)
from minicode.testing import make_minicode_workspace
from minicode.tools import (
    RUN_COMMAND_NAME,
    SHELL_EXECUTE_ACTION,
    DefaultShellHostPolicy,
    register_run_command_tool,
    run_command_capability_grants,
    shell_scope,
    tool_resource,
)
from minicode.tools.run_command import CommandCompleted
from minicode.workspace import discover_workspace


run_command_module = importlib.import_module("minicode.tools.run_command")


class FakeRunner:
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
        self.cwd: Path | None = None
        self.command: str | None = None

    def run(
        self,
        command: str,
        *,
        cwd: Path,
        timeout_ms: int,
        max_capture_bytes: int,
    ) -> CommandCompleted:
        self.dispatch_count += 1
        self.cwd = cwd
        self.command = command
        return CommandCompleted(
            exit_code=self.exit_code,
            timed_out=self.timed_out,
            duration_ms=7,
            stdout=self.stdout,
            stderr=self.stderr,
        )


def _workspace(tmp_path):
    fixture = make_minicode_workspace(tmp_path)
    return fixture, discover_workspace(cwd=fixture.root)


def _agent(
    *,
    agent_id: str = "agent-1",
    session_id: str | None = None,
    grants: tuple[CapabilityGrant, ...] = (),
    legacy: set[str] | None = None,
) -> Agent:
    return Agent.create(
        agent_id=agent_id,
        session=Session(session_id or f"session-{agent_id}"),
        capabilities=legacy or set(),
        capability_grants=grants,
    )


def _registry(workspace, *, resources=None, policy=None, runner=None) -> ToolRegistry:
    return register_run_command_tool(
        ToolRegistry(),
        workspace,
        resources=resources,
        policy=policy,
        runner=runner,
    )


def _run(registry: ToolRegistry, agent: Agent, arguments: dict[str, object]):
    return asyncio.run(
        registry.execute(
            ToolCall("call-command", RUN_COMMAND_NAME, arguments),
            agent.control,
        )
    )


def _quoted_python_command(script: str) -> str:
    return subprocess.list2cmdline([sys.executable, "-c", script])


def test_command_exits_zero(tmp_path):
    _fixture, workspace = _workspace(tmp_path)
    runner = FakeRunner(stdout=b"ok\n")
    registry = _registry(workspace, runner=runner)
    agent = _agent(
        grants=run_command_capability_grants(agent_id="agent-1", workspace=workspace),
    )

    result = _run(registry, agent, {"command": "python -c print('ok')"})

    assert result.ok is True
    assert result.output["ok"] is True  # type: ignore[index]
    assert result.output["exit_code"] == 0  # type: ignore[index]
    assert result.output["stdout"]["preview"] == "ok\n"  # type: ignore[index]
    assert runner.dispatch_count == 1


def test_command_exits_nonzero_as_normal_result(tmp_path):
    _fixture, workspace = _workspace(tmp_path)
    runner = FakeRunner(exit_code=1, stderr=b"failed\n")
    registry = _registry(workspace, runner=runner)
    agent = _agent(
        grants=run_command_capability_grants(agent_id="agent-1", workspace=workspace),
    )

    result = _run(registry, agent, {"command": "python -m pytest"})

    assert result.ok is True
    assert result.output["ok"] is True  # type: ignore[index]
    assert result.output["exit_code"] == 1  # type: ignore[index]
    assert result.output["timed_out"] is False  # type: ignore[index]
    assert result.output["stderr"]["preview"] == "failed\n"  # type: ignore[index]


def test_timeout_is_structured_result(tmp_path):
    _fixture, workspace = _workspace(tmp_path)
    registry = _registry(workspace)
    agent = _agent(
        grants=run_command_capability_grants(agent_id="agent-1", workspace=workspace),
    )
    command = _quoted_python_command("import time; time.sleep(1)")

    result = _run(registry, agent, {"command": command, "timeout_ms": 50})

    assert result.ok is True
    assert result.output["ok"] is True  # type: ignore[index]
    assert result.output["timed_out"] is True  # type: ignore[index]
    assert result.output["exit_code"] is None  # type: ignore[index]


def test_cwd_escape_denied_before_subprocess_dispatch(tmp_path):
    _fixture, workspace = _workspace(tmp_path)
    runner = FakeRunner()
    registry = _registry(workspace, runner=runner)
    agent = _agent(
        grants=run_command_capability_grants(agent_id="agent-1", workspace=workspace),
    )

    result = _run(registry, agent, {"command": "echo nope", "cwd": ".."})

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ErrorCode.EACCES
    assert runner.dispatch_count == 0


def test_missing_tool_execute_denied(tmp_path):
    _fixture, workspace = _workspace(tmp_path)
    runner = FakeRunner()
    registry = _registry(workspace, runner=runner)
    agent = _agent(
        grants=(CapabilityGrant("agent-1", SHELL_EXECUTE_ACTION, shell_scope(workspace.workspace_id)),),
    )

    result = _run(registry, agent, {"command": "echo hidden"})

    assert registry.model_schemas(agent.control) == ()
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ErrorCode.EACCES
    assert runner.dispatch_count == 0


def test_missing_shell_execute_denied(tmp_path):
    _fixture, workspace = _workspace(tmp_path)
    runner = FakeRunner()
    registry = _registry(workspace, runner=runner)
    agent = _agent(
        grants=(CapabilityGrant("agent-1", TOOL_EXECUTE_ACTION, tool_resource(RUN_COMMAND_NAME)),),
    )

    result = _run(registry, agent, {"command": "echo denied"})

    assert [schema.name for schema in registry.model_schemas(agent.control)] == [RUN_COMMAND_NAME]
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ErrorCode.EACCES
    assert runner.dispatch_count == 0


def test_large_stdout_externalizes_to_resource_handle(tmp_path):
    _fixture, workspace = _workspace(tmp_path)
    resources = ResourceService(LocalResourceStore(tmp_path / "resources"))
    runner = FakeRunner(stdout=b"x" * 5000)
    registry = _registry(workspace, resources=resources, runner=runner)
    agent = _agent(
        grants=run_command_capability_grants(agent_id="agent-1", workspace=workspace),
    )

    result = _run(registry, agent, {"command": "emit large stdout"})

    stdout = result.output["stdout"]  # type: ignore[index]
    assert stdout["bytes"] == 5000
    assert stdout["preview_bytes"] == 4096
    assert stdout["truncated"] is True
    assert stdout["resource"]["uri"].startswith("artifact://res_")
    assert resources.metrics.resources_created == 1


def test_large_stderr_externalizes_to_resource_handle(tmp_path):
    _fixture, workspace = _workspace(tmp_path)
    resources = ResourceService(LocalResourceStore(tmp_path / "resources"))
    runner = FakeRunner(stderr=b"e" * 5000)
    registry = _registry(workspace, resources=resources, runner=runner)
    agent = _agent(
        grants=run_command_capability_grants(agent_id="agent-1", workspace=workspace),
    )

    result = _run(registry, agent, {"command": "emit large stderr"})

    stderr = result.output["stderr"]  # type: ignore[index]
    assert stderr["bytes"] == 5000
    assert stderr["preview_bytes"] == 4096
    assert stderr["truncated"] is True
    assert stderr["resource"]["uri"].startswith("artifact://res_")
    assert resources.metrics.resources_created == 1


def test_authorized_resource_handle_read_succeeds(tmp_path):
    _fixture, workspace = _workspace(tmp_path)
    resources = ResourceService(LocalResourceStore(tmp_path / "resources"))
    runner = FakeRunner(stdout=b"x" * 5000)
    registry = _registry(workspace, resources=resources, runner=runner)
    agent = _agent(
        grants=run_command_capability_grants(agent_id="agent-1", workspace=workspace),
        legacy={RESOURCE_READ_ACTION},
    )
    handle = _run(registry, agent, {"command": "emit large"}).output["stdout"]["resource"]  # type: ignore[index]

    read = resources.read(
        handle["uri"],  # type: ignore[index]
        owner=ResourceOwner(agent.control.agent_id, agent.control.session_id),
        capability_evaluator=CapabilityEvaluator.from_agent_capabilities(
            agent_id=agent.control.agent_id,
            capabilities=agent.control.capabilities,
            capability_grants=agent.control.capability_grants,
        ),
    )

    assert read.data == b"x" * 5000


def test_unauthorized_resource_handle_read_denied(tmp_path):
    _fixture, workspace = _workspace(tmp_path)
    resources = ResourceService(LocalResourceStore(tmp_path / "resources"))
    runner = FakeRunner(stdout=b"x" * 5000)
    registry = _registry(workspace, resources=resources, runner=runner)
    agent = _agent(
        grants=run_command_capability_grants(agent_id="agent-1", workspace=workspace),
    )
    handle = _run(registry, agent, {"command": "emit large"}).output["stdout"]["resource"]  # type: ignore[index]

    with pytest.raises(ResourceAccessDenied):
        resources.read(
            handle["uri"],  # type: ignore[index]
            owner=ResourceOwner(agent.control.agent_id, agent.control.session_id),
            capability_evaluator=CapabilityEvaluator(),
        )


def test_may_mutate_confirm_deny_skips_dispatch(tmp_path):
    _fixture, workspace = _workspace(tmp_path)
    runner = FakeRunner()
    registry = _registry(
        workspace,
        policy=DefaultShellHostPolicy(confirm_mutation=lambda _request: False),
        runner=runner,
    )
    agent = _agent(
        grants=run_command_capability_grants(agent_id="agent-1", workspace=workspace),
    )

    result = _run(
        registry,
        agent,
        {"command": "python -c \"open('x.txt','w').write('x')\"", "mutation_intent": "may_mutate"},
    )

    assert result.ok is True
    assert result.output["ok"] is False  # type: ignore[index]
    assert result.output["error"]["code"] == "host_confirmation_required"  # type: ignore[index]
    assert runner.dispatch_count == 0


def test_may_mutate_confirm_allow_executes(tmp_path):
    _fixture, workspace = _workspace(tmp_path)
    runner = FakeRunner(stdout=b"mutated\n")
    registry = _registry(
        workspace,
        policy=DefaultShellHostPolicy(confirm_mutation=lambda _request: True),
        runner=runner,
    )
    agent = _agent(
        grants=run_command_capability_grants(agent_id="agent-1", workspace=workspace),
    )

    result = _run(
        registry,
        agent,
        {"command": "python -c \"open('x.txt','w').write('x')\"", "mutation_intent": "may_mutate"},
    )

    assert result.ok is True
    assert result.output["ok"] is True  # type: ignore[index]
    assert runner.dispatch_count == 1


def test_model_arguments_cannot_self_approve(tmp_path):
    _fixture, workspace = _workspace(tmp_path)
    runner = FakeRunner()
    registry = _registry(workspace, runner=runner)
    agent = _agent(
        grants=run_command_capability_grants(agent_id="agent-1", workspace=workspace),
    )

    result = _run(
        registry,
        agent,
        {
            "command": "python -c \"open('x.txt','w').write('x')\"",
            "mutation_intent": "may_mutate",
            "approved": True,
        },
    )

    assert result.ok is True
    assert result.output["ok"] is False  # type: ignore[index]
    assert runner.dispatch_count == 0


def test_windows_execution_path(monkeypatch):
    monkeypatch.setattr(run_command_module.os, "name", "nt")
    monkeypatch.setitem(os.environ, "COMSPEC", r"C:\Windows\System32\cmd.exe")

    argv = run_command_module.platform_shell_argv("echo hello")

    assert argv == [
        "powershell.exe",
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        "echo hello",
    ]


def test_resource_read_tool_requires_resource_capability_for_command_output(tmp_path):
    _fixture, workspace = _workspace(tmp_path)
    resources = ResourceService(LocalResourceStore(tmp_path / "resources"))
    runner = FakeRunner(stdout=b"x" * 5000)
    command_registry = _registry(workspace, resources=resources, runner=runner)
    agent = _agent(
        grants=run_command_capability_grants(agent_id="agent-1", workspace=workspace),
    )
    handle = _run(command_registry, agent, {"command": "emit large"}).output["stdout"]["resource"]  # type: ignore[index]
    read_registry = ToolRegistry()
    for definition in resource_tool_definitions(resources):
        read_registry.register(definition)

    denied = _run_resource_read(read_registry, agent, handle["uri"])  # type: ignore[index]
    authorized = _agent(
        grants=run_command_capability_grants(agent_id="agent-1", workspace=workspace),
        legacy={RESOURCE_READ_ACTION, RESOURCE_STAT_ACTION},
    )
    allowed = _run_resource_read(read_registry, authorized, handle["uri"])  # type: ignore[index]

    assert denied.ok is False
    assert denied.error is not None
    assert denied.error.code is ErrorCode.EACCES
    assert allowed.ok is True
    assert allowed.output["content"] == "x" * 5000  # type: ignore[index]


def test_real_subprocess_uses_workspace_cwd(tmp_path):
    _fixture, workspace = _workspace(tmp_path)
    registry = _registry(workspace)
    agent = _agent(
        grants=run_command_capability_grants(agent_id="agent-1", workspace=workspace),
    )
    command = _quoted_python_command("from pathlib import Path; print(Path.cwd().name)")

    result = _run(registry, agent, {"command": command})

    assert result.ok is True
    assert result.output["exit_code"] == 0  # type: ignore[index]
    assert result.output["stdout"]["preview"].strip() == workspace.root.name  # type: ignore[index]


def _run_resource_read(registry: ToolRegistry, agent: Agent, uri: str):
    return asyncio.run(
        registry.execute(
            ToolCall("call-resource-read", "resource_read", {"uri": uri, "limit": 6000}),
            agent.control,
        )
    )
