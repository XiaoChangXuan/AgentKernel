from __future__ import annotations

import asyncio
from collections.abc import Mapping

import pytest

from agentkernel import (
    Agent,
    AgentAlreadyExists,
    AgentControlBlock,
    AgentRegistry,
    AgentRegistryCorruptionError,
    AgentTreeError,
    CapabilityGrant,
    ErrorCode,
    EventType,
    InvalidAgentParent,
    JsonlSessionPersistence,
    ProcessControlBlock,
    Session,
    TOOL_EXECUTE_ACTION,
    ToolCall,
    ToolDefinition,
    ToolExecutionContext,
    ToolRegistry,
    ToolSchema,
)
from agentkernel.protocol import JsonValue


async def charge(
    arguments: Mapping[str, JsonValue],
    _context: ToolExecutionContext,
) -> JsonValue:
    return {"charged": arguments["amount"]}


def payment_tool() -> ToolDefinition:
    return ToolDefinition(
        schema=ToolSchema(
            name="payment.charge",
            description="Charge a payment.",
            input_schema={"type": "object"},
        ),
        handler=charge,
        required_capability="payment.charge",
    )


def test_root_child_queries_and_multiple_roots_are_stable() -> None:
    registry = AgentRegistry()
    root = registry.create_root(
        agent_id="agent-root",
        session=Session("session-root"),
        creation_id="create-root",
    )
    child_a = registry.create_child(
        parent_agent_id=root.control.agent_id,
        agent_id="agent-child-a",
        session=Session("session-child-a"),
        creation_id="create-child-a",
        record_session=root.session,
    )
    child_b = registry.create_child(
        parent_agent_id=root.control.agent_id,
        agent_id="agent-child-b",
        session=Session("session-child-b"),
        creation_id="create-child-b",
        record_session=root.session,
    )
    second_root = registry.create_root(
        agent_id="agent-other-root",
        session=Session("session-other-root"),
        creation_id="create-other-root",
    )

    assert registry.contains(root.control.agent_id)
    assert registry.get(child_a.control.agent_id) is child_a.control
    assert registry.parent_of(root.control.agent_id) is None
    assert registry.parent_of(child_a.control.agent_id) == root.control.agent_id
    assert registry.children_of(root.control.agent_id) == (
        child_a.control.agent_id,
        child_b.control.agent_id,
    )
    assert registry.children_of(second_root.control.agent_id) == ()
    assert registry.root_of(child_b.control.agent_id) == root.control.agent_id
    assert registry.lineage(child_b.control.agent_id) == (
        root.control.agent_id,
        child_b.control.agent_id,
    )
    assert registry.root_of(second_root.control.agent_id) == second_root.control.agent_id


def test_duplicate_missing_parent_and_self_parent_are_rejected() -> None:
    registry = AgentRegistry()
    registry.create_root(
        agent_id="agent-root",
        session=Session("session-root"),
        creation_id="create-root",
    )

    with pytest.raises(AgentAlreadyExists, match="agent already exists"):
        registry.create_root(
            agent_id="agent-root",
            session=Session("session-duplicate"),
            creation_id="create-duplicate",
        )

    with pytest.raises(InvalidAgentParent, match="parent agent not found"):
        registry.create_child(
            parent_agent_id="missing-parent",
            agent_id="agent-orphan",
            session=Session("session-orphan"),
            creation_id="create-orphan",
        )

    with pytest.raises(ValueError, match="own parent"):
        Agent.create(
            agent_id="agent-self",
            session=Session("session-self"),
            parent_agent_id="agent-self",
        )


def test_shared_session_identity_is_rejected() -> None:
    registry = AgentRegistry()
    registry.create_root(
        agent_id="agent-a",
        session=Session("session-shared"),
        creation_id="create-a",
    )

    with pytest.raises(AgentTreeError, match="already belongs"):
        registry.create_root(
            agent_id="agent-b",
            session=Session("session-shared"),
            creation_id="create-b",
        )


def test_child_is_deny_by_default_and_parent_authority_is_not_copied() -> None:
    grant = CapabilityGrant(
        subject="agent-parent",
        action=TOOL_EXECUTE_ACTION,
        resource_scope="tool://payment.charge",
    )
    registry = AgentRegistry()
    parent = registry.create_root(
        agent_id="agent-parent",
        session=Session("session-parent"),
        capabilities={"payment.charge"},
        capability_grants=(grant,),
        creation_id="create-parent",
    )
    child = registry.create_child(
        parent_agent_id=parent.control.agent_id,
        agent_id="agent-child",
        session=Session("session-child"),
        creation_id="create-child",
        record_session=parent.session,
    )

    assert child.control.parent_agent_id == parent.control.agent_id
    assert child.control.capabilities == frozenset()
    assert child.control.capability_bounding_set == frozenset()
    assert child.control.capability_grants == ()
    assert parent.control.capabilities == frozenset({"payment.charge"})
    assert parent.control.capability_grants == (grant,)
    assert child.control.budget is not parent.control.budget


def test_child_does_not_see_or_execute_parent_authorized_tool() -> None:
    tools = ToolRegistry()
    tools.register(payment_tool())
    registry = AgentRegistry()
    parent = registry.create_root(
        agent_id="agent-parent",
        session=Session("session-parent"),
        capabilities={"payment.charge"},
        creation_id="create-parent",
    )
    child = registry.create_child(
        parent_agent_id=parent.control.agent_id,
        agent_id="agent-child",
        session=Session("session-child"),
        creation_id="create-child",
        record_session=parent.session,
    )

    assert [schema.name for schema in tools.model_schemas(parent.control)] == [
        "payment.charge"
    ]
    assert tools.model_schemas(child.control) == ()

    result = asyncio.run(
        tools.execute(
            ToolCall("call-1", "payment.charge", {"amount": 10}),
            child.control,
        )
    )

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ErrorCode.EACCES


def test_process_identity_is_not_capability_subject() -> None:
    tools = ToolRegistry()
    tools.register(payment_tool())
    registry = AgentRegistry()
    root = registry.create_root(
        agent_id="agent-root",
        session=Session("session-root"),
        creation_id="create-root",
    )
    child = registry.create_child(
        parent_agent_id=root.control.agent_id,
        agent_id="agent-child",
        session=Session("session-child"),
        creation_id="create-child",
        record_session=root.session,
    )
    process = ProcessControlBlock.create(
        process_id="payment.charge",
        agent=child.control,
    )

    result = asyncio.run(
        tools.execute(
            ToolCall("call-1", "payment.charge", {"amount": 10}),
            child.control,
        )
    )

    assert process.capability_snapshot.agent_id == child.control.agent_id
    assert process.capability_snapshot.agent_id != process.process_id
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ErrorCode.EACCES


def test_registry_reconstructs_identity_tree_from_jsonl_session(tmp_path) -> None:
    parent_path = tmp_path / "parent.jsonl"
    parent_session = Session(
        "session-parent",
        JsonlSessionPersistence(parent_path),
    )
    registry = AgentRegistry()
    root = registry.create_root(
        agent_id="agent-root",
        session=parent_session,
        creation_id="create-root",
    )
    child = registry.create_child(
        parent_agent_id=root.control.agent_id,
        agent_id="agent-child",
        session=Session(
            "session-child",
            JsonlSessionPersistence(tmp_path / "child.jsonl"),
        ),
        creation_id="create-child",
        record_session=parent_session,
    )
    parent_session.close()
    child.session.close()

    restored_session = Session.load(
        "session-parent",
        JsonlSessionPersistence(parent_path),
    )
    try:
        reconstructed = AgentRegistry.reconstruct((restored_session,))
    finally:
        restored_session.close()

    assert reconstructed.contains(root.control.agent_id)
    assert reconstructed.contains(child.control.agent_id)
    assert reconstructed.parent_of(child.control.agent_id) == root.control.agent_id
    assert reconstructed.children_of(root.control.agent_id) == (child.control.agent_id,)
    assert reconstructed.root_of(child.control.agent_id) == root.control.agent_id
    assert reconstructed.lineage(child.control.agent_id) == (
        root.control.agent_id,
        child.control.agent_id,
    )
    assert reconstructed.get(child.control.agent_id).capabilities == frozenset()
    assert reconstructed.get(child.control.agent_id).capability_grants == ()


def test_duplicate_replay_is_idempotent_for_same_creation_fact() -> None:
    session = Session("session-root")
    session.append(
        EventType.AGENT_CREATED,
        {
            "agent_id": "agent-root",
            "parent_agent_id": None,
            "session_id": "session-root",
            "creation_id": "create-root",
        },
    )

    reconstructed = AgentRegistry.reconstruct((session, session))

    assert reconstructed.contains("agent-root")
    assert reconstructed.children_of("agent-root") == ()


def test_conflicting_durable_identity_fact_is_rejected() -> None:
    session = Session("session-root")
    session.append(
        EventType.AGENT_CREATED,
        {
            "agent_id": "agent-root",
            "parent_agent_id": None,
            "session_id": "session-root",
            "creation_id": "create-root",
        },
    )
    session.append(
        EventType.AGENT_CREATED,
        {
            "agent_id": "agent-root",
            "parent_agent_id": None,
            "session_id": "session-root",
            "creation_id": "create-root-conflict",
        },
    )

    with pytest.raises(AgentRegistryCorruptionError, match="multiple creation facts"):
        AgentRegistry.reconstruct((session,))


def test_missing_parent_and_cycle_in_durable_facts_are_rejected() -> None:
    missing = Session("session-missing")
    missing.append(
        EventType.AGENT_CREATED,
        {
            "agent_id": "agent-child",
            "parent_agent_id": "agent-missing",
            "session_id": "session-child",
            "creation_id": "create-child",
        },
    )

    with pytest.raises(AgentRegistryCorruptionError, match="missing parent"):
        AgentRegistry.reconstruct((missing,))

    first = Session("session-first")
    first.append(
        EventType.AGENT_CREATED,
        {
            "agent_id": "agent-a",
            "parent_agent_id": "agent-b",
            "session_id": "session-a",
            "creation_id": "create-a",
        },
    )
    second = Session("session-second")
    second.append(
        EventType.AGENT_CREATED,
        {
            "agent_id": "agent-b",
            "parent_agent_id": "agent-a",
            "session_id": "session-b",
            "creation_id": "create-b",
        },
    )

    with pytest.raises(AgentRegistryCorruptionError, match="cycle"):
        AgentRegistry.reconstruct((first, second))


def test_existing_agent_create_behavior_remains_backward_compatible() -> None:
    session = Session("session-legacy")
    agent = Agent.create(agent_id="agent-legacy", session=session)

    assert isinstance(agent.control, AgentControlBlock)
    assert agent.control.parent_agent_id is None
    assert session.events == ()
