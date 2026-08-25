"""Context pressure measurement and replaceable reclaim policy."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from .model import ContextBudget, ContextPressureState


class ContextReclaimAction(StrEnum):
    """Ordered reclaim mechanisms understood by ContextManager."""

    EVICT = "evict"
    PRUNE_TOOL_RESULTS = "prune_tool_results"
    COMPACT = "compact"


@dataclass(frozen=True, slots=True)
class ContextPressureConfig:
    """Budget-relative pressure thresholds and desired post-reclaim headroom."""

    pressured_ratio: float = 0.75
    critical_ratio: float = 0.90
    target_ratio: float = 0.70

    def __post_init__(self) -> None:
        if not 0 < self.target_ratio <= self.pressured_ratio:
            raise ValueError("target_ratio must be positive and <= pressured_ratio")
        if not self.pressured_ratio < self.critical_ratio < 1:
            raise ValueError(
                "pressure ratios must satisfy pressured_ratio < critical_ratio < 1"
            )


@dataclass(frozen=True, slots=True)
class ContextPressure:
    """Measured Context resources for one projection and working set."""

    state: ContextPressureState
    projected_tokens: int
    working_set_tokens: int
    input_budget_tokens: int
    reserved_output_tokens: int
    target_tokens: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "state", ContextPressureState(self.state))
        for name in (
            "projected_tokens",
            "working_set_tokens",
            "input_budget_tokens",
            "reserved_output_tokens",
            "target_tokens",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"ContextPressure {name} must be non-negative")

    @property
    def tokens_over_target(self) -> int:
        return max(0, self.projected_tokens - self.target_tokens)


@runtime_checkable
class ContextReclaimPolicy(Protocol):
    """Choose mechanisms for a measured pressure state."""

    def actions(self, pressure: ContextPressure) -> tuple[ContextReclaimAction, ...]: ...


class DefaultContextReclaimPolicy:
    """Prefer deterministic cheap reclaim before model-backed compaction."""

    def actions(self, pressure: ContextPressure) -> tuple[ContextReclaimAction, ...]:
        if pressure.state is ContextPressureState.NORMAL:
            return ()
        if pressure.state is ContextPressureState.PRESSURED:
            return (ContextReclaimAction.EVICT,)
        if pressure.state is ContextPressureState.CRITICAL:
            return (
                ContextReclaimAction.EVICT,
                ContextReclaimAction.PRUNE_TOOL_RESULTS,
            )
        return (
            ContextReclaimAction.EVICT,
            ContextReclaimAction.PRUNE_TOOL_RESULTS,
            ContextReclaimAction.COMPACT,
        )


def assess_context_pressure(
    *,
    projected_tokens: int,
    working_set_tokens: int,
    budget: ContextBudget,
    config: ContextPressureConfig,
    force_overflow: bool = False,
) -> ContextPressure:
    """Derive one pressure state from explicit projected and physical resources."""

    for name, value in (
        ("projected_tokens", projected_tokens),
        ("working_set_tokens", working_set_tokens),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be non-negative")
    available = budget.available_input_tokens
    target = int(available * config.target_ratio)
    if force_overflow or projected_tokens > available:
        state = ContextPressureState.OVERFLOW
    elif projected_tokens > int(available * config.critical_ratio):
        state = ContextPressureState.CRITICAL
    elif projected_tokens > int(available * config.pressured_ratio):
        state = ContextPressureState.PRESSURED
    else:
        state = ContextPressureState.NORMAL
    return ContextPressure(
        state=state,
        projected_tokens=projected_tokens,
        working_set_tokens=working_set_tokens,
        input_budget_tokens=available,
        reserved_output_tokens=budget.reserved_output_tokens,
        target_tokens=target,
    )
