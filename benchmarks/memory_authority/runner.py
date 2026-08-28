"""Offline Shared Memory Authority V0.9D benchmark."""

from __future__ import annotations

import argparse
import json
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentkernel import (
    AgentRegistry,
    CapabilityDelegator,
    CapabilityEvaluator,
    CapabilityGrant,
    DelegateCapabilityRequest,
    EventType,
    InMemoryIPCPersistence,
    InMemoryMemoryStore,
    KernelIPC,
    LocalResourceStore,
    MEMORY_ADMIT_ACTION,
    MEMORY_FORGET_ACTION,
    MEMORY_PROPOSE_ACTION,
    MEMORY_READ_ACTION,
    MEMORY_WRITE_ACTION,
    ProcessControlBlock,
    ProcessManager,
    RESOURCE_READ_ACTION,
    ResourceAccessDenied,
    ResourceOwner,
    ResourceService,
    ResourceShareGrant,
    Session,
    MemoryAccessDenied,
    MemoryProvenance,
    MemoryService,
    memory_namespace_scope,
    project_memories_to_context_pages,
)
from benchmarks.runtimebench.environment import current_commit, generated_at


DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "results" / "memory_authority_v0.9d.json"
BENCHMARK_VERSION = "0.9D"
RUNTIME_VERSION = "AgentKernel V0.9D"
AGENT_A = "agent-a"
AGENT_B = "agent-b"
AGENT_C = "agent-c"
PUBLIC = "public"
PRIVATE = "private"


@dataclass(frozen=True, slots=True)
class AuthorityCase:
    case_id: str
    name: str
    status: str
    mechanism_under_test: tuple[str, ...]
    oracle: str
    evidence: Mapping[str, Any]
    limitations: tuple[str, ...] = (
        "Synthetic deterministic fixture; no cryptographic secrecy or information-flow control claim.",
    )

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
class AuthorityDocument:
    cases: tuple[AuthorityCase, ...]
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


def run_memory_authority_benchmark() -> AuthorityDocument:
    with tempfile.TemporaryDirectory(prefix="agentkernel-memory-authority-") as root:
        tmp = Path(root)
        cases = (
            _evaluate("D1", "URI Is Not Authority", ("MemoryService.read", "CapabilityEvaluator"), "Knowing memory://... without a current grant must deny.", _d1_uri_is_not_authority),
            _evaluate("D2", "Delegated Read", ("CapabilityDelegator", "memory.read"), "A delegated memory.read grant allows the child Agent to read exactly that memory.", _d2_delegated_read),
            _evaluate("D3", "Read/Mutation Separation", ("memory.read", "memory.write", "memory.forget", "memory.admit"), "memory.read never implies lifecycle or admission mutation authority.", _d3_read_mutation_separation),
            _evaluate("D4", "Delegated Mutation", ("CapabilityDelegator", "memory.write"), "Delegated mutation authority records the actor without changing owner.", _d4_delegated_mutation),
            _evaluate("D5", "Admission Separation", ("memory.propose", "memory.admit"), "memory.propose does not imply memory.admit.", _d5_admission_separation),
            _evaluate("D6", "Revocation By Evaluator", ("CapabilityEvaluator", "memory.read"), "Removing a grant from the current evaluator denies future access.", _d6_revocation_by_evaluator),
            _evaluate("D7", "Search Revocation", ("MemoryService.search",), "Fresh search excludes memory no longer authorized.", _d7_search_revocation),
            _evaluate("D8", "Context Revocation", ("Context VM", "MemoryService.search"), "Fresh Context projection excludes memory no longer authorized.", _d8_context_revocation),
            _evaluate("D9", "Historical Observation", ("Session", "MemoryService.read"), "Old Session observations remain durable even when future reads are denied.", _d9_historical_observation),
            _evaluate("D10", "IPC Is Not Authority", ("KernelIPC", "MemoryService.read"), "A memory URI carried in IPC payload is data, not authority.", _d10_ipc_is_not_authority),
            _evaluate("D11", "Enumeration Isolation", ("MemoryService.list", "MemoryService.history", "ResourceShareGrant"), "List/history/proposal enumeration filters per object scope; ResourceShare is not Memory authority.", lambda: _d11_enumeration_isolation(tmp)),
            _evaluate("D12", "Agent/Process Separation", ("ProcessControlBlock", "CapabilityEvaluator"), "Authority subject is Agent identity, not Process id.", _d12_agent_process_separation),
            _evaluate("D13", "Scope Isolation", ("memory_namespace_scope", "MemoryService.search"), "A namespace grant reveals matching scope only.", _d13_scope_isolation),
            _evaluate("D14", "Delegation Attenuation", ("CapabilityDelegator",), "Delegation cannot broaden parent authority.", _d14_delegation_attenuation),
            _evaluate("D15", "Trust Orthogonality", ("MemoryProposal", "CapabilityEvaluator"), "Trust/source class does not override read authority.", _d15_trust_orthogonality),
            _evaluate("D16", "Lifecycle Orthogonality", ("memory/stale", "memory.read"), "Lifecycle state does not create or remove authority by itself.", _d16_lifecycle_orthogonality),
        )
    return AuthorityDocument(cases=cases, commit=current_commit(), timestamp=generated_at())


def write_memory_authority_benchmark(
    document: AuthorityDocument,
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


def format_human_report(document: AuthorityDocument) -> str:
    payload = document.as_dict()
    lines = ["Shared Memory Authority V0.9D", ""]
    for case in document.cases:
        lines.append(f"{case.case_id} {case.name:<34} {case.status}")
    lines.extend(["", f"Memory Authority: {payload['passed']}/{payload['total']} PASS"])
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run deterministic AgentKernel Shared Memory Authority V0.9D."
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Write result artifact path.")
    parser.add_argument("--no-write", action="store_true", help="Do not write the result artifact.")
    args = parser.parse_args(argv)

    document = run_memory_authority_benchmark()
    print(
        json.dumps(document.as_dict(), ensure_ascii=False, indent=2, sort_keys=True)
        if args.json
        else format_human_report(document)
    )
    if not args.no_write:
        write_memory_authority_benchmark(document, args.output)
    return 0 if document.as_dict()["decision"] == "PASS" else 1


def _evaluate(
    case_id: str,
    name: str,
    mechanism: tuple[str, ...],
    oracle: str,
    check: Callable[[], tuple[bool, Mapping[str, Any]]],
) -> AuthorityCase:
    try:
        success, evidence = check()
    except Exception as error:  # pragma: no cover - benchmark artifact includes unexpected failures.
        success = False
        evidence = {"unexpected_error": f"{type(error).__name__}: {error}"}
    return AuthorityCase(
        case_id=case_id,
        name=name,
        status="PASS" if success else "FAIL",
        mechanism_under_test=mechanism,
        oracle=oracle,
        evidence=evidence,
    )


def _d1_uri_is_not_authority() -> tuple[bool, Mapping[str, Any]]:
    memory = _service()
    record = _write(memory, PRIVATE, "Secret launch plan.")
    denied = _denied(lambda: memory.read(record.uri, agent_id=AGENT_B, capability_evaluator=CapabilityEvaluator(())))
    return denied, {"uri_observed": record.uri, "read_denied": denied}


def _d2_delegated_read() -> tuple[bool, Mapping[str, Any]]:
    memory = _service()
    record = _write(memory, PUBLIC, "Shared roadmap summary.")
    decision = CapabilityDelegator().delegate(
        DelegateCapabilityRequest(AGENT_A, AGENT_B, MEMORY_READ_ACTION, record.uri),
        parent_grants=(CapabilityGrant(AGENT_A, MEMORY_READ_ACTION, record.uri),),
    )
    restored = memory.read(
        record.uri,
        agent_id=AGENT_B,
        capability_evaluator=CapabilityEvaluator((decision.delegated_grant,)),
    )
    return decision.allowed and restored.memory_id == record.memory_id, {
        "delegation_allowed": decision.allowed,
        "memory_id": restored.memory_id,
    }


def _d3_read_mutation_separation() -> tuple[bool, Mapping[str, Any]]:
    memory = _service()
    record = _write(memory, PUBLIC, "Read-only memory.")
    read_only = _memory_grant(AGENT_B, MEMORY_READ_ACTION, record.uri)
    can_read = memory.read(record.uri, agent_id=AGENT_B, capability_evaluator=read_only).memory_id == record.memory_id
    stale_denied = _denied(lambda: memory.mark_stale(record.uri, agent_id=AGENT_B, reason="read-only actor tried lifecycle mutation", evidence_provenance=_provenance(), capability_evaluator=read_only))
    forget_denied = _denied(lambda: memory.forget(record.uri, agent_id=AGENT_B, capability_evaluator=read_only))
    write_denied = _denied(lambda: memory.supersede(agent_id=AGENT_B, old_memory_id=record.memory_id, content="mutated", provenance=_provenance(), capability_evaluator=read_only))
    proposal = _proposal(memory, PUBLIC, "Read-only actor cannot admit.")
    admit_denied = _denied(lambda: memory.admit(proposal.proposal_id, agent_id=AGENT_B, reason="read-only actor tried to admit", evidence_provenance=_provenance(), capability_evaluator=read_only))
    return can_read and stale_denied and forget_denied and write_denied and admit_denied, {
        "can_read": can_read,
        "stale_denied": stale_denied,
        "forget_denied": forget_denied,
        "write_denied": write_denied,
        "admit_denied": admit_denied,
    }


def _d4_delegated_mutation() -> tuple[bool, Mapping[str, Any]]:
    memory = _service()
    record = _write(memory, PUBLIC, "Mutable by delegated actor.")
    decision = CapabilityDelegator().delegate(
        DelegateCapabilityRequest(AGENT_A, AGENT_B, MEMORY_WRITE_ACTION, record.uri),
        parent_grants=(CapabilityGrant(AGENT_A, MEMORY_WRITE_ACTION, record.uri),),
    )
    stale = memory.mark_stale(
        record.uri,
        agent_id=AGENT_B,
        reason="delegated actor supplied fresher evidence",
        evidence_provenance=_provenance(),
        capability_evaluator=CapabilityEvaluator((decision.delegated_grant,)),
    )
    event = memory.durable_events()[-1]
    return (
        decision.allowed
        and stale.lifecycle_state == "STALE"
        and stale.owner_agent_id == AGENT_A
        and event.agent_id == AGENT_B
        and event.owner_agent_id == AGENT_A
    ), {
        "delegation_allowed": decision.allowed,
        "lifecycle_state": stale.lifecycle_state,
        "owner_agent_id": stale.owner_agent_id,
        "mutation_actor": event.agent_id,
    }


def _d5_admission_separation() -> tuple[bool, Mapping[str, Any]]:
    memory = _service()
    propose_only = _grants(AGENT_B, _namespace_grant(MEMORY_PROPOSE_ACTION, PUBLIC))
    proposal = memory.propose(agent_id=AGENT_B, owner_agent_id=AGENT_A, namespace=PUBLIC, content="Shared memory proposal requires separate admission.", provenance=_provenance("tool", "TOOL_DERIVED"), capability_evaluator=propose_only)
    denied = _denied(lambda: memory.admit(proposal.proposal_id, agent_id=AGENT_B, reason="self admit", evidence_provenance=_provenance(), capability_evaluator=propose_only))
    admitted = _admit(memory, proposal.proposal_id, PUBLIC)
    return denied and admitted.owner_agent_id == AGENT_A, {
        "proposal_id": proposal.proposal_id,
        "proposer_agent_id": proposal.proposer_agent_id,
        "admit_denied_for_proposer": denied,
        "authorized_admit_owner": admitted.owner_agent_id,
    }


def _d6_revocation_by_evaluator() -> tuple[bool, Mapping[str, Any]]:
    memory = _service()
    record = _write(memory, PUBLIC, "Revocable fact.")
    delegated = _memory_grant(AGENT_B, MEMORY_READ_ACTION, record.uri)
    before = memory.read(record.uri, agent_id=AGENT_B, capability_evaluator=delegated).memory_id
    after_denied = _denied(lambda: memory.read(record.uri, agent_id=AGENT_B, capability_evaluator=CapabilityEvaluator(())))
    return before == record.memory_id and after_denied, {"before_read": before, "after_denied": after_denied}


def _d7_search_revocation() -> tuple[bool, Mapping[str, Any]]:
    memory = _service()
    record = _write(memory, PUBLIC, "Fresh search visible before revoke.")
    visible = memory.search(agent_id=AGENT_B, owner_agent_id=AGENT_A, namespace=None, query="search", limit=5, capability_evaluator=_memory_grant(AGENT_B, MEMORY_READ_ACTION, record.uri))
    revoked = memory.search(agent_id=AGENT_B, owner_agent_id=AGENT_A, namespace=None, query="search", limit=5, capability_evaluator=_grants(AGENT_B, _namespace_grant(MEMORY_READ_ACTION, PRIVATE)))
    return len(visible) == 1 and revoked == (), {
        "visible_before": len(visible),
        "visible_after": len(revoked),
    }


def _d8_context_revocation() -> tuple[bool, Mapping[str, Any]]:
    memory = _service()
    record = _write(memory, PUBLIC, "Fresh context visible before revoke.")
    visible = memory.search(agent_id=AGENT_B, owner_agent_id=AGENT_A, namespace=None, query="context", limit=5, capability_evaluator=_memory_grant(AGENT_B, MEMORY_READ_ACTION, record.uri))
    revoked = memory.search(agent_id=AGENT_B, owner_agent_id=AGENT_A, namespace=None, query="context", limit=5, capability_evaluator=_grants(AGENT_B, _namespace_grant(MEMORY_READ_ACTION, PRIVATE)))
    projection = project_memories_to_context_pages(revoked, top_k=5)
    return len(visible) == 1 and revoked == () and projection.pages == (), {
        "visible_before": len(visible),
        "visible_after": len(revoked),
        "context_pages_after": len(projection.pages),
    }


def _d9_historical_observation() -> tuple[bool, Mapping[str, Any]]:
    memory = _service()
    session = Session("session-b")
    record = _write(memory, PUBLIC, "Observed before revoke.")
    session.append(EventType.TOOL_RESULT, {"name": "memory.read", "memory_uri": record.uri, "content": record.content})
    denied = _denied(lambda: memory.read(record.uri, agent_id=AGENT_B, capability_evaluator=CapabilityEvaluator(())))
    return denied and session.events[0].data["content"] == record.content, {
        "future_read_denied": denied,
        "historical_content": session.events[0].data["content"],
    }


def _d10_ipc_is_not_authority() -> tuple[bool, Mapping[str, Any]]:
    memory = _service()
    record = _write(memory, PRIVATE, "IPC should not grant this.")
    registry = AgentRegistry()
    sender_session = Session("session-a")
    receiver_session = Session("session-b")
    sender = registry.create_root(agent_id=AGENT_A, session=sender_session, record=False)
    receiver = registry.create_root(agent_id=AGENT_B, session=receiver_session, record=False)
    manager = ProcessManager(agent_registry=registry)
    manager.create_process(process_id="process-a", agent=sender.control)
    manager.create_process(process_id="process-b", agent=receiver.control)
    ipc = KernelIPC(agent_registry=registry, process_manager=manager, sessions={AGENT_A: sender_session, AGENT_B: receiver_session}, persistence=InMemoryIPCPersistence())
    ipc.create_channel(channel_id="channel-ab", sender_agent_id=AGENT_A, receiver_agent_id=AGENT_B)
    ipc.send(channel_id="channel-ab", sender_process_id="process-a", payload={"memory_uri": record.uri})
    delivered = ipc.receive(channel_id="channel-ab", receiver_agent_id=AGENT_B)
    denied = _denied(lambda: memory.read(record.uri, agent_id=AGENT_B, capability_evaluator=CapabilityEvaluator(())))
    return delivered is not None and denied, {"payload": delivered.payload if delivered else None, "future_read_denied": denied}


def _d11_enumeration_isolation(tmp: Path) -> tuple[bool, Mapping[str, Any]]:
    memory = _service()
    public_record = _write(memory, PUBLIC, "Public fact.")
    private_record = _write(memory, PRIVATE, "Private fact.")
    public_proposal = _proposal(memory, PUBLIC, "Public proposal.")
    private_proposal = _proposal(memory, PRIVATE, "Private proposal.")
    admitted_public = _admit(memory, public_proposal.proposal_id, PUBLIC)
    _admit(memory, private_proposal.proposal_id, PRIVATE)
    public_read = _grants(AGENT_B, _namespace_grant(MEMORY_READ_ACTION, PUBLIC))
    listed = memory.list(agent_id=AGENT_B, owner_agent_id=AGENT_A, namespace=None, capability_evaluator=public_read)
    proposals = memory.list_proposals(agent_id=AGENT_B, owner_agent_id=AGENT_A, namespace=None, capability_evaluator=public_read)
    decisions = memory.admission_history(agent_id=AGENT_B, owner_agent_id=AGENT_A, namespace=None, capability_evaluator=public_read)
    service = ResourceService(LocalResourceStore(tmp / "resources"), resource_id_factory=lambda: "res_0001", handle_id_factory=lambda: "hdl_0001")
    handle = service.create_artifact(b"bytes", owner=ResourceOwner(AGENT_A, "session-a"), media_type="text/plain", encoding="utf-8", source_tool_name="producer", source_tool_call_id="call-1", source_operation_id="op-1")
    resource_payload = ResourceShareGrant("share_0001", "res_0001", AGENT_A, AGENT_B, (RESOURCE_READ_ACTION,), 1.0, "corr-1").as_payload()
    resource_denied_with_memory_grant = _denied(lambda: service.read(handle.uri, owner=ResourceOwner(AGENT_B, "session-b"), capability_evaluator=public_read), ResourceAccessDenied)
    listed_ids = [item.memory_id for item in listed]
    success = (
        listed_ids == [public_record.memory_id, admitted_public.memory_id]
        and [item.proposal_id for item in proposals] == [public_proposal.proposal_id]
        and [item.proposal_id for item in decisions] == [public_proposal.proposal_id]
        and private_record.memory_id not in listed_ids
        and resource_denied_with_memory_grant
    )
    return success, {
        "listed_memory_ids": listed_ids,
        "visible_proposals": [item.proposal_id for item in proposals],
        "visible_decisions": [item.proposal_id for item in decisions],
        "resource_denied_with_memory_grant": resource_denied_with_memory_grant,
        "resource_share_payload_resource_id": resource_payload["resource_id"],
    }


def _d12_agent_process_separation() -> tuple[bool, Mapping[str, Any]]:
    memory = _service()
    record = _write(memory, PUBLIC, "Agent-owned memory.")
    registry = AgentRegistry()
    agent = registry.create_root(agent_id=AGENT_B, session=Session("session-b"), record=False)
    process = ProcessControlBlock.create(process_id=AGENT_A, agent=agent.control)
    process_grant = CapabilityEvaluator((CapabilityGrant(process.process_id, MEMORY_READ_ACTION, record.uri),))
    denied = _denied(lambda: memory.read(record.uri, agent_id=AGENT_B, capability_evaluator=process_grant))
    return process.capability_snapshot.agent_id == AGENT_B and denied, {
        "process_id": process.process_id,
        "process_agent_id": process.agent_id,
        "grant_subject": process.process_id,
        "request_subject": AGENT_B,
        "denied": denied,
    }


def _d13_scope_isolation() -> tuple[bool, Mapping[str, Any]]:
    memory = _service()
    _write(memory, PUBLIC, "Public only.")
    private = _write(memory, PRIVATE, "Private only.")
    public_read = _grants(AGENT_B, _namespace_grant(MEMORY_READ_ACTION, PUBLIC))
    private_denied = _denied(lambda: memory.read(private.uri, agent_id=AGENT_B, capability_evaluator=public_read))
    results = memory.search(agent_id=AGENT_B, owner_agent_id=AGENT_A, namespace=None, query="only", limit=5, capability_evaluator=public_read)
    return private_denied and [item.namespace for item in results] == [PUBLIC], {
        "private_denied": private_denied,
        "result_namespaces": [item.namespace for item in results],
    }


def _d14_delegation_attenuation() -> tuple[bool, Mapping[str, Any]]:
    narrow = CapabilityGrant(AGENT_A, MEMORY_READ_ACTION, memory_namespace_scope(AGENT_A, PUBLIC))
    decision = CapabilityDelegator().delegate(
        DelegateCapabilityRequest(AGENT_A, AGENT_B, MEMORY_READ_ACTION, f"memory://{AGENT_A}/**"),
        parent_grants=(narrow,),
    )
    return not decision.allowed and decision.reason == "parent_authority_not_found", {
        "delegation_allowed": decision.allowed,
        "reason": decision.reason,
    }


def _d15_trust_orthogonality() -> tuple[bool, Mapping[str, Any]]:
    memory = _service()
    proposal = memory.propose(agent_id=AGENT_A, owner_agent_id=AGENT_A, namespace=PUBLIC, content="Tool-derived claim.", provenance=_provenance("read_file", "TOOL_DERIVED"), capability_evaluator=_grants(AGENT_A, _namespace_grant(MEMORY_PROPOSE_ACTION, PUBLIC)))
    read = _grants(AGENT_B, _namespace_grant(MEMORY_READ_ACTION, PUBLIC))
    visible = memory.read_proposal(proposal.proposal_id, agent_id=AGENT_B, capability_evaluator=read)
    denied = _denied(lambda: memory.read_proposal(proposal.proposal_id, agent_id=AGENT_C, capability_evaluator=CapabilityEvaluator(())))
    return visible.has_untrusted_origin and denied, {
        "source_class": visible.provenance.source_class,
        "has_untrusted_origin": visible.has_untrusted_origin,
        "unauthorized_denied": denied,
    }


def _d16_lifecycle_orthogonality() -> tuple[bool, Mapping[str, Any]]:
    memory = _service()
    record = _write(memory, PUBLIC, "Lifecycle gated by authority.")
    stale = memory.mark_stale(record.uri, agent_id=AGENT_A, reason="fresh evidence", evidence_provenance=_provenance(), capability_evaluator=_grants(AGENT_A, _namespace_grant(MEMORY_WRITE_ACTION, PUBLIC)))
    visible = memory.read(record.uri, agent_id=AGENT_B, include_inactive=True, capability_evaluator=_memory_grant(AGENT_B, MEMORY_READ_ACTION, record.uri))
    denied = _denied(lambda: memory.read(record.uri, agent_id=AGENT_C, include_inactive=True, capability_evaluator=CapabilityEvaluator(())))
    return stale.lifecycle_state == "STALE" and visible.lifecycle_state == "STALE" and denied, {
        "lifecycle_state": visible.lifecycle_state,
        "unauthorized_denied": denied,
    }

def _service(store=None) -> MemoryService:
    memory_ids = iter(f"mem_{index:04d}" for index in range(3000))
    event_ids = iter(f"mev_{index:04d}" for index in range(7000))
    proposal_ids = iter(f"mpr_{index:04d}" for index in range(3000))
    decision_ids = iter(f"mad_{index:04d}" for index in range(3000))
    ticks = iter(float(index) for index in range(12000))
    return MemoryService(
        store or InMemoryMemoryStore(),
        memory_id_factory=lambda: next(memory_ids),
        event_id_factory=lambda: next(event_ids),
        proposal_id_factory=lambda: next(proposal_ids),
        decision_id_factory=lambda: next(decision_ids),
        clock=lambda: next(ticks),
    )


def _grants(agent_id: str, *items: tuple[str, str]) -> CapabilityEvaluator:
    return CapabilityEvaluator(CapabilityGrant(agent_id, action, scope) for action, scope in items)


def _namespace_grant(action: str, namespace: str) -> tuple[str, str]:
    return (action, memory_namespace_scope(AGENT_A, namespace))


def _memory_grant(agent_id: str, action: str, uri: str) -> CapabilityEvaluator:
    return CapabilityEvaluator((CapabilityGrant(agent_id, action, uri),))


def _provenance(source: str = "host", source_class: str = "HOST_VERIFIED") -> MemoryProvenance:
    return MemoryProvenance(
        source=source,
        source_class=source_class,  # type: ignore[arg-type]
        source_session_id="session-a",
        source_event_id="event-1",
        source_agent_id=AGENT_A,
    )


def _write(memory: MemoryService, namespace: str, content: str):
    return memory.remember(
        agent_id=AGENT_A,
        owner_agent_id=AGENT_A,
        namespace=namespace,
        content=content,
        provenance=_provenance(),
        capability_evaluator=_grants(AGENT_A, _namespace_grant(MEMORY_WRITE_ACTION, namespace)),
    )


def _proposal(memory: MemoryService, namespace: str, content: str = "Candidate memory."):
    return memory.propose(
        agent_id=AGENT_A,
        owner_agent_id=AGENT_A,
        namespace=namespace,
        content=content,
        provenance=_provenance("user", "USER_EXPLICIT"),
        capability_evaluator=_grants(AGENT_A, _namespace_grant(MEMORY_PROPOSE_ACTION, namespace)),
    )


def _admit(memory: MemoryService, proposal_id: str, namespace: str):
    return memory.admit(
        proposal_id,
        agent_id=AGENT_A,
        reason="host admitted",
        evidence_provenance=_provenance(),
        capability_evaluator=_grants(AGENT_A, _namespace_grant(MEMORY_ADMIT_ACTION, namespace)),
    )


def _denied(call: Callable[[], object], error_type: type[Exception] = MemoryAccessDenied) -> bool:
    try:
        call()
    except error_type:
        return True
    return False


def _stable_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _stable_json(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_stable_json(item) for item in value]
    if isinstance(value, Path):
        return value.as_posix()
    return value
