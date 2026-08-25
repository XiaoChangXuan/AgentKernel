from __future__ import annotations

import asyncio
from dataclasses import asdict

import pytest

from agentkernel import (
    Agent,
    ApproximateTokenEstimator,
    ContextBudget,
    ContextCompactionConfig,
    ContextCompactor,
    ContextManager,
    ContextPageKind,
    ContextPolicyConfig,
    ContextPressureConfig,
    ContextPressureState,
    ContextProjector,
    DefaultAgentLoop,
    DefaultContextPolicy,
    EventType,
    JsonlSessionPersistence,
    MessageRole,
    ModelResponse,
    PromptService,
    ScriptedLLM,
    Session,
    SessionStatus,
    ToolResultPruner,
    ToolResultPrunerConfig,
    ToolRegistry,
)
from agentkernel.context.compaction import context_page_fingerprint

from tests.context_support import append_text_turn, append_tool_turn


ESTIMATOR = ApproximateTokenEstimator(1)
BUDGET = ContextBudget(max_tokens=650, reserved_output_tokens=0)


def reclamation_manager(*, retained_tail_tokens: int = 240) -> ContextManager:
    return ContextManager(
        projector=ContextProjector(ESTIMATOR),
        policy=DefaultContextPolicy(
            ContextPolicyConfig(
                recent_turns=1,
                large_tool_result_threshold_tokens=100,
                tool_result_cold_after_turns=0,
            )
        ),
        pressure_config=ContextPressureConfig(
            pressured_ratio=0.70,
            critical_ratio=0.85,
            target_ratio=0.65,
        ),
        pruner=ToolResultPruner(
            ToolResultPrunerConfig(
                threshold_tokens=100,
                head_tokens=35,
                tail_tokens=35,
            ),
            estimator=ESTIMATOR,
        ),
        compactor=ContextCompactor(
            ContextCompactionConfig(
                retained_tail_tokens=retained_tail_tokens,
                minimum_source_tokens=100,
                provider="offline-test",
                model="scripted-summary",
            ),
            estimator=ESTIMATOR,
        ),
    )


def append_history(session: Session, start: int, stop: int) -> None:
    for turn in range(start, stop + 1):
        append_text_turn(
            session,
            turn,
            f"user-{turn}:" + ("u" * 105),
            f"assistant-{turn}:" + ("a" * 100),
        )


def test_pressure_is_derived_from_explicit_budget_resources() -> None:
    session = Session("under-budget")
    append_text_turn(session, 1, "small", "answer")
    manager = reclamation_manager()
    llm = ScriptedLLM([ModelResponse("unused")])

    pressure = manager.pressure(session, current_turn=1, budget=BUDGET)
    assert pressure.state is ContextPressureState.NORMAL
    assert pressure.input_budget_tokens == BUDGET.available_input_tokens
    assert pressure.reserved_output_tokens == BUDGET.reserved_output_tokens
    assert llm.requests == []


def test_under_budget_prepare_makes_no_pruning_or_compaction_call() -> None:
    session = Session("under-budget-async")
    append_text_turn(session, 1, "small", "answer")
    manager = reclamation_manager()
    llm = ScriptedLLM([ModelResponse("unused")])

    working_set = asyncio.run(
        manager.prepare_working_set(
            session,
            current_turn=1,
            budget=BUDGET,
            llm=llm,
        )
    )

    assert working_set.metrics.pressure_state is ContextPressureState.NORMAL
    assert working_set.metrics.pruned_pages == 0
    assert working_set.metrics.compaction_count == 0
    assert llm.requests == []


def test_tool_result_pruning_is_deterministic_and_preserves_error_tail() -> None:
    session = Session("pruning")
    output = "HEADER\n" + ("normal output\n" * 4_000) + "FATAL ERROR: disk full"
    append_tool_turn(session, 1, output=output)
    projector = ContextProjector(ESTIMATOR)
    raw_messages = session.derive_messages()
    raw_page = next(
        page
        for page in projector.project(session)
        if page.kind is ContextPageKind.TOOL_RESULT
    )
    pruner = ToolResultPruner(
        ToolResultPrunerConfig(
            threshold_tokens=200,
            head_tokens=90,
            tail_tokens=70,
        ),
        estimator=ESTIMATOR,
    )

    first = pruner.prune(raw_page)
    second = pruner.prune(raw_page)

    assert first == second
    assert first.page_id == raw_page.page_id
    assert first.token_cost < raw_page.token_cost
    assert "HEADER" in first.content
    assert "omitted" in first.content
    assert "FATAL ERROR: disk full" in first.content
    assert first.pruning is not None
    assert first.pruning.source_page_id == raw_page.page_id
    assert first.pruning.original_token_cost == raw_page.token_cost
    assert session.derive_messages() == raw_messages


def test_compaction_reduces_tokens_and_preserves_provenance_and_tail() -> None:
    session = Session("compaction")
    append_history(session, 1, 8)
    manager = reclamation_manager()
    raw_messages = session.derive_messages()
    recent_messages = raw_messages[-2:]
    before_tokens = sum(page.token_cost for page in manager.projector.project(session))
    llm = ScriptedLLM([ModelResponse("Goal and constraints retained; continue next step.")])

    working_set = asyncio.run(
        manager.prepare_working_set(
            session,
            current_turn=8,
            budget=BUDGET,
            llm=llm,
        )
    )

    summaries = [
        page for page in manager.projector.project(session)
        if page.kind is ContextPageKind.SUMMARY
    ]
    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.summary is not None
    assert summary.summary.source_start_seq == min(summary.summary.source_event_seqs)
    assert summary.summary.source_end_seq == max(summary.summary.source_event_seqs)
    assert summary.summary.source_page_ids
    assert summary.summary.source_token_cost > summary.summary.summary_token_cost
    assert summary.summary.provider == "offline-test"
    assert summary.summary.model == "scripted-summary"
    assert working_set.metrics.projected_tokens < before_tokens
    assert working_set.metrics.selected_tokens <= BUDGET.available_input_tokens
    assert working_set.metrics.compaction_count == 1
    assert working_set.metrics.compacted_source_tokens > 0
    assert working_set.metrics.summary_tokens == summary.token_cost
    assert working_set.to_messages()[-2:] == recent_messages
    assert session.derive_messages() == raw_messages


def test_pinned_constraint_survives_compaction_verbatim() -> None:
    session = Session("pinned")
    constraint = "永远不要修改 production database"
    append_text_turn(session, 1, constraint, "acknowledged")
    append_history(session, 2, 10)
    manager = reclamation_manager()
    manager.pin("session:pinned:event:2")
    llm = ScriptedLLM([ModelResponse("Older work checkpoint.")])

    working_set = asyncio.run(
        manager.prepare_working_set(
            session,
            current_turn=10,
            budget=BUDGET,
            llm=llm,
        )
    )

    pinned = next(page for page in working_set.pages if page.page_id.endswith(":event:2"))
    assert pinned.pinned
    assert pinned.content == constraint
    summary = next(
        page for page in manager.projector.project(session)
        if page.kind is ContextPageKind.SUMMARY
    )
    assert "session:pinned:event:2" not in summary.summary.source_page_ids  # type: ignore[union-attr]


def test_compaction_range_keeps_tool_call_and_result_atomic() -> None:
    session = Session("atomic")
    append_tool_turn(session, 1, output="tool output " * 30)
    append_history(session, 2, 6)
    manager = reclamation_manager(retained_tail_tokens=200)
    pages = manager.policy.apply(
        manager.projector.project(session),
        current_turn=6,
    )
    selected = manager.compactor.select_range(pages)

    assert selected is not None
    tool_pages = [page for page in pages if page.atomic_group]
    included = [page.page_id in selected.source_page_ids for page in tool_pages]
    assert included
    assert all(included) or not any(included)


def test_compaction_aborts_when_source_policy_identity_changes() -> None:
    session = Session("range-stability")
    append_history(session, 1, 8)
    manager = reclamation_manager()
    classified = manager.policy.apply(
        manager.projector.project(session),
        current_turn=8,
    )
    selected = manager.compactor.select_range(classified)
    assert selected is not None
    source_page_id = selected.source_page_ids[0]

    def change_pin_during_summary(_request):  # type: ignore[no-untyped-def]
        manager.pin(source_page_id)
        return ModelResponse("This checkpoint must not commit.")

    with pytest.raises(RuntimeError, match="source range changed"):
        asyncio.run(
            manager.prepare_working_set(
                session,
                current_turn=8,
                budget=BUDGET,
                llm=ScriptedLLM([change_pin_during_summary]),
            )
        )

    assert all(
        event.type is not EventType.CONTEXT_SUMMARY_CREATED
        for event in session.events
    )
    assert session.events[-1].type is EventType.CONTEXT_COMPACTION_ABORTED
    assert session.recovery_analysis.status is SessionStatus.COMPLETED


def test_crash_before_summary_commit_leaves_raw_projection_active(tmp_path) -> None:
    path = tmp_path / "before-summary.jsonl"
    session = Session("crash-before", JsonlSessionPersistence(path))
    append_history(session, 1, 5)
    projector = ContextProjector(ESTIMATOR)
    compactor = ContextCompactor(
        ContextCompactionConfig(retained_tail_tokens=200, minimum_source_tokens=100),
        estimator=ESTIMATOR,
    )
    raw_pages = projector.project(session)
    selected = compactor.select_range(raw_pages)
    assert selected is not None
    identity = {
        "compaction_id": "interrupted-compaction",
        "summary_page_id": "session:crash-before:summary:interrupted-compaction",
        "source_start_seq": selected.source_start_seq,
        "source_end_seq": selected.source_end_seq,
        "source_page_ids": list(selected.source_page_ids),
        "source_event_seqs": list(selected.source_event_seqs),
        "source_token_cost": selected.source_token_cost,
        "original_source_token_cost": selected.original_source_token_cost,
        "source_fingerprint": context_page_fingerprint(selected.pages),
        "parent_summary_page_ids": [],
    }
    session.append(EventType.CONTEXT_COMPACTION_REQUESTED, identity)
    session.append(EventType.CONTEXT_COMPACTION_STARTED, identity)
    session.flush()
    before = projector.project(session)
    session.close()

    restored = Session.load("crash-before", JsonlSessionPersistence(path))
    try:
        assert restored.recovery_analysis.status is SessionStatus.INTERRUPTED
        assert restored.recovery_analysis.active_compaction_id == "interrupted-compaction"
        assert projector.project(restored) == before == raw_pages
        assert all(page.kind is not ContextPageKind.SUMMARY for page in before)
    finally:
        restored.close()


def test_durable_summary_replays_identically_after_restart(tmp_path) -> None:
    path = tmp_path / "durable-summary.jsonl"
    session = Session("durable-summary", JsonlSessionPersistence(path))
    append_history(session, 1, 8)
    manager = reclamation_manager()
    asyncio.run(
        manager.prepare_working_set(
            session,
            current_turn=8,
            budget=BUDGET,
            llm=ScriptedLLM([ModelResponse("Durable checkpoint.")]),
        )
    )
    before_pages = manager.projector.project(session)
    before_working_set = manager.build_working_set(
        session,
        current_turn=8,
        budget=BUDGET,
    )
    session.close()

    restored = Session.load("durable-summary", JsonlSessionPersistence(path))
    try:
        after_manager = reclamation_manager()
        after_pages = after_manager.projector.project(restored)
        after_working_set = after_manager.build_working_set(
            restored,
            current_turn=8,
            budget=BUDGET,
        )
        assert restored.recovery_analysis.status is SessionStatus.COMPLETED
        assert after_pages == before_pages
        assert after_working_set == before_working_set
        assert asdict(after_working_set.metrics) == asdict(before_working_set.metrics)
    finally:
        restored.close()


def test_rolling_compaction_replaces_prior_summary() -> None:
    session = Session("rolling")
    append_history(session, 1, 8)
    manager = reclamation_manager()
    llm = ScriptedLLM(
        [
            ModelResponse("Checkpoint S1."),
            ModelResponse("Checkpoint S2 with new work."),
        ]
    )
    first = asyncio.run(
        manager.prepare_working_set(
            session,
            current_turn=8,
            budget=BUDGET,
            llm=llm,
        )
    )
    first_summary = next(
        page for page in manager.projector.project(session)
        if page.kind is ContextPageKind.SUMMARY
    )
    append_history(session, 9, 15)

    second = asyncio.run(
        manager.prepare_working_set(
            session,
            current_turn=15,
            budget=BUDGET,
            llm=llm,
        )
    )

    summaries = [
        page for page in manager.projector.project(session)
        if page.kind is ContextPageKind.SUMMARY
    ]
    assert len(summaries) == 1
    assert summaries[0].page_id != first_summary.page_id
    assert summaries[0].summary is not None
    assert summaries[0].summary.parent_summary_page_ids == (first_summary.page_id,)
    assert second.metrics.compaction_count == 2
    assert second.metrics.projected_tokens <= first.metrics.projected_tokens + 250
    assert len(llm.requests) == 2
    assert all(message.role is not MessageRole.TOOL for message in second.to_messages())


def test_default_loop_uses_context_compaction_service_seam() -> None:
    session = Session("loop-compaction")
    append_history(session, 1, 8)
    agent = Agent.create(agent_id="loop-agent", session=session)
    manager = reclamation_manager()

    def final_answer(request):  # type: ignore[no-untyped-def]
        assert any("durable context checkpoint" in item.content for item in request.messages)
        assert request.messages[-1].content == "continue the active task"
        return ModelResponse("continued")

    llm = ScriptedLLM(
        [
            ModelResponse("Loop integration checkpoint."),
            final_answer,
        ]
    )
    loop = DefaultAgentLoop(
        llm=llm,
        tools=ToolRegistry(),
        prompt=PromptService(),
        context=manager,
        context_budget=BUDGET,
    )

    answer = asyncio.run(loop.run(agent, "continue the active task"))

    assert answer == "continued"
    assert len(llm.requests) == 2
    assert session.recovery_analysis.completed_compaction_ids
