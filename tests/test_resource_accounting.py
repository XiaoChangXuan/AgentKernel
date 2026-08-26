from __future__ import annotations

import asyncio

import pytest

from agentkernel import (
    Agent,
    AgentBudget,
    AgentState,
    CooperativeScheduler,
    DefaultAgentLoop,
    EventType,
    LocalResourceStore,
    ModelResponse,
    ModelUsage,
    ProcessBudgetExceeded,
    ProcessState,
    PromptService,
    ResourceMetrics,
    ResourceOwner,
    ResourceService,
    SchedulerSafePoint,
    ScriptedLLM,
    Session,
    ToolRegistry,
    UsageCollector,
)


def make_agent(*, budget: AgentBudget | None = None) -> Agent:
    return Agent.create(
        agent_id="agent-1",
        session=Session("session-1"),
        budget=budget,
    )


def test_usage_accumulation_from_llm_tool_and_resource_metrics(tmp_path) -> None:
    clock = {"now": 0.0}
    metrics = ResourceMetrics()
    collector = UsageCollector(clock=lambda: clock["now"])
    service = ResourceService(
        LocalResourceStore(tmp_path / "resources"),
        metrics=metrics,
        resource_id_factory=lambda: "res_usage",
        handle_id_factory=lambda: "hdl_usage",
    )
    owner = ResourceOwner("agent-1", "session-1")
    handle = service.create_artifact(
        b"abcdef",
        owner=owner,
        media_type="text/plain",
        encoding="utf-8",
        source_tool_name="logs",
        source_tool_call_id="call-1",
        source_operation_id="op-1",
    )

    collector.start_process("process-1")
    collector.record_llm_usage(
        "process-1",
        ModelUsage(input_tokens=10, output_tokens=5, total_tokens=15),
        model_cost=0.25,
    )
    collector.record_tool_call("process-1")
    collector.begin_resource_metrics("process-1", metrics.snapshot())

    service.read(handle.uri, owner=owner, offset=1, limit=4)
    collector.observe_resource_metrics("process-1", metrics.snapshot())
    clock["now"] = 2.5

    snapshot = collector.snapshot("process-1")

    assert snapshot.token_usage == 15
    assert snapshot.model_cost == 0.25
    assert snapshot.tool_calls == 1
    assert snapshot.resource_reads == 1
    assert snapshot.resource_bytes == 4
    assert snapshot.wall_time == 2.5


def test_budget_exceeded_reports_first_limit() -> None:
    collector = UsageCollector()
    collector.record_llm_usage(
        "process-1",
        ModelUsage(input_tokens=6, output_tokens=5, total_tokens=11),
    )

    exceeded = collector.exceeded_budget(
        "process-1",
        AgentBudget(max_token_usage=10),
    )

    assert exceeded is not None
    assert exceeded.limit == "max_token_usage"
    assert exceeded.usage == 11
    assert exceeded.maximum == 10


def test_scheduler_blocks_running_process_when_budget_is_exceeded() -> None:
    agent = make_agent(budget=AgentBudget(max_token_usage=10))
    collector = UsageCollector()
    scheduler = CooperativeScheduler(usage_collector=collector)
    process = scheduler.create_process(process_id="process-1", agent=agent.control)
    scheduler.dispatch("process-1")
    collector.record_llm_usage(
        "process-1",
        ModelUsage(input_tokens=7, output_tokens=4, total_tokens=11),
    )

    with pytest.raises(ProcessBudgetExceeded) as captured:
        scheduler.safe_point("process-1", SchedulerSafePoint.AFTER_LLM_CALL)

    assert captured.value.exceeded.limit == "max_token_usage"
    assert process.state is ProcessState.BLOCKED
    assert process.blocked_reason == "budget_exceeded:max_token_usage"
    assert scheduler.blocked_registry == {
        "process-1": "budget_exceeded:max_token_usage"
    }


def test_default_loop_blocks_process_after_provider_usage_exceeds_budget() -> None:
    agent = make_agent(budget=AgentBudget(max_token_usage=5))
    collector = UsageCollector()
    scheduler = CooperativeScheduler(usage_collector=collector)
    process = scheduler.create_process(process_id="process-1", agent=agent.control)
    loop = DefaultAgentLoop(
        llm=ScriptedLLM(
            [
                ModelResponse(
                    content="too much",
                    usage=ModelUsage(input_tokens=4, output_tokens=3, total_tokens=7),
                )
            ]
        ),
        tools=ToolRegistry(),
        prompt=PromptService(),
        scheduler=scheduler,
    )

    with pytest.raises(ProcessBudgetExceeded) as captured:
        asyncio.run(loop.run(agent, "hello", process_id="process-1"))

    assert captured.value.exceeded.limit == "max_token_usage"
    assert process.state is ProcessState.BLOCKED
    assert agent.control.state is AgentState.PAUSED
    assert collector.snapshot("process-1").token_usage == 7
    assert [event.type for event in agent.session.events] == [
        EventType.TURN_START,
        EventType.USER_MESSAGE,
        EventType.STEP_START,
        EventType.ASSISTANT_MESSAGE,
        EventType.STEP_END,
        EventType.TURN_END,
    ]
    assert agent.session.events[-1].data["reason"] == "resource_budget_exceeded"


def test_host_can_resume_after_budget_pause_by_resetting_usage() -> None:
    agent = make_agent(budget=AgentBudget(max_token_usage=5))
    collector = UsageCollector()
    scheduler = CooperativeScheduler(usage_collector=collector)
    process = scheduler.create_process(process_id="process-1", agent=agent.control)
    first_loop = DefaultAgentLoop(
        llm=ScriptedLLM(
            [
                ModelResponse(
                    content="blocked",
                    usage=ModelUsage(input_tokens=5, output_tokens=1, total_tokens=6),
                )
            ]
        ),
        tools=ToolRegistry(),
        prompt=PromptService(),
        scheduler=scheduler,
    )

    with pytest.raises(ProcessBudgetExceeded):
        asyncio.run(first_loop.run(agent, "hello", process_id="process-1"))

    assert process.state is ProcessState.BLOCKED
    assert agent.control.state is AgentState.PAUSED

    collector.reset_process("process-1")
    scheduler.unblock("process-1")
    agent.control.transition(AgentState.READY)
    second_loop = DefaultAgentLoop(
        llm=ScriptedLLM(
            [
                ModelResponse(
                    content="resumed",
                    usage=ModelUsage(input_tokens=1, output_tokens=1, total_tokens=2),
                )
            ]
        ),
        tools=ToolRegistry(),
        prompt=PromptService(),
        scheduler=scheduler,
    )

    answer = asyncio.run(second_loop.run(agent, "continue", process_id="process-1"))

    assert answer == "resumed"
    assert process.state is ProcessState.READY
    assert agent.control.state is AgentState.READY
    assert collector.snapshot("process-1").token_usage == 2
