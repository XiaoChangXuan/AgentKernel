"""V0.6 tutorial: Capability checks gate tool visibility and execution.

Run from the repository root:

    python examples/tutorials/v0_6_capability_core.py
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
    Agent,
    CapabilityGrant,
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
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            schema=ToolSchema("math.add", "Add two integers.", {"type": "object"}),
            handler=add,
            required_action=TOOL_EXECUTE_ACTION,
            required_resource="tool://math.add",
        )
    )
    allowed_agent = Agent.create(
        agent_id="agent-allowed",
        session=Session("session-allowed"),
        capability_grants=(
            CapabilityGrant("agent-allowed", TOOL_EXECUTE_ACTION, "tool://math.add"),
        ),
    )
    denied_agent = Agent.create(
        agent_id="agent-denied",
        session=Session("session-denied"),
    )
    call = ToolCall("call-add-1", "math.add", {"left": 20, "right": 22})

    allowed = await registry.execute(call, allowed_agent.control)
    denied = await registry.execute(call, denied_agent.control)
    denied_code = denied.error.code.value if denied.error is not None else "none"

    print("V0.6 Capability Core")
    print(
        "visible_tools_allowed="
        + ",".join(schema.name for schema in registry.model_schemas(allowed_agent.control))
    )
    print(f"visible_tools_denied={len(registry.model_schemas(denied_agent.control))}")
    print(f"allowed_result={allowed.output}")
    print(f"denied_ok={denied.ok}")
    print(f"denied_code={denied_code}")
    print(f"matches_eacces={denied.error is not None and denied.error.code is ErrorCode.EACCES}")
    print()
    print("本实验验证什么 / WHAT THIS DEMONSTRATES")
    print("- Capability grants affect model-visible tool schemas.")
    print("- Execution is checked again at the Tool boundary.")
    print("- A grantless agent receives EACCES for unauthorized execution.")
    print()
    print("本实验不证明什么 / WHAT THIS DOES NOT DEMONSTRATE")
    print("- It does not use a real model provider.")
    print("- It does not implement RBAC, IAM, namespace, or revocation.")
    print("- It does not prove production security.")


if __name__ == "__main__":
    asyncio.run(main())
