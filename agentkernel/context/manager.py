"""Budgeted Context working-set selection mechanism."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from ..session import Session
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
from .projector import ContextProjector


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


class ContextManager:
    """Project, classify, select, page in, and order Context Pages."""

    def __init__(
        self,
        *,
        projector: ContextProjector | None = None,
        policy: ContextPolicy | None = None,
    ) -> None:
        self.projector = projector or ContextProjector()
        self.policy = policy or DefaultContextPolicy()
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
        } | self._requested
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
        selected_cost = sum(page.token_cost for page in selected)
        evicted_cost = sum(page.token_cost for page in evicted)
        metrics = ContextMetrics(
            projected_pages=len(pages),
            selected_pages=len(selected),
            evicted_pages=len(evicted),
            pinned_pages=sum(page.pinned for page in pages),
            projected_tokens=projected_cost,
            selected_tokens=selected_cost,
            evicted_tokens=evicted_cost,
            budget_tokens=available,
        )
        working_set = ContextWorkingSet(
            pages=selected,
            evicted_pages=evicted,
            budget=budget,
            metrics=metrics,
        )
        working_set.to_messages()
        self._requested.clear()
        return working_set


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
