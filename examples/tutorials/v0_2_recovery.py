"""V0.2 tutorial: durable Session truth survives runtime loss.

Run from the repository root:

    python examples/tutorials/v0_2_recovery.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
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
    return int(arguments["left"]) + int(arguments["right"])


def final_answer(request: ModelRequest) -> ModelResponse:
    last = request.messages[-1]
    if last.role is not MessageRole.TOOL:
        raise AssertionError("expected replayed tool result")
    payload = json.loads(last.content)
    return ModelResponse(content=f"final answer: {payload['output']}")


async def run_once(path: Path) -> tuple[str, int, tuple[str, ...]]:
    session = Session("tutorial-v0-2-session", JsonlSessionPersistence(path))
    agent = Agent.create(
        agent_id="tutorial-agent",
        session=session,
        capabilities={"math.add"},
    )
    tools = ToolRegistry()
    tools.register(
        ToolDefinition(
            schema=ToolSchema("math.add", "Add.", {"type": "object"}),
            handler=add,
            required_capability="math.add",
        )
    )
    answer = await DefaultAgentLoop(
        llm=ScriptedLLM(
            [
                ModelResponse(
                    tool_calls=(
                        ToolCall(
                            "call-add-1",
                            "math.add",
                            {"left": 7, "right": 35},
                        ),
                    )
                ),
                final_answer,
            ]
        ),
        tools=tools,
        prompt=PromptService("Use the tool."),
    ).run(agent, "What is 7 + 35?")
    event_types = tuple(event.type.value for event in session.events)
    count = len(session.events)
    session.close()
    return answer, count, event_types


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="agentkernel-v0-2-") as directory:
        path = Path(directory) / "session.jsonl"
        answer, before_count, before_types = await run_once(path)

        restored = Session.load(
            "tutorial-v0-2-session",
            JsonlSessionPersistence(path),
        )
        try:
            restored_messages = restored.derive_messages()
            after_types = tuple(event.type.value for event in restored.events)

            print("V0.2 Persistence / Recovery")
            print(f"before_crash_answer={answer}")
            print(f"before_crash_events={before_count}")
            print("runtime_object_discarded=true")
            print(f"after_restart_status={restored.recovery_analysis.status.value}")
            print(f"after_restart_events={len(restored.events)}")
            print(f"derived_messages={len(restored_messages)}")
            print(f"lost_durable_facts={before_types != after_types}")
            print()
            print("本实验验证什么 / WHAT THIS DEMONSTRATES")
            print("- Session JSONL persistence survives runtime object loss.")
            print("- Recovery analysis can reconstruct completed durable facts.")
            print("- Derived messages come from Session truth after restart.")
            print()
            print("本实验不证明什么 / WHAT THIS DOES NOT DEMONSTRATE")
            print("- It does not use a real model provider.")
            print("- It does not inject every possible crash prefix.")
            print("- It does not prove corrupted-log recovery.")
        finally:
            restored.close()


if __name__ == "__main__":
    asyncio.run(main())
