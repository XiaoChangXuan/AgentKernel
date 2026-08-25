"""Replaceable deterministic Context selection policy."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol, runtime_checkable

from .model import (
    ContextPage,
    ContextPageKind,
    ContextProtocolError,
    ContextTemperature,
)


@dataclass(frozen=True, slots=True)
class ContextPolicyConfig:
    """Deterministic thresholds used by DefaultContextPolicy."""

    recent_turns: int = 2
    large_tool_result_threshold_tokens: int = 2_048
    tool_result_cold_after_turns: int = 1
    pin_current_user: bool = True

    def __post_init__(self) -> None:
        for name in (
            "recent_turns",
            "large_tool_result_threshold_tokens",
            "tool_result_cold_after_turns",
        ):
            value = getattr(self, name)
            minimum = 1 if name == "large_tool_result_threshold_tokens" else 0
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < minimum
            ):
                raise ValueError(f"{name} must be an integer >= {minimum}")
        if not isinstance(self.pin_current_user, bool):
            raise TypeError("pin_current_user must be a boolean")


@runtime_checkable
class ContextPolicy(Protocol):
    """Assign selection metadata without owning Context Page projection."""

    def apply(
        self,
        pages: tuple[ContextPage, ...],
        *,
        current_turn: int,
    ) -> tuple[ContextPage, ...]: ...


class DefaultContextPolicy:
    """Simple explainable recency and large-result policy."""

    def __init__(self, config: ContextPolicyConfig | None = None) -> None:
        self.config = config or ContextPolicyConfig()

    def apply(
        self,
        pages: tuple[ContextPage, ...],
        *,
        current_turn: int,
    ) -> tuple[ContextPage, ...]:
        if (
            isinstance(current_turn, bool)
            or not isinstance(current_turn, int)
            or current_turn < 1
        ):
            raise ValueError("current_turn must be a positive integer")
        classified: list[ContextPage] = []
        cold_groups: set[str] = set()
        for page in pages:
            if page.kind is ContextPageKind.SYSTEM:
                classified.append(
                    with_selection(
                        page,
                        True,
                        1_000,
                        ContextTemperature.PINNED,
                    )
                )
                continue
            assert page.turn is not None
            age = max(0, current_turn - page.turn)
            is_current_user = (
                page.kind is ContextPageKind.USER_MESSAGE
                and page.turn == current_turn
            )
            if is_current_user and self.config.pin_current_user:
                selected = with_selection(
                    page,
                    True,
                    900,
                    ContextTemperature.PINNED,
                )
            elif age == 0:
                selected = with_selection(
                    page,
                    False,
                    400,
                    ContextTemperature.HOT,
                )
            elif age < self.config.recent_turns:
                selected = with_selection(
                    page,
                    False,
                    300 - age,
                    ContextTemperature.HOT,
                )
            else:
                selected = with_selection(
                    page,
                    False,
                    100,
                    ContextTemperature.WARM,
                )
            classified.append(selected)
            if page.kind is ContextPageKind.TOOL_RESULT and page.atomic_group:
                if (
                    page.token_cost
                    >= self.config.large_tool_result_threshold_tokens
                    or age > self.config.tool_result_cold_after_turns
                ):
                    cold_groups.add(page.atomic_group)

        return tuple(
            with_selection(page, False, 0, ContextTemperature.COLD)
            if page.atomic_group in cold_groups and not page.pinned
            else page
            for page in classified
        )


def with_selection(
    page: ContextPage,
    pinned: bool,
    priority: int,
    temperature: ContextTemperature,
) -> ContextPage:
    return replace(
        page,
        pinned=pinned,
        priority=priority,
        temperature=temperature,
    )


def validate_policy_projection(
    projected: tuple[ContextPage, ...],
    classified: tuple[ContextPage, ...],
) -> None:
    if not isinstance(classified, tuple):
        raise ContextProtocolError("ContextPolicy must return a tuple of pages")
    raw_by_id = {page.page_id: page for page in projected}
    classified_by_id = {page.page_id: page for page in classified}
    if (
        len(raw_by_id) != len(projected)
        or len(classified_by_id) != len(classified)
        or set(raw_by_id) != set(classified_by_id)
    ):
        raise ContextProtocolError(
            "ContextPolicy must preserve every projected page identity exactly once"
        )
    for page_id, raw in raw_by_id.items():
        selected = classified_by_id[page_id]
        if _without_selection(raw) != _without_selection(selected):
            raise ContextProtocolError(
                "ContextPolicy may change only priority, temperature, and pinned"
            )


def _without_selection(page: ContextPage) -> ContextPage:
    return replace(
        page,
        priority=0,
        temperature=ContextTemperature.WARM,
        pinned=False,
    )
