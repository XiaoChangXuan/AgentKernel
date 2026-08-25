from __future__ import annotations

import pytest

from agentkernel import (
    Agent,
    CapabilityGrant,
    CapabilitySnapshot,
    EventType,
    ProcessControlBlock,
    ProcessState,
    Session,
    SessionEvent,
    ToolCall,
    analyze_recovery,
)
from agentkernel.tool_effects import ToolEffectKind


def event(seq: int, event_type: EventType, data: dict) -> SessionEvent:
    return SessionEvent(seq=seq, type=event_type, data=data, time=float(seq))


def test_process_creation_from_agent_control_block() -> None:
    grant = CapabilityGrant(
        subject="agent-1",
        action="resource.read",
        resource_scope="artifact://project-a/**",
    )
    agent = Agent.create(
        agent_id="agent-1",
        session=Session("session-1"),
        capabilities={"resource.read"},
        capability_bounding_set={"resource.read"},
        capability_grants=(grant,),
    )

    process = ProcessControlBlock.create(
        process_id="process-1",
        agent=agent.control,
        priority=10,
    )

    assert process.process_id == "process-1"
    assert process.agent_id == "agent-1"
    assert process.session_id == "session-1"
    assert process.state is ProcessState.CREATED
    assert process.priority == 10
    assert process.budget is agent.control.budget
    assert process.capability_snapshot.agent_id == agent.control.agent_id
    assert process.capability_snapshot.capability_grants == (grant,)


def test_process_lifecycle_transition_validation() -> None:
    agent = Agent.create(agent_id="agent-1", session=Session("session-1"))
    process = ProcessControlBlock.create(
        process_id="process-1",
        agent=agent.control,
    )

    process.transition(ProcessState.READY)
    process.transition(ProcessState.RUNNING)
    process.transition(ProcessState.WAITING, wait_reason="llm")

    assert process.state is ProcessState.WAITING
    assert process.wait_reason == "llm"

    process.transition(ProcessState.READY)
    process.transition(ProcessState.RUNNING)
    process.transition(ProcessState.BLOCKED, blocked_reason="durable_reconcile")

    assert process.state is ProcessState.BLOCKED
    assert process.wait_reason is None
    assert process.blocked_reason == "durable_reconcile"

    process.transition(ProcessState.EXITED, exit_status="cancelled")

    assert process.state is ProcessState.EXITED
    assert process.blocked_reason is None
    assert process.exit_status == "cancelled"


def test_invalid_process_transition_is_rejected() -> None:
    agent = Agent.create(agent_id="agent-1", session=Session("session-1"))
    process = ProcessControlBlock.create(
        process_id="process-1",
        agent=agent.control,
    )

    with pytest.raises(RuntimeError, match="invalid process transition"):
        process.transition(ProcessState.RUNNING)

    process.transition(ProcessState.READY)
    process.transition(ProcessState.RUNNING)
    with pytest.raises(ValueError, match="wait_reason"):
        process.transition(ProcessState.WAITING)

    with pytest.raises(ValueError, match="capability_snapshot is required"):
        ProcessControlBlock(
            process_id="process-2",
            agent_id="agent-1",
            session_id="session-1",
            state=ProcessState.CREATED,
        )


def test_recovery_analysis_creates_process_state() -> None:
    agent = Agent.create(agent_id="agent-1", session=Session("session-1"))
    interrupted = analyze_recovery(
        (
            event(1, EventType.TURN_START, {"turn": 1}),
            event(2, EventType.USER_MESSAGE, {"turn": 1, "content": "Hello"}),
        )
    )
    recovered = ProcessControlBlock.from_recovery(
        process_id="process-1",
        agent=agent.control,
        recovery=interrupted,
    )

    assert recovered.state is ProcessState.READY
    assert recovered.exit_status is None
    assert recovered.blocked_reason is None

    completed = analyze_recovery(
        (
            event(1, EventType.TURN_START, {"turn": 1}),
            event(2, EventType.USER_MESSAGE, {"turn": 1, "content": "Hello"}),
            event(3, EventType.STEP_START, {"turn": 1, "step": 1}),
            event(
                4,
                EventType.ASSISTANT_MESSAGE,
                {"turn": 1, "step": 1, "content": "Done", "tool_calls": []},
            ),
            event(5, EventType.STEP_END, {"turn": 1, "step": 1}),
            event(6, EventType.TURN_END, {"turn": 1, "reason": "completed"}),
        )
    )
    exited = ProcessControlBlock.from_recovery(
        process_id="process-2",
        agent=agent.control,
        recovery=completed,
    )

    assert exited.state is ProcessState.EXITED
    assert exited.exit_status == "completed"


def test_recovery_blocks_dispatched_reconcilable_operation() -> None:
    agent = Agent.create(agent_id="agent-1", session=Session("session-1"))
    call = ToolCall("call-1", "payment.charge", {"amount": 10})
    analysis = analyze_recovery(
        (
            event(1, EventType.TURN_START, {"turn": 1}),
            event(2, EventType.USER_MESSAGE, {"turn": 1, "content": "Pay"}),
            event(3, EventType.STEP_START, {"turn": 1, "step": 1}),
            event(
                4,
                EventType.ASSISTANT_MESSAGE,
                {
                    "turn": 1,
                    "step": 1,
                    "content": "",
                    "tool_calls": [call.as_dict()],
                },
            ),
            event(5, EventType.TOOL_CALL, {"turn": 1, "step": 1, **call.as_dict()}),
            event(
                6,
                EventType.TOOL_PREPARE,
                {
                    "turn": 1,
                    "step": 1,
                    "operation_id": "op-1",
                    "tool_call_id": call.call_id,
                    "tool_name": call.name,
                    "effect_kind": ToolEffectKind.RECONCILABLE_MUTATION,
                },
            ),
            event(
                7,
                EventType.TOOL_DISPATCH,
                {"turn": 1, "step": 1, "operation_id": "op-1", "attempt": 1},
            ),
        )
    )

    process = ProcessControlBlock.from_recovery(
        process_id="process-1",
        agent=agent.control,
        recovery=analysis,
    )

    assert process.state is ProcessState.BLOCKED
    assert process.blocked_reason == "durable_operation_recovery"


def test_capability_remains_agent_owned() -> None:
    grant = CapabilityGrant(
        subject="agent-1",
        action="tool.execute",
        resource_scope="tool://search",
    )
    agent = Agent.create(
        agent_id="agent-1",
        session=Session("session-1"),
        capabilities={"search"},
        capability_grants=(grant,),
    )

    process = ProcessControlBlock.create(
        process_id="process-1",
        agent=agent.control,
    )

    assert process.capability_snapshot.agent_id == agent.control.agent_id
    assert process.capability_snapshot.capability_grants[0].subject == "agent-1"
    assert process.capability_snapshot.capability_grants[0].subject != process.process_id

    with pytest.raises(ValueError, match="capability snapshot must match agent_id"):
        ProcessControlBlock(
            process_id="process-2",
            agent_id="agent-1",
            session_id="session-1",
            state=ProcessState.CREATED,
            capability_snapshot=CapabilitySnapshot(
                agent_id="other-agent",
                capabilities=frozenset(),
                capability_bounding_set=frozenset(),
            ),
        )
