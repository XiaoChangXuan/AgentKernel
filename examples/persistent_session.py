"""Persist one deterministic agent turn, restart, and replay model history."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from agentkernel import (  # noqa: E402
    Agent,
    DefaultAgentLoop,
    JsonlSessionPersistence,
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
    return int(arguments["a"]) + int(arguments["b"])


def answer_from_tool(request: ModelRequest) -> ModelResponse:
    message = request.messages[-1]
    if message.role is not MessageRole.TOOL:
        raise AssertionError("expected a persisted Tool Result")
    result = json.loads(message.content)
    return ModelResponse(content=f"The result is {result['output']}.")


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="agentkernel-") as directory:
        path = Path(directory) / "persistent-example.jsonl"
        session = Session(
            "persistent-example",
            JsonlSessionPersistence(path),
        )
        agent = Agent.create(
            agent_id="persistent-example-agent",
            session=session,
            capabilities={"math.add"},
        )
        tools = ToolRegistry()
        tools.register(
            ToolDefinition(
                schema=ToolSchema(
                    "math.add",
                    "Add two numbers.",
                    {
                        "type": "object",
                        "properties": {
                            "a": {"type": "number"},
                            "b": {"type": "number"},
                        },
                        "required": ["a", "b"],
                    },
                ),
                handler=add,
                required_capability="math.add",
            )
        )
        loop = DefaultAgentLoop(
            llm=ScriptedLLM(
                [
                    ModelResponse(
                        tool_calls=(
                            ToolCall("call-1", "math.add", {"a": 20, "b": 22}),
                        )
                    ),
                    answer_from_tool,
                ]
            ),
            tools=tools,
            prompt=PromptService("Use the math tool."),
        )

        answer = await loop.run(agent, "What is 20 + 22?")
        original_history = session.derive_messages()
        session.flush()
        session.close()

        restored = Session.load(
            "persistent-example",
            JsonlSessionPersistence(path),
        )
        try:
            restored_history = restored.derive_messages()
            print(answer)
            print(f"Recovery status: {restored.recovery_analysis.status.value}")
            print(f"Persisted events: {len(restored.events)}")
            print(f"History identical: {restored_history == original_history}")
        finally:
            restored.close()


if __name__ == "__main__":
    asyncio.run(main())
