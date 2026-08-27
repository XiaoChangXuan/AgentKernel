"""JSON schema helpers for RuntimeBench V0.8."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, is_dataclass, asdict
from typing import Any, TypeAlias


JsonValue: TypeAlias = Any

RUNTIMEBENCH_VERSION = "0.8"
RUNTIME_VERSION = "AgentKernel V0.8"


@dataclass(frozen=True, slots=True)
class FixtureSpec:
    fixture_id: str
    fixture_version: str = "v0.8"
    deterministic: bool = True
    offline: bool = True
    parameters: Mapping[str, JsonValue] = field(default_factory=dict)

    def as_dict(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "fixture_id": self.fixture_id,
            "fixture_version": self.fixture_version,
            "deterministic": self.deterministic,
            "offline": self.offline,
        }
        if self.parameters:
            payload["parameters"] = _jsonify(self.parameters)
        return payload


@dataclass(frozen=True, slots=True)
class BaselineSpec:
    name: str
    type: str
    description: str

    def as_dict(self) -> dict[str, JsonValue]:
        return {
            "name": self.name,
            "type": self.type,
            "description": self.description,
        }


@dataclass(frozen=True, slots=True)
class FailureInjectionSpec:
    enabled: bool
    point: str
    description: str

    def as_dict(self) -> dict[str, JsonValue]:
        return {
            "enabled": self.enabled,
            "point": self.point,
            "description": self.description,
        }


@dataclass(frozen=True, slots=True)
class ResultSpec:
    status: str
    oracle: str

    def as_dict(self) -> dict[str, JsonValue]:
        return {
            "status": self.status,
            "oracle": self.oracle,
        }


@dataclass(frozen=True, slots=True)
class RuntimeBenchRecord:
    benchmark_id: str
    category: str
    description: str
    fixture: FixtureSpec
    mechanism_under_test: Sequence[str]
    baseline: BaselineSpec
    metrics: Mapping[str, JsonValue]
    result: ResultSpec
    success: bool
    limitations: Sequence[str]
    raw_records: Sequence[Mapping[str, JsonValue]]
    runtime_version: str = RUNTIME_VERSION
    failure_injection: FailureInjectionSpec | None = None

    def as_dict(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "benchmark_id": self.benchmark_id,
            "category": self.category,
            "description": self.description,
            "runtime_version": self.runtime_version,
            "fixture": self.fixture.as_dict(),
            "mechanism_under_test": list(self.mechanism_under_test),
            "baseline": self.baseline.as_dict(),
            "metrics": _jsonify(self.metrics),
            "result": self.result.as_dict(),
            "success": self.success,
            "limitations": list(self.limitations),
            "raw_records": _jsonify(list(self.raw_records)),
        }
        if self.failure_injection is not None:
            payload["failure_injection"] = self.failure_injection.as_dict()
        return payload


@dataclass(frozen=True, slots=True)
class RuntimeBenchDocument:
    commit: str
    generated_at: str
    environment: Mapping[str, JsonValue]
    benchmarks: Sequence[RuntimeBenchRecord]
    runtimebench_version: str = RUNTIMEBENCH_VERSION
    runtime_version: str = RUNTIME_VERSION

    def as_dict(self) -> dict[str, JsonValue]:
        benchmark_payloads = [benchmark.as_dict() for benchmark in self.benchmarks]
        passed = sum(1 for benchmark in self.benchmarks if benchmark.success)
        total = len(self.benchmarks)
        summary = {
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "decision": "PASS" if passed == total else "FAIL",
        }
        return {
            "runtimebench_version": self.runtimebench_version,
            "runtime_version": self.runtime_version,
            "commit": self.commit,
            "generated_at": self.generated_at,
            "environment": _jsonify(self.environment),
            "summary": summary,
            "benchmarks": benchmark_payloads,
        }


def status_for(success: bool) -> str:
    return "pass" if success else "fail"


def _jsonify(value: JsonValue) -> JsonValue:
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonify(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonify(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_jsonify(item) for item in value]
    return value
