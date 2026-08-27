"""Integrated replay coordinator for V0.8 multi-agent runtime recovery."""

from __future__ import annotations

import copy
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from .agent import AgentControlBlock, AgentRegistry, AgentRegistryError
from .accounting import HostBudget
from .capabilities import CapabilityGrant
from .events import EventType
from .ipc import IPCError, IPCPersistence, KernelIPC
from .protocol import JsonValue
from .recovery import (
    DurableOperationRecovery,
    OperationRecoveryClassification,
    RecoveryAnalysis,
    SessionStatus,
)
from .resources import ResourceShareError, ResourceShareRegistry, ResourceStore
from .scheduler import (
    CooperativeScheduler,
    ProcessManager,
    ProcessNotFound,
    ProcessTreeError,
)
from .session import Session


class MultiAgentRecoveryCorruptionError(RuntimeError):
    """Raised when durable multi-agent facts cannot be replayed consistently."""


class ProcessRecoveryDisposition(StrEnum):
    """Runtime disposition after durable facts have been replayed."""

    NOT_ADMITTED = "NOT_ADMITTED"
    NEEDS_RECONCILIATION = "NEEDS_RECONCILIATION"
    CORRUPTED = "CORRUPTED"


@dataclass(frozen=True, slots=True)
class RecoveryDisposition:
    """Recovery disposition for one reconstructed Process identity."""

    process_id: str
    agent_id: str
    session_id: str
    disposition: ProcessRecoveryDisposition
    reason: str


@dataclass(frozen=True, slots=True)
class DurableRecoveryObligation:
    """A durable side-effect obligation surfaced during integrated recovery."""

    session_id: str
    operation_id: str
    classification: OperationRecoveryClassification
    tool_call_id: str
    tool_name: str
    effect_kind: str
    turn: int
    step: int
    dispatch_attempts: int
    authorization: Mapping[str, JsonValue] | None = None


@dataclass(frozen=True, slots=True)
class MultiAgentRecoveryResult:
    """Fresh runtime objects and validated durable recovery facts."""

    agent_registry: AgentRegistry
    process_manager: ProcessManager
    scheduler: CooperativeScheduler
    resource_shares: ResourceShareRegistry | None
    ipc: KernelIPC | None
    session_analyses: Mapping[str, RecoveryAnalysis]
    process_dispositions: tuple[RecoveryDisposition, ...]
    durable_obligations: tuple[DurableRecoveryObligation, ...]
    warnings: tuple[str, ...] = ()


class IntegratedRecoveryCoordinator:
    """Reconstruct coordinated multi-agent runtime state from durable facts.

    The coordinator validates durable semantic facts and returns fresh runtime
    mechanisms. It does not repair sessions, schedule processes, deliver IPC,
    reconcile durable tools, or retry side effects.
    """

    def recover(
        self,
        sessions: Iterable[Session],
        *,
        current_capability_grants: Mapping[str, Iterable[CapabilityGrant]]
        | None = None,
        resource_store: ResourceStore | None = None,
        ipc_persistence: IPCPersistence | None = None,
        host_budget: HostBudget | None = None,
    ) -> MultiAgentRecoveryResult:
        session_tuple = tuple(sessions)
        session_by_id = _sessions_by_id(session_tuple)
        analyses = _analyze_sessions(session_tuple)

        try:
            agent_registry = AgentRegistry.reconstruct(
                session_tuple,
                current_capability_grants=current_capability_grants,
            )
            _validate_session_ownership(session_by_id, agent_registry)

            process_manager = ProcessManager.reconstruct(
                session_tuple,
                agent_registry=agent_registry,
            )
            _validate_process_ownership(process_manager, agent_registry, session_by_id)

            resource_shares = None
            if resource_store is None:
                if _has_resource_share_facts(session_tuple):
                    raise MultiAgentRecoveryCorruptionError(
                        "ResourceStore is required to replay resource/shared facts"
                    )
            else:
                resource_shares = ResourceShareRegistry.reconstruct(
                    session_tuple,
                    agent_registry=agent_registry,
                    resource_lookup=resource_store.stat,
                )

            scheduler = CooperativeScheduler(
                process_manager,
                host_budget=host_budget,
            )

            ipc = None
            if ipc_persistence is None:
                if _has_ipc_audit_facts(session_tuple):
                    raise MultiAgentRecoveryCorruptionError(
                        "IPCPersistence is required to replay IPC audit facts"
                    )
            else:
                ipc = KernelIPC.reconstruct(
                    agent_registry=agent_registry,
                    process_manager=process_manager,
                    scheduler=scheduler,
                    persistence=ipc_persistence,
                    sessions=_sessions_by_agent(agent_registry, session_by_id),
                )
                _validate_ipc_runtime(ipc, process_manager, agent_registry)
        except (
            AgentRegistryError,
            IPCError,
            ProcessTreeError,
            ResourceShareError,
            ValueError,
            TypeError,
        ) as error:
            raise MultiAgentRecoveryCorruptionError(str(error)) from error

        _validate_durable_authorization_principals(
            analyses,
            agent_registry,
        )
        obligations = _durable_obligations(analyses)
        dispositions = _process_dispositions(
            process_manager,
            obligations_by_session=_obligations_by_session(obligations),
        )
        warnings = _warnings_from_analyses(analyses)

        return MultiAgentRecoveryResult(
            agent_registry=agent_registry,
            process_manager=process_manager,
            scheduler=scheduler,
            resource_shares=resource_shares,
            ipc=ipc,
            session_analyses=dict(sorted(analyses.items())),
            process_dispositions=dispositions,
            durable_obligations=obligations,
            warnings=warnings,
        )


def recover_multi_agent_runtime(
    sessions: Iterable[Session],
    *,
    current_capability_grants: Mapping[str, Iterable[CapabilityGrant]] | None = None,
    resource_store: ResourceStore | None = None,
    ipc_persistence: IPCPersistence | None = None,
    host_budget: HostBudget | None = None,
) -> MultiAgentRecoveryResult:
    """Convenience wrapper around :class:`IntegratedRecoveryCoordinator`."""

    return IntegratedRecoveryCoordinator().recover(
        sessions,
        current_capability_grants=current_capability_grants,
        resource_store=resource_store,
        ipc_persistence=ipc_persistence,
        host_budget=host_budget,
    )


def _sessions_by_id(sessions: tuple[Session, ...]) -> dict[str, Session]:
    by_id: dict[str, Session] = {}
    for session in sessions:
        if session.session_id in by_id:
            raise MultiAgentRecoveryCorruptionError(
                f"duplicate Session supplied for recovery: {session.session_id}"
            )
        by_id[session.session_id] = session
    return by_id


def _analyze_sessions(sessions: tuple[Session, ...]) -> dict[str, RecoveryAnalysis]:
    analyses: dict[str, RecoveryAnalysis] = {}
    for session in sessions:
        try:
            analysis = session.recovery_analysis
        except Exception as error:
            raise MultiAgentRecoveryCorruptionError(
                f"session {session.session_id!r} failed recovery analysis: {error}"
            ) from error
        if analysis.status is SessionStatus.CORRUPTED:
            raise MultiAgentRecoveryCorruptionError(
                f"session {session.session_id!r} is corrupted: {analysis.corruption}"
            )
        analyses[session.session_id] = analysis
    return analyses


def _validate_session_ownership(
    session_by_id: Mapping[str, Session],
    agent_registry: AgentRegistry,
) -> None:
    agents = _registry_agents(agent_registry)
    owners_by_session: dict[str, str] = {}
    for agent in agents:
        if agent.session_id not in session_by_id:
            raise MultiAgentRecoveryCorruptionError(
                f"Agent {agent.agent_id!r} primary Session is missing: "
                f"{agent.session_id!r}"
            )
        previous = owners_by_session.get(agent.session_id)
        if previous is not None:
            raise MultiAgentRecoveryCorruptionError(
                f"Session {agent.session_id!r} belongs to multiple Agents: "
                f"{previous!r}, {agent.agent_id!r}"
            )
        owners_by_session[agent.session_id] = agent.agent_id
    for session_id in session_by_id:
        if session_id not in owners_by_session:
            raise MultiAgentRecoveryCorruptionError(
                f"Session {session_id!r} does not belong to a reconstructed Agent"
            )


def _validate_process_ownership(
    process_manager: ProcessManager,
    agent_registry: AgentRegistry,
    session_by_id: Mapping[str, Session],
) -> None:
    process_ids = {process.process_id for process in process_manager.list_processes()}
    for process in process_manager.list_processes():
        if process.session_id not in session_by_id:
            raise MultiAgentRecoveryCorruptionError(
                f"Process {process.process_id!r} Session is missing: "
                f"{process.session_id!r}"
            )
        if not agent_registry.contains(process.agent_id):
            raise MultiAgentRecoveryCorruptionError(
                f"Process {process.process_id!r} references missing Agent "
                f"{process.agent_id!r}"
            )
        agent = agent_registry.get(process.agent_id)
        if process.session_id != agent.session_id:
            raise MultiAgentRecoveryCorruptionError(
                f"Process {process.process_id!r} Session does not match Agent "
                f"{process.agent_id!r}"
            )
        if (
            process.parent_process_id is not None
            and process.parent_process_id not in process_ids
        ):
            raise MultiAgentRecoveryCorruptionError(
                f"Process {process.process_id!r} references missing parent "
                f"{process.parent_process_id!r}"
            )


def _validate_durable_authorization_principals(
    analyses: Mapping[str, RecoveryAnalysis],
    agent_registry: AgentRegistry,
) -> None:
    owner_by_session = {
        agent.session_id: agent.agent_id
        for agent in _registry_agents(agent_registry)
    }
    for session_id, analysis in sorted(analyses.items()):
        owner_agent_id = owner_by_session.get(session_id)
        for operation in analysis.durable_operations:
            if operation.authorization is None:
                continue
            authorized_agent_id = operation.authorization.get("agent_id")
            if authorized_agent_id != owner_agent_id:
                raise MultiAgentRecoveryCorruptionError(
                    "durable operation authorization agent_id does not match "
                    "owning Session Agent: "
                    f"{operation.operation_id!r} in {session_id!r}"
                )


def _validate_ipc_runtime(
    ipc: KernelIPC,
    process_manager: ProcessManager,
    agent_registry: AgentRegistry,
) -> None:
    for channel in ipc.list_channels():
        _require_agent(agent_registry, channel.sender_agent_id)
        _require_agent(agent_registry, channel.receiver_agent_id)
        if channel.receiver_process_id is not None:
            receiver = _require_process(process_manager, channel.receiver_process_id)
            if receiver.agent_id != channel.receiver_agent_id:
                raise MultiAgentRecoveryCorruptionError(
                    "IPC channel receiver Process does not belong to receiver Agent"
                )
    for message in ipc.list_messages():
        _require_agent(agent_registry, message.sender_agent_id)
        _require_agent(agent_registry, message.receiver_agent_id)
        sender = _require_process(process_manager, message.sender_process_id)
        if sender.agent_id != message.sender_agent_id:
            raise MultiAgentRecoveryCorruptionError(
                "IPC message sender Process does not belong to sender Agent"
            )
        if message.receiver_process_id is not None:
            receiver = _require_process(process_manager, message.receiver_process_id)
            if receiver.agent_id != message.receiver_agent_id:
                raise MultiAgentRecoveryCorruptionError(
                    "IPC message receiver Process does not belong to receiver Agent"
                )


def _durable_obligations(
    analyses: Mapping[str, RecoveryAnalysis],
) -> tuple[DurableRecoveryObligation, ...]:
    obligations: list[DurableRecoveryObligation] = []
    for session_id, analysis in sorted(analyses.items()):
        for operation in analysis.durable_operations:
            if operation.classification is OperationRecoveryClassification.COMPLETED:
                continue
            obligations.append(_obligation_from_operation(session_id, operation))
    return tuple(
        sorted(
            obligations,
            key=lambda item: (
                item.session_id,
                item.turn,
                item.step,
                item.operation_id,
            ),
        )
    )


def _obligation_from_operation(
    session_id: str,
    operation: DurableOperationRecovery,
) -> DurableRecoveryObligation:
    return DurableRecoveryObligation(
        session_id=session_id,
        operation_id=operation.operation_id,
        classification=operation.classification,
        tool_call_id=operation.tool_call.call_id,
        tool_name=operation.tool_call.name,
        effect_kind=operation.effect_kind.value,
        turn=operation.turn,
        step=operation.step,
        dispatch_attempts=operation.dispatch_attempts,
        authorization=copy.deepcopy(
            None if operation.authorization is None else dict(operation.authorization)
        ),
    )


def _obligations_by_session(
    obligations: tuple[DurableRecoveryObligation, ...],
) -> dict[str, tuple[DurableRecoveryObligation, ...]]:
    grouped: dict[str, list[DurableRecoveryObligation]] = {}
    for obligation in obligations:
        grouped.setdefault(obligation.session_id, []).append(obligation)
    return {session_id: tuple(items) for session_id, items in grouped.items()}


def _process_dispositions(
    process_manager: ProcessManager,
    *,
    obligations_by_session: Mapping[str, tuple[DurableRecoveryObligation, ...]],
) -> tuple[RecoveryDisposition, ...]:
    dispositions: list[RecoveryDisposition] = []
    for process in sorted(
        process_manager.list_processes(),
        key=lambda item: item.process_id,
    ):
        obligations = obligations_by_session.get(process.session_id, ())
        if any(
            obligation.classification
            in {
                OperationRecoveryClassification.RECONCILE_REQUIRED,
                OperationRecoveryClassification.MANUAL_REQUIRED,
            }
            for obligation in obligations
        ):
            disposition = ProcessRecoveryDisposition.NEEDS_RECONCILIATION
            reason = "durable_side_effect_requires_host_action"
        else:
            disposition = ProcessRecoveryDisposition.NOT_ADMITTED
            reason = "fresh_scheduler_does_not_restore_runtime_state"
        dispositions.append(
            RecoveryDisposition(
                process_id=process.process_id,
                agent_id=process.agent_id,
                session_id=process.session_id,
                disposition=disposition,
                reason=reason,
            )
        )
    return tuple(dispositions)


def _warnings_from_analyses(
    analyses: Mapping[str, RecoveryAnalysis],
) -> tuple[str, ...]:
    warnings: list[str] = []
    for session_id, analysis in sorted(analyses.items()):
        warnings.extend(f"{session_id}: {warning}" for warning in analysis.warnings)
    return tuple(warnings)


def _has_resource_share_facts(sessions: tuple[Session, ...]) -> bool:
    return _has_session_event_type(sessions, {EventType.RESOURCE_SHARED})


def _has_ipc_audit_facts(sessions: tuple[Session, ...]) -> bool:
    return _has_session_event_type(
        sessions,
        {EventType.IPC_SEND, EventType.IPC_RECEIVE, EventType.IPC_ACK},
    )


def _has_session_event_type(
    sessions: tuple[Session, ...],
    event_types: frozenset[EventType] | set[EventType],
) -> bool:
    return any(
        event.type in event_types
        for session in sessions
        for event in session.events
    )


def _sessions_by_agent(
    agent_registry: AgentRegistry,
    session_by_id: Mapping[str, Session],
) -> dict[str, Session]:
    return {
        agent.agent_id: session_by_id[agent.session_id]
        for agent in _registry_agents(agent_registry)
    }


def _registry_agents(agent_registry: AgentRegistry) -> tuple[AgentControlBlock, ...]:
    return agent_registry.list_agents()


def _require_agent(agent_registry: AgentRegistry, agent_id: str) -> AgentControlBlock:
    if not agent_registry.contains(agent_id):
        raise MultiAgentRecoveryCorruptionError(f"Agent not found: {agent_id}")
    return agent_registry.get(agent_id)


def _require_process(process_manager: ProcessManager, process_id: str):
    try:
        return process_manager.get(process_id)
    except ProcessNotFound as error:
        raise MultiAgentRecoveryCorruptionError(
            f"Process not found: {process_id}"
        ) from error
