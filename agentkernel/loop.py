"""Thin default Turn/Step/LLM/Tool orchestration loop."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import TypeVar

from .agent import Agent, AgentState
from .durable_tools import DurableToolExecutor
from .events import EventType
from .hooks import HookEvent, HookManager, HookPoint
from .llm import LLMService
from .prompt import PromptService
from .protocol import ModelRequest, ToolCall, ToolResult
from .tools import ToolRegistry

T = TypeVar("T")


class LoopBudgetExceeded(RuntimeError):
    """Raised after the kernel closes a turn that exhausted a hard budget."""

    def __init__(self, limit: str, maximum: int) -> None:
        self.limit = limit
        self.maximum = maximum
        super().__init__(f"{limit} budget exhausted at {maximum}")


class DefaultAgentLoop:
    """Drive model steps and sequential tool execution for one turn."""

    def __init__(
        self,
        *,
        llm: LLMService,
        tools: ToolRegistry,
        prompt: PromptService,
        hooks: HookManager | None = None,
        tool_executor: DurableToolExecutor | None = None,
    ) -> None:
        self._llm = llm
        self._tools = tools
        self._prompt = prompt
        self._hooks = hooks or HookManager()
        self._tool_executor = tool_executor or DurableToolExecutor(tools)

    async def run(self, agent: Agent, user_input: str) -> str:
        """Run one user turn until a final model answer or kernel failure."""

        if agent.control.state is not AgentState.READY:
            raise RuntimeError(f"agent must be READY, got {agent.control.state}")
        turn = 1 + sum(
            event.type is EventType.TURN_START for event in agent.session.events
        )
        agent.control.transition(AgentState.RUNNING)
        agent.session.append(EventType.TURN_START, {"turn": turn})
        agent.session.append(
            EventType.USER_MESSAGE,
            {"turn": turn, "content": user_input},
        )

        steps = 0
        tool_calls = 0
        step_open = False
        turn_closed = False
        current_step = 0
        try:
            while True:
                if steps >= agent.control.budget.max_steps_per_turn:
                    turn_closed = self._close_budget_failure(
                        agent=agent,
                        turn=turn,
                        step=None,
                        limit="max_steps_per_turn",
                        maximum=agent.control.budget.max_steps_per_turn,
                    )
                    raise LoopBudgetExceeded(
                        "max_steps_per_turn",
                        agent.control.budget.max_steps_per_turn,
                    )

                steps += 1
                current_step = steps
                agent.session.append(
                    EventType.STEP_START,
                    {"turn": turn, "step": current_step},
                )
                step_open = True
                await self._hooks.notify(
                    HookEvent(
                        point=HookPoint.BEFORE_STEP,
                        agent_id=agent.control.agent_id,
                        turn=turn,
                        step=current_step,
                    )
                )

                assembly = self._prompt.assemble(agent.control, self._tools)
                request = ModelRequest(
                    messages=agent.session.derive_messages(),
                    tools=assembly.tools,
                    system_prompt=assembly.system_prompt,
                )
                response = await self._while_waiting(
                    agent,
                    self._llm.generate(request),
                )
                agent.session.append(
                    EventType.ASSISTANT_MESSAGE,
                    {
                        "turn": turn,
                        "step": current_step,
                        "content": response.content,
                        "tool_calls": [call.as_dict() for call in response.tool_calls],
                    },
                )

                if not response.tool_calls:
                    agent.session.append(
                        EventType.STEP_END,
                        {
                            "turn": turn,
                            "step": current_step,
                            "outcome": "completed",
                        },
                    )
                    step_open = False
                    agent.session.append(
                        EventType.TURN_END,
                        {"turn": turn, "reason": "completed"},
                    )
                    turn_closed = True
                    agent.control.transition(AgentState.READY)
                    return response.content

                for call in response.tool_calls:
                    if tool_calls >= agent.control.budget.max_tool_calls_per_turn:
                        turn_closed = self._close_budget_failure(
                            agent=agent,
                            turn=turn,
                            step=current_step,
                            limit="max_tool_calls_per_turn",
                            maximum=agent.control.budget.max_tool_calls_per_turn,
                        )
                        step_open = False
                        raise LoopBudgetExceeded(
                            "max_tool_calls_per_turn",
                            agent.control.budget.max_tool_calls_per_turn,
                        )
                    tool_calls += 1
                    await self._execute_tool(
                        agent=agent,
                        call=call,
                        turn=turn,
                        step=current_step,
                    )

                agent.session.append(
                    EventType.STEP_END,
                    {
                        "turn": turn,
                        "step": current_step,
                        "outcome": "tool_calls",
                    },
                )
                step_open = False
        except LoopBudgetExceeded:
            raise
        except BaseException as error:
            pending_tool_calls = agent.session.recovery_analysis.pending_tool_calls
            if step_open and not pending_tool_calls:
                agent.session.append(
                    EventType.STEP_END,
                    {
                        "turn": turn,
                        "step": current_step,
                        "outcome": "error",
                    },
                )
            if not turn_closed and not pending_tool_calls:
                agent.session.append(
                    EventType.TURN_END,
                    {
                        "turn": turn,
                        "reason": "error",
                        "error_type": type(error).__name__,
                    },
                )
            if agent.control.state is AgentState.WAITING:
                agent.control.transition(AgentState.RUNNING)
            if agent.control.state is AgentState.RUNNING:
                agent.control.transition(AgentState.FAILED)
            raise

    async def _execute_tool(
        self,
        *,
        agent: Agent,
        call: ToolCall,
        turn: int,
        step: int,
    ) -> ToolResult:
        agent.session.append(
            EventType.TOOL_CALL,
            {"turn": turn, "step": step, **call.as_dict()},
        )
        await self._hooks.notify(
            HookEvent(
                point=HookPoint.BEFORE_TOOL,
                agent_id=agent.control.agent_id,
                turn=turn,
                step=step,
                tool_call=call,
            )
        )
        result = await self._while_waiting(
            agent,
            self._tool_executor.execute(
                call,
                agent.control,
                agent.session,
                turn=turn,
                step=step,
            ),
        )
        agent.session.append(
            EventType.TOOL_RESULT,
            {"turn": turn, "step": step, **result.as_dict()},
        )
        await self._hooks.notify(
            HookEvent(
                point=HookPoint.AFTER_TOOL,
                agent_id=agent.control.agent_id,
                turn=turn,
                step=step,
                tool_call=call,
                tool_result=result,
            )
        )
        return result

    async def _while_waiting(self, agent: Agent, operation: Awaitable[T]) -> T:
        agent.control.transition(AgentState.WAITING)
        try:
            return await operation
        finally:
            if agent.control.state is AgentState.WAITING:
                agent.control.transition(AgentState.RUNNING)

    @staticmethod
    def _close_budget_failure(
        *,
        agent: Agent,
        turn: int,
        step: int | None,
        limit: str,
        maximum: int,
    ) -> bool:
        if step is not None:
            agent.session.append(
                EventType.STEP_END,
                {
                    "turn": turn,
                    "step": step,
                    "outcome": "budget_exceeded",
                },
            )
        agent.session.append(
            EventType.TURN_END,
            {
                "turn": turn,
                "reason": "budget_exceeded",
                "limit": limit,
                "maximum": maximum,
            },
        )
        agent.control.transition(AgentState.FAILED)
        return True
