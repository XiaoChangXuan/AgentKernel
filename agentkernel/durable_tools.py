"""Durable single-operation Tool execution protocol for V0.3."""

from __future__ import annotations

import uuid
from collections.abc import Callable

from .agent import AgentControlBlock
from .events import EventType
from .protocol import ErrorCode, JsonValue, ToolCall, ToolResult
from .recovery import DurableOperationRecovery, OperationRecoveryClassification
from .session import Session
from .tools import (
    ReconcileResult,
    ReconcileStatus,
    ToolDefinition,
    ToolEffectKind,
    ToolExecutionContext,
    ToolRegistry,
    ToolResultProcessor,
)


class DurableToolExecutionError(RuntimeError):
    """The durable execution mechanism was invoked with inconsistent state."""


OperationIdFactory = Callable[[], str]


class DurableToolExecutor:
    """Authorize, journal, dispatch, and classify one Tool operation."""

    def __init__(
        self,
        tools: ToolRegistry,
        *,
        operation_id_factory: OperationIdFactory | None = None,
        result_processor: ToolResultProcessor | None = None,
    ) -> None:
        self._tools = tools
        self._operation_id_factory = operation_id_factory or _new_operation_id
        self._result_processor = result_processor

    async def execute(
        self,
        call: ToolCall,
        agent: AgentControlBlock,
        session: Session,
        *,
        turn: int,
        step: int,
    ) -> ToolResult:
        """Execute a live Tool Call, durably preparing mutations first."""

        resolved = self._tools.resolve_for_execution(call, agent)
        if isinstance(resolved, ToolResult):
            return resolved
        if resolved.effect_kind is ToolEffectKind.READ_ONLY:
            context = ToolExecutionContext(
                agent_id=agent.agent_id,
                session_id=agent.session_id,
                tool_call_id=call.call_id,
                operation_id=self._fresh_operation_id(session),
            )
            result = await self._tools.invoke(resolved, call, context)
            return await self._process_result(call, result, context)

        operation_id = self._fresh_operation_id(session)
        session.append(
            EventType.TOOL_PREPARE,
            {
                "turn": turn,
                "step": step,
                "operation_id": operation_id,
                "tool_call_id": call.call_id,
                "tool_name": call.name,
                "effect_kind": resolved.effect_kind.value,
            },
        )
        session.flush()
        return await self._dispatch(
            definition=resolved,
            call=call,
            agent=agent,
            session=session,
            turn=turn,
            step=step,
            operation_id=operation_id,
            attempt=1,
        )

    async def retry(
        self,
        operation: DurableOperationRecovery,
        agent: AgentControlBlock,
        session: Session,
    ) -> ToolResult:
        """Explicitly retry one operation whose analysis permits retry."""

        operation = self._current_operation(operation, session)
        if operation.classification not in {
            OperationRecoveryClassification.SAFE_TO_RETRY,
            OperationRecoveryClassification.IDEMPOTENT_RETRY_ALLOWED,
        }:
            raise DurableToolExecutionError(
                f"operation {operation.operation_id} is not retryable: "
                f"{operation.classification.value}"
            )
        resolved = self._resolve_matching(operation, agent)
        if (
            operation.classification
            is OperationRecoveryClassification.IDEMPOTENT_RETRY_ALLOWED
            and resolved.effect_kind is not ToolEffectKind.IDEMPOTENT_MUTATION
        ):
            raise DurableToolExecutionError(
                "idempotent retry classification requires IDEMPOTENT_MUTATION"
            )
        return await self._dispatch(
            definition=resolved,
            call=operation.tool_call,
            agent=agent,
            session=session,
            turn=operation.turn,
            step=operation.step,
            operation_id=operation.operation_id,
            attempt=operation.dispatch_attempts + 1,
        )

    async def reconcile(
        self,
        operation: DurableOperationRecovery,
        agent: AgentControlBlock,
        session: Session,
    ) -> ReconcileResult:
        """Explicitly observe external state and journal the observation."""

        operation = self._current_operation(operation, session)
        if (
            operation.classification
            is not OperationRecoveryClassification.RECONCILE_REQUIRED
        ):
            raise DurableToolExecutionError(
                f"operation {operation.operation_id} does not require reconciliation"
            )
        definition = self._resolve_matching(operation, agent)
        if definition.effect_kind is not ToolEffectKind.RECONCILABLE_MUTATION:
            raise DurableToolExecutionError(
                "reconciliation requires RECONCILABLE_MUTATION"
            )
        context = ToolExecutionContext(
            agent_id=agent.agent_id,
            session_id=agent.session_id,
            tool_call_id=operation.tool_call.call_id,
            operation_id=operation.operation_id,
            attempt=max(operation.dispatch_attempts, 1),
        )
        try:
            observed = await definition.reconcile(context)
        except TimeoutError:
            observed = ReconcileResult(
                ReconcileStatus.UNKNOWN,
                message="reconciliation timed out",
            )
        except Exception as error:
            observed = ReconcileResult(
                ReconcileStatus.UNKNOWN,
                message=f"reconciliation failed: {error}",
            )
        if observed.status is ReconcileStatus.SUCCEEDED:
            processed = await self._process_result(
                operation.tool_call,
                ToolResult.success(operation.tool_call, observed.output),
                context,
            )
            observed = ReconcileResult(
                ReconcileStatus.SUCCEEDED,
                output=processed.output,
                message=observed.message,
            )
        payload: dict[str, JsonValue] = {
            "turn": operation.turn,
            "step": operation.step,
            "operation_id": operation.operation_id,
            "observed_status": observed.status.value,
        }
        if observed.status is ReconcileStatus.SUCCEEDED:
            payload["output"] = observed.output
        if observed.message is not None:
            payload["message"] = observed.message
        session.append(EventType.TOOL_RECONCILE, payload)
        session.flush()

        if observed.status is ReconcileStatus.SUCCEEDED:
            self._append_commit(
                session,
                operation.turn,
                operation.step,
                operation.operation_id,
                observed.output,
            )
        elif observed.status is ReconcileStatus.FAILED:
            self._append_abort(
                session,
                operation.turn,
                operation.step,
                operation.operation_id,
                ErrorCode.EIO,
                observed.message or "external system reported failure",
            )
        return observed

    async def _dispatch(
        self,
        *,
        definition: ToolDefinition,
        call: ToolCall,
        agent: AgentControlBlock,
        session: Session,
        turn: int,
        step: int,
        operation_id: str,
        attempt: int,
    ) -> ToolResult:
        session.append(
            EventType.TOOL_DISPATCH,
            {
                "turn": turn,
                "step": step,
                "operation_id": operation_id,
                "attempt": attempt,
            },
        )
        session.flush()
        context = ToolExecutionContext(
            agent_id=agent.agent_id,
            session_id=agent.session_id,
            tool_call_id=call.call_id,
            operation_id=operation_id,
            attempt=attempt,
        )
        result = await self._tools.invoke(definition, call, context)
        result = await self._process_result(call, result, context)
        if result.ok:
            self._append_commit(
                session,
                turn,
                step,
                operation_id,
                result.output,
            )
        else:
            assert result.error is not None
            self._append_abort(
                session,
                turn,
                step,
                operation_id,
                result.error.code,
                result.error.message,
            )
        return result

    async def _process_result(
        self,
        call: ToolCall,
        result: ToolResult,
        context: ToolExecutionContext,
    ) -> ToolResult:
        if self._result_processor is None:
            return result
        processed = await self._result_processor.process(call, result, context)
        if not isinstance(processed, ToolResult):
            raise DurableToolExecutionError(
                "result processor must return ToolResult"
            )
        if processed.call_id != call.call_id or processed.name != call.name:
            raise DurableToolExecutionError(
                "result processor must preserve Tool Call identity"
            )
        return processed

    @staticmethod
    def _append_commit(
        session: Session,
        turn: int,
        step: int,
        operation_id: str,
        output: JsonValue,
    ) -> None:
        session.append(
            EventType.TOOL_COMMIT,
            {
                "turn": turn,
                "step": step,
                "operation_id": operation_id,
                "output": output,
            },
        )
        session.flush()

    @staticmethod
    def _append_abort(
        session: Session,
        turn: int,
        step: int,
        operation_id: str,
        error_code: ErrorCode,
        message: str,
    ) -> None:
        session.append(
            EventType.TOOL_ABORT,
            {
                "turn": turn,
                "step": step,
                "operation_id": operation_id,
                "error_code": error_code.value,
                "message": message,
            },
        )
        session.flush()

    def _resolve_matching(
        self,
        operation: DurableOperationRecovery,
        agent: AgentControlBlock,
    ) -> ToolDefinition:
        resolved = self._tools.resolve_for_execution(operation.tool_call, agent)
        if isinstance(resolved, ToolResult):
            code = resolved.error.code.value if resolved.error is not None else "UNKNOWN"
            raise DurableToolExecutionError(
                f"operation tool is unavailable or unauthorized: {code}"
            )
        if resolved.effect_kind is not operation.effect_kind:
            raise DurableToolExecutionError(
                "registered Tool effect kind differs from durable prepare record"
            )
        return resolved

    @staticmethod
    def _current_operation(
        requested: DurableOperationRecovery,
        session: Session,
    ) -> DurableOperationRecovery:
        analysis = session.recovery_analysis
        for current in analysis.durable_operations:
            if current.operation_id != requested.operation_id:
                continue
            if (
                current.tool_call != requested.tool_call
                or current.effect_kind is not requested.effect_kind
                or current.turn != requested.turn
                or current.step != requested.step
            ):
                raise DurableToolExecutionError(
                    "requested operation differs from the current durable record"
                )
            if (
                analysis.active_turn != current.turn
                or analysis.active_step != current.step
            ):
                raise DurableToolExecutionError(
                    "durable recovery action requires the prepared Step to remain open"
                )
            return current
        raise DurableToolExecutionError(
            f"operation is not present in the current session: "
            f"{requested.operation_id}"
        )

    def _fresh_operation_id(self, session: Session) -> str:
        operation_id = self._operation_id_factory()
        if not isinstance(operation_id, str) or not operation_id:
            raise DurableToolExecutionError(
                "operation_id_factory must return a non-empty string"
            )
        for event in session.events:
            if event.data.get("operation_id") == operation_id:
                raise DurableToolExecutionError(
                    f"operation_id already exists in session: {operation_id}"
                )
            if (
                event.type is EventType.TOOL_CALL
                and event.data.get("call_id") == operation_id
            ):
                raise DurableToolExecutionError(
                    "operation_id must differ from every tool_call_id"
                )
        return operation_id


def _new_operation_id() -> str:
    return f"op_{uuid.uuid4().hex}"
