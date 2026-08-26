"""Long-horizon RuntimeBench composition checks for AgentKernel V0.7."""

from __future__ import annotations

import argparse
import asyncio
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from agentkernel import (
    Agent,
    AgentBudget,
    AuthorizationRequest,
    CapabilityEvaluator,
    CapabilityGrant,
    ContextBudget,
    ContextManager,
    CooperativeScheduler,
    DurableToolExecutor,
    ErrorCode,
    EventType,
    JsonlSessionPersistence,
    LocalResourceStore,
    ModelUsage,
    OperationRecoveryClassification,
    ProcessBudgetExceeded,
    ProcessControlBlock,
    ProcessState,
    RESOURCE_READ_ACTION,
    ReconcileResult,
    ReconcileStatus,
    ResourceAccessDenied,
    ResourceMetrics,
    ResourceOwner,
    ResourceService,
    SchedulerSafePoint,
    Session,
    TOOL_EXECUTE_ACTION,
    ToolCall,
    ToolDefinition,
    ToolEffectKind,
    ToolExecutionContext,
    ToolRegistry,
    ToolResult,
    ToolSchema,
    UsageCollector,
)
from agentkernel.protocol import JsonValue
from benchmarks.common.metrics import BenchmarkRecord, Timer
from benchmarks.common.reporter import print_json_records, write_json_records


BENCHMARK = "runtimebench_v0.7"
LONG_HORIZON_PROFILES = (100, 500, 1000)
PAYMENT_ACTION = "payment.charge"


class _CrashAfterExternalEffect(BaseException):
    """Synthetic process crash after the fake external side effect succeeds."""


@dataclass(frozen=True, slots=True)
class _CrashRecoveryOutcome:
    session: Session
    crash_recovered_events: int
    replay_time_ms: float
    reconcile_required: bool
    recovery_mapping_legal: bool
    authorization_metadata_present: bool
    reconcile_succeeded: bool


@dataclass(frozen=True, slots=True)
class _BudgetOutcome:
    blocked: bool
    recovered: bool
    blocked_reason: str | None
    state_after_recovery: str
    observed_usage: int | float | None
    maximum: int | float | None


@dataclass(frozen=True, slots=True)
class _CapabilityDenialOutcome:
    denial_count: int
    unauthorized_effect_count: int
    tool_denied: bool
    resource_denied: bool
    mutation_denied: bool

    @property
    def success(self) -> bool:
        return (
            self.denial_count == 3
            and self.unauthorized_effect_count == 0
            and self.tool_denied
            and self.resource_denied
            and self.mutation_denied
        )


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, amount: float) -> None:
        self.now += amount


class _StableIdFactory:
    def __init__(self, prefix: str, profile_steps: int) -> None:
        self._prefix = prefix
        self._profile_steps = profile_steps
        self._next = 0

    def __call__(self) -> str:
        self._next += 1
        return f"{self._prefix}p{self._profile_steps}{self._next:04d}"


class _LongHorizonPaymentService:
    """In-memory side-effect fixture keyed by durable operation id."""

    def __init__(self, *, crash_on_charge: bool = False) -> None:
        self.crash_on_charge = crash_on_charge
        self._payments: dict[str, JsonValue] = {}
        self.execution_count = 0
        self.duplicate_external_effects = 0
        self.reconcile_count = 0

    async def charge(
        self,
        arguments: Mapping[str, JsonValue],
        context: ToolExecutionContext,
    ) -> JsonValue:
        existing = self._payments.get(context.operation_id)
        if existing is not None:
            self.duplicate_external_effects += 1
            return existing
        self.execution_count += 1
        payment: JsonValue = {
            "request_id": context.operation_id,
            "invoice_id": arguments["invoice_id"],
            "amount_cents": arguments["amount_cents"],
            "status": "succeeded",
        }
        self._payments[context.operation_id] = payment
        if self.crash_on_charge:
            raise _CrashAfterExternalEffect(context.operation_id)
        return payment

    async def reconcile(self, context: ToolExecutionContext) -> ReconcileResult:
        self.reconcile_count += 1
        payment = self._payments.get(context.operation_id)
        if payment is None:
            return ReconcileResult(ReconcileStatus.NOT_FOUND)
        return ReconcileResult(ReconcileStatus.SUCCEEDED, output=payment)

    @property
    def payment_count(self) -> int:
        return len(self._payments)


async def _forbidden_tool(
    _arguments: Mapping[str, JsonValue],
    _context: ToolExecutionContext,
) -> JsonValue:
    return {"unexpected": True}


def run_long_horizon_profiles(
    profiles: Iterable[int] = LONG_HORIZON_PROFILES,
) -> list[BenchmarkRecord]:
    """Run deterministic long-horizon profiles and return raw records."""

    return [_run_profile(_validate_profile_steps(steps)) for steps in profiles]


def _run_profile(profile_steps: int) -> BenchmarkRecord:
    timer = Timer()
    agent_id = f"agent-long-{profile_steps}"
    session_id = f"session-long-{profile_steps}"
    process_id = f"process-long-{profile_steps}"
    payment_resource = f"payment://charges/profile-{profile_steps}"
    payment_scope = "payment://charges/**"
    grants = (
        CapabilityGrant(agent_id, PAYMENT_ACTION, payment_scope),
        CapabilityGrant(agent_id, RESOURCE_READ_ACTION, "artifact://**"),
    )
    budget = AgentBudget(max_token_usage=(profile_steps * 10) - 1)
    resource_metrics = ResourceMetrics()
    clock = _FakeClock()
    collector = UsageCollector(clock=clock)
    scheduler = CooperativeScheduler(usage_collector=collector)
    payment_service = _LongHorizonPaymentService(crash_on_charge=True)

    with tempfile.TemporaryDirectory(
        prefix="agentkernel-long-horizon-"
    ) as temp_root:
        root = Path(temp_root)
        session_path = root / "session.jsonl"
        resource_root = root / "resources"
        session = Session(session_id, JsonlSessionPersistence(session_path))
        agent = _agent(
            session,
            agent_id=agent_id,
            grants=grants,
            budget=budget,
        )
        owner = ResourceOwner(agent_id, session_id)
        resource_service = ResourceService(
            LocalResourceStore(resource_root),
            metrics=resource_metrics,
            resource_id_factory=_StableIdFactory("res_", profile_steps),
            handle_id_factory=_StableIdFactory("hdl_", profile_steps),
        )
        evaluator = CapabilityEvaluator(agent.control.capability_grants)
        process = scheduler.create_process(process_id=process_id, agent=agent.control)
        scheduler.dispatch(process_id)
        collector.start_process(process_id)

        crash_turn = min(profile_steps, max(2, profile_steps // 2 + 1))
        resource_turns = _resource_turns(profile_steps, crash_turn)
        resource_uris: list[str] = []
        resource_markers: list[str] = []

        for turn in range(1, crash_turn):
            if turn in resource_turns:
                uri, marker = _append_resource_turn(
                    session,
                    turn=turn,
                    profile_steps=profile_steps,
                    owner=owner,
                    resources=resource_service,
                    evaluator=evaluator,
                    collector=collector,
                    process_id=process_id,
                )
                resource_uris.append(uri)
                resource_markers.append(marker)
            else:
                _append_plain_turn(session, turn, profile_steps)
            _record_turn_usage(collector, process_id)

        crash_outcome = _execute_crash_then_reconcile(
            session=session,
            session_path=session_path,
            turn=crash_turn,
            agent_id=agent_id,
            grants=grants,
            budget=budget,
            payment_resource=payment_resource,
            payment_service=payment_service,
        )
        session = crash_outcome.session
        agent = _agent(
            session,
            agent_id=agent_id,
            grants=grants,
            budget=budget,
        )
        _record_turn_usage(collector, process_id)
        collector.record_tool_call(process_id)

        for turn in range(crash_turn + 1, profile_steps + 1):
            if turn in resource_turns:
                uri, marker = _append_resource_turn(
                    session,
                    turn=turn,
                    profile_steps=profile_steps,
                    owner=owner,
                    resources=resource_service,
                    evaluator=evaluator,
                    collector=collector,
                    process_id=process_id,
                )
                resource_uris.append(uri)
                resource_markers.append(marker)
            else:
                _append_plain_turn(session, turn, profile_steps)
            _record_turn_usage(collector, process_id)

        session.flush()
        session.close()

        replay_timer = Timer()
        final_session = Session.load(session_id, JsonlSessionPersistence(session_path))
        final_replay_time_ms = replay_timer.elapsed_ms()
        final_agent = _agent(
            final_session,
            agent_id=agent_id,
            grants=grants,
            budget=budget,
        )
        final_analysis = final_session.recovery_analysis
        final_process = ProcessControlBlock.from_recovery(
            process_id=f"{process_id}-final",
            agent=final_agent.control,
            recovery=final_analysis,
        )
        working_set = ContextManager().build_working_set(
            final_session,
            current_turn=profile_steps,
            budget=ContextBudget(max_tokens=1800, reserved_output_tokens=200),
            system_prompt="AgentKernel long-horizon RuntimeBench fixture.",
        )
        usage_before_budget_recovery = collector.snapshot(process_id)
        budget_outcome = _block_and_recover_budget(
            scheduler,
            collector,
            process_id,
        )
        usage_after_budget_recovery = collector.snapshot(process_id)
        resource_restart_success = _restart_resource_read_success(
            resource_root,
            owner=owner,
            grants=final_agent.control.capability_grants,
            uri=resource_uris[-1] if resource_uris else None,
            marker=resource_markers[-1] if resource_markers else None,
        )
        denials = _capability_denials(
            profile_steps=profile_steps,
            resource_root=resource_root,
            owner=owner,
            resource_uri=resource_uris[-1] if resource_uris else None,
        )

        turn_end_count = sum(
            event.type is EventType.TURN_END for event in final_session.events
        )
        final_marker = _turn_marker(profile_steps, profile_steps)
        final_marker_present = _session_contains(final_session, final_marker)
        final_operation = final_analysis.durable_operations[0]
        final_durable_consistency = (
            final_operation.classification
            is OperationRecoveryClassification.COMPLETED
            and final_operation.result_persisted
            and payment_service.execution_count == 1
            and payment_service.payment_count == 1
        )
        recovery_success_count = sum(
            (
                crash_outcome.reconcile_required
                and crash_outcome.recovery_mapping_legal,
                final_analysis.status.value == "completed",
                final_process.state is ProcessState.EXITED,
            )
        )
        recovery_failure_count = 3 - recovery_success_count
        agent_process_session_isolation = (
            process.process_id != agent_id
            and process.agent_id == agent_id
            and process.session_id == session_id
            and process.capability_snapshot is not None
            and process.capability_snapshot.agent_id == agent_id
            and final_agent.control.agent_id == agent_id
            and final_agent.session.session_id == session_id
        )
        truth_preserved = (
            final_marker_present
            and turn_end_count == profile_steps
            and resource_restart_success
            and final_durable_consistency
        )
        success = (
            turn_end_count == profile_steps
            and len(final_session.events) >= crash_outcome.crash_recovered_events
            and crash_outcome.reconcile_required
            and crash_outcome.recovery_mapping_legal
            and crash_outcome.reconcile_succeeded
            and payment_service.duplicate_external_effects == 0
            and denials.success
            and resource_restart_success
            and budget_outcome.blocked
            and budget_outcome.recovered
            and agent_process_session_isolation
            and truth_preserved
            and final_durable_consistency
            and recovery_failure_count == 0
        )
        metrics = {
            "profile_steps": profile_steps,
            "steps_completed": turn_end_count,
            "session_events": len(final_session.events),
            "recovered_events": len(final_session.events),
            "crash_recovered_events": crash_outcome.crash_recovered_events,
            "replay_time_ms": round(
                crash_outcome.replay_time_ms + final_replay_time_ms,
                3,
            ),
            "durable_operations": len(final_analysis.durable_operations),
            "reconcile_required_count": 1
            if crash_outcome.reconcile_required
            else 0,
            "duplicate_external_effects": payment_service.duplicate_external_effects,
            "context_working_set_tokens_peak": working_set.metrics.selected_tokens,
            "context_projected_tokens": working_set.metrics.projected_tokens,
            "context_evicted_tokens": working_set.metrics.evicted_tokens,
            "reclaim_tokens_saved": working_set.metrics.reclaim_tokens_saved,
            "context_reclaim_count": (
                working_set.metrics.evicted_pages
                + working_set.metrics.pruned_pages
                + working_set.metrics.compaction_count
            ),
            "resource_count": resource_metrics.resources_created,
            "resource_bytes": resource_metrics.resource_bytes_stored,
            "resource_reads": usage_before_budget_recovery.resource_reads,
            "resource_read_bytes": usage_before_budget_recovery.resource_bytes,
            "resource_restart_success": resource_restart_success,
            "budget_blocks": 1 if budget_outcome.blocked else 0,
            "budget_recoveries": 1 if budget_outcome.recovered else 0,
            "budget_block_reason": budget_outcome.blocked_reason,
            "budget_observed_usage": budget_outcome.observed_usage,
            "budget_maximum": budget_outcome.maximum,
            "capability_denials": denials.denial_count,
            "unauthorized_effect_count": denials.unauthorized_effect_count,
            "recovery_success_count": recovery_success_count,
            "recovery_failure_count": recovery_failure_count,
            "agent_process_session_isolation": agent_process_session_isolation,
            "truth_preserved": truth_preserved,
            "final_durable_consistency": final_durable_consistency,
            "authorization_metadata_present": (
                crash_outcome.authorization_metadata_present
                and final_operation.authorization is not None
            ),
            "token_usage": usage_before_budget_recovery.token_usage,
            "tool_calls": usage_before_budget_recovery.tool_calls,
            "token_usage_after_budget_recovery": (
                usage_after_budget_recovery.token_usage
            ),
            "wall_time": usage_before_budget_recovery.wall_time,
            "wall_time_ms": timer.elapsed_ms(),
            "success": success,
        }
        final_session.close()

    return BenchmarkRecord(
        benchmark=BENCHMARK,
        case=f"long_horizon_{profile_steps}_steps",
        strategy="agentkernel_composed_runtime_mechanisms",
        metrics=metrics,
    )


def _execute_crash_then_reconcile(
    *,
    session: Session,
    session_path: Path,
    turn: int,
    agent_id: str,
    grants: tuple[CapabilityGrant, ...],
    budget: AgentBudget,
    payment_resource: str,
    payment_service: _LongHorizonPaymentService,
) -> _CrashRecoveryOutcome:
    call = ToolCall(
        f"call-payment-{turn}",
        "payment.charge",
        {"invoice_id": f"invoice-{turn}", "amount_cents": turn * 100},
    )
    _append_call_prefix(
        session,
        turn=turn,
        call=call,
        user_content=f"Charge deterministic invoice at turn {turn}.",
    )
    agent = _agent(session, agent_id=agent_id, grants=grants, budget=budget)
    crashed = False
    try:
        asyncio.run(
            DurableToolExecutor(
                _payment_registry(payment_service, payment_resource=payment_resource),
                operation_id_factory=lambda: f"op_long_{turn}",
            ).execute(call, agent.control, session, turn=turn, step=1)
        )
    except _CrashAfterExternalEffect:
        crashed = True
    session.close()

    replay_timer = Timer()
    restarted = Session.load(
        agent.session.session_id,
        JsonlSessionPersistence(session_path),
    )
    replay_time_ms = replay_timer.elapsed_ms()
    restarted_agent = _agent(
        restarted,
        agent_id=agent_id,
        grants=grants,
        budget=budget,
    )
    analysis = restarted.recovery_analysis
    operation = analysis.durable_operations[0]
    recovered = ProcessControlBlock.from_recovery(
        process_id=f"process-recovered-{turn}",
        agent=restarted_agent.control,
        recovery=analysis,
    )
    reconcile_result = asyncio.run(
        DurableToolExecutor(
            _payment_registry(payment_service, payment_resource=payment_resource)
        ).reconcile(operation, restarted_agent.control, restarted)
    )
    if reconcile_result.status is ReconcileStatus.SUCCEEDED:
        result = ToolResult.success(operation.tool_call, reconcile_result.output)
        restarted.append(
            EventType.TOOL_RESULT,
            {"turn": turn, "step": 1, **result.as_dict()},
        )
        restarted.append(EventType.STEP_END, {"turn": turn, "step": 1})
        restarted.append(EventType.TURN_END, {"turn": turn, "reason": "completed"})
        restarted.flush()

    return _CrashRecoveryOutcome(
        session=restarted,
        crash_recovered_events=len(restarted.events),
        replay_time_ms=replay_time_ms,
        reconcile_required=(
            crashed
            and operation.classification
            is OperationRecoveryClassification.RECONCILE_REQUIRED
        ),
        recovery_mapping_legal=(
            recovered.state is ProcessState.BLOCKED
            and recovered.blocked_reason == "durable_operation_recovery"
        ),
        authorization_metadata_present=operation.authorization is not None,
        reconcile_succeeded=reconcile_result.status is ReconcileStatus.SUCCEEDED,
    )


def _append_plain_turn(session: Session, turn: int, profile_steps: int) -> None:
    marker = _turn_marker(profile_steps, turn)
    session.append(EventType.TURN_START, {"turn": turn})
    session.append(
        EventType.USER_MESSAGE,
        {
            "turn": turn,
            "content": (
                f"{marker} user asks for deterministic progress. "
                + ("context pressure input " * 20)
            ),
        },
    )
    session.append(EventType.STEP_START, {"turn": turn, "step": 1})
    session.append(
        EventType.ASSISTANT_MESSAGE,
        {
            "turn": turn,
            "step": 1,
            "content": (
                f"{marker} assistant preserves the durable fact. "
                + ("runtime stability response " * 20)
            ),
        },
    )
    session.append(EventType.STEP_END, {"turn": turn, "step": 1})
    session.append(EventType.TURN_END, {"turn": turn, "reason": "completed"})


def _append_resource_turn(
    session: Session,
    *,
    turn: int,
    profile_steps: int,
    owner: ResourceOwner,
    resources: ResourceService,
    evaluator: CapabilityEvaluator,
    collector: UsageCollector,
    process_id: str,
) -> tuple[str, str]:
    marker = f"RESOURCE_PROFILE_{profile_steps}_TURN_{turn}"
    data = (marker + "\n").encode("utf-8") + (b"x" * 4096)
    handle = resources.create_artifact(
        data,
        owner=owner,
        media_type="text/plain",
        encoding="utf-8",
        source_tool_name="long_horizon.resource",
        source_tool_call_id=f"call-resource-{turn}",
        source_operation_id=f"op-resource-{turn}",
    )
    before = resources.metrics.snapshot()
    read = resources.read(
        handle.uri,
        owner=owner,
        offset=0,
        limit=len(marker),
        capability_evaluator=evaluator,
    )
    collector.observe_resource_metrics(process_id, before)
    collector.observe_resource_metrics(process_id, resources.metrics.snapshot())
    collector.record_tool_call(process_id)

    call = ToolCall(
        f"call-resource-{turn}",
        "resource.create",
        {"marker": marker},
    )
    diagnostic_blob = "diagnostic line " + ("z" * 16000)
    session.append(EventType.TURN_START, {"turn": turn})
    session.append(
        EventType.USER_MESSAGE,
        {
            "turn": turn,
            "content": f"Create and preserve resource marker {marker}.",
        },
    )
    session.append(EventType.STEP_START, {"turn": turn, "step": 1})
    session.append(
        EventType.ASSISTANT_MESSAGE,
        {
            "turn": turn,
            "step": 1,
            "content": "",
            "tool_calls": [call.as_dict()],
        },
    )
    session.append(EventType.TOOL_CALL, {"turn": turn, "step": 1, **call.as_dict()})
    session.append(
        EventType.TOOL_RESULT,
        {
            "turn": turn,
            "step": 1,
            **ToolResult.success(
                call,
                {
                    "uri": handle.uri,
                    "bytes": len(data),
                    "marker": marker,
                    "read_back": read.data.decode("utf-8"),
                    "diagnostic_blob": diagnostic_blob,
                },
            ).as_dict(),
        },
    )
    session.append(EventType.STEP_END, {"turn": turn, "step": 1})
    session.append(EventType.TURN_END, {"turn": turn, "reason": "completed"})
    return handle.uri, marker


def _append_call_prefix(
    session: Session,
    *,
    turn: int,
    call: ToolCall,
    user_content: str,
) -> None:
    session.append(EventType.TURN_START, {"turn": turn})
    session.append(EventType.USER_MESSAGE, {"turn": turn, "content": user_content})
    session.append(EventType.STEP_START, {"turn": turn, "step": 1})
    session.append(
        EventType.ASSISTANT_MESSAGE,
        {"turn": turn, "step": 1, "content": "", "tool_calls": [call.as_dict()]},
    )
    session.append(EventType.TOOL_CALL, {"turn": turn, "step": 1, **call.as_dict()})


def _record_turn_usage(collector: UsageCollector, process_id: str) -> None:
    collector.record_llm_usage(
        process_id,
        ModelUsage(input_tokens=6, output_tokens=4, total_tokens=10),
    )


def _block_and_recover_budget(
    scheduler: CooperativeScheduler,
    collector: UsageCollector,
    process_id: str,
) -> _BudgetOutcome:
    blocked = False
    observed_usage: int | float | None = None
    maximum: int | float | None = None
    blocked_reason = None
    try:
        scheduler.safe_point(process_id, SchedulerSafePoint.AFTER_LLM_CALL)
    except ProcessBudgetExceeded as error:
        blocked = True
        observed_usage = error.exceeded.usage
        maximum = error.exceeded.maximum
        blocked_reason = scheduler.manager.get(process_id).blocked_reason

    recovered = False
    state_after_recovery = scheduler.manager.get(process_id).state.value
    if blocked:
        collector.reset_process(process_id)
        scheduler.unblock(process_id)
        scheduler.dispatch(process_id)
        collector.record_llm_usage(
            process_id,
            ModelUsage(input_tokens=1, output_tokens=1, total_tokens=2),
        )
        try:
            scheduler.safe_point(process_id, SchedulerSafePoint.AFTER_LLM_CALL)
            recovered = scheduler.manager.get(process_id).state is ProcessState.RUNNING
        except ProcessBudgetExceeded:
            recovered = False
        state_after_recovery = scheduler.manager.get(process_id).state.value
        if recovered:
            scheduler.exit_process(process_id, exit_status="completed")

    return _BudgetOutcome(
        blocked=blocked,
        recovered=recovered,
        blocked_reason=blocked_reason,
        state_after_recovery=state_after_recovery,
        observed_usage=observed_usage,
        maximum=maximum,
    )


def _restart_resource_read_success(
    resource_root: Path,
    *,
    owner: ResourceOwner,
    grants: tuple[CapabilityGrant, ...],
    uri: str | None,
    marker: str | None,
) -> bool:
    if uri is None or marker is None:
        return False
    restarted = ResourceService(LocalResourceStore(resource_root))
    read = restarted.read(
        uri,
        owner=owner,
        offset=0,
        limit=len(marker),
        capability_evaluator=CapabilityEvaluator(grants),
    )
    return read.data.decode("utf-8") == marker


def _capability_denials(
    *,
    profile_steps: int,
    resource_root: Path,
    owner: ResourceOwner,
    resource_uri: str | None,
) -> _CapabilityDenialOutcome:
    tool_denied, tool_effects = _tool_denial(profile_steps)
    resource_denied, resource_effects = _resource_denial(
        resource_root,
        owner=owner,
        resource_uri=resource_uri,
    )
    mutation_denied, mutation_effects = _mutation_denial(profile_steps)
    denied = sum((tool_denied, resource_denied, mutation_denied))
    return _CapabilityDenialOutcome(
        denial_count=denied,
        unauthorized_effect_count=tool_effects + resource_effects + mutation_effects,
        tool_denied=tool_denied,
        resource_denied=resource_denied,
        mutation_denied=mutation_denied,
    )


def _tool_denial(profile_steps: int) -> tuple[bool, int]:
    calls = {"count": 0}

    async def handler(
        arguments: Mapping[str, JsonValue],
        context: ToolExecutionContext,
    ) -> JsonValue:
        calls["count"] += 1
        return await _forbidden_tool(arguments, context)

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            schema=ToolSchema(
                "forbidden.inspect",
                "Forbidden diagnostic tool.",
                {"type": "object"},
            ),
            handler=handler,
            required_action=TOOL_EXECUTE_ACTION,
            required_resource="tool://forbidden.inspect",
        )
    )
    agent = Agent.create(
        agent_id=f"agent-tool-deny-{profile_steps}",
        session=Session(f"session-tool-deny-{profile_steps}"),
    )
    result = asyncio.run(
        registry.execute(
            ToolCall("call-forbidden", "forbidden.inspect", {}),
            agent.control,
        )
    )
    visible = registry.model_schemas(agent.control)
    denied = (
        not result.ok
        and result.error is not None
        and result.error.code is ErrorCode.EACCES
        and not visible
    )
    return denied, calls["count"]


def _resource_denial(
    resource_root: Path,
    *,
    owner: ResourceOwner,
    resource_uri: str | None,
) -> tuple[bool, int]:
    if resource_uri is None:
        return False, 0
    metrics = ResourceMetrics()
    service = ResourceService(LocalResourceStore(resource_root), metrics=metrics)
    before_reads = metrics.resource_reads
    denied = False
    try:
        service.read(
            resource_uri,
            owner=owner,
            capability_evaluator=CapabilityEvaluator(),
        )
    except ResourceAccessDenied:
        denied = True
    return denied, metrics.resource_reads - before_reads


def _mutation_denial(profile_steps: int) -> tuple[bool, int]:
    service = _LongHorizonPaymentService()
    payment_resource = f"payment://charges/denied-{profile_steps}"
    registry = _payment_registry(service, payment_resource=payment_resource)
    session = Session(f"session-mutation-deny-{profile_steps}")
    agent = Agent.create(
        agent_id=f"agent-mutation-deny-{profile_steps}",
        session=session,
    )
    call = ToolCall(
        "call-payment-denied",
        "payment.charge",
        {"invoice_id": "denied", "amount_cents": 1},
    )
    _append_call_prefix(
        session,
        turn=1,
        call=call,
        user_content="Attempt unauthorized payment.",
    )
    result = asyncio.run(
        DurableToolExecutor(registry).execute(
            call,
            agent.control,
            session,
            turn=1,
            step=1,
        )
    )
    denied_events = [
        event for event in session.events if event.type is EventType.AUTHORIZATION_DENIED
    ]
    prepare_events = [
        event for event in session.events if event.type is EventType.TOOL_PREPARE
    ]
    dispatch_events = [
        event for event in session.events if event.type is EventType.TOOL_DISPATCH
    ]
    denied = (
        not result.ok
        and result.error is not None
        and result.error.code is ErrorCode.EACCES
        and len(denied_events) == 1
        and not prepare_events
        and not dispatch_events
    )
    return denied, service.execution_count


def _payment_registry(
    service: _LongHorizonPaymentService,
    *,
    payment_resource: str,
) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            schema=ToolSchema(
                "payment.charge",
                "Charge a deterministic fake invoice.",
                {
                    "type": "object",
                    "properties": {
                        "invoice_id": {"type": "string"},
                        "amount_cents": {"type": "integer"},
                    },
                    "required": ["invoice_id", "amount_cents"],
                    "additionalProperties": False,
                },
            ),
            handler=service.charge,
            required_action=PAYMENT_ACTION,
            required_resource=payment_resource,
            effect_kind=ToolEffectKind.RECONCILABLE_MUTATION,
            reconcile_handler=service.reconcile,
        )
    )
    return registry


def _agent(
    session: Session,
    *,
    agent_id: str,
    grants: tuple[CapabilityGrant, ...],
    budget: AgentBudget,
) -> Agent:
    return Agent.create(
        agent_id=agent_id,
        session=session,
        capability_grants=grants,
        budget=budget,
    )


def _resource_turns(profile_steps: int, crash_turn: int) -> frozenset[int]:
    interval = max(10, profile_steps // 10)
    turns = set(range(interval, profile_steps + 1, interval))
    if not turns:
        turns.add(profile_steps)
    if crash_turn in turns:
        turns.remove(crash_turn)
        replacement = crash_turn - 1 if crash_turn > 1 else crash_turn + 1
        if 1 <= replacement <= profile_steps:
            turns.add(replacement)
    return frozenset(turns)


def _turn_marker(profile_steps: int, turn: int) -> str:
    return f"PROFILE_{profile_steps}_TURN_{turn}"


def _session_contains(session: Session, needle: str) -> bool:
    for event in session.events:
        if needle in str(event.data):
            return True
    return False


def _validate_profile_steps(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 4:
        raise ValueError("long-horizon profile steps must be an integer >= 4")
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="runtimebench_long_horizon.json")
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument(
        "--profiles",
        default=",".join(str(item) for item in LONG_HORIZON_PROFILES),
        help="Comma-separated step counts.",
    )
    args = parser.parse_args()

    profiles = tuple(int(item) for item in args.profiles.split(",") if item)
    records = run_long_horizon_profiles(profiles)
    if not args.no_write:
        write_json_records(args.output, records)
    print_json_records(records)


if __name__ == "__main__":
    main()
