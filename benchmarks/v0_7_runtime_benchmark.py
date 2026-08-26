"""Deterministic offline validation for V0.7 process runtime primitives."""

from __future__ import annotations

import argparse

from agentkernel import (
    Agent,
    AgentBudget,
    CapabilityGrant,
    CooperativeScheduler,
    EventType,
    ModelUsage,
    NoRunnableProcess,
    OperationRecoveryClassification,
    ProcessBudgetExceeded,
    ProcessControlBlock,
    ProcessState,
    SchedulerSafePoint,
    Session,
    SessionEvent,
    ToolCall,
    UsageCollector,
    analyze_recovery,
)
from agentkernel.tool_effects import ToolEffectKind

from benchmarks.common.metrics import BenchmarkRecord
from benchmarks.common.reporter import print_json_records, write_json_records


BENCHMARK = "v0.7_runtime"


def run() -> list[BenchmarkRecord]:
    """Run all V0.7 runtime primitive benchmarks."""

    return [
        _scheduler_lifecycle(),
        _waiting_blocked_ready_transition(),
        _budget_blocking(),
        _usage_accounting(),
        _process_crash_recovery(),
        _boundary_isolation(),
    ]


def _scheduler_lifecycle() -> BenchmarkRecord:
    agent = _agent("agent-lifecycle", "session-lifecycle")
    process = ProcessControlBlock.create(
        process_id="process-lifecycle",
        agent=agent.control,
    )
    states: list[str] = [process.state.value]

    process.transition(ProcessState.READY)
    states.append(process.state.value)
    process.transition(ProcessState.RUNNING)
    states.append(process.state.value)
    process.transition(ProcessState.WAITING, wait_reason="llm")
    states.append(process.state.value)
    process.transition(ProcessState.READY)
    process.transition(ProcessState.RUNNING)
    process.transition(ProcessState.BLOCKED, blocked_reason="durable_recovery")
    states.append(process.state.value)
    process.transition(ProcessState.EXITED, exit_status="completed")
    states.append(process.state.value)

    expected = "CREATED>READY>RUNNING>WAITING>BLOCKED>EXITED"
    observed = ">".join(states)
    return BenchmarkRecord(
        benchmark=BENCHMARK,
        case="scheduler_lifecycle",
        strategy="process_control_block_state_machine",
        metrics={
            "expected_states": expected,
            "observed_states": observed,
            "final_state": process.state.value,
            "exit_status": process.exit_status,
            "wait_reason_cleared": process.wait_reason is None,
            "blocked_reason_cleared": process.blocked_reason is None,
            "success": observed == expected and process.exit_status == "completed",
        },
    )


def _waiting_blocked_ready_transition() -> BenchmarkRecord:
    agent = _agent("agent-transition", "session-transition")
    scheduler = CooperativeScheduler()
    process = scheduler.create_process(
        process_id="process-transition",
        agent=agent.control,
    )

    scheduler.dispatch("process-transition")
    scheduler.yield_process(
        "process-transition",
        ProcessState.WAITING,
        reason="tool_result",
    )
    waiting_registered = scheduler.waiting_registry.get("process-transition")
    scheduler.wake("process-transition")
    ready_after_wake = scheduler.ready_queue == ("process-transition",)
    scheduler.dispatch("process-transition")
    scheduler.yield_process(
        "process-transition",
        ProcessState.BLOCKED,
        reason="budget_review",
    )
    blocked_registered = scheduler.blocked_registry.get("process-transition")
    scheduler.unblock("process-transition")

    return BenchmarkRecord(
        benchmark=BENCHMARK,
        case="waiting_blocked_ready_transition",
        strategy="cooperative_scheduler_registries",
        metrics={
            "waiting_reason": waiting_registered,
            "ready_after_wake": ready_after_wake,
            "blocked_reason": blocked_registered,
            "final_state": process.state.value,
            "ready_queue_size": len(scheduler.ready_queue),
            "waiting_registry_size": len(scheduler.waiting_registry),
            "blocked_registry_size": len(scheduler.blocked_registry),
            "success": (
                waiting_registered == "tool_result"
                and ready_after_wake
                and blocked_registered == "budget_review"
                and process.state is ProcessState.READY
                and scheduler.ready_queue == ("process-transition",)
                and not scheduler.waiting_registry
                and not scheduler.blocked_registry
            ),
        },
    )


def _budget_blocking() -> BenchmarkRecord:
    clock = _FakeClock()
    collector = UsageCollector(clock=clock)
    scheduler = CooperativeScheduler(usage_collector=collector)
    agent = _agent(
        "agent-budget",
        "session-budget",
        budget=AgentBudget(max_token_usage=10),
    )
    process = scheduler.create_process(
        process_id="process-budget",
        agent=agent.control,
    )
    scheduler.dispatch("process-budget")
    collector.record_llm_usage(
        "process-budget",
        ModelUsage(input_tokens=7, output_tokens=4, total_tokens=11),
    )

    blocked = False
    exceeded_limit = None
    exceeded_usage = None
    try:
        scheduler.safe_point("process-budget", SchedulerSafePoint.AFTER_LLM_CALL)
    except ProcessBudgetExceeded as error:
        blocked = True
        exceeded_limit = error.exceeded.limit
        exceeded_usage = error.exceeded.usage

    return BenchmarkRecord(
        benchmark=BENCHMARK,
        case="budget_blocking",
        strategy="usage_collector_scheduler_safe_point",
        metrics={
            "budget_limit": "max_token_usage",
            "budget_maximum": 10,
            "observed_usage": exceeded_usage,
            "exceeded_limit": exceeded_limit,
            "blocked": blocked,
            "process_state": process.state.value,
            "blocked_reason": process.blocked_reason,
            "blocked_registry_size": len(scheduler.blocked_registry),
            "success": (
                blocked
                and exceeded_limit == "max_token_usage"
                and exceeded_usage == 11
                and process.state is ProcessState.BLOCKED
                and process.blocked_reason
                == "budget:process:process-budget:max_token_usage"
            ),
        },
    )


def _usage_accounting() -> BenchmarkRecord:
    clock = _FakeClock()
    collector = UsageCollector(clock=clock)

    collector.start_process("process-usage")
    collector.record_llm_usage(
        "process-usage",
        ModelUsage(input_tokens=20, output_tokens=8, total_tokens=28),
        model_cost=0.42,
    )
    collector.record_tool_call("process-usage", count=2)
    collector.record_resource_read("process-usage", 4096)
    collector.record_resource_read("process-usage", 1024)
    clock.advance(3.25)

    snapshot = collector.snapshot("process-usage")

    return BenchmarkRecord(
        benchmark=BENCHMARK,
        case="usage_accounting",
        strategy="process_usage_snapshot",
        metrics={
            "process_id": snapshot.process_id,
            "token_usage": snapshot.token_usage,
            "model_cost": snapshot.model_cost,
            "tool_calls": snapshot.tool_calls,
            "resource_reads": snapshot.resource_reads,
            "resource_bytes": snapshot.resource_bytes,
            "wall_time": snapshot.wall_time,
            "success": (
                snapshot.process_id == "process-usage"
                and snapshot.token_usage == 28
                and snapshot.model_cost == 0.42
                and snapshot.tool_calls == 2
                and snapshot.resource_reads == 2
                and snapshot.resource_bytes == 5120
                and snapshot.wall_time == 3.25
            ),
        },
    )


def _process_crash_recovery() -> BenchmarkRecord:
    agent = _agent("agent-recovery", "session-recovery")
    call = ToolCall("call-payment", "payment.charge", {"amount": 100})
    analysis = analyze_recovery(
        (
            _event(1, EventType.TURN_START, {"turn": 1}),
            _event(
                2,
                EventType.USER_MESSAGE,
                {"turn": 1, "content": "charge payment"},
            ),
            _event(3, EventType.STEP_START, {"turn": 1, "step": 1}),
            _event(
                4,
                EventType.ASSISTANT_MESSAGE,
                {
                    "turn": 1,
                    "step": 1,
                    "content": "",
                    "tool_calls": [call.as_dict()],
                },
            ),
            _event(5, EventType.TOOL_CALL, {"turn": 1, "step": 1, **call.as_dict()}),
            _event(
                6,
                EventType.TOOL_PREPARE,
                {
                    "turn": 1,
                    "step": 1,
                    "operation_id": "op-payment",
                    "tool_call_id": call.call_id,
                    "tool_name": call.name,
                    "effect_kind": ToolEffectKind.RECONCILABLE_MUTATION,
                },
            ),
            _event(
                7,
                EventType.TOOL_DISPATCH,
                {"turn": 1, "step": 1, "operation_id": "op-payment", "attempt": 1},
            ),
        )
    )
    recovered = ProcessControlBlock.from_recovery(
        process_id="process-recovered",
        agent=agent.control,
        recovery=analysis,
    )
    scheduler = CooperativeScheduler()
    scheduler.add_process(recovered)
    blocked_registered = scheduler.blocked_registry.get("process-recovered")
    no_runnable_before_unblock = False
    try:
        scheduler.schedule_next()
    except NoRunnableProcess:
        no_runnable_before_unblock = True
    scheduler.unblock("process-recovered")
    dispatched = scheduler.dispatch("process-recovered")

    operation = analysis.durable_operations[0]
    return BenchmarkRecord(
        benchmark=BENCHMARK,
        case="process_crash_recovery",
        strategy="session_replay_to_process_state",
        metrics={
            "session_status": analysis.status.value,
            "durable_operation_classification": operation.classification.value,
            "recovered_state": ProcessState.BLOCKED.value,
            "blocked_reason": blocked_registered,
            "no_runnable_before_unblock": no_runnable_before_unblock,
            "state_after_unblock_dispatch": dispatched.state.value,
            "session_truth_preserved": len(analysis.durable_operations) == 1,
            "success": (
                operation.classification
                is OperationRecoveryClassification.RECONCILE_REQUIRED
                and blocked_registered == "durable_operation_recovery"
                and no_runnable_before_unblock
                and dispatched.state is ProcessState.RUNNING
            ),
        },
    )


def _boundary_isolation() -> BenchmarkRecord:
    grant = CapabilityGrant(
        subject="agent-boundary",
        action="resource.read",
        resource_scope="artifact://boundary/**",
    )
    session = Session("session-boundary")
    agent = Agent.create(
        agent_id="agent-boundary",
        session=session,
        capabilities={"legacy.search"},
        capability_bounding_set={"legacy.search"},
        capability_grants=(grant,),
    )
    process = ProcessControlBlock.create(
        process_id="process-boundary",
        agent=agent.control,
    )
    process.transition(ProcessState.READY)
    session.append(
        EventType.USER_MESSAGE,
        {"turn": 1, "content": "session fact"},
    )

    snapshot_grant = process.capability_snapshot.capability_grants[0]
    return BenchmarkRecord(
        benchmark=BENCHMARK,
        case="agent_process_session_boundary_isolation",
        strategy="identity_and_durability_separation",
        metrics={
            "agent_id": agent.control.agent_id,
            "process_id": process.process_id,
            "session_id": session.session_id,
            "process_agent_id": process.agent_id,
            "process_session_id": process.session_id,
            "capability_subject": snapshot_grant.subject,
            "capability_subject_is_agent": snapshot_grant.subject
            == agent.control.agent_id,
            "capability_subject_is_process": snapshot_grant.subject
            == process.process_id,
            "session_event_count": len(session.events),
            "process_state_after_session_append": process.state.value,
            "success": (
                process.agent_id == agent.control.agent_id
                and process.session_id == session.session_id
                and snapshot_grant.subject == agent.control.agent_id
                and snapshot_grant.subject != process.process_id
                and len(session.events) == 1
                and process.state is ProcessState.READY
            ),
        },
    )


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, amount: float) -> None:
        self.now += amount


def _agent(
    agent_id: str,
    session_id: str,
    *,
    budget: AgentBudget | None = None,
) -> Agent:
    return Agent.create(
        agent_id=agent_id,
        session=Session(session_id),
        budget=budget,
    )


def _event(seq: int, event_type: EventType, data: dict) -> SessionEvent:
    return SessionEvent(seq=seq, type=event_type, data=data, time=float(seq))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="v0.7_runtime.json")
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()

    records = run()
    if not args.no_write:
        write_json_records(args.output, records)
    print_json_records(records)


if __name__ == "__main__":
    main()
