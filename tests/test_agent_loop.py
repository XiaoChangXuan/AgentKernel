from __future__ import annotations

import asyncio
import json
from typing import Mapping

import pytest

from agentkernel import (
    Agent,
    AgentBudget,
    AgentState,
    DefaultAgentLoop,
    EventType,
    HookManager,
    HookPoint,
    LoopBudgetExceeded,
    MessageRole,
    ModelRequest,
    ModelResponse,
    PromptService,
    ScriptedLLM,
    Session,
    ToolCall,
    ToolDefinition,
    ToolExecutionContext,
    ToolRegistry,
    ToolSchema,
)
from agentkernel.protocol import JsonValue


async def add(
    arguments: Mapping[str, JsonValue],
    _context: ToolExecutionContext,
) -> JsonValue:
    return int(arguments["left"]) + int(arguments["right"])


def build_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            schema=ToolSchema(
                "math.add",
                "Add two integers.",
                {"type": "object"},
            ),
            handler=add,
            required_capability="math.add",
        )
    )
    return registry


def test_complete_user_tool_result_answer_flow() -> None:
    session = Session("session-1")
    agent = Agent.create(
        agent_id="agent-1",
        session=session,
        capabilities={"math.add"},
        capability_bounding_set={"math.add"},
    )
    first = ModelResponse(
        tool_calls=(
            ToolCall("call-1", "math.add", {"left": 20, "right": 22}),
        )
    )

    def finish(request: ModelRequest) -> ModelResponse:
        assert request.messages[-1].role is MessageRole.TOOL
        assert json.loads(request.messages[-1].content)["output"] == 42
        return ModelResponse(content="The result is 42.")

    llm = ScriptedLLM([first, finish])
    hooks = HookManager()
    notifications: list[HookPoint] = []
    for point in HookPoint:
        hooks.subscribe(point, lambda event, points=notifications: points.append(event.point))
    loop = DefaultAgentLoop(
        llm=llm,
        tools=build_registry(),
        prompt=PromptService("Use the available tools."),
        hooks=hooks,
    )

    answer = asyncio.run(loop.run(agent, "What is 20 + 22?"))

    assert answer == "The result is 42."
    assert agent.control.state is AgentState.READY
    assert len(llm.requests) == 2
    assert [event.type for event in session.events] == [
        EventType.TURN_START,
        EventType.USER_MESSAGE,
        EventType.STEP_START,
        EventType.ASSISTANT_MESSAGE,
        EventType.TOOL_CALL,
        EventType.TOOL_RESULT,
        EventType.STEP_END,
        EventType.STEP_START,
        EventType.ASSISTANT_MESSAGE,
        EventType.STEP_END,
        EventType.TURN_END,
    ]
    assert notifications == [
        HookPoint.BEFORE_STEP,
        HookPoint.BEFORE_TOOL,
        HookPoint.AFTER_TOOL,
        HookPoint.BEFORE_STEP,
    ]


def repeated_calls(count: int) -> list[ModelResponse]:
    return [
        ModelResponse(
            tool_calls=(
                ToolCall(
                    call_id=f"call-{index}",
                    name="math.add",
                    arguments={"left": index, "right": 1},
                ),
            )
        )
        for index in range(count)
    ]


def test_step_budget_terminates_a_continuing_tool_loop() -> None:
    session = Session("session-1")
    agent = Agent.create(
        agent_id="agent-1",
        session=session,
        capabilities={"math.add"},
        budget=AgentBudget(max_steps_per_turn=2, max_tool_calls_per_turn=10),
    )
    loop = DefaultAgentLoop(
        llm=ScriptedLLM(repeated_calls(3)),
        tools=build_registry(),
        prompt=PromptService(),
    )

    with pytest.raises(LoopBudgetExceeded) as captured:
        asyncio.run(loop.run(agent, "Keep adding."))

    assert captured.value.limit == "max_steps_per_turn"
    assert agent.control.state is AgentState.FAILED
    assert session.events[-1].type is EventType.TURN_END
    assert session.events[-1].data["reason"] == "budget_exceeded"
    assert session.events[-1].data["limit"] == "max_steps_per_turn"
    assert sum(event.type is EventType.TOOL_RESULT for event in session.events) == 2


def test_tool_call_budget_stops_before_an_excess_call_executes() -> None:
    session = Session("session-1")
    agent = Agent.create(
        agent_id="agent-1",
        session=session,
        capabilities={"math.add"},
        budget=AgentBudget(max_steps_per_turn=4, max_tool_calls_per_turn=1),
    )
    loop = DefaultAgentLoop(
        llm=ScriptedLLM(repeated_calls(2)),
        tools=build_registry(),
        prompt=PromptService(),
    )

    with pytest.raises(LoopBudgetExceeded) as captured:
        asyncio.run(loop.run(agent, "Keep adding."))

    assert captured.value.limit == "max_tool_calls_per_turn"
    assert sum(event.type is EventType.TOOL_CALL for event in session.events) == 1
    assert sum(event.type is EventType.TOOL_RESULT for event in session.events) == 1
    assert session.events[-1].data["limit"] == "max_tool_calls_per_turn"

