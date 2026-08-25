"""Benchmark V0.4 Context VM against simple history strategies."""

from __future__ import annotations

import argparse
import asyncio

from agentkernel import (
    ApproximateRequestTokenAccounting,
    ApproximateTokenEstimator,
    ContextBudget,
    ContextCompactionConfig,
    ContextCompactor,
    ContextManager,
    ContextPolicyConfig,
    ContextProjector,
    DefaultContextPolicy,
    Message,
    ModelRequest,
    ModelResponse,
    ScriptedLLM,
    ToolResultPruner,
    ToolResultPrunerConfig,
)

from benchmarks.common.metrics import BenchmarkRecord, Timer
from benchmarks.common.reporter import print_json_records, write_json_records
from benchmarks.context_vm.fixture import (
    SYSTEM_PROMPT,
    build_context_fixture,
    durable_summary,
)


BENCHMARK = "context_vm"
ESTIMATOR = ApproximateTokenEstimator(4)
ACCOUNTING = ApproximateRequestTokenAccounting(estimator=ESTIMATOR)
BUDGET = ContextBudget(max_tokens=6_000, reserved_output_tokens=500)


def run() -> list[BenchmarkRecord]:
    fixture = build_context_fixture(1000)
    return [
        _full_history(fixture),
        _simple_summary(fixture),
        _replacement_history(fixture),
        asyncio.run(_agentkernel_context_vm(fixture)),
    ]


def _full_history(fixture) -> BenchmarkRecord:  # type: ignore[no-untyped-def]
    timer = Timer()
    request = ModelRequest(
        messages=fixture.session.derive_messages(),
        system_prompt=SYSTEM_PROMPT,
    )
    estimate = ACCOUNTING.estimate_request(request)
    return BenchmarkRecord(
        benchmark=BENCHMARK,
        case="1000_turn_agent",
        strategy="full_history",
        metrics={
            "context_tokens": estimate.total_tokens,
            "reclaim_tokens": 0,
            "compaction_cost": 0,
            "final_correctness": _contains_all(request, fixture.markers),
            "recovery_ability": True,
            "latency_ms": timer.elapsed_ms(),
            "success": _contains_all(request, fixture.markers),
        },
    )


def _simple_summary(fixture) -> BenchmarkRecord:  # type: ignore[no-untyped-def]
    timer = Timer()
    messages = list(fixture.session.derive_messages())
    summary = "Earlier turns summarized as routine diagnostic work."
    request = ModelRequest(
        messages=(Message.user(summary), *messages[-20:]),
        system_prompt=SYSTEM_PROMPT,
    )
    estimate = ACCOUNTING.estimate_request(request)
    full_tokens = _full_history_tokens(fixture)
    correct = _contains_all(request, fixture.markers)
    return BenchmarkRecord(
        benchmark=BENCHMARK,
        case="1000_turn_agent",
        strategy="simple_summary",
        metrics={
            "context_tokens": estimate.total_tokens,
            "reclaim_tokens": max(0, full_tokens - estimate.total_tokens),
            "compaction_cost": 0,
            "final_correctness": correct,
            "recovery_ability": False,
            "latency_ms": timer.elapsed_ms(),
            "success": correct,
        },
    )


def _replacement_history(fixture) -> BenchmarkRecord:  # type: ignore[no-untyped-def]
    timer = Timer()
    messages = list(fixture.session.derive_messages())
    replacement = "Replacement checkpoint: " + durable_summary()
    request = ModelRequest(
        messages=(Message.user(replacement), *messages[-20:]),
        system_prompt=SYSTEM_PROMPT,
    )
    estimate = ACCOUNTING.estimate_request(request)
    full_tokens = _full_history_tokens(fixture)
    correct = _contains_all(request, fixture.markers)
    return BenchmarkRecord(
        benchmark=BENCHMARK,
        case="1000_turn_agent",
        strategy="replacement_history",
        metrics={
            "context_tokens": estimate.total_tokens,
            "reclaim_tokens": max(0, full_tokens - estimate.total_tokens),
            "compaction_cost": 0,
            "final_correctness": correct,
            "recovery_ability": False,
            "latency_ms": timer.elapsed_ms(),
            "success": correct,
        },
    )


async def _agentkernel_context_vm(fixture) -> BenchmarkRecord:  # type: ignore[no-untyped-def]
    timer = Timer()
    manager = ContextManager(
        projector=ContextProjector(ESTIMATOR),
        policy=DefaultContextPolicy(
            ContextPolicyConfig(
                recent_turns=5,
                large_tool_result_threshold_tokens=500,
                tool_result_cold_after_turns=1,
            )
        ),
        pruner=ToolResultPruner(
            ToolResultPrunerConfig(
                threshold_tokens=500,
                head_tokens=220,
                tail_tokens=180,
            ),
            estimator=ESTIMATOR,
        ),
        compactor=ContextCompactor(
            ContextCompactionConfig(
                retained_tail_tokens=2_000,
                minimum_source_tokens=1_000,
                provider="offline-scripted",
                model="fake-llm",
            ),
            estimator=ESTIMATOR,
        ),
    )
    working_set = await manager.prepare_working_set(
        fixture.session,
        current_turn=1000,
        budget=BUDGET,
        llm=ScriptedLLM([ModelResponse(durable_summary())]),
        system_prompt=SYSTEM_PROMPT,
    )
    request = ModelRequest(
        messages=working_set.to_messages(),
        system_prompt=working_set.system_prompt,
    )
    correct = _contains_all(request, fixture.markers)
    metrics = working_set.metrics
    return BenchmarkRecord(
        benchmark=BENCHMARK,
        case="1000_turn_agent",
        strategy="agentkernel_context_vm",
        metrics={
            "context_tokens": metrics.selected_tokens,
            "reclaim_tokens": metrics.reclaim_tokens_saved,
            "compaction_cost": metrics.compacted_source_tokens + metrics.summary_tokens,
            "compaction_count": metrics.compaction_count,
            "pruned_pages": metrics.pruned_pages,
            "final_correctness": correct,
            "recovery_ability": (
                fixture.session.recovery_analysis.status.value == "completed"
                and metrics.compaction_count > 0
            ),
            "latency_ms": timer.elapsed_ms(),
            "success": correct,
        },
    )


def _contains_all(request: ModelRequest, markers: tuple[str, ...]) -> bool:
    content = "\n".join(message.content for message in request.messages)
    return all(marker in content for marker in markers)


def _full_history_tokens(fixture) -> int:  # type: ignore[no-untyped-def]
    return ACCOUNTING.estimate_request(
        ModelRequest(
            messages=fixture.session.derive_messages(),
            system_prompt=SYSTEM_PROMPT,
        )
    ).total_tokens


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="context_vm.json")
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()

    records = run()
    if not args.no_write:
        write_json_records(args.output, records)
    print_json_records(records)


if __name__ == "__main__":
    main()
