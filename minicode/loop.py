from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from collections.abc import Iterable

from agentkernel import (
    Agent,
    AgentBudget,
    ContextBudget,
    ContextManager,
    CooperativeScheduler,
    EventType,
    JsonlSessionPersistence,
    LocalResourceStore,
    ModelUsage,
    OperationRecoveryClassification,
    ProcessBudgetExceeded,
    ProcessCancelled,
    ProcessNotFound,
    ProcessPaused,
    ProcessState,
    ResourceService,
    SchedulerSafePoint,
    Session,
    ToolCall,
    ToolRegistry,
    UsageCollector,
)
from agentkernel.protocol import JsonValue, ToolResult

from .config import ApprovalMode, MiniCodeConfig
from .durable_patch import DurableApplyPatchAdapter
from .errors import MiniCodeError
from .instructions import InstructionSource, discover_agent_instructions
from .model import (
    MiniCodeModelRequest,
    MiniCodeModelResponse,
    ModelAdapter,
    ModelAdapterError,
)
from .tools import (
    APPLY_PATCH_NAME,
    DefaultShellHostPolicy,
    ShellHostPolicy,
    minicode_capability_grants,
    register_minicode_tools,
)
from .tools.run_command import CommandRunner, ShellPolicyRequest
from .trace import TraceRecorder
from .workspace import WorkspaceIdentity, discover_workspace


class MiniCodeRunStatus(StrEnum):
    COMPLETED = "completed"
    MAX_TURNS = "max_turns"
    RECOVERY_REQUIRED = "recovery_required"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    BUDGET_EXCEEDED = "budget_exceeded"
    MODEL_ERROR = "model_error"


@dataclass(frozen=True, slots=True)
class MiniCodeRunResult:
    status: MiniCodeRunStatus
    session_id: str
    agent_id: str
    process_id: str
    turns: int
    steps: int
    final_message: str | None = None
    reason: str | None = None
    trace_events: int = 0

    @property
    def ok(self) -> bool:
        return self.status is MiniCodeRunStatus.COMPLETED

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "ok": self.ok,
            "status": self.status.value,
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "process_id": self.process_id,
            "turns": self.turns,
            "steps": self.steps,
            "final_message": self.final_message,
            "reason": self.reason,
            "trace_events": self.trace_events,
        }


@dataclass(frozen=True, slots=True)
class MiniCodeRuntime:
    workspace: WorkspaceIdentity
    session: Session
    agent: Agent
    process_id: str
    registry: ToolRegistry
    resources: ResourceService
    scheduler: CooperativeScheduler
    usage: UsageCollector
    trace: TraceRecorder


class MiniCodeAgentLoop:
    """MiniCode's small coding loop over AgentKernel runtime primitives."""

    def __init__(
        self,
        *,
        model: ModelAdapter,
        config: MiniCodeConfig | None = None,
        workspace: WorkspaceIdentity | None = None,
        session: Session | None = None,
        session_path: Path | None = None,
        agent_id: str = "minicode-agent",
        process_id: str | None = None,
        registry: ToolRegistry | None = None,
        resources: ResourceService | None = None,
        scheduler: CooperativeScheduler | None = None,
        trace: TraceRecorder | None = None,
        shell_policy: ShellHostPolicy | None = None,
        command_runner: CommandRunner | None = None,
        context_manager: ContextManager | None = None,
        context_budget: ContextBudget | None = None,
    ) -> None:
        self.model = model
        self.config = config or MiniCodeConfig()
        self.workspace = workspace or discover_workspace(
            explicit_workspace=self.config.workspace,
            task_cwd=self.config.task_cwd,
        )
        self.session_path = session_path
        self.session = session or self._new_session(session_path=session_path)
        self.agent = Agent.create(
            agent_id=agent_id,
            session=self.session,
            capability_grants=minicode_capability_grants(
                agent_id=agent_id,
                workspace=self.workspace,
            ),
            budget=AgentBudget(max_steps_per_turn=max(1, self.config.max_turns * 8)),
        )
        self.resources = resources or ResourceService(
            LocalResourceStore(self.workspace.root / ".minicode" / "resources")
        )
        self.trace = trace or TraceRecorder(jsonl_path=self.config.trace_jsonl)
        self.usage = UsageCollector()
        self.scheduler = scheduler or CooperativeScheduler(usage_collector=self.usage)
        self.process_id = process_id or f"minicode-proc-{uuid.uuid4().hex[:12]}"
        self.registry = registry or register_minicode_tools(
            ToolRegistry(),
            self.workspace,
            session=self.session,
            resources=self.resources,
            policy=shell_policy or _policy_from_approval(self.config.approve),
            runner=command_runner,
        )
        self.context_manager = context_manager or ContextManager()
        self.context_budget = context_budget or ContextBudget(max_tokens=16_000)
        self._ensure_process()

    @classmethod
    def resume(
        cls,
        *,
        model: ModelAdapter,
        session_path: Path,
        session_id: str,
        config: MiniCodeConfig | None = None,
        **kwargs: object,
    ) -> "MiniCodeAgentLoop":
        session = Session.load(session_id, JsonlSessionPersistence(session_path))
        return cls(
            model=model,
            config=config,
            session=session,
            session_path=session_path,
            **kwargs,
        )

    def run(self, task: str | None = None) -> MiniCodeRunResult:
        return asyncio.run(self.run_async(task))

    async def run_async(self, task: str | None = None) -> MiniCodeRunResult:
        recovery_result = self._stop_for_manual_recovery_if_needed()
        if recovery_result is not None:
            return recovery_result

        turn = self._next_turn()
        steps = 0
        final_message: str | None = None
        self.trace.record(
            "task/start",
            {
                "session_id": self.session.session_id,
                "agent_id": self.agent.control.agent_id,
                "process_id": self.process_id,
            },
        )

        try:
            self.scheduler.dispatch(self.process_id)
            self.scheduler.safe_point(self.process_id, SchedulerSafePoint.BEFORE_TURN_START)
            self.session.append(EventType.TURN_START, {"turn": turn})
            if task:
                self.session.append(EventType.USER_MESSAGE, {"turn": turn, "content": task})
            self.session.flush()

            for step in range(1, self.config.max_turns + 1):
                steps = step
                self.scheduler.safe_point(self.process_id, SchedulerSafePoint.BEFORE_STEP_START)
                self.session.append(EventType.STEP_START, {"turn": turn, "step": step})
                self.session.flush()

                request = self._build_model_request(turn=turn, step=step)
                self.trace.record(
                    "model/request",
                    {
                        "turn": turn,
                        "step": step,
                        "messages": len(request.messages),
                        "tools": [tool.name for tool in request.tools],
                    },
                )
                self.scheduler.safe_point(self.process_id, SchedulerSafePoint.BEFORE_LLM_CALL)
                try:
                    response = self.model.complete(request)
                except ModelAdapterError as error:
                    self.trace.record(
                        "model/error",
                        {"turn": turn, "step": step, "code": error.code},
                    )
                    return self._result(
                        MiniCodeRunStatus.MODEL_ERROR,
                        turns=1,
                        steps=steps,
                        reason=error.code,
                    )
                self._record_model_usage(response.usage, response.model_cost)
                self.scheduler.safe_point(self.process_id, SchedulerSafePoint.AFTER_LLM_CALL)
                self._append_assistant(turn, step, response)

                if not response.tool_calls:
                    final_message = response.assistant_text
                    self.session.append(EventType.STEP_END, {"turn": turn, "step": step})
                    self.session.append(
                        EventType.TURN_END,
                        {"turn": turn, "reason": "final_answer"},
                    )
                    self.session.flush()
                    self.scheduler.yield_process(
                        self.process_id,
                        ProcessState.EXITED,
                        exit_status="completed",
                    )
                    self.trace.record(
                        "task/completed",
                        {"turn": turn, "step": step, "message": final_message},
                    )
                    return self._result(
                        MiniCodeRunStatus.COMPLETED,
                        turns=1,
                        steps=steps,
                        final_message=final_message,
                    )

                for call in response.tool_calls:
                    result = await self._execute_tool(call, turn=turn, step=step)
                    result_payload = result.as_dict()
                    result_payload.update({"turn": turn, "step": step})
                    self.session.append(EventType.TOOL_RESULT, result_payload)
                    self.session.flush()
                    self.trace.record(
                        "tool/result",
                        {
                            "turn": turn,
                            "step": step,
                            "tool": result.name,
                            "ok": result.ok,
                        },
                    )
                self.session.append(EventType.STEP_END, {"turn": turn, "step": step})
                self.session.flush()

            self.session.append(EventType.TURN_END, {"turn": turn, "reason": "max_turns"})
            self.session.flush()
            self.scheduler.yield_process(
                self.process_id,
                ProcessState.EXITED,
                exit_status="max_turns",
            )
            return self._result(
                MiniCodeRunStatus.MAX_TURNS,
                turns=1,
                steps=steps,
                reason="max_turns",
            )
        except ProcessPaused as error:
            return self._result(
                MiniCodeRunStatus.PAUSED,
                turns=1,
                steps=steps,
                reason=str(error),
            )
        except ProcessCancelled as error:
            return self._result(
                MiniCodeRunStatus.CANCELLED,
                turns=1,
                steps=steps,
                reason=error.reason,
            )
        except ProcessBudgetExceeded as error:
            return self._result(
                MiniCodeRunStatus.BUDGET_EXCEEDED,
                turns=1,
                steps=steps,
                reason=str(error),
            )

    def _build_model_request(self, *, turn: int, step: int) -> MiniCodeModelRequest:
        instructions = discover_agent_instructions(self.workspace)
        system_prompt = build_system_prompt(self.workspace, instructions)
        working_set = self.context_manager.build_working_set(
            self.session,
            current_turn=turn,
            budget=self.context_budget,
            system_prompt=system_prompt,
        )
        schemas = self.registry.model_schemas(self.agent.control)
        return MiniCodeModelRequest(
            messages=working_set.to_messages(),
            tools=schemas,
            system_prompt=system_prompt,
            metadata={
                "turn": turn,
                "step": step,
                "workspace_id": self.workspace.workspace_id,
            },
        )

    async def _execute_tool(self, call: ToolCall, *, turn: int, step: int) -> ToolResult:
        self.trace.record(
            "tool/call",
            {"turn": turn, "step": step, "tool": call.name, "call_id": call.call_id},
        )
        self.session.append(EventType.TOOL_CALL, {"turn": turn, "step": step, **call.as_dict()})
        self.session.flush()
        self.scheduler.safe_point(self.process_id, SchedulerSafePoint.BEFORE_TOOL_CALL)
        if call.name == APPLY_PATCH_NAME:
            self.scheduler.safe_point(
                self.process_id,
                SchedulerSafePoint.BEFORE_DURABLE_DISPATCH,
            )
            result = await DurableApplyPatchAdapter(self.registry).execute(
                self.workspace,
                call,
                self.agent.control,
                self.session,
                turn=turn,
                step=step,
            )
            self.scheduler.safe_point(
                self.process_id,
                SchedulerSafePoint.AFTER_DURABLE_DISPATCH,
            )
        else:
            result = await self.registry.execute(call, self.agent.control)
        self.usage.record_tool_call(self.process_id)
        self.usage.observe_resource_metrics(
            self.process_id,
            self.resources.metrics.snapshot(),
        )
        self.scheduler.safe_point(self.process_id, SchedulerSafePoint.AFTER_TOOL_CALL)
        return result

    def _append_assistant(
        self,
        turn: int,
        step: int,
        response: MiniCodeModelResponse,
    ) -> None:
        self.session.append(
            EventType.ASSISTANT_MESSAGE,
            {
                "turn": turn,
                "step": step,
                "content": response.assistant_text,
                "tool_calls": [call.as_dict() for call in response.tool_calls],
            },
        )
        self.session.flush()
        self.trace.record(
            "model/response",
            {
                "turn": turn,
                "step": step,
                "finish_reason": response.finish_reason,
                "tool_calls": [call.name for call in response.tool_calls],
                "content_chars": len(response.assistant_text),
            },
        )

    def _record_model_usage(self, usage: ModelUsage | None, model_cost: float) -> None:
        self.usage.record_llm_usage(
            self.process_id,
            usage,
            model_cost=model_cost,
        )

    def _ensure_process(self) -> None:
        try:
            self.scheduler.manager.get(self.process_id)
        except ProcessNotFound:
            self.scheduler.create_process(
                process_id=self.process_id,
                agent=self.agent.control,
            )
        self.usage.start_process(self.process_id)
        self.usage.begin_resource_metrics(
            self.process_id,
            self.resources.metrics.snapshot(),
        )

    def _stop_for_manual_recovery_if_needed(self) -> MiniCodeRunResult | None:
        blocking = [
            operation
            for operation in self.session.recovery_analysis.durable_operations
            if operation.classification
            in {
                OperationRecoveryClassification.RECONCILE_REQUIRED,
                OperationRecoveryClassification.MANUAL_REQUIRED,
            }
        ]
        if not blocking:
            return None
        reason = ",".join(operation.operation_id for operation in blocking)
        self.trace.record("recovery/required", {"operation_ids": [operation.operation_id for operation in blocking]})
        return self._result(
            MiniCodeRunStatus.RECOVERY_REQUIRED,
            turns=0,
            steps=0,
            reason=reason,
        )

    def _next_turn(self) -> int:
        turns = [
            int(event.data["turn"])
            for event in self.session.events
            if event.type is EventType.TURN_END and isinstance(event.data.get("turn"), int)
        ]
        return (max(turns) + 1) if turns else 1

    def _new_session(self, *, session_path: Path | None) -> Session:
        session_id = f"minicode-session-{uuid.uuid4().hex[:12]}"
        if session_path is None:
            return Session(session_id)
        return Session(session_id, JsonlSessionPersistence(session_path))

    def _result(
        self,
        status: MiniCodeRunStatus,
        *,
        turns: int,
        steps: int,
        final_message: str | None = None,
        reason: str | None = None,
    ) -> MiniCodeRunResult:
        return MiniCodeRunResult(
            status=status,
            session_id=self.session.session_id,
            agent_id=self.agent.control.agent_id,
            process_id=self.process_id,
            turns=turns,
            steps=steps,
            final_message=final_message,
            reason=reason,
            trace_events=len(self.trace.events),
        )


def build_system_prompt(
    workspace: WorkspaceIdentity,
    instructions: Iterable[InstructionSource],
) -> str:
    parts = [
        "You are MiniCode, a small coding agent running inside AgentKernel.",
        "Use only the provided tools. apply_patch is the durable filesystem mutation path.",
        "run_command results, including nonzero exit codes, are observations to inspect.",
        f"Workspace root: {workspace.root}",
        f"Workspace id: {workspace.workspace_id}",
    ]
    for source in instructions:
        parts.append(f"AGENTS.md ({source.relative_path}):\n{source.content}")
    return "\n\n".join(parts)


def _policy_from_approval(approval: ApprovalMode) -> DefaultShellHostPolicy:
    if approval == "always":
        return DefaultShellHostPolicy(confirm_mutation=lambda _request: True)
    if approval == "never":
        return DefaultShellHostPolicy(confirm_mutation=lambda _request: False)

    def confirm(request: ShellPolicyRequest) -> bool:
        return False

    return DefaultShellHostPolicy(confirm_mutation=confirm)
