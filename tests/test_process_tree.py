from __future__ import annotations

import pytest

from agentkernel import (
    Agent,
    AgentRegistry,
    AuthorizationRequest,
    CapabilityEvaluator,
    CapabilityGrant,
    CooperativeScheduler,
    EventType,
    InvalidProcessParent,
    JsonlSessionPersistence,
    ProcessAlreadyExists,
    ProcessControlBlock,
    ProcessManager,
    ProcessRegistryCorruptionError,
    ProcessSessionConflict,
    ProcessState,
    ProcessTreeError,
    Session,
    TOOL_EXECUTE_ACTION,
)


def _agent_tree(
    *,
    parent_has_payment: bool = False,
) -> tuple[AgentRegistry, Agent, Agent]:
    registry = AgentRegistry()
    grant = CapabilityGrant(
        subject="agent-parent",
        action=TOOL_EXECUTE_ACTION,
        resource_scope="tool://payment.charge",
    )
    parent = registry.create_root(
        agent_id="agent-parent",
        session=Session("session-parent"),
        capabilities={"payment.charge"} if parent_has_payment else (),
        capability_grants=(grant,) if parent_has_payment else (),
        creation_id="create-agent-parent",
    )
    child = registry.create_child(
        parent_agent_id=parent.control.agent_id,
        agent_id="agent-child",
        session=Session("session-child"),
        creation_id="create-agent-child",
        record_session=parent.session,
    )
    return registry, parent, child


def test_process_tree_queries_support_roots_same_agent_and_child_agent() -> None:
    registry, parent, child = _agent_tree()
    manager = ProcessManager(agent_registry=registry)

    root = manager.create_process(
        process_id="process-parent-root",
        agent=parent.control,
        record_session=parent.session,
        creation_id="create-process-root",
    )
    same_agent_child = manager.create_child_process(
        parent_process_id=root.process_id,
        process_id="process-same-agent-child",
        agent=parent.control,
        record_session=parent.session,
        creation_id="create-process-same-agent-child",
    )
    child_agent_process = manager.create_child_process(
        parent_process_id=root.process_id,
        process_id="process-child-agent",
        agent=child.control,
        record_session=child.session,
        creation_id="create-process-child-agent",
    )
    second_root = manager.create_process(
        process_id="process-second-root",
        agent=parent.control,
        creation_id="create-process-second-root",
    )

    assert manager.parent_of(root.process_id) is None
    assert manager.parent_of(same_agent_child.process_id) == root.process_id
    assert manager.children_of(root.process_id) == (
        same_agent_child.process_id,
        child_agent_process.process_id,
    )
    assert manager.children_of(second_root.process_id) == ()
    assert manager.root_of(child_agent_process.process_id) == root.process_id
    assert manager.root_of(second_root.process_id) == second_root.process_id
    assert manager.lineage(child_agent_process.process_id) == (
        root.process_id,
        child_agent_process.process_id,
    )
    assert manager.descendants_of(root.process_id) == (
        same_agent_child.process_id,
        child_agent_process.process_id,
    )
    assert manager.depth(root.process_id) == 0
    assert manager.depth(child_agent_process.process_id) == 1

    assert registry.parent_of(child.control.agent_id) == parent.control.agent_id
    assert registry.children_of(parent.control.agent_id) == (child.control.agent_id,)
    assert not registry.contains(same_agent_child.process_id)
    assert registry.lineage(child.control.agent_id) != manager.lineage(
        child_agent_process.process_id
    )


def test_parent_validation_duplicate_self_parent_and_session_ownership() -> None:
    registry, parent, _child = _agent_tree()
    manager = ProcessManager(agent_registry=registry)
    manager.create_process(process_id="process-root", agent=parent.control)

    with pytest.raises(ProcessAlreadyExists, match="process already exists"):
        manager.create_process(process_id="process-root", agent=parent.control)

    with pytest.raises(InvalidProcessParent, match="parent process not found"):
        manager.create_child_process(
            parent_process_id="missing-parent",
            process_id="process-orphan",
            agent=parent.control,
        )

    with pytest.raises(ValueError, match="own parent"):
        ProcessControlBlock.create(
            process_id="process-self",
            agent=parent.control,
            parent_process_id="process-self",
        )

    unregistered = Agent.create(
        agent_id="agent-unregistered",
        session=Session("session-unregistered"),
    )
    with pytest.raises(ProcessTreeError, match="owning agent not found"):
        manager.create_process(
            process_id="process-unregistered-agent",
            agent=unregistered.control,
        )

    wrong_session_agent = Agent.create(
        agent_id=parent.control.agent_id,
        session=Session("session-wrong"),
    )
    with pytest.raises(ProcessTreeError, match="primary session"):
        manager.create_process(
            process_id="process-wrong-session",
            agent=wrong_session_agent.control,
        )

    wrong_session = Session("session-not-owner")
    with pytest.raises(ProcessTreeError, match="owning Agent session"):
        manager.record_process_created(wrong_session, manager.get("process-root"))


def test_process_lineage_does_not_grant_agent_authority() -> None:
    registry, parent, child = _agent_tree(parent_has_payment=True)
    manager = ProcessManager(agent_registry=registry)
    parent_process = manager.create_process(
        process_id="process-parent",
        agent=parent.control,
    )
    child_agent_process = manager.create_child_process(
        parent_process_id=parent_process.process_id,
        process_id="process-child-agent",
        agent=child.control,
    )

    evaluator = CapabilityEvaluator.from_agent_capabilities(
        agent_id=child_agent_process.capability_snapshot.agent_id,
        capabilities=child_agent_process.capability_snapshot.capabilities,
        capability_grants=child_agent_process.capability_snapshot.capability_grants,
    )
    decision = evaluator.authorize(
        AuthorizationRequest(
            agent_id=child.control.agent_id,
            action=TOOL_EXECUTE_ACTION,
            resource="tool://payment.charge",
        )
    )
    process_subject_decision = evaluator.authorize(
        AuthorizationRequest(
            agent_id=child_agent_process.process_id,
            action=TOOL_EXECUTE_ACTION,
            resource="tool://payment.charge",
        )
    )

    assert parent.control.capability_grants
    assert child.control.capability_grants == ()
    assert child_agent_process.parent_process_id == parent_process.process_id
    assert child_agent_process.capability_snapshot.agent_id == child.control.agent_id
    assert decision.allowed is False
    assert process_subject_decision.allowed is False


def test_child_exit_is_observable_without_exiting_parent_or_mutating_session() -> None:
    registry, parent, _child = _agent_tree()
    manager = ProcessManager(agent_registry=registry)
    parent_process = manager.create_process(
        process_id="process-parent",
        agent=parent.control,
    )
    child_process = manager.create_child_process(
        parent_process_id=parent_process.process_id,
        process_id="process-child",
        agent=parent.control,
    )
    events_before = parent.session.events

    child_process.transition(ProcessState.EXITED, exit_status="failed")

    assert parent_process.state is ProcessState.READY
    assert manager.exited_children_of(parent_process.process_id) == (child_process,)
    assert manager.child_exit_statuses(parent_process.process_id) == {
        child_process.process_id: "failed"
    }
    assert parent.session.events == events_before


def test_durable_process_creation_reconstructs_process_tree_from_jsonl(
    tmp_path,
) -> None:
    parent_session = Session(
        "session-parent",
        JsonlSessionPersistence(tmp_path / "parent.jsonl"),
    )
    child_session = Session(
        "session-child",
        JsonlSessionPersistence(tmp_path / "child.jsonl"),
    )
    registry = AgentRegistry()
    parent = registry.create_root(
        agent_id="agent-parent",
        session=parent_session,
        creation_id="create-agent-parent",
    )
    child = registry.create_child(
        parent_agent_id=parent.control.agent_id,
        agent_id="agent-child",
        session=child_session,
        creation_id="create-agent-child",
        record_session=parent_session,
    )
    manager = ProcessManager(agent_registry=registry)
    root = manager.create_process(
        process_id="process-parent-root",
        agent=parent.control,
        record_session=parent_session,
        creation_id="create-process-parent-root",
    )
    same_agent_child = manager.create_child_process(
        parent_process_id=root.process_id,
        process_id="process-same-agent-child",
        agent=parent.control,
        record_session=parent_session,
        creation_id="create-process-same-agent-child",
    )
    child_agent_process = manager.create_child_process(
        parent_process_id=root.process_id,
        process_id="process-child-agent",
        agent=child.control,
        record_session=child_session,
        creation_id="create-process-child-agent",
    )
    parent_session.close()
    child_session.close()

    restored_parent = Session.load(
        "session-parent",
        JsonlSessionPersistence(tmp_path / "parent.jsonl"),
    )
    restored_child = Session.load(
        "session-child",
        JsonlSessionPersistence(tmp_path / "child.jsonl"),
    )
    try:
        reconstructed_agents = AgentRegistry.reconstruct(
            (restored_parent, restored_child)
        )
        reconstructed_processes = ProcessManager.reconstruct(
            (restored_parent, restored_child),
            agent_registry=reconstructed_agents,
        )
    finally:
        restored_parent.close()
        restored_child.close()

    assert reconstructed_agents.parent_of(child.control.agent_id) == parent.control.agent_id
    assert reconstructed_processes.children_of(root.process_id) == (
        same_agent_child.process_id,
        child_agent_process.process_id,
    )
    assert reconstructed_processes.parent_of(child_agent_process.process_id) == (
        root.process_id
    )
    assert reconstructed_processes.root_of(child_agent_process.process_id) == (
        root.process_id
    )
    assert reconstructed_processes.get(child_agent_process.process_id).agent_id == (
        child.control.agent_id
    )
    assert reconstructed_processes.get(child_agent_process.process_id).session_id == (
        child.control.session_id
    )


def test_duplicate_process_creation_replay_is_idempotent() -> None:
    registry, parent, _child = _agent_tree()
    session = Session(parent.control.session_id)
    session.append(
        EventType.PROCESS_CREATED,
        {
            "process_id": "process-root",
            "agent_id": parent.control.agent_id,
            "session_id": parent.control.session_id,
            "parent_process_id": None,
            "creation_id": "create-process-root",
        },
    )

    reconstructed = ProcessManager.reconstruct(
        (session, session),
        agent_registry=registry,
    )

    assert reconstructed.parent_of("process-root") is None
    assert reconstructed.children_of("process-root") == ()


def test_conflicting_process_creation_facts_are_rejected() -> None:
    registry, parent, _child = _agent_tree()
    session = Session(parent.control.session_id)
    session.append(
        EventType.PROCESS_CREATED,
        {
            "process_id": "process-root",
            "agent_id": parent.control.agent_id,
            "session_id": parent.control.session_id,
            "parent_process_id": None,
            "creation_id": "create-process-root",
        },
    )
    session.append(
        EventType.PROCESS_CREATED,
        {
            "process_id": "process-root",
            "agent_id": parent.control.agent_id,
            "session_id": parent.control.session_id,
            "parent_process_id": None,
            "creation_id": "create-process-root-again",
        },
    )

    with pytest.raises(ProcessRegistryCorruptionError, match="multiple creation"):
        ProcessManager.reconstruct((session,), agent_registry=registry)


def test_missing_parent_and_cycle_durable_facts_are_rejected() -> None:
    registry, parent, _child = _agent_tree()
    missing = Session(parent.control.session_id)
    missing.append(
        EventType.PROCESS_CREATED,
        {
            "process_id": "process-child",
            "agent_id": parent.control.agent_id,
            "session_id": parent.control.session_id,
            "parent_process_id": "process-missing",
            "creation_id": "create-process-child",
        },
    )

    with pytest.raises(ProcessRegistryCorruptionError, match="missing parent"):
        ProcessManager.reconstruct((missing,), agent_registry=registry)

    cycle = Session(parent.control.session_id)
    cycle.append(
        EventType.PROCESS_CREATED,
        {
            "process_id": "process-a",
            "agent_id": parent.control.agent_id,
            "session_id": parent.control.session_id,
            "parent_process_id": "process-b",
            "creation_id": "create-process-a",
        },
    )
    cycle.append(
        EventType.PROCESS_CREATED,
        {
            "process_id": "process-b",
            "agent_id": parent.control.agent_id,
            "session_id": parent.control.session_id,
            "parent_process_id": "process-a",
            "creation_id": "create-process-b",
        },
    )

    with pytest.raises(ProcessRegistryCorruptionError, match="cycle"):
        ProcessManager.reconstruct((cycle,), agent_registry=registry)


def test_old_v0_7_process_creation_remains_compatible() -> None:
    agent = Agent.create(agent_id="agent-legacy", session=Session("session-legacy"))
    manager = ProcessManager()

    process = manager.create_process(process_id="process-legacy", agent=agent.control)

    assert process.parent_process_id is None
    assert process.state is ProcessState.READY
    assert manager.children_of(process.process_id) == ()


def test_scheduler_remains_process_scheduler_and_guards_session_writer() -> None:
    registry = AgentRegistry()
    first = registry.create_root(
        agent_id="agent-one",
        session=Session("session-one"),
        creation_id="create-agent-one",
    )
    second = registry.create_root(
        agent_id="agent-two",
        session=Session("session-two"),
        creation_id="create-agent-two",
    )
    manager = ProcessManager(agent_registry=registry)
    scheduler = CooperativeScheduler(manager)
    first_root = scheduler.create_process(
        process_id="process-one-root",
        agent=first.control,
    )
    same_session = scheduler.create_process(
        process_id="process-one-sibling",
        agent=first.control,
    )
    other_session = scheduler.create_process(
        process_id="process-two-root",
        agent=second.control,
    )

    assert scheduler.dispatch(first_root.process_id) is first_root
    with pytest.raises(ProcessSessionConflict, match="concurrent writers"):
        scheduler.dispatch(same_session.process_id)

    dispatched = scheduler.dispatch()

    assert dispatched is other_session
    assert first_root.state is ProcessState.RUNNING
    assert same_session.state is ProcessState.READY
    assert other_session.state is ProcessState.RUNNING
