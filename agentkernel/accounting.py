"""Process resource usage accounting for cooperative runtime scheduling."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Callable

from .agent import AgentBudget
from .protocol import ModelUsage
from .resources.model import ResourceMetricsSnapshot


@dataclass(frozen=True, slots=True)
class ProcessUsageSnapshot:
    """Point-in-time resource usage for one runtime process."""

    process_id: str
    token_usage: int = 0
    model_cost: float = 0.0
    tool_calls: int = 0
    resource_reads: int = 0
    resource_bytes: int = 0
    wall_time: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.process_id, str) or not self.process_id:
            raise ValueError("process_id must be a non-empty string")
        for name in (
            "token_usage",
            "tool_calls",
            "resource_reads",
            "resource_bytes",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        for name in ("model_cost", "wall_time"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError(f"{name} must be a non-negative finite number")
            object.__setattr__(self, name, float(value))


@dataclass(frozen=True, slots=True)
class UsageLimitExceeded:
    """One runtime budget violation observed for a process."""

    limit: str
    usage: int | float
    maximum: int | float

    def __post_init__(self) -> None:
        if not self.limit:
            raise ValueError("limit must not be empty")


@dataclass(slots=True)
class _UsageCounters:
    token_usage: int = 0
    model_cost: float = 0.0
    tool_calls: int = 0
    resource_reads: int = 0
    resource_bytes: int = 0
    started_at: float | None = None


class UsageCollector:
    """Collect process-local runtime usage from Kernel observable boundaries."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._counters: dict[str, _UsageCounters] = {}
        self._resource_snapshots: dict[tuple[str, str], ResourceMetricsSnapshot] = {}

    def start_process(self, process_id: str) -> None:
        """Begin wall-clock accounting for a process if it has not started."""

        counters = self._ensure(process_id)
        if counters.started_at is None:
            counters.started_at = self._clock()

    def reset_process(self, process_id: str) -> None:
        """Reset non-durable usage counters for host-controlled recovery tests."""

        self._validate_process_id(process_id)
        self._counters[process_id] = _UsageCounters(started_at=self._clock())
        for key in tuple(self._resource_snapshots):
            if key[0] == process_id:
                del self._resource_snapshots[key]

    def record_llm_usage(
        self,
        process_id: str,
        usage: ModelUsage | None,
        *,
        model_cost: float = 0.0,
    ) -> None:
        """Accumulate provider-reported model usage for one process."""

        counters = self._ensure_started(process_id)
        if usage is not None:
            counters.token_usage += usage.total_tokens
        counters.model_cost += _validate_non_negative_number(
            model_cost,
            "model_cost",
        )

    def record_tool_call(self, process_id: str, count: int = 1) -> None:
        """Accumulate executed Tool calls for one process."""

        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("tool call count must be a non-negative integer")
        self._ensure_started(process_id).tool_calls += count

    def record_resource_read(self, process_id: str, byte_count: int) -> None:
        """Accumulate one Resource read without coupling to ResourceService."""

        if (
            isinstance(byte_count, bool)
            or not isinstance(byte_count, int)
            or byte_count < 0
        ):
            raise ValueError("resource byte_count must be a non-negative integer")
        counters = self._ensure_started(process_id)
        counters.resource_reads += 1
        counters.resource_bytes += byte_count

    def begin_resource_metrics(
        self,
        process_id: str,
        snapshot: ResourceMetricsSnapshot,
        *,
        source: str = "default",
    ) -> None:
        """Set a ResourceService metrics baseline for later delta collection."""

        self._ensure_started(process_id)
        self._resource_snapshots[(process_id, _validate_source(source))] = snapshot

    def observe_resource_metrics(
        self,
        process_id: str,
        snapshot: ResourceMetricsSnapshot,
        *,
        source: str = "default",
    ) -> None:
        """Collect ResourceService metric deltas since the previous snapshot."""

        counters = self._ensure_started(process_id)
        key = (process_id, _validate_source(source))
        previous = self._resource_snapshots.get(key)
        if previous is not None:
            read_delta = snapshot.resource_reads - previous.resource_reads
            byte_delta = snapshot.resource_bytes_read - previous.resource_bytes_read
            if read_delta < 0 or byte_delta < 0:
                raise ValueError("resource metrics must be monotonic")
            counters.resource_reads += read_delta
            counters.resource_bytes += byte_delta
        self._resource_snapshots[key] = snapshot

    def snapshot(self, process_id: str) -> ProcessUsageSnapshot:
        """Return a stable usage snapshot for one process."""

        counters = self._ensure(process_id)
        wall_time = 0.0
        if counters.started_at is not None:
            wall_time = max(0.0, self._clock() - counters.started_at)
        return ProcessUsageSnapshot(
            process_id=process_id,
            token_usage=counters.token_usage,
            model_cost=counters.model_cost,
            tool_calls=counters.tool_calls,
            resource_reads=counters.resource_reads,
            resource_bytes=counters.resource_bytes,
            wall_time=wall_time,
        )

    def exceeded_budget(
        self,
        process_id: str,
        budget: AgentBudget,
    ) -> UsageLimitExceeded | None:
        """Return the first exceeded runtime budget limit, if any."""

        return exceeded_budget(self.snapshot(process_id), budget)

    def _ensure_started(self, process_id: str) -> _UsageCounters:
        self.start_process(process_id)
        return self._counters[process_id]

    def _ensure(self, process_id: str) -> _UsageCounters:
        self._validate_process_id(process_id)
        counters = self._counters.get(process_id)
        if counters is None:
            counters = _UsageCounters()
            self._counters[process_id] = counters
        return counters

    @staticmethod
    def _validate_process_id(process_id: str) -> None:
        if not isinstance(process_id, str) or not process_id:
            raise ValueError("process_id must be a non-empty string")


def exceeded_budget(
    snapshot: ProcessUsageSnapshot,
    budget: AgentBudget,
) -> UsageLimitExceeded | None:
    """Evaluate optional process runtime quotas against a usage snapshot."""

    checks: tuple[tuple[str, int | float, int | float | None], ...] = (
        ("max_token_usage", snapshot.token_usage, budget.max_token_usage),
        ("max_model_cost", snapshot.model_cost, budget.max_model_cost),
        ("max_total_tool_calls", snapshot.tool_calls, budget.max_total_tool_calls),
        ("max_resource_reads", snapshot.resource_reads, budget.max_resource_reads),
        ("max_resource_bytes", snapshot.resource_bytes, budget.max_resource_bytes),
        ("max_wall_time_seconds", snapshot.wall_time, budget.max_wall_time_seconds),
    )
    for limit, usage, maximum in checks:
        if maximum is not None and usage > maximum:
            return UsageLimitExceeded(limit=limit, usage=usage, maximum=maximum)
    return None


def _validate_source(source: str) -> str:
    if not isinstance(source, str) or not source:
        raise ValueError("resource metrics source must be a non-empty string")
    return source


def _validate_non_negative_number(value: float, name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError(f"{name} must be a non-negative finite number")
    return float(value)
