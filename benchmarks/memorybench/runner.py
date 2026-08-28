"""Offline MemoryBench V0.9 runner."""

from __future__ import annotations

import argparse
import json
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentkernel import (
    CapabilityDelegator,
    CapabilityEvaluator,
    CapabilityGrant,
    DelegateCapabilityRequest,
    InMemoryMemoryStore,
    JsonlMemoryStore,
    MEMORY_FORGET_ACTION,
    MEMORY_READ_ACTION,
    MEMORY_WRITE_ACTION,
    MemoryAccessDenied,
    MemoryProvenance,
    MemoryService,
    memory_namespace_scope,
    project_memories_to_context_pages,
)
from benchmarks.runtimebench.environment import current_commit, generated_at


DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "results" / "memorybench_v0.9.json"
MEMORYBENCH_VERSION = "0.9"
RUNTIME_VERSION = "AgentKernel V0.9"
AGENT_A = "agent-a"
AGENT_B = "agent-b"
NAMESPACE = "preferences"


@dataclass(frozen=True, slots=True)
class MemoryBenchCase:
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
class MemoryBenchDocument:
    cases: tuple[MemoryBenchCase, ...]
    version: str = MEMORYBENCH_VERSION
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
            "cases": [case.as_dict() for case in self.cases],
            "deterministic": True,
            "offline": True,
            "network": "not_used",
        }


def run_memorybench() -> MemoryBenchDocument:
    with tempfile.TemporaryDirectory(prefix="agentkernel-memorybench-") as root:
        tmp = Path(root)
        cases = (
            _m1_cross_session_persistence(tmp / "m1.jsonl"),
            _m2_provenance(tmp / "m2.jsonl"),
            _m3_supersede(),
            _m4_forget(tmp / "m4.jsonl"),
            _m5_capability_isolation(),
            _m6_delegated_read(),
            _m7_context_boundedness(),
            _m8_index_rebuild(),
        )
    return MemoryBenchDocument(
        cases=cases,
        commit=current_commit(),
        timestamp=generated_at(),
    )


def write_memorybench(
    document: MemoryBenchDocument,
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


def format_human_report(document: MemoryBenchDocument) -> str:
    payload = document.as_dict()
    lines = ["MemoryBench V0.9", ""]
    for case in document.cases:
        lines.append(f"{case.case_id} {case.name:<34} {case.status}")
    lines.extend(["", f"MemoryBench: {payload['passed']}/{payload['total']} PASS"])
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run deterministic AgentKernel MemoryBench V0.9.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Write result artifact path.")
    parser.add_argument("--no-write", action="store_true", help="Do not write the result artifact.")
    args = parser.parse_args(argv)

    document = run_memorybench()
    print(
        json.dumps(document.as_dict(), ensure_ascii=False, indent=2, sort_keys=True)
        if args.json
        else format_human_report(document)
    )
    if not args.no_write:
        write_memorybench(document, args.output)
    return 0 if document.as_dict()["decision"] == "PASS" else 1


def _m1_cross_session_persistence(path: Path) -> MemoryBenchCase:
    first = _service(JsonlMemoryStore(path))
    written = first.remember(
        agent_id=AGENT_A,
        namespace=NAMESPACE,
        content="Preferred language is Chinese.",
        provenance=_provenance("session-a", "2"),
        capability_evaluator=_grants(AGENT_A, MEMORY_WRITE_ACTION),
    )
    first.close()

    second = _service(JsonlMemoryStore(path))
    results = second.search(
        agent_id=AGENT_A,
        owner_agent_id=AGENT_A,
        namespace=NAMESPACE,
        query="language",
        limit=5,
        capability_evaluator=_grants(AGENT_A, MEMORY_READ_ACTION),
    )
    success = len(results) == 1 and results[0].memory_id == written.memory_id
    second.close()
    return _case(
        "M1",
        "Cross-session persistence",
        success,
        ("JsonlMemoryStore", "MemoryService.search"),
        "Session B fresh runtime retrieves memory written in Session A.",
        {"memory_id": written.memory_id, "retrieved_ids": [item.memory_id for item in results]},
    )


def _m2_provenance(path: Path) -> MemoryBenchCase:
    first = _service(JsonlMemoryStore(path))
    written = first.remember(
        agent_id=AGENT_A,
        namespace=NAMESPACE,
        content="User prefers concise answers.",
        provenance=_provenance("session-1", "event-7"),
        capability_evaluator=_grants(AGENT_A, MEMORY_WRITE_ACTION),
    )
    first.close()
    second = _service(JsonlMemoryStore(path))
    restored = second.read(
        written.uri,
        agent_id=AGENT_A,
        capability_evaluator=_grants(AGENT_A, MEMORY_READ_ACTION),
    )
    success = (
        restored.provenance.source == "session"
        and restored.provenance.source_session_id == "session-1"
        and restored.provenance.source_event_id == "event-7"
    )
    second.close()
    return _case(
        "M2",
        "Provenance",
        success,
        ("MemoryRecord.provenance", "JsonlMemoryStore"),
        "Source session and source event survive restart.",
        restored.provenance.as_dict(),
    )


def _m3_supersede() -> MemoryBenchCase:
    memory = _service()
    old = memory.remember(
        agent_id=AGENT_A,
        namespace=NAMESPACE,
        content="Project uses Python 3.11.",
        provenance=_provenance(),
        capability_evaluator=_grants(AGENT_A, MEMORY_WRITE_ACTION),
    )
    new = memory.supersede(
        agent_id=AGENT_A,
        old_memory_id=old.memory_id,
        content="Project uses Python 3.12.",
        provenance=_provenance(),
        capability_evaluator=_grants(AGENT_A, MEMORY_WRITE_ACTION, MEMORY_FORGET_ACTION),
    )
    active = memory.search(
        agent_id=AGENT_A,
        owner_agent_id=AGENT_A,
        namespace=NAMESPACE,
        query="Python",
        limit=5,
        capability_evaluator=_grants(AGENT_A, MEMORY_READ_ACTION),
    )
    old_projection = memory.read(
        old.memory_id,
        agent_id=AGENT_A,
        include_inactive=True,
        capability_evaluator=_grants(AGENT_A, MEMORY_READ_ACTION),
    )
    success = [item.memory_id for item in active] == [new.memory_id] and not old_projection.active
    return _case(
        "M3",
        "Supersede",
        success,
        ("memory/remembered", "memory/superseded", "active projection"),
        "Old record remains durable but active search returns only the superseding record.",
        {
            "old_memory_id": old.memory_id,
            "new_memory_id": new.memory_id,
            "active_result_ids": [item.memory_id for item in active],
            "old_active": old_projection.active,
            "old_superseded_by": old_projection.superseded_by_memory_id,
        },
    )


def _m4_forget(path: Path) -> MemoryBenchCase:
    first = _service(JsonlMemoryStore(path))
    record = first.remember(
        agent_id=AGENT_A,
        namespace=NAMESPACE,
        content="Temporary preference.",
        provenance=_provenance(),
        capability_evaluator=_grants(AGENT_A, MEMORY_WRITE_ACTION),
    )
    first.forget(
        record.uri,
        agent_id=AGENT_A,
        capability_evaluator=_grants(AGENT_A, MEMORY_FORGET_ACTION),
    )
    first.close()
    second = _service(JsonlMemoryStore(path))
    active = second.search(
        agent_id=AGENT_A,
        owner_agent_id=AGENT_A,
        namespace=NAMESPACE,
        query="Temporary",
        limit=5,
        capability_evaluator=_grants(AGENT_A, MEMORY_READ_ACTION),
    )
    inactive = second.read(
        record.uri,
        agent_id=AGENT_A,
        include_inactive=True,
        capability_evaluator=_grants(AGENT_A, MEMORY_READ_ACTION),
    )
    success = active == () and inactive.active is False and len(second.durable_events()) == 2
    second.close()
    return _case(
        "M4",
        "Forget",
        success,
        ("memory/forgotten", "active projection"),
        "Forgotten memory is not active after restart while durable history remains explainable.",
        {
            "active_result_count": len(active),
            "inactive_active_flag": inactive.active,
            "durable_event_count": 2,
            "forgotten_at_present": inactive.forgotten_at is not None,
        },
    )


def _m5_capability_isolation() -> MemoryBenchCase:
    memory = _service()
    record = memory.remember(
        agent_id=AGENT_A,
        namespace=NAMESPACE,
        content="Secret preference.",
        provenance=_provenance(),
        capability_evaluator=_grants(AGENT_A, MEMORY_WRITE_ACTION),
    )
    denied = False
    try:
        memory.read(record.memory_id, agent_id=AGENT_B, capability_evaluator=CapabilityEvaluator(()))
    except MemoryAccessDenied:
        denied = True
    return _case(
        "M5",
        "Capability isolation",
        denied,
        ("CapabilityEvaluator", "memory.read"),
        "Agent B knowing memory_id is not enough to read Agent A memory.",
        {"known_memory_id": record.memory_id, "unauthorized_read_denied": denied},
    )


def _m6_delegated_read() -> MemoryBenchCase:
    memory = _service()
    parent_grant = CapabilityGrant(
        AGENT_A,
        MEMORY_READ_ACTION,
        memory_namespace_scope(AGENT_A, NAMESPACE),
    )
    record = memory.remember(
        agent_id=AGENT_A,
        namespace=NAMESPACE,
        content="Delegated visible preference.",
        provenance=_provenance(),
        capability_evaluator=_grants(AGENT_A, MEMORY_WRITE_ACTION),
    )
    decision = CapabilityDelegator().delegate(
        DelegateCapabilityRequest(
            parent_agent_id=AGENT_A,
            child_agent_id=AGENT_B,
            action=MEMORY_READ_ACTION,
            resource_scope=memory_namespace_scope(AGENT_A, NAMESPACE),
        ),
        parent_grants=(parent_grant,),
    )
    delegated = memory.read(
        record.uri,
        agent_id=AGENT_B,
        capability_evaluator=CapabilityEvaluator((decision.delegated_grant,)),
    )
    success = decision.allowed and delegated.memory_id == record.memory_id
    return _case(
        "M6",
        "Delegated read",
        success,
        ("CapabilityDelegator", "CapabilityEvaluator", "memory.read"),
        "Existing delegation can derive a narrowed memory.read grant for Agent B.",
        {
            "delegation_allowed": decision.allowed,
            "delegation_id": decision.delegation_id,
            "delegated_memory_id": delegated.memory_id,
        },
    )


def _m7_context_boundedness() -> MemoryBenchCase:
    memory = _service()
    write = _grants(AGENT_A, MEMORY_WRITE_ACTION)
    for index in range(1000):
        memory.remember(
            agent_id=AGENT_A,
            namespace=NAMESPACE,
            content=f"Memory number {index} about Python preference.",
            provenance=_provenance(),
            capability_evaluator=write,
        )
    results = memory.search(
        agent_id=AGENT_A,
        owner_agent_id=AGENT_A,
        namespace=NAMESPACE,
        query="Python preference",
        limit=5,
        capability_evaluator=_grants(AGENT_A, MEMORY_READ_ACTION),
    )
    projection = project_memories_to_context_pages(
        results,
        top_k=5,
        total_memory_records=1000,
    )
    success = projection.total_memory_records == 1000 and len(projection.pages) == 5
    return _case(
        "M7",
        "Context boundedness",
        success,
        ("MemoryService.search", "ContextPage projection"),
        "1000 durable memories produce only bounded selected ContextPages.",
        {
            "durable_memory_count": 1000,
            "search_limit": 5,
            "projected_page_count": len(projection.pages),
            "selected_count": projection.selected_count,
        },
    )


def _m8_index_rebuild() -> MemoryBenchCase:
    memory = _service()
    record = memory.remember(
        agent_id=AGENT_A,
        namespace=NAMESPACE,
        content="Index rebuild should find lexical content.",
        provenance=_provenance(),
        capability_evaluator=_grants(AGENT_A, MEMORY_WRITE_ACTION),
    )
    memory.rebuild_index()
    memory.drop_index()
    memory.rebuild_index()
    results = memory.search(
        agent_id=AGENT_A,
        owner_agent_id=AGENT_A,
        namespace=NAMESPACE,
        query="lexical",
        limit=5,
        capability_evaluator=_grants(AGENT_A, MEMORY_READ_ACTION),
    )
    success = [item.memory_id for item in results] == [record.memory_id]
    return _case(
        "M8",
        "Index rebuild",
        success,
        ("durable events", "rebuildable lexical index"),
        "Dropping and rebuilding the lexical index does not lose durable MemoryRecord facts.",
        {"memory_id": record.memory_id, "result_ids": [item.memory_id for item in results]},
    )


def _case(
    case_id: str,
    name: str,
    success: bool,
    mechanism: tuple[str, ...],
    oracle: str,
    evidence: Mapping[str, Any],
    limitations: tuple[str, ...] = (),
) -> MemoryBenchCase:
    return MemoryBenchCase(
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


def _grants(agent_id: str, *actions: str) -> CapabilityEvaluator:
    return CapabilityEvaluator(
        CapabilityGrant(agent_id, action, memory_namespace_scope(AGENT_A, NAMESPACE))
        for action in actions
    )


def _provenance(
    source_session_id: str | None = None,
    source_event_id: str | None = None,
) -> MemoryProvenance:
    return MemoryProvenance(
        source="session" if source_session_id else "host",
        source_session_id=source_session_id,
        source_event_id=source_event_id,
        source_agent_id=AGENT_A,
    )


def _stable_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _stable_json(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_stable_json(item) for item in value]
    if isinstance(value, Path):
        return value.as_posix()
    return value
