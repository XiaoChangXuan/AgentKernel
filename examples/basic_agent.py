"""Run the deterministic AgentKernel V0.1 tool loop."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Mapping
from pathlib import Path

# Keep the documented source-checkout command runnable without installing first.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from agentkernel import (  # noqa: E402
    Agent,
    DefaultAgentLoop,
    MessageRole,
    ModelRequest,
    ModelResponse,
    PromptService,
    ScriptedLLM,
    Session,
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
    left = arguments.get("left")
    right = arguments.get("right")
    if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
        raise ValueError("left and right must be numbers")
    return left + right


def answer_from_tool(request: ModelRequest) -> ModelResponse:
    tool_message = request.messages[-1]
    if tool_message.role is not MessageRole.TOOL:
        raise AssertionError("the second request must contain the tool result")
    payload = json.loads(tool_message.content)
    return ModelResponse(content=f"The result is {payload['output']}.")


async def main() -> None:
    session = Session("example-session")
    agent = Agent.create(
        agent_id="example-agent",
        session=session,
        capabilities={"math.add"},
        capability_bounding_set={"math.add"},
    )

    tools = ToolRegistry()
    tools.register(
        ToolDefinition(
            schema=ToolSchema(
                name="math.add",
                description="Add two numbers.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "left": {"type": "number"},
                        "right": {"type": "number"},
                    },
                    "required": ["left", "right"],
                },
            ),
            handler=add,
            required_capability="math.add",
        )
    )

    llm = ScriptedLLM(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="call-1",
                        name="math.add",
                        arguments={"left": 20, "right": 22},
                    ),
                )
            ),
            answer_from_tool,
        ]
    )
    loop = DefaultAgentLoop(
        llm=llm,
        tools=tools,
        prompt=PromptService("Use tools when they are required."),
    )

    answer = await loop.run(agent, "What is 20 + 22?")
    print(answer)
    print("\nSession Event Log:")
    for event in session.events:
        print(json.dumps(event.as_dict(), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
