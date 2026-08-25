"""Tool registry, model schema projection, and guarded execution."""

from __future__ import annotations

import asyncio
import copy
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping, Protocol, TypeAlias

from .agent import AgentControlBlock
from .capabilities import (
    AuthorizationDecision,
    AuthorizationRequest,
    CapabilityEvaluator,
    CapabilityGrant,
    TOOL_EXECUTE_ACTION,
    legacy_tool_request,
)
from .protocol import (
    ErrorCode,
    JsonValue,
    ToolCall,
    ToolResult,
    ToolSchema,
    is_json_value,
)
from .tool_effects import ReconcileResult, ReconcileStatus, ToolEffectKind


class ToolConcurrency(StrEnum):
    """Reserved execution classification; V0.1 dispatch remains sequential."""

    PARALLEL = "parallel"
    EXCLUSIVE = "exclusive"


class ToolExecutionError(RuntimeError):
    """Typed handler failure preserved across the Tool syscall boundary."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = ErrorCode(code)


class ToolResultProcessor(Protocol):
    async def process(
        self,
        call: ToolCall,
        result: ToolResult,
        context: "ToolExecutionContext",
    ) -> ToolResult: ...


@dataclass(frozen=True, slots=True)
class ToolExecutionContext:
    """Kernel metadata passed to a tool implementation, never to the model."""

    agent_id: str
    session_id: str
    tool_call_id: str
    operation_id: str
    attempt: int = 1
    capability_evaluator: CapabilityEvaluator | None = None

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, str) and value
            for value in (
                self.agent_id,
                self.session_id,
                self.tool_call_id,
                self.operation_id,
            )
        ):
            raise ValueError("tool execution identities must be non-empty strings")
        if (
            isinstance(self.attempt, bool)
            or not isinstance(self.attempt, int)
            or self.attempt < 1
        ):
            raise ValueError("tool execution attempt must be a positive integer")


ToolHandler: TypeAlias = Callable[
    [Mapping[str, JsonValue], ToolExecutionContext], Awaitable[JsonValue]
]
ReconcileHandler: TypeAlias = Callable[
    [ToolExecutionContext], Awaitable[ReconcileResult]
]


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """Host runtime definition paired with a model-visible schema."""

    schema: ToolSchema
    handler: ToolHandler
    required_capability: str | None = None
    timeout_seconds: float | None = None
    concurrency: ToolConcurrency = ToolConcurrency.PARALLEL
    effect_kind: ToolEffectKind = ToolEffectKind.READ_ONLY
    reconcile_handler: ReconcileHandler | None = None
    required_action: str | None = None
    required_resource: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "effect_kind", ToolEffectKind(self.effect_kind))
        if (self.required_action is None) != (self.required_resource is None):
            raise ValueError(
                "required_action and required_resource must be provided together"
            )
        for name in ("required_action", "required_resource"):
            value = getattr(self, name)
            if value is not None and not value:
                raise ValueError(f"{name} must not be empty")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("tool timeout_seconds must be positive")
        if (
            self.effect_kind is ToolEffectKind.RECONCILABLE_MUTATION
            and self.reconcile_handler is None
        ):
            raise ValueError(
                "RECONCILABLE_MUTATION requires a reconcile_handler"
            )
        if (
            self.effect_kind is not ToolEffectKind.RECONCILABLE_MUTATION
            and self.reconcile_handler is not None
        ):
            raise ValueError(
                "reconcile_handler is only valid for RECONCILABLE_MUTATION"
            )

    async def execute(
        self,
        arguments: Mapping[str, JsonValue],
        context: ToolExecutionContext,
    ) -> JsonValue:
        """Invoke the host handler under its optional timeout."""

        if self.timeout_seconds is None:
            return await self.handler(arguments, context)
        async with asyncio.timeout(self.timeout_seconds):
            return await self.handler(arguments, context)

    async def reconcile(self, context: ToolExecutionContext) -> ReconcileResult:
        """Query external state through a Tool-owned reconciliation contract."""

        if self.reconcile_handler is None:
            raise RuntimeError(
                f'tool "{self.schema.name}" does not support reconciliation'
            )
        if self.timeout_seconds is None:
            result = await self.reconcile_handler(context)
        else:
            async with asyncio.timeout(self.timeout_seconds):
                result = await self.reconcile_handler(context)
        if not isinstance(result, ReconcileResult):
            raise TypeError("reconcile handler must return ReconcileResult")
        return result


@dataclass(frozen=True, slots=True)
class ToolAuthorization:
    """Kernel authorization snapshot for one Tool execution request."""

    definition: ToolDefinition
    request: AuthorizationRequest
    decision: AuthorizationDecision

    @property
    def allowed(self) -> bool:
        return self.decision.allowed

    @property
    def reason(self) -> str:
        return self.decision.reason

    def as_context(self) -> dict[str, JsonValue]:
        """Return the durable JSON authorization context."""

        payload: dict[str, JsonValue] = {
            "agent_id": self.request.agent_id,
            "action": self.request.action,
            "resource_scope": self.request.resource,
            "reason": self.decision.reason,
        }
        if self.decision.matched_grant is not None:
            payload["matched_grant"] = _grant_payload(
                self.decision.matched_grant
            )
        return payload


class ToolRegistry:
    """Own model projection and the enforced tool/syscall boundary."""

    def __init__(self) -> None:
        self._definitions: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        """Register one unique tool and snapshot its model schema."""

        name = definition.schema.name
        if name in self._definitions:
            raise ValueError(f'tool "{name}" is already registered')
        schema = ToolSchema(
            name=name,
            description=definition.schema.description,
            input_schema=copy.deepcopy(dict(definition.schema.input_schema)),
        )
        self._definitions[name] = ToolDefinition(
            schema=schema,
            handler=definition.handler,
            required_capability=definition.required_capability,
            timeout_seconds=definition.timeout_seconds,
            concurrency=definition.concurrency,
            effect_kind=definition.effect_kind,
            reconcile_handler=definition.reconcile_handler,
            required_action=definition.required_action,
            required_resource=definition.required_resource,
        )

    def model_schemas(self, agent: AgentControlBlock) -> tuple[ToolSchema, ...]:
        """Project only model-facing fields for tools the agent may execute."""

        schemas: list[ToolSchema] = []
        evaluator = _evaluator_for_agent(agent)
        for definition in self._definitions.values():
            if not _is_authorized(definition, agent, evaluator):
                continue
            schemas.append(
                ToolSchema(
                    name=definition.schema.name,
                    description=definition.schema.description,
                    input_schema=copy.deepcopy(dict(definition.schema.input_schema)),
                )
            )
        return tuple(schemas)

    def evaluator_for_agent(self, agent: AgentControlBlock) -> CapabilityEvaluator:
        """Build the effective tool/resource evaluator for Kernel use."""

        return _evaluator_for_agent(agent)

    async def execute(
        self,
        call: ToolCall,
        agent: AgentControlBlock,
    ) -> ToolResult:
        """Resolve, authorize, execute, and normalize one tool call."""

        resolved = self.resolve_for_execution(call, agent)
        if isinstance(resolved, ToolResult):
            return resolved
        definition = resolved
        if definition.effect_kind is not ToolEffectKind.READ_ONLY:
            return ToolResult.failure(
                call,
                ErrorCode.EINVAL,
                f'mutation tool "{call.name}" requires DurableToolExecutor',
            )
        context = ToolExecutionContext(
            agent_id=agent.agent_id,
            session_id=agent.session_id,
            tool_call_id=call.call_id,
            operation_id=f"op_{uuid.uuid4().hex}",
            capability_evaluator=_evaluator_for_agent(agent),
        )
        return await self.invoke(resolved, call, context)

    def resolve_for_execution(
        self,
        call: ToolCall,
        agent: AgentControlBlock,
    ) -> ToolDefinition | ToolResult:
        """Resolve and authorize before any durable intent is created."""

        authorization = self.authorization_for_execution(call, agent)
        if isinstance(authorization, ToolResult):
            return authorization
        if not authorization.allowed:
            return ToolResult.failure(
                call,
                ErrorCode.EACCES,
                authorization.reason,
            )
        return authorization.definition

    def authorization_for_execution(
        self,
        call: ToolCall,
        agent: AgentControlBlock,
    ) -> ToolAuthorization | ToolResult:
        """Resolve a call and return its authorization decision."""

        definition = self._definitions.get(call.name)
        if definition is None:
            return ToolResult.failure(
                call,
                ErrorCode.ENOENT,
                f'tool "{call.name}" is not registered',
            )
        return self.authorization_for_definition(definition, agent)

    def authorization_for_definition(
        self,
        definition: ToolDefinition,
        agent: AgentControlBlock,
    ) -> ToolAuthorization:
        """Evaluate a registered definition for the current agent."""

        evaluator = _evaluator_for_agent(agent)
        return _tool_authorization(definition, agent, evaluator)

    async def invoke(
        self,
        definition: ToolDefinition,
        call: ToolCall,
        context: ToolExecutionContext,
    ) -> ToolResult:
        """Invoke one already-authorized Tool and normalize runtime failures."""

        try:
            output = await definition.execute(call.arguments, context)
            if not is_json_value(output):
                raise TypeError("tool returned a value that is not lossless JSON")
        except ToolExecutionError as error:
            return ToolResult.failure(call, error.code, str(error))
        except TimeoutError:
            return ToolResult.failure(
                call,
                ErrorCode.ETIMEDOUT,
                f'tool "{call.name}" exceeded its timeout',
            )
        except Exception as error:
            return ToolResult.failure(
                call,
                ErrorCode.EIO,
                f'tool "{call.name}" failed: {error}',
            )
        return ToolResult.success(call, output)


def _evaluator_for_agent(agent: AgentControlBlock) -> CapabilityEvaluator:
    return CapabilityEvaluator.from_agent_capabilities(
        agent_id=agent.agent_id,
        capabilities=agent.capabilities,
        capability_grants=agent.capability_grants,
    )


def _is_authorized(
    definition: ToolDefinition,
    agent: AgentControlBlock,
    evaluator: CapabilityEvaluator,
) -> bool:
    return _authorization_decision(definition, agent, evaluator).allowed


def _authorization_decision(
    definition: ToolDefinition,
    agent: AgentControlBlock,
    evaluator: CapabilityEvaluator,
) -> AuthorizationDecision:
    return _tool_authorization(definition, agent, evaluator).decision


def _tool_authorization(
    definition: ToolDefinition,
    agent: AgentControlBlock,
    evaluator: CapabilityEvaluator,
) -> ToolAuthorization:
    checks = _authorization_checks(definition, agent)
    primary = checks[-1]
    primary_decision = AuthorizationDecision(True, "allowed")
    for request in checks:
        if _is_default_tool_request(definition, request):
            decision = AuthorizationDecision(True, "allowed")
        else:
            decision = evaluator.authorize(request)
        if not decision.allowed:
            return ToolAuthorization(definition, request, decision)
        if request == primary:
            primary_decision = decision
    return ToolAuthorization(definition, primary, primary_decision)


def _authorization_checks(
    definition: ToolDefinition,
    agent: AgentControlBlock,
) -> tuple[AuthorizationRequest, ...]:
    checks: list[AuthorizationRequest] = []
    capability = definition.required_capability
    if capability is not None:
        checks.append(
            legacy_tool_request(
                agent_id=agent.agent_id,
                required_capability=capability,
            )
        )
    if definition.required_action is None or definition.required_resource is None:
        if checks:
            return tuple(checks)
        return (
            AuthorizationRequest(
                agent_id=agent.agent_id,
                action=TOOL_EXECUTE_ACTION,
                resource=f"tool://{definition.schema.name}",
            ),
        )
    checks.append(
        AuthorizationRequest(
            agent_id=agent.agent_id,
            action=definition.required_action,
            resource=definition.required_resource,
        )
    )
    return tuple(checks)


def _is_default_tool_request(
    definition: ToolDefinition,
    request: AuthorizationRequest,
) -> bool:
    return (
        definition.required_capability is None
        and definition.required_action is None
        and definition.required_resource is None
        and request.action == TOOL_EXECUTE_ACTION
        and request.resource == f"tool://{definition.schema.name}"
    )


def _grant_payload(grant: CapabilityGrant) -> dict[str, JsonValue]:
    return {
        "subject": grant.subject,
        "action": grant.action,
        "resource_scope": grant.resource_scope,
    }
