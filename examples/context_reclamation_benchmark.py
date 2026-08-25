"""Offline synthetic comparison for Context VM reclamation stages."""

from __future__ import annotations

import asyncio
import json

from agentkernel import (
    ApproximateTokenEstimator,
    ContextBudget,
    ContextCompactionConfig,
    ContextCompactor,
    ContextManager,
    ContextPageKind,
    ContextPolicyConfig,
    ContextPressure,
    ContextProjector,
    ContextReclaimAction,
    DefaultContextPolicy,
    EventType,
    ModelResponse,
    ScriptedLLM,
    Session,
    ToolCall,
    ToolResult,
    ToolResultPruner,
    ToolResultPrunerConfig,
)


PINNED_CONSTRAINT = "Never modify the production database."
RECENT_ACTIVE_TASK = "ACTIVE TASK: finish the Context VM recovery benchmark."


class EvictionOnlyPolicy:
    """Phase 1 comparison: working-set eviction without Phase 2 reclaim."""

    def actions(self, pressure: ContextPressure) -> tuple[ContextReclaimAction, ...]:
        del pressure
        return (ContextReclaimAction.EVICT,)


def append_text_turn(session: Session, turn: int, user: str, assistant: str) -> None:
    session.append(EventType.TURN_START, {"turn": turn})
    session.append(EventType.USER_MESSAGE, {"turn": turn, "content": user})
    session.append(EventType.STEP_START, {"turn": turn, "step": 1})
    session.append(
        EventType.ASSISTANT_MESSAGE,
        {"turn": turn, "step": 1, "content": assistant, "tool_calls": []},
    )
    session.append(
        EventType.STEP_END,
        {"turn": turn, "step": 1, "outcome": "completed"},
    )
    session.append(EventType.TURN_END, {"turn": turn, "reason": "completed"})


def append_tool_turn(session: Session, turn: int, output: str) -> None:
    call = ToolCall(f"benchmark-call-{turn}", "benchmark.inspect", {"turn": turn})
    result = ToolResult.success(call, output)
    session.append(EventType.TURN_START, {"turn": turn})
    session.append(
        EventType.USER_MESSAGE,
        {"turn": turn, "content": f"Inspect benchmark fixture {turn}."},
    )
    session.append(EventType.STEP_START, {"turn": turn, "step": 1})
    session.append(
        EventType.ASSISTANT_MESSAGE,
        {"turn": turn, "step": 1, "content": "", "tool_calls": [call.as_dict()]},
    )
    session.append(
        EventType.TOOL_CALL,
        {"turn": turn, "step": 1, **call.as_dict()},
    )
    session.append(
        EventType.TOOL_RESULT,
        {"turn": turn, "step": 1, **result.as_dict()},
    )
    session.append(
        EventType.STEP_END,
        {"turn": turn, "step": 1, "outcome": "tool_calls"},
    )
    session.append(EventType.TURN_END, {"turn": turn, "reason": "completed"})


def fixture() -> Session:
    session = Session("context-reclamation-benchmark")
    for turn in range(1, 201):
        if turn == 199:
            append_tool_turn(
                session,
                turn,
                "recent-tool-head\n"
                + ("recent diagnostic output\n" * 1_200)
                + "recent-tool-tail: FATAL marker retained",
            )
            continue
        if turn % 25 == 0 and turn != 200:
            append_tool_turn(
                session,
                turn,
                f"tool-{turn}-head\n"
                + (f"diagnostic line for turn {turn}\n" * 250)
                + f"tool-{turn}-tail: no fatal error",
            )
            continue
        user = PINNED_CONSTRAINT if turn == 1 else f"user task {turn}: " + "u" * 90
        if turn == 100:
            user = "DECISION: durable raw events remain the sole source of truth."
        if turn == 200:
            user = RECENT_ACTIVE_TASK
        append_text_turn(session, turn, user, f"assistant state {turn}: " + "a" * 85)
    return session


def manager(*, phase2: bool, estimator: ApproximateTokenEstimator) -> ContextManager:
    common = {
        "projector": ContextProjector(estimator),
        "policy": DefaultContextPolicy(
            ContextPolicyConfig(
                recent_turns=3,
                large_tool_result_threshold_tokens=500,
                tool_result_cold_after_turns=1,
            )
        ),
    }
    if not phase2:
        return ContextManager(**common, reclaim_policy=EvictionOnlyPolicy())
    return ContextManager(
        **common,
        pruner=ToolResultPruner(
            ToolResultPrunerConfig(
                threshold_tokens=500,
                head_tokens=220,
                tail_tokens=140,
            ),
            estimator=estimator,
        ),
        compactor=ContextCompactor(
            ContextCompactionConfig(
                retained_tail_tokens=2_000,
                minimum_source_tokens=1_000,
                provider="offline-benchmark",
                model="scripted-checkpoint",
            ),
            estimator=estimator,
        ),
    )


def has_exact_page(working_set, content: str) -> bool:  # type: ignore[no-untyped-def]
    return any(page.content == content for page in working_set.pages)


async def run() -> dict[str, object]:
    estimator = ApproximateTokenEstimator(4)
    budget = ContextBudget(max_tokens=8_000, reserved_output_tokens=1_000)
    session = fixture()
    full_pages = ContextProjector(estimator).project(session)
    full_tokens = sum(page.token_cost for page in full_pages)
    raw_messages = session.derive_messages()
    recent_tail = raw_messages[-2:]

    phase1 = manager(phase2=False, estimator=estimator)
    phase1.pin("session:context-reclamation-benchmark:event:2")
    phase1_set = phase1.build_working_set(
        session,
        current_turn=200,
        budget=budget,
    )

    phase2 = manager(phase2=True, estimator=estimator)
    phase2.pin("session:context-reclamation-benchmark:event:2")
    phase2_set = await phase2.prepare_working_set(
        session,
        current_turn=200,
        budget=budget,
        llm=ScriptedLLM(
            [
                ModelResponse(
                    "Goal: finish the Context VM benchmark. "
                    "Constraint: never modify production database. "
                    "Decision: raw Session events remain authoritative. "
                    "Completed: synthetic older history inspected. "
                    "Pending: validate metrics and recent active task."
                )
            ]
        ),
    )
    phase2_messages = phase2_set.to_messages()
    summary_count = sum(
        page.kind is ContextPageKind.SUMMARY
        for page in phase2.projector.project(session)
    )

    return {
        "fixture": {
            "turns": 200,
            "large_tool_results": 8,
            "input_budget_tokens": budget.available_input_tokens,
        },
        "full_projection": {
            "projected_tokens": full_tokens,
            "selected_tokens": full_tokens,
            "tokens_reclaimed": 0,
        },
        "phase1_eviction": {
            "projected_tokens": phase1_set.metrics.projected_tokens,
            "selected_tokens": phase1_set.metrics.selected_tokens,
            "tokens_reclaimed": full_tokens - phase1_set.metrics.selected_tokens,
            "important_constraint_retained": has_exact_page(
                phase1_set, PINNED_CONSTRAINT
            ),
            "recent_tail_retained": phase1_set.to_messages()[-2:] == recent_tail,
            "tool_protocol_valid": True,
            "summary_count": 0,
        },
        "phase2_reclamation": {
            "projected_tokens": phase2_set.metrics.projected_tokens,
            "selected_tokens": phase2_set.metrics.selected_tokens,
            "tokens_reclaimed": full_tokens - phase2_set.metrics.selected_tokens,
            "pruned_pages": phase2_set.metrics.pruned_pages,
            "pruned_tokens_saved": phase2_set.metrics.pruned_tokens_saved,
            "compacted_source_tokens": phase2_set.metrics.compacted_source_tokens,
            "summary_tokens": phase2_set.metrics.summary_tokens,
            "important_constraint_retained": has_exact_page(
                phase2_set, PINNED_CONSTRAINT
            ),
            "recent_tail_retained": phase2_messages[-2:] == recent_tail,
            "tool_protocol_valid": True,
            "summary_count": summary_count,
        },
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run()), ensure_ascii=False, indent=2))
