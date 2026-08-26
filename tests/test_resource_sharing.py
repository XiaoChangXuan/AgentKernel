from __future__ import annotations

import inspect

import pytest

from agentkernel import (
    ARTIFACT_RESOURCE_SCOPE,
    AgentRegistry,
    AuthorizationRequest,
    CapabilityEvaluator,
    CapabilityGrant,
    DelegateCapabilityRequest,
    EventType,
    InMemoryIPCPersistence,
    KernelIPC,
    LocalResourceStore,
    ProcessManager,
    RESOURCE_READ_ACTION,
    RESOURCE_STAT_ACTION,
    ResourceAccessDenied,
    ResourceNotFound,
    ResourceOwner,
    ResourceService,
    ResourceShareConflict,
    ResourceShareCorruptionError,
    ResourceShareGrant,
    ResourceShareRegistry,
    ResourceShareRequest,
    Session,
)


OWNER = ResourceOwner("agent-owner", "session-owner")
GRANTEE = ResourceOwner("agent-grantee", "session-grantee")
THIRD = ResourceOwner("agent-third", "session-third")


def _registry_with_agents() -> tuple[AgentRegistry, Session, Session, Session]:
    registry = AgentRegistry()
    owner_session = Session(OWNER.session_id)
    grantee_session = Session(GRANTEE.session_id)
    third_session = Session(THIRD.session_id)
    registry.create_root(
        agent_id=OWNER.agent_id,
        session=owner_session,
        creation_id="create-owner",
        record=False,
    )
    registry.create_child(
        parent_agent_id=OWNER.agent_id,
        agent_id=GRANTEE.agent_id,
        session=grantee_session,
        creation_id="create-grantee",
        record=False,
    )
    registry.create_root(
        agent_id=THIRD.agent_id,
        session=third_session,
        creation_id="create-third",
        record=False,
    )
    return registry, owner_session, grantee_session, third_session


def _resource_world(tmp_path):
    registry, owner_session, grantee_session, third_session = _registry_with_agents()
    store = LocalResourceStore(tmp_path / "resources")
    shares = ResourceShareRegistry(
        agent_registry=registry,
        clock=lambda: 100.0,
        share_id_factory=lambda: "share_generated",
    )
    service = ResourceService(
        store,
        share_registry=shares,
        resource_id_factory=lambda: "res_secret",
        handle_id_factory=lambda: "hdl_secret",
        clock=lambda: 10.0,
    )
    handle = service.create_artifact(
        b"secret-bytes",
        owner=OWNER,
        media_type="text/plain",
        encoding="utf-8",
        source_tool_name="producer",
        source_tool_call_id="call-producer",
        source_operation_id="op-producer",
    )
    return (
        registry,
        owner_session,
        grantee_session,
        third_session,
        store,
        shares,
        service,
        handle,
    )


def _capability_evaluator(
    agent_id: str = GRANTEE.agent_id,
    *,
    read: bool = True,
    stat: bool = True,
) -> CapabilityEvaluator:
    grants: list[CapabilityGrant] = []
    if read:
        grants.append(
            CapabilityGrant(agent_id, RESOURCE_READ_ACTION, ARTIFACT_RESOURCE_SCOPE)
        )
    if stat:
        grants.append(
            CapabilityGrant(agent_id, RESOURCE_STAT_ACTION, ARTIFACT_RESOURCE_SCOPE)
        )
    return CapabilityEvaluator(grants)


def test_resource_share_grant_payload_round_trips_canonically() -> None:
    grant = ResourceShareGrant(
        share_id="share_manual",
        resource_id="res_secret",
        owner_agent_id=OWNER.agent_id,
        grantee_agent_id=GRANTEE.agent_id,
        allowed_actions=(RESOURCE_STAT_ACTION, RESOURCE_READ_ACTION),
        created_at=1.0,
        correlation_id="corr-share",
        expires_at={"note": "metadata-only"},
    )

    payload = grant.as_payload()
    restored = ResourceShareGrant.from_payload(payload)

    assert restored == grant
    assert payload == {
        "share_id": "share_manual",
        "resource_id": "res_secret",
        "owner_agent_id": OWNER.agent_id,
        "grantee_agent_id": GRANTEE.agent_id,
        "allowed_actions": [RESOURCE_READ_ACTION, RESOURCE_STAT_ACTION],
        "created_at": 1.0,
        "correlation_id": "corr-share",
        "expires_at": {"note": "metadata-only"},
    }


def test_owner_creates_exact_share_and_ownership_does_not_change(tmp_path) -> None:
    (
        _registry,
        owner_session,
        _grantee_session,
        _third_session,
        store,
        shares,
        service,
        handle,
    ) = _resource_world(tmp_path)

    decision = service.share(
        handle.uri,
        owner=OWNER,
        grantee_agent_id=GRANTEE.agent_id,
        allowed_actions=(RESOURCE_READ_ACTION,),
        record_session=owner_session,
        share_id="share_manual",
        correlation_id="corr-share",
    )

    assert decision.allowed is True
    assert decision.reason == "allowed"
    assert decision.grant is not None
    assert decision.grant.resource_id == "res_secret"
    assert decision.grant.allowed_actions == (RESOURCE_READ_ACTION,)
    assert shares.get_share("share_manual") == decision.grant
    assert [event.type for event in owner_session.events] == [
        EventType.RESOURCE_SHARED
    ]
    assert owner_session.events[0].data == decision.grant.as_payload()
    assert store.stat("res_secret").owner == OWNER


def test_share_creation_denies_invalid_principals_resources_and_actions(tmp_path) -> None:
    (
        _registry,
        owner_session,
        _grantee_session,
        _third_session,
        _store,
        _shares,
        service,
        handle,
    ) = _resource_world(tmp_path)

    assert service.share(
        handle.uri,
        owner=GRANTEE,
        grantee_agent_id=THIRD.agent_id,
        allowed_actions=(RESOURCE_READ_ACTION,),
        record_session=owner_session,
        share_id="share_nonowner",
    ).reason == "not_resource_owner"
    assert service.share(
        "artifact://res_missing",
        owner=OWNER,
        grantee_agent_id=GRANTEE.agent_id,
        allowed_actions=(RESOURCE_READ_ACTION,),
        record_session=owner_session,
        share_id="share_missing",
    ).reason == "resource_not_found"
    assert service.share(
        handle.uri,
        owner=OWNER,
        grantee_agent_id="agent-missing",
        allowed_actions=(RESOURCE_READ_ACTION,),
        record_session=owner_session,
        share_id="share_unknown_grantee",
    ).reason == "grantee_agent_not_found"
    assert service.share(
        handle.uri,
        owner=OWNER,
        grantee_agent_id=OWNER.agent_id,
        allowed_actions=(RESOURCE_READ_ACTION,),
        record_session=owner_session,
        share_id="share_self",
    ).reason == "self_share_denied"
    assert service.share(
        handle.uri,
        owner=OWNER,
        grantee_agent_id=GRANTEE.agent_id,
        allowed_actions=("resource.delete",),
        record_session=owner_session,
        share_id="share_delete",
    ).reason == "unsupported_action"


def test_cross_agent_access_requires_capability_and_active_share(tmp_path) -> None:
    (
        _registry,
        owner_session,
        _grantee_session,
        _third_session,
        _store,
        _shares,
        service,
        handle,
    ) = _resource_world(tmp_path)
    read_capability = _capability_evaluator(read=True, stat=False)
    no_capability = CapabilityEvaluator(())

    with pytest.raises(ResourceAccessDenied, match="not shared"):
        service.read(
            handle.uri,
            owner=GRANTEE,
            capability_evaluator=read_capability,
        )

    service.share(
        handle.uri,
        owner=OWNER,
        grantee_agent_id=GRANTEE.agent_id,
        allowed_actions=(RESOURCE_READ_ACTION,),
        record_session=owner_session,
        share_id="share_read",
    )

    with pytest.raises(ResourceAccessDenied, match="lacks"):
        service.read(
            handle.uri,
            owner=GRANTEE,
            capability_evaluator=no_capability,
        )

    read = service.read(
        handle.uri,
        owner=GRANTEE,
        capability_evaluator=read_capability,
    )

    assert read.data == b"secret-bytes"


def test_share_actions_are_narrow_and_independent(tmp_path) -> None:
    (
        _registry,
        owner_session,
        _grantee_session,
        _third_session,
        _store,
        _shares,
        service,
        handle,
    ) = _resource_world(tmp_path)
    evaluator = _capability_evaluator(read=True, stat=True)
    service.share(
        handle.uri,
        owner=OWNER,
        grantee_agent_id=GRANTEE.agent_id,
        allowed_actions=(RESOURCE_STAT_ACTION,),
        record_session=owner_session,
        share_id="share_stat",
    )

    assert service.stat(
        handle.uri,
        owner=GRANTEE,
        capability_evaluator=evaluator,
    ).uri == handle.uri
    with pytest.raises(ResourceAccessDenied, match="not shared"):
        service.read(
            handle.uri,
            owner=GRANTEE,
            capability_evaluator=evaluator,
        )


def test_handle_ipc_payload_and_grant_like_data_do_not_create_share(tmp_path) -> None:
    (
        registry,
        owner_session,
        grantee_session,
        _third_session,
        _store,
        _shares,
        service,
        handle,
    ) = _resource_world(tmp_path)
    manager = ProcessManager(agent_registry=registry)
    manager.create_process(process_id="process-owner", agent=registry.get(OWNER.agent_id))
    manager.create_child_process(
        parent_process_id="process-owner",
        process_id="process-grantee",
        agent=registry.get(GRANTEE.agent_id),
    )
    ipc = KernelIPC(
        agent_registry=registry,
        process_manager=manager,
        sessions={OWNER.agent_id: owner_session, GRANTEE.agent_id: grantee_session},
        persistence=InMemoryIPCPersistence(),
    )
    ipc.create_channel(
        channel_id="channel-share-test",
        sender_agent_id=OWNER.agent_id,
        receiver_agent_id=GRANTEE.agent_id,
    )

    ipc.send(
        channel_id="channel-share-test",
        sender_process_id="process-owner",
        payload={
            "grant": ResourceShareGrant(
                share_id="share_payload",
                resource_id="res_secret",
                owner_agent_id=OWNER.agent_id,
                grantee_agent_id=GRANTEE.agent_id,
                allowed_actions=(RESOURCE_READ_ACTION,),
                created_at=1.0,
                correlation_id="payload-only",
            ).as_payload(),
        },
        resource_refs=[handle.uri],
        message_id="message-resource-ref",
    )
    delivered = ipc.receive(
        channel_id="channel-share-test",
        receiver_agent_id=GRANTEE.agent_id,
    )

    assert delivered is not None
    assert delivered.resource_refs == (handle.uri,)
    with pytest.raises(ResourceAccessDenied, match="not shared"):
        service.read(
            handle.uri,
            owner=GRANTEE,
            capability_evaluator=_capability_evaluator(read=True, stat=False),
        )


def test_agent_process_lineage_and_capability_delegation_do_not_create_share(
    tmp_path,
) -> None:
    registry, owner_session, grantee_session, _third_session = _registry_with_agents()
    parent_grant = CapabilityGrant(
        OWNER.agent_id,
        RESOURCE_READ_ACTION,
        ARTIFACT_RESOURCE_SCOPE,
    )
    registry.install_capability_grants(OWNER.agent_id, (parent_grant,))
    store = LocalResourceStore(tmp_path / "resources")
    service = ResourceService(
        store,
        resource_id_factory=lambda: "res_secret",
        handle_id_factory=lambda: "hdl_secret",
    )
    handle = service.create_artifact(
        b"secret-bytes",
        owner=OWNER,
        media_type="text/plain",
        encoding="utf-8",
        source_tool_name="producer",
        source_tool_call_id="call-producer",
        source_operation_id="op-producer",
    )
    manager = ProcessManager(agent_registry=registry)
    parent_process = manager.create_process(
        process_id="process-owner",
        agent=registry.get(OWNER.agent_id),
    )
    child_process = manager.create_child_process(
        parent_process_id=parent_process.process_id,
        process_id="process-grantee",
        agent=registry.get(GRANTEE.agent_id),
    )
    decision = registry.delegate_capability(
        DelegateCapabilityRequest(
            OWNER.agent_id,
            GRANTEE.agent_id,
            RESOURCE_READ_ACTION,
            ARTIFACT_RESOURCE_SCOPE,
        ),
        record_session=grantee_session,
    )
    evaluator = CapabilityEvaluator(registry.get(GRANTEE.agent_id).capability_grants)

    assert decision.allowed is True
    assert registry.parent_of(GRANTEE.agent_id) == OWNER.agent_id
    assert manager.parent_of(child_process.process_id) == parent_process.process_id
    assert evaluator.authorize(
        AuthorizationRequest(GRANTEE.agent_id, RESOURCE_READ_ACTION, handle.uri)
    ).allowed
    with pytest.raises(ResourceAccessDenied):
        service.read(handle.uri, owner=GRANTEE, capability_evaluator=evaluator)
    assert [event.type for event in owner_session.events] == []


def test_grantee_cannot_re_share_and_share_does_not_transfer_ownership(tmp_path) -> None:
    (
        _registry,
        owner_session,
        _grantee_session,
        _third_session,
        store,
        _shares,
        service,
        handle,
    ) = _resource_world(tmp_path)
    service.share(
        handle.uri,
        owner=OWNER,
        grantee_agent_id=GRANTEE.agent_id,
        allowed_actions=(RESOURCE_READ_ACTION,),
        record_session=owner_session,
        share_id="share_read",
    )

    decision = service.share(
        handle.uri,
        owner=GRANTEE,
        grantee_agent_id=THIRD.agent_id,
        allowed_actions=(RESOURCE_READ_ACTION,),
        record_session=Session(GRANTEE.session_id),
        share_id="share_reshare",
    )

    assert decision.allowed is False
    assert decision.reason == "not_resource_owner"
    assert store.stat("res_secret").owner == OWNER


def test_share_replay_is_idempotent_and_conflicts_are_rejected(tmp_path) -> None:
    (
        registry,
        owner_session,
        _grantee_session,
        _third_session,
        store,
        shares,
        service,
        handle,
    ) = _resource_world(tmp_path)
    decision = service.share(
        handle.uri,
        owner=OWNER,
        grantee_agent_id=GRANTEE.agent_id,
        allowed_actions=(RESOURCE_READ_ACTION,),
        record_session=owner_session,
        share_id="share_read",
        correlation_id="corr-read",
    )

    replayed = shares.replay_shares(
        (owner_session, owner_session),
        resource_lookup=store.stat,
    )
    reconstructed = ResourceShareRegistry.reconstruct(
        (owner_session,),
        agent_registry=registry,
        resource_lookup=store.stat,
    )

    assert decision.allowed is True
    assert len(replayed) == 1
    assert reconstructed.is_shared_with(
        resource_id="res_secret",
        grantee_agent_id=GRANTEE.agent_id,
        owner_agent_id=OWNER.agent_id,
        action=RESOURCE_READ_ACTION,
    )

    conflicting = Session(OWNER.session_id)
    assert decision.grant is not None
    conflicting.append(EventType.RESOURCE_SHARED, decision.grant.as_payload())
    conflicting.append(
        EventType.RESOURCE_SHARED,
        ResourceShareGrant(
            share_id=decision.grant.share_id,
            resource_id="res_secret",
            owner_agent_id=OWNER.agent_id,
            grantee_agent_id=THIRD.agent_id,
            allowed_actions=(RESOURCE_READ_ACTION,),
            created_at=101.0,
            correlation_id="corr-conflict",
        ).as_payload(),
    )

    with pytest.raises(ResourceShareCorruptionError, match="conflicting"):
        ResourceShareRegistry.reconstruct(
            (conflicting,),
            agent_registry=registry,
            resource_lookup=store.stat,
        )


def test_restart_reconstruction_enables_dual_authorized_access(tmp_path) -> None:
    (
        registry,
        owner_session,
        _grantee_session,
        _third_session,
        store,
        _shares,
        service,
        handle,
    ) = _resource_world(tmp_path)
    service.share(
        handle.uri,
        owner=OWNER,
        grantee_agent_id=GRANTEE.agent_id,
        allowed_actions=(RESOURCE_READ_ACTION,),
        record_session=owner_session,
        share_id="share_read",
    )
    restarted_registry = ResourceShareRegistry.reconstruct(
        (owner_session,),
        agent_registry=registry,
        resource_lookup=store.stat,
    )
    restarted_service = ResourceService(store, share_registry=restarted_registry)

    read = restarted_service.read(
        handle.uri,
        owner=GRANTEE,
        capability_evaluator=_capability_evaluator(read=True, stat=False),
    )

    assert read.data == b"secret-bytes"


def test_replay_requires_owner_session_and_existing_resource(tmp_path) -> None:
    (
        registry,
        owner_session,
        grantee_session,
        _third_session,
        store,
        _shares,
        service,
        handle,
    ) = _resource_world(tmp_path)
    decision = service.share(
        handle.uri,
        owner=OWNER,
        grantee_agent_id=GRANTEE.agent_id,
        allowed_actions=(RESOURCE_READ_ACTION,),
        record_session=owner_session,
        share_id="share_read",
    )
    assert decision.grant is not None
    grantee_session.append(EventType.RESOURCE_SHARED, decision.grant.as_payload())

    with pytest.raises(ResourceShareCorruptionError, match="owner Session"):
        ResourceShareRegistry.reconstruct(
            (grantee_session,),
            agent_registry=registry,
            resource_lookup=store.stat,
        )

    with pytest.raises(ResourceShareCorruptionError, match="missing resource"):
        ResourceShareRegistry.reconstruct(
            (owner_session,),
            agent_registry=registry,
            resource_lookup=lambda _resource_id: (_ for _ in ()).throw(
                ResourceNotFound("missing")
            ),
        )


def test_old_v0_5_owner_compatibility_and_fail_closed_cross_agent(tmp_path) -> None:
    store = LocalResourceStore(tmp_path / "resources")
    service = ResourceService(
        store,
        resource_id_factory=lambda: "res_legacy",
        handle_id_factory=lambda: "hdl_legacy",
    )
    handle = service.create_artifact(
        b"legacy",
        owner=OWNER,
        media_type="text/plain",
        encoding="utf-8",
        source_tool_name="producer",
        source_tool_call_id="call-producer",
        source_operation_id="op-producer",
    )

    assert service.read(handle.uri, owner=OWNER, limit=64).data == b"legacy"
    with pytest.raises(ResourceAccessDenied):
        service.read(
            handle.uri,
            owner=GRANTEE,
            capability_evaluator=_capability_evaluator(read=True, stat=False),
        )


def test_resource_store_remains_storage_only() -> None:
    assert list(inspect.signature(LocalResourceStore.read).parameters) == [
        "self",
        "resource_id",
        "offset",
        "limit",
    ]
