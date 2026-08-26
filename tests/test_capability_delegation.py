from __future__ import annotations

import asyncio
from collections.abc import Mapping

import pytest

from agentkernel import (
    AgentRegistry,
    AgentRegistryCorruptionError,
    AuthorizationRequest,
    CapabilityBoundError,
    CapabilityDelegator,
    CapabilityEvaluator,
    CapabilityGrant,
    DelegateCapabilityRequest,
    DelegationProvenance,
    DurableToolExecutor,
    ErrorCode,
    EventType,
    JsonlSessionPersistence,
    ProcessControlBlock,
    Session,
    TOOL_EXECUTE_ACTION,
    ToolCall,
    ToolDefinition,
    ToolEffectKind,
    ToolExecutionContext,
    ToolRegistry,
    ToolSchema,
    grant_fingerprint,
)
from agentkernel.protocol import JsonValue


READ_ACTION = "resource.read"
PROJECT_SCOPE = "artifact://project-a/**"
LOG_SCOPE = "artifact://project-a/logs/**"
PAYMENT_ACTION = "payment.charge"
PAYMENT_SCOPE = "payment://charges/**"


async def add(
    arguments: Mapping[str, JsonValue],
    _context: ToolExecutionContext,
) -> JsonValue:
    return int(arguments["left"]) + int(arguments["right"])


async def charge(
    arguments: Mapping[str, JsonValue],
    _context: ToolExecutionContext,
) -> JsonValue:
    return {"charged": arguments["amount_cents"]}


def math_tool(name: str = "math.add") -> ToolDefinition:
    return ToolDefinition(
        schema=ToolSchema(name, "Add two integers.", {"type": "object"}),
        handler=add,
        required_action=TOOL_EXECUTE_ACTION,
        required_resource=f"tool://{name}",
    )


def payment_tool() -> ToolDefinition:
    return ToolDefinition(
        schema=ToolSchema(
            "payment.charge",
            "Charge a fake invoice.",
            {"type": "object"},
        ),
        handler=charge,
        required_action=PAYMENT_ACTION,
        required_resource=PAYMENT_SCOPE,
        effect_kind=ToolEffectKind.IDEMPOTENT_MUTATION,
    )


def make_parent_child(
    parent_grant: CapabilityGrant,
) -> tuple[AgentRegistry, Session, Session]:
    registry = AgentRegistry()
    parent_session = Session("session-parent")
    child_session = Session("session-child")
    parent = registry.create_root(
        agent_id="agent-parent",
        session=parent_session,
        capability_grants=(parent_grant,),
        creation_id="create-parent",
    )
    registry.create_child(
        parent_agent_id=parent.control.agent_id,
        agent_id="agent-child",
        session=child_session,
        creation_id="create-child",
        record_session=parent_session,
    )
    return registry, parent_session, child_session


def delegate_read(
    registry: AgentRegistry,
    child_session: Session,
    *,
    scope: str = LOG_SCOPE,
    constraints: Mapping[str, JsonValue] | None = None,
):
    return registry.delegate_capability(
        DelegateCapabilityRequest(
            parent_agent_id="agent-parent",
            child_agent_id="agent-child",
            action=READ_ACTION,
            resource_scope=scope,
            constraints=constraints or {},
            correlation_id="delegate-read",
        ),
        record_session=child_session,
    )


def begin_call(session: Session, call: ToolCall) -> None:
    session.append(EventType.TURN_START, {"turn": 1})
    session.append(EventType.USER_MESSAGE, {"turn": 1, "content": "run tool"})
    session.append(EventType.STEP_START, {"turn": 1, "step": 1})
    session.append(
        EventType.ASSISTANT_MESSAGE,
        {"turn": 1, "step": 1, "content": "", "tool_calls": [call.as_dict()]},
    )
    session.append(EventType.TOOL_CALL, {"turn": 1, "step": 1, **call.as_dict()})


def test_direct_parent_child_delegation_installs_durable_child_grant() -> None:
    parent_grant = CapabilityGrant("agent-parent", READ_ACTION, PROJECT_SCOPE)
    registry, parent_session, child_session = make_parent_child(parent_grant)
    original_child_control = registry.get("agent-child")

    assert original_child_control.capability_grants == ()

    decision = delegate_read(registry, child_session)

    assert decision.allowed is True
    assert decision.delegated_grant is not None
    assert decision.delegated_grant.subject == "agent-child"
    assert original_child_control.capability_grants == ()
    assert registry.get("agent-child") is not original_child_control
    assert registry.get("agent-child").capability_grants == (
        decision.delegated_grant,
    )
    assert registry.get("agent-parent").capability_grants == (parent_grant,)
    assert [event.type for event in child_session.events] == [
        EventType.CAPABILITY_DELEGATED
    ]
    assert all(
        event.type is not EventType.CAPABILITY_DELEGATED
        for event in parent_session.events
    )


def test_unknown_and_non_parent_delegation_are_denied() -> None:
    grant = CapabilityGrant("agent-parent", READ_ACTION, PROJECT_SCOPE)
    registry, _parent_session, child_session = make_parent_child(grant)
    other = registry.create_root(
        agent_id="agent-other",
        session=Session("session-other"),
        capability_grants=(
            CapabilityGrant("agent-other", READ_ACTION, PROJECT_SCOPE),
        ),
        creation_id="create-other",
    )

    missing_parent = registry.delegate_capability(
        DelegateCapabilityRequest(
            "missing-parent",
            "agent-child",
            READ_ACTION,
            LOG_SCOPE,
        ),
        record=False,
    )
    missing_child = registry.delegate_capability(
        DelegateCapabilityRequest(
            "agent-parent",
            "missing-child",
            READ_ACTION,
            LOG_SCOPE,
        ),
        record=False,
    )
    non_parent = registry.delegate_capability(
        DelegateCapabilityRequest(
            other.control.agent_id,
            "agent-child",
            READ_ACTION,
            LOG_SCOPE,
        ),
        record_session=child_session,
    )

    assert missing_parent.allowed is False
    assert missing_parent.reason == "parent_agent_not_found"
    assert missing_child.allowed is False
    assert missing_child.reason == "child_agent_not_found"
    assert non_parent.allowed is False
    assert non_parent.reason == "not_direct_child"
    assert registry.get("agent-child").capability_grants == ()


def test_scope_and_action_narrowing_are_enforced() -> None:
    grant = CapabilityGrant("agent-parent", READ_ACTION, PROJECT_SCOPE)
    registry, _parent_session, child_session = make_parent_child(grant)

    equal = delegate_read(registry, child_session, scope=PROJECT_SCOPE)
    allowed = delegate_read(registry, child_session, scope=LOG_SCOPE)
    sibling = delegate_read(
        registry,
        child_session,
        scope="artifact://project-b/logs/**",
    )
    write = registry.delegate_capability(
        DelegateCapabilityRequest(
            "agent-parent",
            "agent-child",
            "resource.write",
            LOG_SCOPE,
        ),
        record_session=child_session,
    )

    assert equal.allowed is True
    assert allowed.allowed is True
    assert sibling.allowed is False
    assert sibling.reason == "parent_authority_not_found"
    assert write.allowed is False
    assert write.reason == "parent_authority_not_found"


def test_parent_grant_fingerprint_uses_canonical_constraints() -> None:
    first = CapabilityGrant(
        "agent-parent",
        READ_ACTION,
        "ARTIFACT://project-a/logs/**",
        {"tenant": "blue", "max_bytes": 1024},
    )
    second = CapabilityGrant(
        "agent-parent",
        READ_ACTION,
        LOG_SCOPE,
        {"max_bytes": 1024, "tenant": "blue"},
    )

    assert grant_fingerprint(first) == grant_fingerprint(second)


def test_legacy_bounding_set_and_delegator_bound_are_respected() -> None:
    with pytest.raises(CapabilityBoundError):
        AgentRegistry().create_root(
            agent_id="agent-parent",
            session=Session("session-parent"),
            capabilities={READ_ACTION},
            capability_bounding_set=set(),
            creation_id="create-parent",
        )

    parent_grant = CapabilityGrant("agent-parent", READ_ACTION, PROJECT_SCOPE)
    bounded = CapabilityDelegator().delegate(
        DelegateCapabilityRequest(
            "agent-parent",
            "agent-child",
            READ_ACTION,
            PROJECT_SCOPE,
        ),
        parent_grants=(parent_grant,),
        parent_bounding_grants=(
            CapabilityGrant("agent-parent", READ_ACTION, LOG_SCOPE),
        ),
    )
    narrowed = CapabilityDelegator().delegate(
        DelegateCapabilityRequest(
            "agent-parent",
            "agent-child",
            READ_ACTION,
            LOG_SCOPE,
        ),
        parent_grants=(parent_grant,),
        parent_bounding_grants=(
            CapabilityGrant("agent-parent", READ_ACTION, LOG_SCOPE),
        ),
    )

    assert bounded.allowed is False
    assert bounded.reason == "outside_parent_bounding_set"
    assert narrowed.allowed is True


def test_scope_normalization_rejects_escape_patterns() -> None:
    with pytest.raises(ValueError, match="path traversal"):
        CapabilityGrant("agent-parent", READ_ACTION, "artifact://project-a/../x")
    with pytest.raises(ValueError, match="encoded"):
        CapabilityGrant("agent-parent", READ_ACTION, "artifact://project-a/%2e%2e/x")
    with pytest.raises(ValueError, match="duplicate"):
        CapabilityGrant("agent-parent", READ_ACTION, "artifact://project-a//x")
    with pytest.raises(ValueError, match="final"):
        CapabilityGrant("agent-parent", READ_ACTION, "artifact://project-a/**/x")


def test_constraint_narrowing_is_fail_closed() -> None:
    grant = CapabilityGrant(
        "agent-parent",
        READ_ACTION,
        PROJECT_SCOPE,
        {"max_bytes": 1024, "tenant": "blue"},
    )
    registry, _parent_session, child_session = make_parent_child(grant)

    stricter = delegate_read(
        registry,
        child_session,
        constraints={"max_bytes": 512, "tenant": "blue"},
    )
    wider = delegate_read(
        registry,
        child_session,
        constraints={"max_bytes": 2048, "tenant": "blue"},
    )
    missing_parent_constraint = delegate_read(
        registry,
        child_session,
        constraints={"max_bytes": 512},
    )
    changed_unknown = delegate_read(
        registry,
        child_session,
        constraints={"max_bytes": 512, "tenant": "red"},
    )
    new_unknown = delegate_read(
        registry,
        child_session,
        constraints={"max_bytes": 512, "tenant": "blue", "region": "us"},
    )

    assert stricter.allowed is True
    assert wider.allowed is False
    assert missing_parent_constraint.allowed is False
    assert changed_unknown.allowed is False
    assert new_unknown.allowed is False


def test_stable_delegation_id_and_duplicate_live_delegation_are_idempotent() -> None:
    grant = CapabilityGrant("agent-parent", READ_ACTION, PROJECT_SCOPE)
    registry, _parent_session, child_session = make_parent_child(grant)
    request = DelegateCapabilityRequest(
        "agent-parent",
        "agent-child",
        READ_ACTION,
        LOG_SCOPE,
        correlation_id="same-request",
    )

    first = registry.delegate_capability(request, record_session=child_session)
    second = registry.delegate_capability(request, record_session=child_session)

    assert first.allowed is True
    assert second.allowed is True
    assert first.delegation_id == second.delegation_id
    assert len(child_session.events) == 1
    assert grant_fingerprint(first.delegated_grant) == grant_fingerprint(  # type: ignore[arg-type]
        second.delegated_grant  # type: ignore[arg-type]
    )


def test_multi_hop_delegation_must_keep_narrowing_chain() -> None:
    root_grant = CapabilityGrant("agent-root", READ_ACTION, PROJECT_SCOPE)
    registry = AgentRegistry()
    root_session = Session("session-root")
    child_session = Session("session-child")
    grandchild_session = Session("session-grandchild")
    root = registry.create_root(
        agent_id="agent-root",
        session=root_session,
        capability_grants=(root_grant,),
        creation_id="create-root",
    )
    child = registry.create_child(
        parent_agent_id=root.control.agent_id,
        agent_id="agent-child",
        session=child_session,
        creation_id="create-child",
        record_session=root_session,
    )
    registry.create_child(
        parent_agent_id=child.control.agent_id,
        agent_id="agent-grandchild",
        session=grandchild_session,
        creation_id="create-grandchild",
        record_session=child_session,
    )

    child_decision = registry.delegate_capability(
        DelegateCapabilityRequest("agent-root", "agent-child", READ_ACTION, LOG_SCOPE),
        record_session=child_session,
    )
    grandchild_decision = registry.delegate_capability(
        DelegateCapabilityRequest(
            "agent-child",
            "agent-grandchild",
            READ_ACTION,
            "artifact://project-a/logs/day-1/**",
        ),
        record_session=grandchild_session,
    )
    wider = registry.delegate_capability(
        DelegateCapabilityRequest(
            "agent-child",
            "agent-grandchild",
            READ_ACTION,
            PROJECT_SCOPE,
        ),
        record_session=grandchild_session,
    )

    assert child_decision.allowed is True
    assert grandchild_decision.allowed is True
    assert grandchild_decision.provenance is not None
    assert grandchild_decision.provenance.depth == 2
    assert (
        grandchild_decision.provenance.parent_delegation_id
        == child_decision.delegation_id
    )
    assert wider.allowed is False


def test_delegated_tool_is_visible_and_forged_tool_is_denied() -> None:
    parent_grant = CapabilityGrant(
        "agent-parent",
        TOOL_EXECUTE_ACTION,
        "tool://math.add",
    )
    registry, _parent_session, child_session = make_parent_child(parent_grant)
    decision = registry.delegate_capability(
        DelegateCapabilityRequest(
            "agent-parent",
            "agent-child",
            TOOL_EXECUTE_ACTION,
            "tool://math.add",
        ),
        record_session=child_session,
    )
    tools = ToolRegistry()
    tools.register(math_tool("math.add"))
    tools.register(math_tool("math.secret"))

    assert decision.allowed is True
    assert [schema.name for schema in tools.model_schemas(registry.get("agent-child"))] == [
        "math.add"
    ]

    allowed = asyncio.run(
        tools.execute(
            ToolCall("call-1", "math.add", {"left": 2, "right": 3}),
            registry.get("agent-child"),
        )
    )
    forged = asyncio.run(
        tools.execute(
            ToolCall("call-2", "math.secret", {"left": 2, "right": 3}),
            registry.get("agent-child"),
        )
    )

    assert allowed.ok is True
    assert allowed.output == 5
    assert forged.ok is False
    assert forged.error is not None
    assert forged.error.code is ErrorCode.EACCES


def test_durable_mutation_requires_delegated_child_authority() -> None:
    parent_grant = CapabilityGrant("agent-parent", PAYMENT_ACTION, PAYMENT_SCOPE)
    registry, _parent_session, child_session = make_parent_child(parent_grant)
    tools = ToolRegistry()
    tools.register(payment_tool())
    call = ToolCall("call-payment", "payment.charge", {"amount_cents": 42})
    begin_call(child_session, call)

    denied = asyncio.run(
        DurableToolExecutor(tools, operation_id_factory=lambda: "op-denied").execute(
            call,
            registry.get("agent-child"),
            child_session,
            turn=1,
            step=1,
        )
    )

    assert denied.ok is False
    assert denied.error is not None
    assert denied.error.code is ErrorCode.EACCES

    allowed_session = Session("session-child-allowed")
    registry = AgentRegistry()
    parent = registry.create_root(
        agent_id="agent-parent",
        session=Session("session-parent-allowed"),
        capability_grants=(parent_grant,),
        creation_id="create-parent-allowed",
    )
    registry.create_child(
        parent_agent_id=parent.control.agent_id,
        agent_id="agent-child",
        session=allowed_session,
        creation_id="create-child-allowed",
        record_session=parent.session,
    )
    decision = registry.delegate_capability(
        DelegateCapabilityRequest(
            "agent-parent",
            "agent-child",
            PAYMENT_ACTION,
            PAYMENT_SCOPE,
        ),
        record_session=allowed_session,
    )
    begin_call(allowed_session, call)

    allowed = asyncio.run(
        DurableToolExecutor(tools, operation_id_factory=lambda: "op-allowed").execute(
            call,
            registry.get("agent-child"),
            allowed_session,
            turn=1,
            step=1,
        )
    )

    assert decision.allowed is True
    assert allowed.ok is True
    assert any(
        event.type is EventType.TOOL_PREPARE
        and event.data["operation_id"] == "op-allowed"
        for event in allowed_session.events
    )


def test_process_lineage_does_not_grant_agent_authority() -> None:
    parent_grant = CapabilityGrant(
        "agent-parent",
        TOOL_EXECUTE_ACTION,
        "tool://math.add",
    )
    registry, _parent_session, _child_session = make_parent_child(parent_grant)
    process = ProcessControlBlock.create(
        process_id="tool://math.add",
        agent=registry.get("agent-child"),
        parent_process_id="process-parent",
    )
    tools = ToolRegistry()
    tools.register(math_tool("math.add"))

    result = asyncio.run(
        tools.execute(
            ToolCall("call-1", "math.add", {"left": 2, "right": 3}),
            registry.get("agent-child"),
        )
    )

    assert process.capability_snapshot.agent_id == "agent-child"
    assert process.capability_snapshot.capability_grants == ()
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ErrorCode.EACCES


def test_jsonl_restart_requires_current_parent_authority_to_activate(tmp_path) -> None:
    parent_grant = CapabilityGrant("agent-parent", READ_ACTION, PROJECT_SCOPE)
    parent_path = tmp_path / "parent.jsonl"
    child_path = tmp_path / "child.jsonl"
    registry = AgentRegistry()
    parent_session = Session(
        "session-parent",
        JsonlSessionPersistence(parent_path),
    )
    child_session = Session(
        "session-child",
        JsonlSessionPersistence(child_path),
    )
    parent = registry.create_root(
        agent_id="agent-parent",
        session=parent_session,
        capability_grants=(parent_grant,),
        creation_id="create-parent",
    )
    registry.create_child(
        parent_agent_id=parent.control.agent_id,
        agent_id="agent-child",
        session=child_session,
        creation_id="create-child",
        record_session=parent_session,
    )
    decision = delegate_read(registry, child_session)
    parent_session.close()
    child_session.close()

    restored_parent = Session.load(
        "session-parent",
        JsonlSessionPersistence(parent_path),
    )
    restored_child = Session.load(
        "session-child",
        JsonlSessionPersistence(child_path),
    )
    try:
        without_authority = AgentRegistry.reconstruct(
            (restored_parent, restored_child)
        )
        with_authority = AgentRegistry.reconstruct(
            (restored_parent, restored_child),
            current_capability_grants={"agent-parent": (parent_grant,)},
        )
    finally:
        restored_parent.close()
        restored_child.close()

    assert decision.allowed is True
    assert without_authority.get("agent-child").capability_grants == ()
    assert with_authority.get("agent-child").capability_grants == (
        decision.delegated_grant,
    )


def test_duplicate_replay_is_idempotent_and_conflict_is_rejected() -> None:
    parent_grant = CapabilityGrant("agent-parent", READ_ACTION, PROJECT_SCOPE)
    registry, _parent_session, child_session = make_parent_child(parent_grant)
    decision = delegate_read(registry, child_session)
    assert decision.allowed is True

    replayed = registry.replay_delegations((child_session, child_session))

    assert len(replayed) == 1
    assert replayed[0].allowed is True

    assert decision.provenance is not None
    assert decision.delegated_grant is not None
    conflicting = Session("session-conflict")
    conflicting.append(
        EventType.CAPABILITY_DELEGATED,
        decision.provenance.as_payload(decision.delegated_grant),
    )
    conflicting_provenance = DelegationProvenance(
        delegation_id=decision.provenance.delegation_id,
        parent_agent_id="agent-parent",
        child_agent_id="agent-child",
        parent_grant_fingerprint=grant_fingerprint(parent_grant),
        action=READ_ACTION,
        resource_scope="artifact://project-a/private/**",
        correlation_id="conflict",
    )
    conflicting_grant = CapabilityGrant(
        "agent-child",
        READ_ACTION,
        "artifact://project-a/private/**",
    )
    conflicting.append(
        EventType.CAPABILITY_DELEGATED,
        conflicting_provenance.as_payload(conflicting_grant),
    )

    with pytest.raises(AgentRegistryCorruptionError, match="conflicting"):
        registry.replay_delegations((conflicting,))


def test_multi_hop_reconstructs_deterministically_from_sessions(tmp_path) -> None:
    root_grant = CapabilityGrant("agent-root", READ_ACTION, PROJECT_SCOPE)
    root_path = tmp_path / "root.jsonl"
    child_path = tmp_path / "child.jsonl"
    grandchild_path = tmp_path / "grandchild.jsonl"
    registry = AgentRegistry()
    root_session = Session("session-root", JsonlSessionPersistence(root_path))
    child_session = Session("session-child", JsonlSessionPersistence(child_path))
    grandchild_session = Session(
        "session-grandchild",
        JsonlSessionPersistence(grandchild_path),
    )
    root = registry.create_root(
        agent_id="agent-root",
        session=root_session,
        capability_grants=(root_grant,),
        creation_id="create-root",
    )
    child = registry.create_child(
        parent_agent_id=root.control.agent_id,
        agent_id="agent-child",
        session=child_session,
        creation_id="create-child",
        record_session=root_session,
    )
    registry.create_child(
        parent_agent_id=child.control.agent_id,
        agent_id="agent-grandchild",
        session=grandchild_session,
        creation_id="create-grandchild",
        record_session=child_session,
    )
    child_decision = registry.delegate_capability(
        DelegateCapabilityRequest("agent-root", "agent-child", READ_ACTION, LOG_SCOPE),
        record_session=child_session,
    )
    grandchild_decision = registry.delegate_capability(
        DelegateCapabilityRequest(
            "agent-child",
            "agent-grandchild",
            READ_ACTION,
            "artifact://project-a/logs/day-1/**",
        ),
        record_session=grandchild_session,
    )
    for session in (root_session, child_session, grandchild_session):
        session.close()

    restored_root = Session.load("session-root", JsonlSessionPersistence(root_path))
    restored_child = Session.load("session-child", JsonlSessionPersistence(child_path))
    restored_grandchild = Session.load(
        "session-grandchild",
        JsonlSessionPersistence(grandchild_path),
    )
    try:
        reconstructed = AgentRegistry.reconstruct(
            (restored_grandchild, restored_child, restored_root),
            current_capability_grants={"agent-root": (root_grant,)},
        )
    finally:
        restored_root.close()
        restored_child.close()
        restored_grandchild.close()

    assert child_decision.allowed is True
    assert grandchild_decision.allowed is True
    assert reconstructed.get("agent-child").capability_grants == (
        child_decision.delegated_grant,
    )
    assert reconstructed.get("agent-grandchild").capability_grants == (
        grandchild_decision.delegated_grant,
    )
    assert CapabilityEvaluator(
        reconstructed.get("agent-grandchild").capability_grants
    ).authorize(
        AuthorizationRequest(
            "agent-grandchild",
            READ_ACTION,
            "artifact://project-a/logs/day-1/file.txt",
        )
    ).allowed
