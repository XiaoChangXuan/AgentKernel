"""Common benchmark metric records and lightweight measurement helpers."""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TypeAlias


MetricValue: TypeAlias = bool | int | float | str | None
MetricMap: TypeAlias = Mapping[str, MetricValue]


@dataclass(frozen=True, slots=True)
class BenchmarkRecord:
    """One JSON-compatible benchmark observation."""

    benchmark: str
    case: str
    strategy: str
    metrics: MetricMap

    def as_dict(self) -> dict[str, object]:
        return {
            "benchmark": self.benchmark,
            "case": self.case,
            "strategy": self.strategy,
            "metrics": dict(self.metrics),
        }


class Timer:
    """Small monotonic timer for benchmark latency measurements."""

    def __init__(self) -> None:
        self._started = time.perf_counter()

    def elapsed_ms(self) -> float:
        return round((time.perf_counter() - self._started) * 1_000, 3)


def records_as_dicts(records: Iterable[BenchmarkRecord]) -> list[dict[str, object]]:
    return [record.as_dict() for record in records]
