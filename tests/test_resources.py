from __future__ import annotations

import asyncio
import hashlib
import json

import pytest

from agentkernel import (
    Agent,
    ApproximateTokenEstimator,
    ContextPageKind,
    ContextProjector,
    DefaultAgentLoop,
    DurableToolExecutor,
    ErrorCode,
    EventType,
    JsonlSessionPersistence,
    LocalResourceStore,
    ModelRequest,
    ModelResponse,
    PromptService,
    ResourceAccessDenied,
    ResourceInvalid,
    ResourceLimits,
    ResourceOwner,
    ResourceService,
    ScriptedLLM,
    Session,
    ThresholdExternalizationPolicy,
    ToolCall,
    ToolDefinition,
    ToolExecutionContext,
    ToolRegistry,
    ToolResultExternalizer,
    ToolResultPruner,
    ToolResultPrunerConfig,
    ToolEffectKind,
    ToolSchema,
    resource_tool_definitions,
)
from agentkernel.protocol import JsonValue
from tests.context_support import append_tool_turn


def service_at(tmp_path, **kwargs) -> ResourceService:  # type: ignore[no-untyped-def]
    return ResourceService(LocalResourceStore(tmp_path / "resources"), **kwargs)


def create_text(
    service: ResourceService,
    content: str,
    *,
    agent_id: str = "agent-1",
    session_id: str = "session-1",
):
    return service.create_artifact(
        content.encode(),
        owner=ResourceOwner(agent_id, session_id),
        media_type="text/plain",
        encoding="utf-8",
        source_tool_name="logs",
        source_tool_call_id="call-1",
        source_operation_id="op-1",
    )


def test_handle_is_opaque_and_restart_resolves_same_bytes(tmp_path) -> None:
    service = service_at(
        tmp_path,
        resource_id_factory=lambda: "res_fixed",
        handle_id_factory=lambda: "hdl_fixed",
        clock=lambda: 123.0,
    )
    handle = create_text(service, "alpha\nbeta\ngamma")

    assert handle.uri == "artifact://res_fixed"
    assert handle.handle_id == "hdl_fixed"
    assert str(tmp_path) not in json.dumps(handle.as_dict())

    restarted = service_at(tmp_path)
    owner = ResourceOwner("agent-1", "session-1")
    assert restarted.stat(handle.uri, owner=owner) == handle
    first = restarted.read(handle.uri, owner=owner, offset=6, limit=4)
    assert first.data == b"beta"
    assert first.next_offset == 10
    assert first.has_more is True


def test_resource_limits_ranges_unknown_and_owner_are_enforced(tmp_path) -> None:
    service = ResourceService(
        LocalResourceStore(tmp_path / "resources"),
        limits=ResourceLimits(max_resource_bytes=8, max_read_bytes=4),
    )
    handle = create_text(service, "12345678")
    owner = ResourceOwner("agent-1", "session-1")

    with pytest.raises(ResourceInvalid, match="exceeds"):
        create_text(service, "123456789")
    with pytest.raises(ResourceInvalid, match="read maximum"):
        service.read(handle.uri, owner=owner, limit=5)
    with pytest.raises(ResourceInvalid, match="offset"):
        service.read(handle.uri, owner=owner, offset=9, limit=1)
    with pytest.raises(ResourceAccessDenied):
        service.read(
            handle.uri,
            owner=ResourceOwner("agent-2", "session-1"),
            limit=1,
        )
    with pytest.raises(ResourceAccessDenied):
        service.stat(
            handle.uri,
            owner=ResourceOwner("agent-1", "session-2"),
        )
    with pytest.raises(ResourceInvalid):
        service.stat(str(tmp_path / "resources"), owner=owner)


def test_atomic_failure_before_commit_emits_no_final_resource(tmp_path) -> None:
    def crash(_metadata) -> None:  # type: ignore[no-untyped-def]
        raise RuntimeError("simulated crash before publish")

    store = LocalResourceStore(tmp_path / "resources", before_commit=crash)
    service = ResourceService(
        store,
        resource_id_factory=lambda: "res_crash",
        handle_id_factory=lambda: "hdl_crash",
    )

    with pytest.raises(RuntimeError, match="simulated crash"):
        create_text(service, "uncommitted")

    assert tuple(store.list_metadata()) == ()
    assert list(store.root.iterdir()) == []


def test_committed_resource_without_tool_result_is_identifiable_orphan(tmp_path) -> None:
    service = service_at(tmp_path)
    session = Session("session-1")
    handle = create_text(service, "durable but not referenced")

    orphans = service.orphaned_resources(session)

    assert [item.uri for item in orphans] == [handle.uri]
    assert orphans[0].source_tool_call_id == "call-1"


def test_resource_tools_hide_driver_and_return_stable_errors(tmp_path) -> None:
    service = service_at(tmp_path)
    handle = create_text(service, "abcdefgh")
    registry = ToolRegistry()
    for definition in resource_tool_definitions(service):
        registry.register(definition)
    owner_agent = Agent.create(
        agent_id="agent-1",
        session=Session("session-1"),
        capabilities={"resource.read"},
    )
    other_agent = Agent.create(
        agent_id="agent-2",
        session=Session("session-2"),
        capabilities={"resource.read"},
    )

    result = asyncio.run(
        registry.execute(
            ToolCall(
                "read-1", "resource_read", {"uri": handle.uri, "offset": 2, "limit": 3}
            ),
            owner_agent.control,
        )
    )
    denied = asyncio.run(
        registry.execute(
            ToolCall("read-2", "resource_read", {"uri": handle.uri}),
            other_agent.control,
        )
    )
    unknown = asyncio.run(
        registry.execute(
            ToolCall(
                "read-3", "resource_read", {"uri": "artifact://res_missing"}
            ),
            owner_agent.control,
        )
    )
    invalid = asyncio.run(
        registry.execute(
            ToolCall(
                "read-4",
                "resource_read",
                {"uri": handle.uri, "limit": service.limits.max_read_bytes + 1},
            ),
            owner_agent.control,
        )
    )

    assert result.ok and result.output["content"] == "cde"  # type: ignore[index]
    assert str(tmp_path) not in json.dumps(result.output)
    assert denied.error is not None and denied.error.code is ErrorCode.EACCES
    assert unknown.error is not None and unknown.error.code is ErrorCode.ENOENT
    assert invalid.error is not None and invalid.error.code is ErrorCode.EINVAL


def test_externalizer_preserves_small_results_and_externalizes_large_json(tmp_path) -> None:
    service = service_at(tmp_path)
    processor = ToolResultExternalizer(
        service,
        ThresholdExternalizationPolicy(
            threshold_bytes=32, preview_head_bytes=8, preview_tail_bytes=4
        ),
    )
    call = ToolCall("call-1", "report", {})
    context = ToolExecutionContext("agent-1", "session-1", "call-1", "op-1")

    from agentkernel import ToolResult

    small = ToolResult.success(call, "short")
    large_output: JsonValue = {"rows": ["x" * 1_000, "tail"]}
    large = ToolResult.success(call, large_output)
    assert asyncio.run(processor.process(call, small, context)) is small
    projected = asyncio.run(processor.process(call, large, context))

    assert projected.ok
    assert projected.output["externalized"] is True  # type: ignore[index]
    uri = projected.output["resource"]["uri"]  # type: ignore[index]
    raw = service.read(
        uri,
        owner=ResourceOwner("agent-1", "session-1"),
        limit=service.limits.max_read_bytes,
    ).data
    assert json.loads(raw) == large_output
    assert service.metrics.snapshot().tool_results_externalized == 1
    assert service.metrics.snapshot().model_visible_bytes_saved > 0


def test_durable_executor_externalizes_before_commit_and_session_result(tmp_path) -> None:
    raw = "HEAD" + ("x" * 4_000) + "TAIL"

    async def output(
        _arguments: dict[str, JsonValue], _context: ToolExecutionContext
    ) -> JsonValue:
        return raw

    tools = ToolRegistry()
    tools.register(
        ToolDefinition(ToolSchema("logs", "Return logs.", {"type": "object"}), output)
    )
    service = service_at(tmp_path)
    externalizer = ToolResultExternalizer(
        service,
        ThresholdExternalizationPolicy(
            threshold_bytes=256, preview_head_bytes=64, preview_tail_bytes=32
        ),
    )
    session = Session("session-1")
    agent = Agent.create(agent_id="agent-1", session=session)
    call = ToolCall("call-1", "logs", {})

    def finish(request: ModelRequest) -> ModelResponse:
        result = json.loads(request.messages[-1].content)
        assert result["output"]["resource"]["uri"].startswith("artifact://")
        assert raw not in request.messages[-1].content
        return ModelResponse(content="done")

    loop = DefaultAgentLoop(
        llm=ScriptedLLM([ModelResponse(tool_calls=(call,)), finish]),
        tools=tools,
        prompt=PromptService("test"),
        tool_executor=DurableToolExecutor(tools, result_processor=externalizer),
    )
    assert asyncio.run(loop.run(agent, "get logs")) == "done"

    serialized = json.dumps([event.as_dict() for event in session.events])
    assert raw not in serialized
    tool_result = next(event for event in session.events if event.type is EventType.TOOL_RESULT)
    uri = tool_result.data["output"]["resource"]["uri"]  # type: ignore[index]
    assert service.read(
        uri, owner=ResourceOwner("agent-1", "session-1"), limit=8_000
    ).data.decode() == raw
    assert service.orphaned_resources(session) == ()


def test_mutation_commit_contains_handle_instead_of_large_raw_output(tmp_path) -> None:
    raw = "mutation-result:" + ("z" * 4_000)

    async def mutate(
        _arguments: dict[str, JsonValue], _context: ToolExecutionContext
    ) -> JsonValue:
        return raw

    tools = ToolRegistry()
    tools.register(
        ToolDefinition(
            ToolSchema("mutate", "Mutate.", {"type": "object"}),
            mutate,
            required_capability="mutate",
            effect_kind=ToolEffectKind.IDEMPOTENT_MUTATION,
        )
    )
    resources = service_at(tmp_path)
    executor = DurableToolExecutor(
        tools,
        operation_id_factory=lambda: "op-mutation",
        result_processor=ToolResultExternalizer(
            resources,
            ThresholdExternalizationPolicy(
                threshold_bytes=256,
                preview_head_bytes=64,
                preview_tail_bytes=32,
            ),
        ),
    )
    session = Session("session-1")
    agent = Agent.create(
        agent_id="agent-1", session=session, capabilities={"mutate"}
    )
    call = ToolCall("call-1", "mutate", {})
    session.append(EventType.TURN_START, {"turn": 1})
    session.append(EventType.USER_MESSAGE, {"turn": 1, "content": "mutate"})
    session.append(EventType.STEP_START, {"turn": 1, "step": 1})
    session.append(
        EventType.ASSISTANT_MESSAGE,
        {"turn": 1, "step": 1, "content": "", "tool_calls": [call.as_dict()]},
    )
    session.append(EventType.TOOL_CALL, {"turn": 1, "step": 1, **call.as_dict()})

    result = asyncio.run(
        executor.execute(call, agent.control, session, turn=1, step=1)
    )
    commit = next(event for event in session.events if event.type is EventType.TOOL_COMMIT)

    assert result.output == commit.data["output"]
    assert commit.data["output"]["resource"]["uri"].startswith("artifact://")  # type: ignore[index]
    assert raw not in json.dumps(commit.as_dict())
    assert len(resources.orphaned_resources(session)) == 1


def test_handle_in_jsonl_survives_session_and_resource_restart(tmp_path) -> None:
    resource_root = tmp_path / "resources"
    session_path = tmp_path / "session.jsonl"
    service = ResourceService(LocalResourceStore(resource_root))
    session = Session("session-1", JsonlSessionPersistence(session_path))
    handle = create_text(service, "restart-safe")
    append_tool_turn(
        session,
        1,
        output={"preview": "restart", "resource": handle.as_dict()},  # type: ignore[arg-type]
    )
    session.flush()
    session.close()

    restored_session = Session.load(
        "session-1", JsonlSessionPersistence(session_path)
    )
    restarted_service = ResourceService(LocalResourceStore(resource_root))
    try:
        uri = json.loads(restored_session.derive_messages()[-1].content)["output"][
            "resource"
        ]["uri"]
        assert restarted_service.read(
            uri,
            owner=ResourceOwner("agent-1", "session-1"),
            limit=64,
        ).data == b"restart-safe"
    finally:
        restored_session.close()


def test_context_pruning_never_mutates_resource_bytes(tmp_path) -> None:
    service = service_at(tmp_path)
    raw = ("line\n" * 2_000).encode()
    handle = service.create_artifact(
        raw,
        owner=ResourceOwner("agent-1", "session-1"),
        media_type="text/plain",
        encoding="utf-8",
        source_tool_name="logs",
        source_tool_call_id="call-1",
        source_operation_id="op-1",
    )
    session = Session("session-1")
    append_tool_turn(
        session,
        1,
        output={
            "preview": "begin " + ("preview " * 100) + "end",
            "resource": handle.as_dict(),
        },  # type: ignore[arg-type]
    )
    page = next(
        page
        for page in ContextProjector(ApproximateTokenEstimator(1)).project(session)
        if page.kind is ContextPageKind.TOOL_RESULT
    )
    pruned = ToolResultPruner(
        ToolResultPrunerConfig(threshold_tokens=100, head_tokens=20, tail_tokens=10),
        estimator=ApproximateTokenEstimator(1),
    ).prune(page)

    assert pruned.content != page.content
    read = service.read(
        handle.uri,
        owner=ResourceOwner("agent-1", "session-1"),
        limit=len(raw),
    ).data
    assert read == raw
    assert hashlib.sha256(read).hexdigest() == handle.sha256
