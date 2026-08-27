"""V0.8 tutorial: multi-agent identity, IPC, sharing, and authority boundaries.

Run from the repository root:

    python examples/tutorials/v0_8_multi_agent_runtime.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from agentkernel import (  # noqa: E402
    AgentRegistry,
    CapabilityEvaluator,
    CapabilityGrant,
    DelegateCapabilityRequest,
    ErrorCode,
    InMemoryIPCPersistence,
    KernelIPC,
    LocalResourceStore,
    ProcessManager,
    RESOURCE_READ_ACTION,
    ResourceAccessDenied,
    ResourceOwner,
    ResourceService,
    ResourceShareRegistry,
    Session,
    TOOL_EXECUTE_ACTION,
    ToolCall,
    ToolDefinition,
    ToolExecutionContext,
    ToolRegistry,
    ToolSchema,
)
from agentkernel.protocol import JsonValue  # noqa: E402


def evaluator_for(agent) -> CapabilityEvaluator:
    return CapabilityEvaluator.from_agent_capabilities(
        agent_id=agent.agent_id,
        capabilities=agent.capabilities,
        capability_grants=agent.capability_grants,
    )


async def add(
    arguments: Mapping[str, JsonValue],
    _context: ToolExecutionContext,
) -> JsonValue:
    return int(arguments["left"]) + int(arguments["right"])


async def main() -> None:
    agents = AgentRegistry()
    parent_tool_grant = CapabilityGrant(
        "agent-parent", TOOL_EXECUTE_ACTION, "tool://math.add"
    )
    parent_resource_grant = CapabilityGrant(
        "agent-parent", RESOURCE_READ_ACTION, "artifact://**"
    )
    parent_session = Session("session-parent")
    child_session = Session("session-child")
    parent = agents.create_root(
        agent_id="agent-parent",
        session=parent_session,
        capability_grants=(parent_tool_grant, parent_resource_grant),
        creation_id="create-parent",
    )
    child = agents.create_child(
        parent_agent_id=parent.control.agent_id,
        agent_id="agent-child",
        session=child_session,
        creation_id="create-child",
        record_session=parent_session,
    )
    processes = ProcessManager(agent_registry=agents)
    parent_process = processes.create_process(
        process_id="process-parent",
        agent=parent.control,
    )
    child_process = processes.create_child_process(
        parent_process_id=parent_process.process_id,
        process_id="process-child",
        agent=child.control,
    )

    tools = ToolRegistry()
    tools.register(
        ToolDefinition(
            schema=ToolSchema("math.add", "Add two integers.", {"type": "object"}),
            handler=add,
            required_action=TOOL_EXECUTE_ACTION,
            required_resource="tool://math.add",
        )
    )
    call = ToolCall("call-add-1", "math.add", {"left": 2, "right": 3})
    before = await tools.execute(call, child.control)

    with tempfile.TemporaryDirectory(prefix="agentkernel-v0-8-") as directory:
        shares = ResourceShareRegistry(
            agent_registry=agents,
            clock=lambda: 100.0,
        )
        resources = ResourceService(
            LocalResourceStore(Path(directory) / "resources"),
            share_registry=shares,
            resource_id_factory=lambda: "res_secret",
            handle_id_factory=lambda: "hdl_secret",
            clock=lambda: 10.0,
        )
        owner = ResourceOwner(parent.control.agent_id, parent.control.session_id)
        child_owner = ResourceOwner(child.control.agent_id, child.control.session_id)
        handle = resources.create_artifact(
            b"secret-bytes",
            owner=owner,
            media_type="text/plain",
            encoding="utf-8",
            source_tool_name="producer",
            source_tool_call_id="call-producer",
            source_operation_id="op-producer",
        )
        ipc = KernelIPC(
            agent_registry=agents,
            process_manager=processes,
            sessions={
                parent.control.agent_id: parent_session,
                child.control.agent_id: child_session,
            },
            persistence=InMemoryIPCPersistence(),
            time_fn=lambda: 1.0,
        )
        ipc.create_channel(
            channel_id="channel-parent-child",
            sender_agent_id=parent.control.agent_id,
            receiver_agent_id=child.control.agent_id,
            receiver_process_id=child_process.process_id,
        )
        ipc.send(
            channel_id="channel-parent-child",
            sender_process_id=parent_process.process_id,
            payload={"body": "use this artifact"},
            resource_refs=(handle.uri,),
            message_id="message-resource-ref",
            correlation_id="corr-message",
        )
        delivered = ipc.receive(
            channel_id="channel-parent-child",
            receiver_agent_id=child.control.agent_id,
            receiver_process_id=child_process.process_id,
        )

        ipc_ref_grants_access = True
        try:
            resources.read(
                handle.uri,
                owner=child_owner,
                capability_evaluator=CapabilityEvaluator(
                    (
                        CapabilityGrant(
                            child.control.agent_id,
                            RESOURCE_READ_ACTION,
                            handle.uri,
                        ),
                    )
                ),
            )
        except ResourceAccessDenied:
            ipc_ref_grants_access = False

        share = resources.share(
            handle.uri,
            owner=owner,
            grantee_agent_id=child.control.agent_id,
            allowed_actions=(RESOURCE_READ_ACTION,),
            record_session=parent_session,
            share_id="share_secret",
            correlation_id="corr-share",
        )
        share_without_capability_grants_access = True
        try:
            resources.read(
                handle.uri,
                owner=child_owner,
                capability_evaluator=evaluator_for(agents.get(child.control.agent_id)),
            )
        except ResourceAccessDenied:
            share_without_capability_grants_access = False

        resource_decision = agents.delegate_capability(
            DelegateCapabilityRequest(
                "agent-parent",
                "agent-child",
                RESOURCE_READ_ACTION,
                handle.uri,
                correlation_id="delegate-resource",
            ),
            record_session=child_session,
        )
        resource_read = resources.read(
            handle.uri,
            owner=child_owner,
            capability_evaluator=evaluator_for(agents.get(child.control.agent_id)),
        )

    tool_decision = agents.delegate_capability(
        DelegateCapabilityRequest(
            "agent-parent",
            "agent-child",
            TOOL_EXECUTE_ACTION,
            "tool://math.add",
            correlation_id="delegate-math",
        ),
        record_session=child_session,
    )
    after = await tools.execute(call, agents.get("agent-child"))
    before_code = before.error.code.value if before.error is not None else "none"

    print("V0.8 Multi-Agent Runtime")
    print(f"lineage={'/'.join(agents.lineage('agent-child'))}")
    print(f"process_lineage={'/'.join(processes.lineage('process-child'))}")
    print(
        "agent_tree_is_process_tree="
        f"{agents.lineage('agent-child') == processes.lineage('process-child')}"
    )
    print(f"child_initial_grants={len(child.control.capability_grants)}")
    print(f"before_delegation_ok={before.ok}")
    print(f"before_delegation_code={before_code}")
    print(f"before_matches_eacces={before.error is not None and before.error.code is ErrorCode.EACCES}")
    print(f"ipc_resource_ref={delivered.resource_refs[0] if delivered else 'none'}")
    print(f"ipc_ref_grants_access={ipc_ref_grants_access}")
    print(f"share_allowed={share.allowed}")
    print(f"share_without_capability_grants_access={share_without_capability_grants_access}")
    print(f"resource_delegation_allowed={resource_decision.allowed}")
    print(f"after_share_and_capability_read={resource_read.data.decode()!r}")
    print(f"tool_delegation_allowed={tool_decision.allowed}")
    print(f"child_current_grants={len(agents.get('agent-child').capability_grants)}")
    print(f"after_delegation_result={after.output}")
    print()
    print("本实验验证什么 / WHAT THIS DEMONSTRATES")
    print("- Agent Tree and Process Tree are separate runtime structures.")
    print("- IPC resource references do not grant authority by themselves.")
    print("- ResourceShare and narrowed delegation are both required for access.")
    print("- Child agents do not implicitly inherit parent tool authority.")
    print()
    print("本实验不证明什么 / WHAT THIS DOES NOT DEMONSTRATE")
    print("- It does not use a real model provider.")
    print("- It does not implement V0.9 memory or full namespace security.")
    print("- It does not prove distributed multi-agent correctness.")


if __name__ == "__main__":
    asyncio.run(main())
