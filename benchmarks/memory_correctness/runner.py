"""Offline Memory Correctness V0.9B benchmark."""

from __future__ import annotations

import argparse
import json
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentkernel import (
    CapabilityEvaluator,
    CapabilityGrant,
    InMemoryMemoryStore,
    JsonlMemoryStore,
    MEMORY_FORGET_ACTION,
    MEMORY_READ_ACTION,
    MEMORY_WRITE_ACTION,
    MemoryAccessDenied,
    MemoryCorruptionError,
    MemoryInvalid,
    MemoryProvenance,
    MemoryService,
    memory_namespace_scope,
    project_conflicting_memories_to_context_pages,
    project_memories_to_context_pages,
)
from benchmarks.runtimebench.environment import current_commit, generated_at


DEFAULT_OUTPUT = (
    Path(__file__).resolve().parents[1]
    / "results"
    / "memory_correctness_v0.9b.json"
)
BENCHMARK_VERSION = "0.9B"
RUNTIME_VERSION = "AgentKernel V0.9B"
AGENT_A = "agent-a"
AGENT_B = "agent-b"
NAMESPACE = "project"
OTHER_NAMESPACE = "other-project"


@dataclass(frozen=True, slots=True)
class CorrectnessCase:
    case_id: str
    name: str
    status: str
    mechanism_under_test: tuple[str, ...]
    oracle: str
    evidence: Mapping[str, Any]
    limitations: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return self.status == "PASS"

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "name": self.name,
            "status": self.status,
            "mechanism_under_test": list(self.mechanism_under_test),
            "oracle": self.oracle,
            "evidence": _stable_json(self.evidence),
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True, slots=True)
class CorrectnessDocument:
    cases: tuple[CorrectnessCase, ...]
    version: str = BENCHMARK_VERSION
    runtime_version: str = RUNTIME_VERSION
    commit: str = ""
    timestamp: str = ""

    def as_dict(self) -> dict[str, Any]:
        passed = sum(1 for case in self.cases if case.passed)
        failed = len(self.cases) - passed
        return {
            "version": self.version,
            "runtime_version": self.runtime_version,
            "commit": self.commit or current_commit(),
            "timestamp": self.timestamp or generated_at(),
            "total": len(self.cases),
            "passed": passed,
            "failed": failed,
            "decision": "PASS" if failed == 0 else "FAIL",
            "deterministic": True,
            "offline": True,
            "network": "not_used",
            "cases": [case.as_dict() for case in self.cases],
        }


def run_memory_correctness_benchmark() -> CorrectnessDocument:
    with tempfile.TemporaryDirectory(prefix="agentkernel-memory-correctness-") as root:
        tmp = Path(root)
        cases = (
            _c1_staleness(tmp / "c1.jsonl"),
            _c2_supersede_chain(),
            _c3_cycle_rejection(),
            _c4_conflict_preservation(),
            _c5_scope_separation(),
            _c6_freshness_evidence(tmp / "c6.jsonl"),
            _c7_capability_isolation(),
            _c8_context_filtering(),
            _c9_conflict_projection(),
            _c10_restart_durability(tmp / "c10.jsonl"),
        )
    return CorrectnessDocument(
        cases=cases,
        commit=current_commit(),
        timestamp=generated_at(),
    )


def write_memory_correctness_benchmark(
    document: CorrectnessDocument,
    output: str | Path = DEFAULT_OUTPUT,
) -> Path:
    path = Path(output)
    if not path.is_absolute():
        path = DEFAULT_OUTPUT.parent / path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document.as_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def format_human_report(document: CorrectnessDocument) -> str:
    payload = document.as_dict()
    lines = ["Memory Correctness V0.9B", ""]
    for case in document.cases:
        lines.append(f"{case.case_id} {case.name:<28} {case.status}")
    lines.extend(["", f"Memory Correctness: {payload['passed']}/{payload['total']} PASS"])
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run deterministic AgentKernel Memory Correctness V0.9B."
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Write result artifact path.")
    parser.add_argument("--no-write", action="store_true", help="Do not write the result artifact.")
    args = parser.parse_args(argv)

    document = run_memory_correctness_benchmark()
    print(
        json.dumps(document.as_dict(), ensure_ascii=False, indent=2, sort_keys=True)
        if args.json
        else format_human_report(document)
    )
    if not args.no_write:
        write_memory_correctness_benchmark(document, args.output)
    return 0 if document.as_dict()["decision"] == "PASS" else 1


def _c1_staleness(path: Path) -> CorrectnessCase:
    first = _service(JsonlMemoryStore(path))
    record = _remember(first, "Project requires Python 3.11.")
    stale = first.mark_stale(
        record.memory_id,
        agent_id=AGENT_A,
        reason="pyproject.toml now requires Python >=3.12",
        evidence_provenance=_provenance(
            "current-observation",
            source_session_id="session-b",
            source_event_id="read-pyproject",
            source_tool_name="read_file",
        ),
        capability_evaluator=_grants(AGENT_A, MEMORY_WRITE_ACTION),
    )
    first.close()
    second = _service(JsonlMemoryStore(path))
    active = _search(second, "Python")
    explicit_stale = second.search(
        agent_id=AGENT_A,
        owner_agent_id=AGENT_A,
        namespace=NAMESPACE,
        query="Python",
        limit=5,
        include_stale=True,
        capability_evaluator=_grants(AGENT_A, MEMORY_READ_ACTION),
    )
    history = second.history(
        agent_id=AGENT_A,
        owner_agent_id=AGENT_A,
        namespace=NAMESPACE,
        capability_evaluator=_grants(AGENT_A, MEMORY_READ_ACTION),
    )
    success = (
        active == ()
        and [item.memory_id for item in explicit_stale] == [record.memory_id]
        and len(history) == 1
        and history[0].lifecycle_state == "STALE"
        and history[0].stale_provenance is not None
    )
    second.close()
    return _case(
        "C1",
        "Staleness",
        success,
        ("memory/stale", "default active retrieval", "history"),
        "Stale memory is hidden by default but durable history restores it after restart.",
        {
            "stale_memory_id": stale.memory_id,
            "default_result_count": len(active),
            "explicit_stale_result_ids": [item.memory_id for item in explicit_stale],
            "history_status": history[0].lifecycle_state if history else None,
            "evidence_source": (
                None if not history or history[0].stale_provenance is None
                else history[0].stale_provenance.source
            ),
        },
    )


def _c2_supersede_chain() -> CorrectnessCase:
    memory = _service()
    m1 = _remember(memory, "Project uses Python 3.10.")
    m2 = _supersede(memory, m1.memory_id, "Project uses Python 3.11.")
    m3 = _supersede(memory, m2.memory_id, "Project uses Python 3.12.")
    active = _search(memory, "Python")
    history = {item.memory_id: item for item in _history(memory)}
    success = (
        [item.memory_id for item in active] == [m3.memory_id]
        and history[m1.memory_id].superseded_by_memory_id == m2.memory_id
        and history[m2.memory_id].superseded_by_memory_id == m3.memory_id
        and history[m3.memory_id].lifecycle_state == "ACTIVE"
    )
    return _case(
        "C2",
        "Supersede Chain",
        success,
        ("memory/superseded", "active projection", "history"),
        "M1 -> M2 -> M3 leaves only M3 active while history preserves the chain.",
        {
            "active_ids": [item.memory_id for item in active],
            "chain": {
                m1.memory_id: history[m1.memory_id].superseded_by_memory_id,
                m2.memory_id: history[m2.memory_id].superseded_by_memory_id,
                m3.memory_id: history[m3.memory_id].supersedes_memory_id,
            },
        },
    )


def _c3_cycle_rejection() -> CorrectnessCase:
    memory = _service()
    m1 = _remember(memory, "Project uses Python 3.11.")
    m2 = _supersede(memory, m1.memory_id, "Project uses Python 3.12.")
    before_count = len(memory.durable_events())
    denied = False
    try:
        _supersede(
            memory,
            m2.memory_id,
            "Project uses Python 3.11 again.",
            memory_id=m1.memory_id,
        )
    except MemoryCorruptionError:
        denied = True
    after_count = len(memory.durable_events())
    return _case(
        "C3",
        "Cycle Rejection",
        denied and before_count == after_count,
        ("MemoryService.supersede", "append-only durable state"),
        "Attempted M2 -> M1 cycle is rejected before durable state changes.",
        {"denied": denied, "before_events": before_count, "after_events": after_count},
    )


def _c4_conflict_preservation() -> CorrectnessCase:
    memory = _service()
    m1 = _remember(memory, "Project requires Python 3.11.")
    m2 = _remember(memory, "Project requires Python 3.12.")
    records = memory.mark_conflict(
        agent_id=AGENT_A,
        memory_ids=(m1.memory_id, m2.memory_id),
        reason="versions disagree",
        evidence_provenance=_provenance("host", source_event_id="review"),
        capability_evaluator=_grants(AGENT_A, MEMORY_WRITE_ACTION),
    )
    active = _search(memory, "Python")
    success = (
        {item.memory_id for item in active} == {m1.memory_id, m2.memory_id}
        and all(item.conflict_group_id for item in records)
        and records[0].conflicts_with_memory_ids == (m2.memory_id,)
        and records[1].conflicts_with_memory_ids == (m1.memory_id,)
    )
    return _case(
        "C4",
        "Conflict Preservation",
        success,
        ("memory/conflict", "active retrieval"),
        "Explicit conflict keeps both active memories and attaches relation metadata.",
        {
            "active_ids": [item.memory_id for item in active],
            "conflict_group": records[0].conflict_group_id,
            "relations": {
                item.memory_id: list(item.conflicts_with_memory_ids) for item in records
            },
        },
    )


def _c5_scope_separation() -> CorrectnessCase:
    memory = _service()
    evaluator = _owner_grants(AGENT_A, MEMORY_WRITE_ACTION)
    m1 = _remember(
        memory,
        "Preferred implementation language is Python.",
        namespace=NAMESPACE,
        evaluator=evaluator,
    )
    m2 = _remember(
        memory,
        "Preferred implementation language is Rust.",
        namespace=OTHER_NAMESPACE,
        evaluator=evaluator,
    )
    rejected = False
    try:
        memory.mark_conflict(
            agent_id=AGENT_A,
            memory_ids=(m1.memory_id, m2.memory_id),
            reason="cross namespace conflict is invalid",
            evidence_provenance=_provenance(),
            capability_evaluator=evaluator,
        )
    except MemoryInvalid:
        rejected = True
    success = (
        rejected
        and memory.read(
            m1.memory_id,
            agent_id=AGENT_A,
            capability_evaluator=_grants(AGENT_A, MEMORY_READ_ACTION),
        ).conflict_group_id is None
        and memory.read(
            m2.memory_id,
            agent_id=AGENT_A,
            capability_evaluator=_grants(AGENT_A, MEMORY_READ_ACTION, namespace=OTHER_NAMESPACE),
        ).conflict_group_id is None
    )
    return _case(
        "C5",
        "Scope Separation",
        success,
        ("namespace", "explicit conflict API"),
        "Kernel does not infer conflict and rejects explicit conflict across owner namespace.",
        {"cross_scope_conflict_rejected": rejected},
    )


def _c6_freshness_evidence(path: Path) -> CorrectnessCase:
    first = _service(JsonlMemoryStore(path))
    record = _remember(first, "Project requires Python 3.11.")
    first.mark_stale(
        record.memory_id,
        agent_id=AGENT_A,
        reason="Current pyproject.toml says requires-python >=3.12",
        evidence_provenance=_provenance(
            "current-observation",
            source_session_id="session-current",
            source_event_id="tool-result-9",
            source_tool_name="read_file",
            note="pyproject.toml requires-python >=3.12",
        ),
        observed_at=99.0,
        capability_evaluator=_grants(AGENT_A, MEMORY_WRITE_ACTION),
    )
    first.close()
    second = _service(JsonlMemoryStore(path))
    restored = second.read(
        record.memory_id,
        agent_id=AGENT_A,
        include_inactive=True,
        capability_evaluator=_grants(AGENT_A, MEMORY_READ_ACTION),
    )
    success = (
        restored.lifecycle_state == "STALE"
        and restored.stale_at == 99.0
        and restored.stale_provenance is not None
        and restored.stale_provenance.source_event_id == "tool-result-9"
    )
    second.close()
    return _case(
        "C6",
        "Freshness Evidence",
        success,
        ("stale provenance", "JsonlMemoryStore"),
        "Freshness evidence source survives restart with stale lifecycle metadata.",
        {
            "status": restored.lifecycle_state,
            "stale_at": restored.stale_at,
            "evidence": (
                None if restored.stale_provenance is None
                else restored.stale_provenance.as_dict()
            ),
        },
    )


def _c7_capability_isolation() -> CorrectnessCase:
    memory = _service()
    record = _remember(memory, "Private preference.")
    denied = False
    try:
        memory.mark_stale(
            record.memory_id,
            agent_id=AGENT_B,
            reason="read-only cannot mutate lifecycle",
            evidence_provenance=_provenance(),
            capability_evaluator=CapabilityEvaluator(
                [
                    CapabilityGrant(
                        AGENT_B,
                        MEMORY_READ_ACTION,
                        memory_namespace_scope(AGENT_A, NAMESPACE),
                    )
                ]
            ),
        )
    except MemoryAccessDenied:
        denied = True
    return _case(
        "C7",
        "Capability Isolation",
        denied,
        ("CapabilityEvaluator", "memory.write lifecycle mutation"),
        "Read authority is not enough to mark stale.",
        {"read_only_mutation_denied": denied},
    )


def _c8_context_filtering() -> CorrectnessCase:
    memory = _service()
    for index in range(100):
        _remember(memory, f"Active memory {index}.")
        stale = _remember(memory, f"Stale memory {index}.")
        memory.mark_stale(
            stale.memory_id,
            agent_id=AGENT_A,
            reason="current evidence invalidated it",
            evidence_provenance=_provenance("current-observation"),
            capability_evaluator=_grants(AGENT_A, MEMORY_WRITE_ACTION),
        )
        old = _remember(memory, f"Superseded memory {index}.")
        _supersede(memory, old.memory_id, f"Superseding active memory {index}.")
        forgotten = _remember(memory, f"Forgotten memory {index}.")
        memory.forget(
            forgotten.memory_id,
            agent_id=AGENT_A,
            capability_evaluator=_grants(AGENT_A, MEMORY_FORGET_ACTION),
        )
    history = _history(memory)
    projection = project_memories_to_context_pages(history, top_k=250)
    selected_states = {item.lifecycle_state for item in projection.selected_records}
    success = selected_states == {"ACTIVE"} and all(
        "status: ACTIVE" in page.content for page in projection.pages
    )
    return _case(
        "C8",
        "Context Filtering",
        success,
        ("Context projection", "Memory lifecycle filtering"),
        "Default projection excludes stale, superseded, and forgotten memories.",
        {
            "history_count": len(history),
            "selected_count": projection.selected_count,
            "selected_states": sorted(selected_states),
        },
    )


def _c9_conflict_projection() -> CorrectnessCase:
    memory = _service()
    m1 = _remember(memory, "Preferred implementation language is Python.")
    m2 = _remember(memory, "Preferred implementation language is Rust.")
    records = memory.mark_conflict(
        agent_id=AGENT_A,
        memory_ids=(m1.memory_id, m2.memory_id),
        reason="preference memories disagree",
        evidence_provenance=_provenance("host", source_session_id="review"),
        capability_evaluator=_grants(AGENT_A, MEMORY_WRITE_ACTION),
        conflict_group_id="conflict_language",
    )
    projection = project_conflicting_memories_to_context_pages(records, top_k=10)
    content = "\n".join(page.content for page in projection.pages)
    success = (
        projection.selected_count == 2
        and "status: ACTIVE" in content
        and "conflict_group: conflict_language" in content
        and "Kernel does not choose truth" in content
    )
    return _case(
        "C9",
        "Conflict Projection",
        success,
        ("Conflict-aware Context projection",),
        "Conflict projection includes both memories, status, provenance, and relation.",
        {
            "selected_count": projection.selected_count,
            "contains_python": "Python" in content,
            "contains_rust": "Rust" in content,
            "contains_conflict_group": "conflict_language" in content,
        },
    )


def _c10_restart_durability(path: Path) -> CorrectnessCase:
    first = _service(JsonlMemoryStore(path))
    stale = _remember(first, "Project requires Python 3.11.")
    first.mark_stale(
        stale.memory_id,
        agent_id=AGENT_A,
        reason="pyproject now says >=3.12",
        evidence_provenance=_provenance("current-observation", source_event_id="read-1"),
        capability_evaluator=_grants(AGENT_A, MEMORY_WRITE_ACTION),
    )
    c1 = _remember(first, "Preferred implementation language is Python.")
    c2 = _remember(first, "Preferred implementation language is Rust.")
    first.mark_conflict(
        agent_id=AGENT_A,
        memory_ids=(c1.memory_id, c2.memory_id),
        reason="conflicting preferences",
        evidence_provenance=_provenance("host", source_event_id="review-1"),
        capability_evaluator=_grants(AGENT_A, MEMORY_WRITE_ACTION),
    )
    s1 = _remember(first, "Project uses Python 3.10.")
    s2 = _supersede(first, s1.memory_id, "Project uses Python 3.12.")
    first.close()
    second = _service(JsonlMemoryStore(path))
    history = {item.memory_id: item for item in _history(second)}
    success = (
        history[stale.memory_id].lifecycle_state == "STALE"
        and history[c1.memory_id].conflicts_with_memory_ids == (c2.memory_id,)
        and history[c2.memory_id].conflicts_with_memory_ids == (c1.memory_id,)
        and history[s1.memory_id].superseded_by_memory_id == s2.memory_id
        and history[s2.memory_id].lifecycle_state == "ACTIVE"
    )
    second.close()
    return _case(
        "C10",
        "Restart Durability",
        success,
        ("JsonlMemoryStore", "lifecycle projection"),
        "Stale, conflict, and supersede metadata recover in a fresh runtime.",
        {
            "stale_status": history[stale.memory_id].lifecycle_state,
            "conflict_relations": {
                c1.memory_id: list(history[c1.memory_id].conflicts_with_memory_ids),
                c2.memory_id: list(history[c2.memory_id].conflicts_with_memory_ids),
            },
            "superseded_by": history[s1.memory_id].superseded_by_memory_id,
        },
    )


def _case(
    case_id: str,
    name: str,
    success: bool,
    mechanism: tuple[str, ...],
    oracle: str,
    evidence: Mapping[str, Any],
    limitations: tuple[str, ...] = (
        "Synthetic deterministic fixture; no truth verification, embeddings, or LLM judge.",
    ),
) -> CorrectnessCase:
    return CorrectnessCase(
        case_id=case_id,
        name=name,
        status="PASS" if success else "FAIL",
        mechanism_under_test=mechanism,
        oracle=oracle,
        evidence=evidence,
        limitations=limitations,
    )


def _service(store=None) -> MemoryService:
    ids = iter(f"mem_{index:04d}" for index in range(5000))
    event_ids = iter(f"mev_{index:04d}" for index in range(10000))
    ticks = iter(float(index) for index in range(10000))
    return MemoryService(
        store or InMemoryMemoryStore(),
        memory_id_factory=lambda: next(ids),
        event_id_factory=lambda: next(event_ids),
        clock=lambda: next(ticks),
    )


def _remember(
    memory: MemoryService,
    content: str,
    *,
    namespace: str = NAMESPACE,
    evaluator: CapabilityEvaluator | None = None,
):
    return memory.remember(
        agent_id=AGENT_A,
        namespace=namespace,
        content=content,
        provenance=_provenance(),
        capability_evaluator=evaluator or _grants(AGENT_A, MEMORY_WRITE_ACTION, namespace=namespace),
    )


def _supersede(
    memory: MemoryService,
    old_memory_id: str,
    content: str,
    *,
    memory_id: str | None = None,
):
    return memory.supersede(
        agent_id=AGENT_A,
        old_memory_id=old_memory_id,
        memory_id=memory_id,
        content=content,
        provenance=_provenance(),
        capability_evaluator=_grants(AGENT_A, MEMORY_WRITE_ACTION, MEMORY_FORGET_ACTION),
    )


def _search(memory: MemoryService, query: str):
    return memory.search(
        agent_id=AGENT_A,
        owner_agent_id=AGENT_A,
        namespace=NAMESPACE,
        query=query,
        limit=10,
        capability_evaluator=_grants(AGENT_A, MEMORY_READ_ACTION),
    )


def _history(memory: MemoryService):
    return memory.history(
        agent_id=AGENT_A,
        owner_agent_id=AGENT_A,
        namespace=NAMESPACE,
        capability_evaluator=_grants(AGENT_A, MEMORY_READ_ACTION),
    )


def _grants(agent_id: str, *actions: str, namespace: str = NAMESPACE) -> CapabilityEvaluator:
    return CapabilityEvaluator(
        CapabilityGrant(agent_id, action, memory_namespace_scope(AGENT_A, namespace))
        for action in actions
    )


def _owner_grants(agent_id: str, *actions: str) -> CapabilityEvaluator:
    return CapabilityEvaluator(
        CapabilityGrant(agent_id, action, f"memory://{AGENT_A}/**")
        for action in actions
    )


def _provenance(
    source: str = "host",
    *,
    source_session_id: str | None = None,
    source_event_id: str | None = None,
    source_tool_name: str | None = None,
    note: str | None = None,
) -> MemoryProvenance:
    return MemoryProvenance(
        source=source,
        source_session_id=source_session_id,
        source_event_id=source_event_id,
        source_agent_id=AGENT_A,
        source_tool_name=source_tool_name,
        note=note,
    )


def _stable_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _stable_json(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_stable_json(item) for item in value]
    if isinstance(value, Path):
        return value.as_posix()
    return value
