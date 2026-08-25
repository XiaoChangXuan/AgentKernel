"""Thin default Turn/Step/LLM/Tool orchestration loop."""

from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass
from typing import TypeVar

from .agent import Agent, AgentState
from .context import ContextBudget, ContextManager, ContextService, ContextWorkingSet
from .durable_tools import DurableToolExecutor
from .events import EventType
from .hooks import HookEvent, HookManager, HookPoint
from .llm import LLMErrorKind, LLMService, LLMServiceError
from .prompt import PromptService
from .protocol import ModelRequest, ModelResponse, ToolCall, ToolResult
from .token_accounting import (
    ApproximateRequestTokenAccounting,
    RequestTokenAccounting,
    RequestTokenEstimate,
)
from .tools import ToolRegistry

T = TypeVar("T")


class LoopBudgetExceeded(RuntimeError):
    """Raised after the kernel closes a turn that exhausted a hard budget."""

    def __init__(self, limit: str, maximum: int) -> None:
        self.limit = limit
        self.maximum = maximum
        super().__init__(f"{limit} budget exhausted at {maximum}")


class ContextOverflowRecoveryError(RuntimeError):
    """Provider overflow could not be safely recovered within one retry."""


@dataclass(frozen=True, slots=True)
class ContextRecoveryRecord:
    """Diagnostics for the latest provider-triggered reclaim in this loop."""

    before_tokens: int
    after_tokens: int
    provider_attempts: int


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
        context: ContextService | None = None,
        context_budget: ContextBudget | None = None,
        token_accounting: RequestTokenAccounting | None = None,
    ) -> None:
        self._llm = llm
        self._tools = tools
        self._prompt = prompt
        self._hooks = hooks or HookManager()
        self._tool_executor = tool_executor or DurableToolExecutor(tools)
        self._context = context or ContextManager()
        limits = llm.context_limits
        self._context_budget = context_budget or (
            ContextBudget(
                max_tokens=limits.context_window_tokens,
                reserved_output_tokens=limits.output_reserve_tokens,
            )
            if limits is not None
            else ContextBudget(
                max_tokens=128_000,
                reserved_output_tokens=16_000,
            )
        )
        self._token_accounting = (
            token_accounting
            or llm.token_accounting
            or ApproximateRequestTokenAccounting()
        )
        self.last_context_recovery: ContextRecoveryRecord | None = None

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
                working_set = await self._while_waiting(
                    agent,
                    self._context.prepare_working_set(
                        agent.session,
                        current_turn=turn,
                        budget=self._context_budget,
                        llm=self._llm,
                        system_prompt=assembly.system_prompt,
                    ),
                )
                request = ModelRequest(
                    messages=working_set.to_messages(),
                    tools=assembly.tools,
                    system_prompt=working_set.system_prompt,
                )
                request, working_set = await self._preflight_request(
                    agent=agent,
                    request=request,
                    working_set=working_set,
                    turn=turn,
                    system_prompt=assembly.system_prompt,
                )
                response = await self._generate_with_overflow_recovery(
                    agent=agent,
                    request=request,
                    working_set=working_set,
                    turn=turn,
                    system_prompt=assembly.system_prompt,
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

    async def _preflight_request(
        self,
        *,
        agent: Agent,
        request: ModelRequest,
        working_set: ContextWorkingSet,
        turn: int,
        system_prompt: str | None,
    ) -> tuple[ModelRequest, ContextWorkingSet]:
        """Use complete-request accounting before the provider sees the request."""

        estimate = self._token_accounting.estimate_request(request)
        if estimate.total_tokens <= self._context_budget.available_input_tokens:
            return request, working_set
        return await self._reclaim_request(
            agent=agent,
            request=request,
            working_set=working_set,
            turn=turn,
            system_prompt=system_prompt,
            before=estimate,
            reason="local request accounting exceeded the input budget",
        )

    async def _generate_with_overflow_recovery(
        self,
        *,
        agent: Agent,
        request: ModelRequest,
        working_set: ContextWorkingSet,
        turn: int,
        system_prompt: str | None,
    ) -> ModelResponse:
        """Retry exactly once, and only after a normalized provider overflow."""

        try:
            return await self._while_waiting(agent, self._llm.generate(request))
        except LLMServiceError as first_error:
            if first_error.kind is not LLMErrorKind.CONTEXT_OVERFLOW:
                raise
            before = self._token_accounting.estimate_request(request)
            recovered_request, _ = await self._reclaim_request(
                agent=agent,
                request=request,
                working_set=working_set,
                turn=turn,
                system_prompt=system_prompt,
                before=before,
                reason="provider reported context overflow",
            )
            after = self._token_accounting.estimate_request(recovered_request)
            self.last_context_recovery = ContextRecoveryRecord(
                before_tokens=before.total_tokens,
                after_tokens=after.total_tokens,
                provider_attempts=2,
            )
            try:
                return await self._while_waiting(
                    agent,
                    self._llm.generate(recovered_request),
                )
            except LLMServiceError as second_error:
                if second_error.kind is LLMErrorKind.CONTEXT_OVERFLOW:
                    raise ContextOverflowRecoveryError(
                        "provider context overflow persisted after one reclaimed retry"
                    ) from second_error
                raise

    async def _reclaim_request(
        self,
        *,
        agent: Agent,
        request: ModelRequest,
        working_set: ContextWorkingSet,
        turn: int,
        system_prompt: str | None,
        before: RequestTokenEstimate,
        reason: str,
    ) -> tuple[ModelRequest, ContextWorkingSet]:
        from .context import ContextBudgetExceeded
        try:
            reclaimed = await self._while_waiting(
                agent,
                self._context.force_reclaim(
                    agent.session,
                    current_turn=turn,
                    budget=self._context_budget,
                    llm=self._llm,
                    previous=working_set,
                    system_prompt=system_prompt,
                ),
            )
        except ContextBudgetExceeded as error:
            raise ContextOverflowRecoveryError(
                f"{reason}; mandatory pages prevent forced reclaim: {error}"
            ) from error
        reclaimed_request = ModelRequest(
            messages=reclaimed.to_messages(),
            tools=request.tools,
            system_prompt=reclaimed.system_prompt,
        )
        after = self._token_accounting.estimate_request(reclaimed_request)
        if after.total_tokens >= before.total_tokens:
            raise ContextOverflowRecoveryError(
                f"{reason}; forced reclaim made no measurable progress "
                f"({before.total_tokens} -> {after.total_tokens} tokens)"
            )
        return reclaimed_request, reclaimed

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
