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
from .process import ProcessState
from .protocol import ModelRequest, ModelResponse, ToolCall, ToolResult
from .scheduler import (
    CooperativeScheduler,
    ProcessCancelled,
    ProcessPaused,
    SchedulerSafePoint,
)
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
        scheduler: CooperativeScheduler | None = None,
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
        self._scheduler = scheduler
        self.last_context_recovery: ContextRecoveryRecord | None = None

    async def run(
        self,
        agent: Agent,
        user_input: str,
        *,
        process_id: str | None = None,
    ) -> str:
        """Run one user turn until a final model answer or kernel failure."""

        if agent.control.state is not AgentState.READY:
            raise RuntimeError(f"agent must be READY, got {agent.control.state}")
        self._dispatch_process(process_id)
        self._scheduler_safe_point(
            process_id,
            SchedulerSafePoint.BEFORE_TURN_START,
        )
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
                self._scheduler_safe_point(
                    process_id,
                    SchedulerSafePoint.BEFORE_STEP_START,
                )
                if steps >= agent.control.budget.max_steps_per_turn:
                    turn_closed = self._close_budget_failure(
                        agent=agent,
                        turn=turn,
                        step=None,
                        limit="max_steps_per_turn",
                        maximum=agent.control.budget.max_steps_per_turn,
                    )
                    self._exit_process(process_id, "budget_exceeded")
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
                    process_id=process_id,
                    wait_reason="context_working_set",
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
                    process_id=process_id,
                )
                self._scheduler_safe_point(
                    process_id,
                    SchedulerSafePoint.BEFORE_LLM_CALL,
                )
                response = await self._generate_with_overflow_recovery(
                    agent=agent,
                    request=request,
                    working_set=working_set,
                    turn=turn,
                    system_prompt=assembly.system_prompt,
                    process_id=process_id,
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
                    self._yield_process(process_id, ProcessState.READY)
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
                        self._exit_process(process_id, "budget_exceeded")
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
                        process_id=process_id,
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
        except ProcessPaused:
            if step_open:
                agent.session.append(
                    EventType.STEP_END,
                    {
                        "turn": turn,
                        "step": current_step,
                        "outcome": "paused",
                    },
                )
            if not turn_closed:
                agent.session.append(
                    EventType.TURN_END,
                    {"turn": turn, "reason": "paused"},
                )
            if agent.control.state is AgentState.WAITING:
                agent.control.transition(AgentState.RUNNING)
            if agent.control.state is AgentState.RUNNING:
                agent.control.transition(AgentState.PAUSED)
            raise
        except ProcessCancelled:
            if step_open:
                agent.session.append(
                    EventType.STEP_END,
                    {
                        "turn": turn,
                        "step": current_step,
                        "outcome": "cancelled",
                    },
                )
            if not turn_closed:
                agent.session.append(
                    EventType.TURN_END,
                    {"turn": turn, "reason": "cancelled"},
                )
            if agent.control.state is AgentState.WAITING:
                agent.control.transition(AgentState.RUNNING)
            if agent.control.state is AgentState.RUNNING:
                agent.control.transition(AgentState.EXITED)
            raise
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
            self._exit_process(process_id, f"failed:{type(error).__name__}")
            raise

    async def _preflight_request(
        self,
        *,
        agent: Agent,
        request: ModelRequest,
        working_set: ContextWorkingSet,
        turn: int,
        system_prompt: str | None,
        process_id: str | None = None,
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
            process_id=process_id,
        )

    async def _generate_with_overflow_recovery(
        self,
        *,
        agent: Agent,
        request: ModelRequest,
        working_set: ContextWorkingSet,
        turn: int,
        system_prompt: str | None,
        process_id: str | None = None,
    ) -> ModelResponse:
        """Retry exactly once, and only after a normalized provider overflow."""

        try:
            return await self._while_waiting(
                agent,
                self._llm.generate(request),
                process_id=process_id,
                wait_reason="llm",
            )
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
                process_id=process_id,
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
                    process_id=process_id,
                    wait_reason="llm",
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
        process_id: str | None = None,
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
                process_id=process_id,
                wait_reason="context_reclaim",
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
        process_id: str | None = None,
    ) -> ToolResult:
        self._scheduler_safe_point(
            process_id,
            SchedulerSafePoint.BEFORE_TOOL_CALL,
        )
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
        self._scheduler_safe_point(
            process_id,
            SchedulerSafePoint.BEFORE_DURABLE_DISPATCH,
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
            process_id=process_id,
            wait_reason="tool",
        )
        self._scheduler_safe_point(
            process_id,
            SchedulerSafePoint.AFTER_DURABLE_DISPATCH,
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

    async def _while_waiting(
        self,
        agent: Agent,
        operation: Awaitable[T],
        *,
        process_id: str | None = None,
        wait_reason: str = "operation",
    ) -> T:
        agent.control.transition(AgentState.WAITING)
        process_waiting = False
        if self._scheduler is not None and process_id is not None:
            process = self._scheduler.manager.get(process_id)
            if process.state is ProcessState.RUNNING:
                self._scheduler.yield_process(
                    process_id,
                    ProcessState.WAITING,
                    reason=wait_reason,
                )
                process_waiting = True
        try:
            return await operation
        finally:
            if agent.control.state is AgentState.WAITING:
                agent.control.transition(AgentState.RUNNING)
            if self._scheduler is not None and process_id is not None and process_waiting:
                process = self._scheduler.manager.get(process_id)
                if process.state is ProcessState.WAITING:
                    self._scheduler.wake(process_id)
                    self._scheduler.dispatch(process_id)

    def _dispatch_process(self, process_id: str | None) -> None:
        if self._scheduler is None or process_id is None:
            return
        process = self._scheduler.manager.get(process_id)
        if process.state is ProcessState.CREATED:
            self._scheduler.admit(process_id)
            process = self._scheduler.manager.get(process_id)
        if process.state is ProcessState.READY:
            self._scheduler.dispatch(process_id)
            return
        if process.state is not ProcessState.RUNNING:
            raise RuntimeError(f"process must be READY or RUNNING, got {process.state}")

    def _scheduler_safe_point(
        self,
        process_id: str | None,
        point: SchedulerSafePoint,
    ) -> None:
        if self._scheduler is None or process_id is None:
            return
        self._scheduler.safe_point(process_id, point)

    def _yield_process(self, process_id: str | None, target: ProcessState) -> None:
        if self._scheduler is None or process_id is None:
            return
        process = self._scheduler.manager.get(process_id)
        if process.state is ProcessState.RUNNING:
            self._scheduler.yield_process(process_id, target)

    def _exit_process(self, process_id: str | None, exit_status: str) -> None:
        if self._scheduler is None or process_id is None:
            return
        process = self._scheduler.manager.get(process_id)
        if process.state is not ProcessState.EXITED:
            self._scheduler.exit_process(process_id, exit_status=exit_status)

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
