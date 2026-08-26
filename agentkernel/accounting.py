"""Process resource usage accounting for cooperative runtime scheduling."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from collections.abc import Iterable
from typing import Callable

from .agent import AgentBudget
from .protocol import ModelUsage
from .resources.model import ResourceMetricsSnapshot


RUNTIME_BUDGET_FIELDS: tuple[str, ...] = (
    "max_token_usage",
    "max_model_cost",
    "max_total_tool_calls",
    "max_resource_reads",
    "max_resource_bytes",
    "max_wall_time_seconds",
)


@dataclass(frozen=True, slots=True)
class HostBudget:
    """Runtime ceilings for the current Kernel host process.

    HostBudget is an enforcement input, not a principal, account, or durable
    billing ledger.
    """

    max_token_usage: int | None = None
    max_model_cost: float | None = None
    max_total_tool_calls: int | None = None
    max_resource_reads: int | None = None
    max_resource_bytes: int | None = None
    max_wall_time_seconds: float | None = None

    def __post_init__(self) -> None:
        for name in (
            "max_token_usage",
            "max_total_tool_calls",
            "max_resource_reads",
            "max_resource_bytes",
        ):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError(f"{name} must be a non-negative integer")
        for name in ("max_model_cost", "max_wall_time_seconds"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError(f"{name} must be a non-negative number")


class BudgetHierarchyError(ValueError):
    """Raised when runtime budget ceilings violate hierarchy constraints."""


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
    """One runtime budget violation observed at a Kernel budget scope."""

    limit: str
    usage: int | float
    maximum: int | float
    scope: str = "process"
    subject: str | None = None

    def __post_init__(self) -> None:
        if not self.limit:
            raise ValueError("limit must not be empty")
        if self.scope not in {"process", "agent", "host"}:
            raise ValueError("scope must be process, agent, or host")
        if self.scope in {"process", "agent"} and not self.subject:
            raise ValueError("process and agent budget violations require subject")
        if self.scope == "host" and self.subject is not None:
            raise ValueError("host budget violations must not have subject")


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

    def aggregate_snapshot(
        self,
        process_id: str,
        source_process_ids: Iterable[str],
    ) -> ProcessUsageSnapshot:
        """Return an aggregate runtime snapshot across process ids."""

        return aggregate_usage_snapshots(
            process_id,
            (self.snapshot(source_id) for source_id in source_process_ids),
        )

    def usage_for_agent(
        self,
        agent_id: str,
        processes: Iterable[object],
    ) -> ProcessUsageSnapshot:
        """Aggregate live process usage attributed to one Agent principal."""

        if not isinstance(agent_id, str) or not agent_id:
            raise ValueError("agent_id must be a non-empty string")
        process_ids = (
            process.process_id
            for process in processes
            if getattr(process, "agent_id", None) == agent_id and _process_is_live(process)
        )
        return self.aggregate_snapshot(f"agent:{agent_id}", process_ids)

    def host_usage(self, processes: Iterable[object]) -> ProcessUsageSnapshot:
        """Aggregate live process usage across the current Kernel runtime."""

        process_ids = (
            process.process_id for process in processes if _process_is_live(process)
        )
        return self.aggregate_snapshot("host", process_ids)

    def exceeded_budget(
        self,
        process_id: str,
        budget: AgentBudget | HostBudget,
        *,
        scope: str = "process",
        subject: str | None = None,
    ) -> UsageLimitExceeded | None:
        """Return the first exceeded runtime budget limit, if any."""

        return exceeded_budget(
            self.snapshot(process_id),
            budget,
            scope=scope,
            subject=subject if subject is not None else (
                process_id if scope != "host" else None
            ),
        )

    def exceeded_agent_budget(
        self,
        agent_id: str,
        processes: Iterable[object],
        budget: AgentBudget,
    ) -> UsageLimitExceeded | None:
        """Return the first exceeded aggregate Agent budget limit, if any."""

        return exceeded_budget(
            self.usage_for_agent(agent_id, processes),
            budget,
            scope="agent",
            subject=agent_id,
        )

    def exceeded_host_budget(
        self,
        processes: Iterable[object],
        budget: HostBudget,
    ) -> UsageLimitExceeded | None:
        """Return the first exceeded aggregate Host budget limit, if any."""

        return exceeded_budget(self.host_usage(processes), budget, scope="host")

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
    budget: AgentBudget | HostBudget,
    *,
    scope: str = "process",
    subject: str | None = None,
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
            return UsageLimitExceeded(
                limit=limit,
                usage=usage,
                maximum=maximum,
                scope=scope,
                subject=subject or (snapshot.process_id if scope != "host" else None),
            )
    return None


def aggregate_usage_snapshots(
    process_id: str,
    snapshots: Iterable[ProcessUsageSnapshot],
) -> ProcessUsageSnapshot:
    """Create a deterministic aggregate snapshot from process snapshots."""

    totals = {
        "token_usage": 0,
        "model_cost": 0.0,
        "tool_calls": 0,
        "resource_reads": 0,
        "resource_bytes": 0,
        "wall_time": 0.0,
    }
    for snapshot in snapshots:
        if not isinstance(snapshot, ProcessUsageSnapshot):
            raise TypeError("snapshots must contain ProcessUsageSnapshot values")
        totals["token_usage"] += snapshot.token_usage
        totals["model_cost"] += snapshot.model_cost
        totals["tool_calls"] += snapshot.tool_calls
        totals["resource_reads"] += snapshot.resource_reads
        totals["resource_bytes"] += snapshot.resource_bytes
        totals["wall_time"] += snapshot.wall_time
    return ProcessUsageSnapshot(process_id=process_id, **totals)


def effective_runtime_budget(
    *budgets: AgentBudget | HostBudget | None,
) -> HostBudget:
    """Return the strictest per-process runtime ceiling across all budgets."""

    values: dict[str, int | float | None] = {}
    for field in RUNTIME_BUDGET_FIELDS:
        present = [
            getattr(budget, field)
            for budget in budgets
            if budget is not None and getattr(budget, field) is not None
        ]
        values[field] = min(present) if present else None
    return HostBudget(**values)


def validate_budget_within(
    child: AgentBudget | HostBudget,
    parent: AgentBudget | HostBudget,
    *,
    child_name: str = "child",
    parent_name: str = "parent",
) -> None:
    """Validate that explicit child ceilings do not exceed parent ceilings.

    None means no local limit, so it does not violate a finite parent ceiling;
    enforcement still checks the parent aggregate budget at safe points.
    """

    for field in RUNTIME_BUDGET_FIELDS:
        child_limit = getattr(child, field)
        parent_limit = getattr(parent, field)
        if child_limit is not None and parent_limit is not None:
            if child_limit > parent_limit:
                raise BudgetHierarchyError(
                    f"{child_name} budget {field} exceeds "
                    f"{parent_name} budget"
                )


def _process_is_live(process: object) -> bool:
    state = getattr(process, "state", None)
    return getattr(state, "value", state) != "EXITED"


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
