"""V0.1 tutorial: a deterministic AgentKernel agent spine.

Run from the repository root:

    python examples/tutorials/v0_1_agent_spine.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Mapping
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
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
    return int(arguments["left"]) + int(arguments["right"])


def final_answer(request: ModelRequest) -> ModelResponse:
    last = request.messages[-1]
    if last.role is not MessageRole.TOOL:
        raise AssertionError("expected the model to observe a Tool Result")
    payload = json.loads(last.content)
    return ModelResponse(content=f"final answer: {payload['output']}")


async def main() -> None:
    session = Session("tutorial-v0-1-session")
    agent = Agent.create(
        agent_id="tutorial-agent",
        session=session,
        capabilities={"math.add"},
    )

    tools = ToolRegistry()
    tools.register(
        ToolDefinition(
            schema=ToolSchema(
                "math.add",
                "Add two integers.",
                {
                    "type": "object",
                    "properties": {
                        "left": {"type": "integer"},
                        "right": {"type": "integer"},
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
                    ToolCall("call-add-1", "math.add", {"left": 20, "right": 22}),
                )
            ),
            final_answer,
        ]
    )

    answer = await DefaultAgentLoop(
        llm=llm,
        tools=tools,
        prompt=PromptService("Use the available tool when needed."),
    ).run(agent, "What is 20 + 22?")

    print("V0.1 Agent Spine")
    print(f"answer={answer}")
    print(f"agent_id={agent.control.agent_id}")
    print(f"session_id={session.session_id}")
    print("event_types=" + ",".join(event.type.value for event in session.events))
    print()
    print("本实验验证什么 / WHAT THIS DEMONSTRATES")
    print("- ScriptedLLM can drive a deterministic tool-use turn.")
    print("- A model proposal crosses the AgentKernel Tool boundary.")
    print("- Session records turn, step, tool/call, and tool/result facts.")
    print()
    print("本实验不证明什么 / WHAT THIS DOES NOT DEMONSTRATE")
    print("- It does not use a real model provider.")
    print("- It does not prove model reasoning quality.")
    print("- It does not prove crash recovery or side-effect safety.")


if __name__ == "__main__":
    asyncio.run(main())
