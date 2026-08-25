"""Budgeted Context working-set selection mechanism."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from ..events import EventType
from ..llm import LLMService
from ..session import Session
from .compaction import ContextCompactor
from .model import (
    ContextBudget,
    ContextBudgetExceeded,
    ContextMetrics,
    ContextPage,
    ContextPageNotFound,
    ContextProtocolError,
    ContextTemperature,
    ContextWorkingSet,
)
from .policy import (
    ContextPolicy,
    DefaultContextPolicy,
    validate_policy_projection,
    with_selection,
)
from .pressure import (
    ContextPressure,
    ContextPressureConfig,
    ContextReclaimAction,
    ContextReclaimPolicy,
    DefaultContextReclaimPolicy,
    assess_context_pressure,
)
from .projector import ContextProjector
from .pruning import ToolResultPruner


@runtime_checkable
class ContextService(Protocol):
    """Replaceable Context VM mechanism consumed by DefaultAgentLoop."""

    def build_working_set(
        self,
        session: Session,
        *,
        current_turn: int,
        budget: ContextBudget,
        system_prompt: str | None = None,
    ) -> ContextWorkingSet: ...

    async def prepare_working_set(
        self,
        session: Session,
        *,
        current_turn: int,
        budget: ContextBudget,
        llm: LLMService,
        system_prompt: str | None = None,
    ) -> ContextWorkingSet: ...


class ContextManager:
    """Project, classify, select, page in, and order Context Pages."""

    def __init__(
        self,
        *,
        projector: ContextProjector | None = None,
        policy: ContextPolicy | None = None,
        pressure_config: ContextPressureConfig | None = None,
        reclaim_policy: ContextReclaimPolicy | None = None,
        pruner: ToolResultPruner | None = None,
        compactor: ContextCompactor | None = None,
    ) -> None:
        self.projector = projector or ContextProjector()
        self.policy = policy or DefaultContextPolicy()
        self.pressure_config = pressure_config or ContextPressureConfig()
        self.reclaim_policy = reclaim_policy or DefaultContextReclaimPolicy()
        self.pruner = pruner or ToolResultPruner(estimator=self.projector.estimator)
        self.compactor = compactor or ContextCompactor(estimator=self.projector.estimator)
        self._manually_pinned: set[str] = set()
        self._requested: set[str] = set()

    def pin(self, page_id: str) -> None:
        self._manually_pinned.add(_checked_page_id(page_id))

    def unpin(self, page_id: str) -> None:
        self._manually_pinned.discard(_checked_page_id(page_id))

    def request_page(self, page_id: str) -> None:
        """Force one available page into the next successful working set."""

        self._requested.add(_checked_page_id(page_id))

    def build_working_set(
        self,
        session: Session,
        *,
        current_turn: int,
        budget: ContextBudget,
        system_prompt: str | None = None,
    ) -> ContextWorkingSet:
        """Build synchronously with eviction and deterministic pruning only."""

        working_set, _, _ = self._build_components(
            session,
            current_turn=current_turn,
            budget=budget,
            system_prompt=system_prompt,
        )
        self._requested.clear()
        return working_set

    async def prepare_working_set(
        self,
        session: Session,
        *,
        current_turn: int,
        budget: ContextBudget,
        llm: LLMService,
        system_prompt: str | None = None,
    ) -> ContextWorkingSet:
        """Run the reclaim pipeline, including durable compaction when policy asks."""

        working_set, initial_pressure, pages = self._build_components(
            session,
            current_turn=current_turn,
            budget=budget,
            system_prompt=system_prompt,
        )
        actions = self.reclaim_policy.actions(initial_pressure)
        if (
            ContextReclaimAction.COMPACT in actions
            and working_set.metrics.projected_tokens > initial_pressure.target_tokens
        ):
            def visible_pages() -> tuple[ContextPage, ...]:
                _, _, current = self._build_components(
                    session,
                    current_turn=current_turn,
                    budget=budget,
                    system_prompt=system_prompt,
                )
                return tuple(current)

            result = await self.compactor.compact(
                session,
                tuple(pages),
                llm,
                visible_pages=visible_pages,
                system_prompt=system_prompt,
            )
            if result is not None:
                working_set, _, _ = self._build_components(
                    session,
                    current_turn=current_turn,
                    budget=budget,
                    system_prompt=system_prompt,
                )
        self._requested.clear()
        return working_set

    def pressure(
        self,
        session: Session,
        *,
        current_turn: int,
        budget: ContextBudget,
        system_prompt: str | None = None,
    ) -> ContextPressure:
        """Report pressure without performing model-backed compaction."""

        _, pressure, _ = self._build_components(
            session,
            current_turn=current_turn,
            budget=budget,
            system_prompt=system_prompt,
        )
        return pressure

    def _build_components(
        self,
        session: Session,
        *,
        current_turn: int,
        budget: ContextBudget,
        system_prompt: str | None,
    ) -> tuple[ContextWorkingSet, ContextPressure, list[ContextPage]]:
        projected = self.projector.project(session, system_prompt=system_prompt)
        classified = self.policy.apply(projected, current_turn=current_turn)
        validate_policy_projection(projected, classified)
        pages = list(classified)
        page_ids = {page.page_id for page in pages}
        missing = (self._manually_pinned | self._requested) - page_ids
        if missing:
            raise ContextPageNotFound(
                f"context page is not available: {sorted(missing)[0]}"
            )
        pages = [
            with_selection(
                page,
                True,
                max(page.priority, 950),
                ContextTemperature.PINNED,
            )
            if page.page_id in self._manually_pinned
            else page
            for page in pages
        ]
        raw_selected, _ = _select_pages(pages, budget, requested=self._requested)
        initial_pressure = assess_context_pressure(
            projected_tokens=sum(page.token_cost for page in pages),
            working_set_tokens=sum(page.token_cost for page in raw_selected),
            budget=budget,
            config=self.pressure_config,
        )
        actions = self.reclaim_policy.actions(initial_pressure)
        if ContextReclaimAction.PRUNE_TOOL_RESULTS in actions:
            pages = [
                self.pruner.prune(page)
                if not page.pinned and page.page_id not in self._requested
                else page
                for page in pages
            ]

        selected, evicted = _select_pages(pages, budget, requested=self._requested)
        selected_cost = sum(page.token_cost for page in selected)
        projected_cost = sum(page.token_cost for page in pages)
        final_pressure = assess_context_pressure(
            projected_tokens=projected_cost,
            working_set_tokens=selected_cost,
            budget=budget,
            config=self.pressure_config,
        )
        pruned = [page.pruning for page in pages if page.pruning is not None]
        summaries = [page.summary for page in pages if page.summary is not None]
        pruned_saved = sum(
            item.original_token_cost - item.retained_token_cost for item in pruned
        )
        compacted_source_tokens = sum(
            item.original_source_token_cost for item in summaries
        )
        summary_tokens = sum(item.summary_token_cost for item in summaries)
        compacted_saved = compacted_source_tokens - summary_tokens
        compaction_count = sum(
            event.type is EventType.CONTEXT_COMPACTION_COMPLETED
            for event in session.events
        )
        metrics = ContextMetrics(
            projected_pages=len(pages),
            selected_pages=len(selected),
            evicted_pages=len(evicted),
            pinned_pages=sum(page.pinned for page in pages),
            projected_tokens=projected_cost,
            selected_tokens=selected_cost,
            evicted_tokens=sum(page.token_cost for page in evicted),
            budget_tokens=budget.available_input_tokens,
            pressure_state=final_pressure.state,
            pruned_pages=len(pruned),
            pruned_tokens_saved=pruned_saved,
            compacted_pages=sum(len(item.source_event_seqs) for item in summaries),
            compacted_source_tokens=compacted_source_tokens,
            summary_tokens=summary_tokens,
            reclaim_tokens_saved=pruned_saved + compacted_saved,
            compaction_count=compaction_count,
        )
        working_set = ContextWorkingSet(
            pages=selected,
            evicted_pages=evicted,
            budget=budget,
            metrics=metrics,
        )
        working_set.to_messages()
        return working_set, initial_pressure, pages


def _select_pages(
    pages: list[ContextPage],
    budget: ContextBudget,
    *,
    requested: set[str],
) -> tuple[tuple[ContextPage, ...], tuple[ContextPage, ...]]:
    by_id = {page.page_id: page for page in pages}
    units: dict[str, set[str]] = {}
    unit_by_page: dict[str, str] = {}
    for page in pages:
        unit_id = page.atomic_group or page.page_id
        units.setdefault(unit_id, set()).add(page.page_id)
        unit_by_page[page.page_id] = unit_id
    _expand_page_closure(
        by_id,
        by_id=by_id,
        units=units,
        unit_by_page=unit_by_page,
    )

    mandatory_ids = {
        page.page_id for page in pages if page.pinned
    } | requested
    selected_ids = _expand_page_closure(
        mandatory_ids,
        by_id=by_id,
        units=units,
        unit_by_page=unit_by_page,
    )
    available = budget.available_input_tokens
    mandatory_cost = sum(by_id[item].token_cost for item in selected_ids)
    if mandatory_cost > available:
        raise ContextBudgetExceeded(
            required_tokens=mandatory_cost,
            available_tokens=available,
        )

    projected_cost = sum(page.token_cost for page in pages)
    if projected_cost <= available:
        selected_ids = set(by_id)
    else:
        candidate_units = [
            unit_id
            for unit_id, members in units.items()
            if members.isdisjoint(selected_ids)
        ]
        candidate_units.sort(
            key=lambda unit_id: _unit_rank(units[unit_id], by_id),
            reverse=True,
        )
        selected_cost = mandatory_cost
        for unit_id in candidate_units:
            closure = _expand_page_closure(
                units[unit_id],
                by_id=by_id,
                units=units,
                unit_by_page=unit_by_page,
            )
            additions = closure - selected_ids
            addition_cost = sum(by_id[item].token_cost for item in additions)
            if selected_cost + addition_cost > available:
                continue
            selected_ids.update(additions)
            selected_cost += addition_cost

    selected = tuple(
        sorted(
            (by_id[item] for item in selected_ids),
            key=lambda page: page.created_seq,
        )
    )
    evicted = tuple(
        page
        for page in sorted(pages, key=lambda item: item.created_seq)
        if page.page_id not in selected_ids
    )
    return selected, evicted


def _checked_page_id(page_id: str) -> str:
    if not isinstance(page_id, str) or not page_id:
        raise ValueError("page_id must be a non-empty string")
    return page_id


def _expand_page_closure(
    initial: Iterable[str],
    *,
    by_id: dict[str, ContextPage],
    units: dict[str, set[str]],
    unit_by_page: dict[str, str],
) -> set[str]:
    selected: set[str] = set()
    pending = list(initial)
    while pending:
        page_id = pending.pop()
        page = by_id.get(page_id)
        if page is None:
            raise ContextProtocolError(f"page dependency is missing: {page_id}")
        unit_members = units[unit_by_page[page_id]]
        for member_id in unit_members:
            if member_id in selected:
                continue
            selected.add(member_id)
            pending.extend(by_id[member_id].dependencies)
    return selected


def _unit_rank(
    page_ids: set[str],
    by_id: dict[str, ContextPage],
) -> tuple[int, int, int, str]:
    temperatures = {
        ContextTemperature.PINNED: 4,
        ContextTemperature.HOT: 3,
        ContextTemperature.WARM: 2,
        ContextTemperature.COLD: 1,
    }
    pages = [by_id[item] for item in page_ids]
    return (
        max(temperatures[page.temperature] for page in pages),
        max(page.priority for page in pages),
        max(page.created_seq for page in pages),
        min(page.page_id for page in pages),
    )
