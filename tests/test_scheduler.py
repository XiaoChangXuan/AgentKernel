from __future__ import annotations

import asyncio

import pytest

from agentkernel import (
    Agent,
    AgentState,
    CooperativeScheduler,
    DefaultAgentLoop,
    NoRunnableProcess,
    ProcessCancelled,
    ProcessState,
    PromptService,
    SchedulerSafePoint,
    ScriptedLLM,
    Session,
    ModelResponse,
    ToolRegistry,
)


def make_agent(session_id: str = "session-1") -> Agent:
    return Agent.create(agent_id="agent-1", session=Session(session_id))


def test_multiple_processes_are_queued_ready_fifo() -> None:
    agent = make_agent()
    scheduler = CooperativeScheduler()

    scheduler.create_process(process_id="process-1", agent=agent.control)
    scheduler.create_process(process_id="process-2", agent=agent.control)

    assert scheduler.ready_queue == ("process-1", "process-2")
    assert scheduler.schedule_next().process_id == "process-1"
    assert scheduler.ready_queue == ("process-2",)


def test_process_dispatch_transitions_ready_to_running() -> None:
    agent = make_agent()
    scheduler = CooperativeScheduler()
    process = scheduler.create_process(process_id="process-1", agent=agent.control)

    dispatched = scheduler.dispatch()

    assert dispatched is process
    assert process.state is ProcessState.RUNNING
    assert scheduler.ready_queue == ()

    scheduler.yield_process("process-1")

    assert process.state is ProcessState.READY
    assert scheduler.ready_queue == ("process-1",)


def test_waiting_process_wakes_back_to_ready() -> None:
    agent = make_agent()
    scheduler = CooperativeScheduler()
    process = scheduler.create_process(process_id="process-1", agent=agent.control)
    scheduler.dispatch("process-1")

    scheduler.yield_process("process-1", ProcessState.WAITING, reason="llm")

    assert process.state is ProcessState.WAITING
    assert scheduler.ready_queue == ()
    assert scheduler.waiting_registry == {"process-1": "llm"}

    scheduler.wake("process-1")

    assert process.state is ProcessState.READY
    assert scheduler.ready_queue == ("process-1",)
    assert scheduler.waiting_registry == {}


def test_blocked_process_unblocks_back_to_ready_after_recovery() -> None:
    agent = make_agent()
    scheduler = CooperativeScheduler()
    process = scheduler.create_process(process_id="process-1", agent=agent.control)
    scheduler.dispatch("process-1")

    scheduler.yield_process(
        "process-1",
        ProcessState.BLOCKED,
        reason="durable_recovery",
    )

    assert process.state is ProcessState.BLOCKED
    assert scheduler.blocked_registry == {"process-1": "durable_recovery"}

    scheduler.unblock("process-1")

    assert process.state is ProcessState.READY
    assert scheduler.ready_queue == ("process-1",)
    assert scheduler.blocked_registry == {}


def test_pause_resume_round_trip() -> None:
    agent = make_agent()
    scheduler = CooperativeScheduler()
    process = scheduler.create_process(process_id="process-1", agent=agent.control)

    scheduler.pause("process-1")

    assert process.state is ProcessState.PAUSED
    assert process.pause_requested is True
    assert scheduler.ready_queue == ()

    scheduler.resume("process-1")

    assert process.state is ProcessState.READY
    assert process.pause_requested is False
    assert scheduler.ready_queue == ("process-1",)


def test_cancellation_safe_point_exits_process() -> None:
    agent = make_agent()
    scheduler = CooperativeScheduler()
    process = scheduler.create_process(process_id="process-1", agent=agent.control)
    scheduler.dispatch("process-1")
    scheduler.cancel("process-1")

    with pytest.raises(ProcessCancelled) as captured:
        scheduler.safe_point("process-1", SchedulerSafePoint.BEFORE_LLM_CALL)

    assert captured.value.safe_point is SchedulerSafePoint.BEFORE_LLM_CALL
    assert process.state is ProcessState.EXITED
    assert process.exit_status == "cancelled"
    with pytest.raises(NoRunnableProcess):
        scheduler.schedule_next()


def test_exited_process_is_not_scheduled() -> None:
    agent = make_agent()
    scheduler = CooperativeScheduler()
    process = scheduler.create_process(process_id="process-1", agent=agent.control)

    scheduler.exit_process("process-1", exit_status="completed")

    assert process.state is ProcessState.EXITED
    assert scheduler.ready_queue == ()
    with pytest.raises(NoRunnableProcess):
        scheduler.schedule_next()


def test_default_loop_cancellation_safe_point_runs_before_turn_start() -> None:
    agent = make_agent()
    scheduler = CooperativeScheduler()
    process = scheduler.create_process(process_id="process-1", agent=agent.control)
    scheduler.cancel("process-1")
    loop = DefaultAgentLoop(
        llm=ScriptedLLM([ModelResponse(content="done")]),
        tools=ToolRegistry(),
        prompt=PromptService(),
        scheduler=scheduler,
    )

    with pytest.raises(ProcessCancelled) as captured:
        asyncio.run(loop.run(agent, "hello", process_id="process-1"))

    assert captured.value.safe_point is SchedulerSafePoint.BEFORE_TURN_START
    assert process.state is ProcessState.EXITED
    assert agent.control.state is AgentState.READY
    assert agent.session.events == ()


def test_default_loop_returns_process_to_ready_after_turn() -> None:
    agent = make_agent()
    scheduler = CooperativeScheduler()
    process = scheduler.create_process(process_id="process-1", agent=agent.control)
    loop = DefaultAgentLoop(
        llm=ScriptedLLM([ModelResponse(content="done")]),
        tools=ToolRegistry(),
        prompt=PromptService(),
        scheduler=scheduler,
    )

    answer = asyncio.run(loop.run(agent, "hello", process_id="process-1"))

    assert answer == "done"
    assert process.state is ProcessState.READY
    assert scheduler.ready_queue == ("process-1",)
