"""V0.8 tutorial: Agent tree identity does not imply authority inheritance.

Run from the repository root:

    python examples/tutorials/v0_8_multi_agent_runtime.py
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Mapping
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from agentkernel import (  # noqa: E402
    AgentRegistry,
    CapabilityGrant,
    DelegateCapabilityRequest,
    ErrorCode,
    Session,
    TOOL_EXECUTE_ACTION,
    ToolCall,
    ToolDefinition,
    ToolExecutionContext,
    ToolRegistry,
    ToolSchema,
)
from agentkernel.protocol import JsonValue  # noqa: E402


async def add(
    arguments: Mapping[str, JsonValue],
    _context: ToolExecutionContext,
) -> JsonValue:
    return int(arguments["left"]) + int(arguments["right"])


async def main() -> None:
    agents = AgentRegistry()
    parent_grant = CapabilityGrant(
        "agent-parent",
        TOOL_EXECUTE_ACTION,
        "tool://math.add",
    )
    parent = agents.create_root(
        agent_id="agent-parent",
        session=Session("session-parent"),
        capability_grants=(parent_grant,),
        creation_id="create-parent",
    )
    child = agents.create_child(
        parent_agent_id=parent.control.agent_id,
        agent_id="agent-child",
        session=Session("session-child"),
        creation_id="create-child",
        record_session=parent.session,
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
    decision = agents.delegate_capability(
        DelegateCapabilityRequest(
            "agent-parent",
            "agent-child",
            TOOL_EXECUTE_ACTION,
            "tool://math.add",
            correlation_id="delegate-math",
        ),
        record_session=child.session,
    )
    after = await tools.execute(call, agents.get("agent-child"))
    before_code = before.error.code.value if before.error is not None else "none"

    print("V0.8 Multi-Agent Runtime")
    print(f"lineage={'/'.join(agents.lineage('agent-child'))}")
    print(f"child_initial_grants={len(child.control.capability_grants)}")
    print(f"before_delegation_ok={before.ok}")
    print(f"before_delegation_code={before_code}")
    print(f"before_matches_eacces={before.error is not None and before.error.code is ErrorCode.EACCES}")
    print(f"delegation_allowed={decision.allowed}")
    print(f"child_current_grants={len(agents.get('agent-child').capability_grants)}")
    print(f"after_delegation_result={after.output}")


if __name__ == "__main__":
    asyncio.run(main())
