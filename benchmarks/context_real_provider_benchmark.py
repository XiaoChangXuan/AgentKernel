"""Context VM quality/resource benchmark; real network use is explicit opt-in."""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import asdict, dataclass

from agentkernel import (
    ApproximateRequestTokenAccounting,
    ApproximateTokenEstimator,
    ContextBudget,
    ContextCompactionConfig,
    ContextCompactor,
    ContextManager,
    ContextPolicyConfig,
    ContextPressure,
    ContextProjector,
    ContextReclaimAction,
    DefaultContextPolicy,
    EventType,
    LLMErrorKind,
    LLMService,
    LLMServiceError,
    Message,
    ModelRequest,
    ModelResponse,
    ScriptedLLM,
    Session,
    ToolCall,
    ToolResult,
    ToolResultPruner,
    ToolResultPrunerConfig,
)
from agentkernel.providers import OpenAICompatibleConfig, OpenAICompatibleLLM


REAL_BENCHMARK_ENV = "AGENTKERNEL_RUN_REAL_BENCHMARK"
REQUIRED_PROVIDER_ENV = (
    "AGENTKERNEL_LLM_BASE_URL",
    "AGENTKERNEL_LLM_API_KEY",
    "AGENTKERNEL_LLM_MODEL",
)
SYSTEM_PROMPT = (
    "Answer the final benchmark question from the supplied history. "
    "Return only JSON with a boolean field named success and a short reason."
)
BUDGET = ContextBudget(max_tokens=5_000, reserved_output_tokens=500)
ESTIMATOR = ApproximateTokenEstimator(4)
ACCOUNTING = ApproximateRequestTokenAccounting(estimator=ESTIMATOR)


class EvictionOnlyPolicy:
    def actions(self, pressure: ContextPressure) -> tuple[ContextReclaimAction, ...]:
        del pressure
        return (ContextReclaimAction.EVICT,)


@dataclass(frozen=True, slots=True)
class QualityCase:
    name: str
    early_fact: str
    middle_fact: str
    fatal_fact: str
    question: str
    expected_marker: str


CASES = (
    QualityCase(
        name="early_constraint",
        early_fact="CONSTRAINT: never modify production.yaml.",
        middle_fact="Decision: staging.yaml may be changed.",
        fatal_fact="FATAL: permission denied",
        question=(
            "A user asks you to edit production.yaml. Must you refuse? "
            "Set success=true only if the constraint is still known."
        ),
        expected_marker="never modify production.yaml",
    ),
    QualityCase(
        name="middle_decision",
        early_fact="CONSTRAINT: preserve all confirmed diagnostic decisions.",
        middle_fact=(
            "DECISION: the database is healthy; do not investigate the database again."
        ),
        fatal_fact="FATAL: permission denied",
        question=(
            "Should database diagnosis be resumed? Set success=true only if the "
            "confirmed decision says not to investigate it again."
        ),
        expected_marker="database is healthy",
    ),
    QualityCase(
        name="large_tool_tail",
        early_fact="CONSTRAINT: diagnose from the complete error evidence.",
        middle_fact="Decision: normal INFO lines are not the root cause.",
        fatal_fact="FATAL: permission denied",
        question=(
            "Identify the real error at the end of the large log. Set success=true "
            "only if it is permission denied."
        ),
        expected_marker="FATAL: permission denied",
    ),
)


def _append_text_turn(session: Session, turn: int, user: str, assistant: str) -> None:
    session.append(EventType.TURN_START, {"turn": turn})
    session.append(EventType.USER_MESSAGE, {"turn": turn, "content": user})
    session.append(EventType.STEP_START, {"turn": turn, "step": 1})
    session.append(
        EventType.ASSISTANT_MESSAGE,
        {"turn": turn, "step": 1, "content": assistant, "tool_calls": []},
    )
    session.append(
        EventType.STEP_END, {"turn": turn, "step": 1, "outcome": "completed"}
    )
    session.append(EventType.TURN_END, {"turn": turn, "reason": "completed"})


def _append_large_tool_turn(session: Session, turn: int, fatal: str) -> None:
    call = ToolCall(f"log-{turn}", "logs.read", {"path": "service.log"})
    output = "LOG HEADER\n" + ("INFO request completed\n" * 2_000) + fatal
    result = ToolResult.success(call, output)
    session.append(EventType.TURN_START, {"turn": turn})
    session.append(EventType.USER_MESSAGE, {"turn": turn, "content": "Read logs."})
    session.append(EventType.STEP_START, {"turn": turn, "step": 1})
    session.append(
        EventType.ASSISTANT_MESSAGE,
        {"turn": turn, "step": 1, "content": "", "tool_calls": [call.as_dict()]},
    )
    session.append(EventType.TOOL_CALL, {"turn": turn, "step": 1, **call.as_dict()})
    session.append(
        EventType.TOOL_RESULT, {"turn": turn, "step": 1, **result.as_dict()}
    )
    session.append(
        EventType.STEP_END, {"turn": turn, "step": 1, "outcome": "tool_calls"}
    )
    session.append(EventType.TURN_END, {"turn": turn, "reason": "completed"})


def _fixture(case: QualityCase, mode: str) -> Session:
    session = Session(f"benchmark-{case.name}-{mode}")
    for turn in range(1, 61):
        if turn == 1:
            user = case.early_fact
        elif turn == 25:
            user = case.middle_fact
        elif turn == 35:
            _append_large_tool_turn(session, turn, case.fatal_fact)
            continue
        elif turn == 60:
            user = case.question
        else:
            user = f"routine diagnostic turn {turn}: " + ("u" * 120)
        _append_text_turn(session, turn, user, f"routine response {turn}: " + ("a" * 110))
    return session


def _manager(*, phase23: bool) -> ContextManager:
    common = {
        "projector": ContextProjector(ESTIMATOR),
        "policy": DefaultContextPolicy(
            ContextPolicyConfig(
                recent_turns=3,
                large_tool_result_threshold_tokens=500,
                tool_result_cold_after_turns=1,
            )
        ),
    }
    if not phase23:
        return ContextManager(**common, reclaim_policy=EvictionOnlyPolicy())
    return ContextManager(
        **common,
        pruner=ToolResultPruner(
            ToolResultPrunerConfig(
                threshold_tokens=500,
                head_tokens=220,
                tail_tokens=140,
            ),
            estimator=ESTIMATOR,
        ),
        compactor=ContextCompactor(
            ContextCompactionConfig(
                retained_tail_tokens=1_800,
                minimum_source_tokens=800,
                provider="benchmark",
                model="configured-provider-or-offline-script",
            ),
            estimator=ESTIMATOR,
        ),
    )


def _offline_summary(case: QualityCase) -> str:
    return (
        "Durable context checkpoint. "
        f"{case.early_fact} {case.middle_fact} Root error: {case.fatal_fact}."
    )


async def _request_for_mode(
    case: QualityCase,
    mode: str,
    summary_llm: LLMService,
) -> tuple[Session, ModelRequest, dict[str, int]]:
    session = _fixture(case, mode)
    projected = ContextProjector(ESTIMATOR).project(session, system_prompt=SYSTEM_PROMPT)
    projected_tokens = sum(page.token_cost for page in projected)
    if mode == "full":
        request = ModelRequest(
            messages=session.derive_messages(),
            system_prompt=SYSTEM_PROMPT,
        )
        return session, request, {
            "projected_tokens": projected_tokens,
            "selected_tokens": projected_tokens,
            "pruning_count": 0,
            "compaction_count": 0,
        }

    manager = _manager(phase23=mode == "phase23")
    manager.pin(f"session:{session.session_id}:event:2")
    if mode == "phase1":
        working_set = manager.build_working_set(
            session, current_turn=60, budget=BUDGET, system_prompt=SYSTEM_PROMPT
        )
    else:
        working_set = await manager.prepare_working_set(
            session,
            current_turn=60,
            budget=BUDGET,
            llm=summary_llm,
            system_prompt=SYSTEM_PROMPT,
        )
    return session, ModelRequest(
        messages=working_set.to_messages(),
        system_prompt=working_set.system_prompt,
    ), {
        "projected_tokens": projected_tokens,
        "selected_tokens": working_set.metrics.selected_tokens,
        "pruning_count": working_set.metrics.pruned_pages,
        "compaction_count": working_set.metrics.compaction_count,
    }


def _offline_quality(request: ModelRequest, marker: str) -> bool:
    return marker.lower() in "\n".join(
        message.content for message in request.messages
    ).lower()


def _parse_quality(response: ModelResponse) -> bool:
    try:
        value = json.loads(response.content)
    except json.JSONDecodeError:
        return False
    return isinstance(value, dict) and value.get("success") is True


async def run(*, real: bool | None = None) -> dict[str, object]:
    enabled = (
        os.getenv(REAL_BENCHMARK_ENV, "").strip().lower() in {"1", "true", "yes"}
        if real is None
        else real
    )
    missing = [name for name in REQUIRED_PROVIDER_ENV if not os.getenv(name, "").strip()]
    if enabled and missing:
        return {
            "status": "prepared_not_executed",
            "reason": "missing environment variables",
            "missing": missing,
            "network_requests": 0,
        }

    provider: LLMService | None = None
    if enabled:
        provider = OpenAICompatibleLLM(OpenAICompatibleConfig.from_env())

    rows: list[dict[str, object]] = []
    for case in CASES:
        for mode in ("full", "phase1", "phase23"):
            summary_llm: LLMService = (
                provider
                if provider is not None
                else ScriptedLLM([ModelResponse(_offline_summary(case))])
            )
            started = time.perf_counter()
            session, request, metrics = await _request_for_mode(
                case, mode, summary_llm
            )
            estimate = ACCOUNTING.estimate_request(request)
            overflow_count = 0
            actual_input_tokens: int | None = None
            llm_calls = metrics["compaction_count"]
            if provider is None:
                success = _offline_quality(request, case.expected_marker)
            else:
                try:
                    response = await provider.generate(request)
                    llm_calls += 1
                    success = _parse_quality(response)
                    if response.usage is not None:
                        actual_input_tokens = response.usage.input_tokens
                except LLMServiceError as error:
                    success = False
                    if error.kind is LLMErrorKind.CONTEXT_OVERFLOW:
                        overflow_count = 1
            rows.append(
                {
                    "case": case.name,
                    "mode": mode,
                    **metrics,
                    "request_estimated_tokens": estimate.total_tokens,
                    "actual_input_tokens": actual_input_tokens,
                    "llm_calls": llm_calls,
                    "overflow_count": overflow_count,
                    "overflow_recovery_count": 0,
                    "tool_calls": 0,
                    "elapsed_seconds": round(time.perf_counter() - started, 4),
                    "success": success,
                    "durable_events": len(session.events),
                }
            )
    return {
        "status": "real_completed" if enabled else "offline_completed",
        "provider": os.getenv("AGENTKERNEL_LLM_MODEL") if enabled else "offline",
        "resource_metrics": rows,
        "quality": [
            {
                "case": case.name,
                **{
                    mode: next(
                        row["success"]
                        for row in rows
                        if row["case"] == case.name and row["mode"] == mode
                    )
                    for mode in ("full", "phase1", "phase23")
                },
            }
            for case in CASES
        ],
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run()), ensure_ascii=False, indent=2))
