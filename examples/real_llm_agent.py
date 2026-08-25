"""Run AgentKernel against a configured OpenAI-compatible API."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Mapping
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from agentkernel import (  # noqa: E402
    Agent,
    DefaultAgentLoop,
    PromptService,
    Session,
    ToolDefinition,
    ToolExecutionContext,
    ToolRegistry,
    ToolSchema,
)
from agentkernel.protocol import JsonValue  # noqa: E402
from agentkernel.providers import (  # noqa: E402
    OpenAICompatibleConfig,
    OpenAICompatibleConfigurationError,
    OpenAICompatibleError,
    OpenAICompatibleLLM,
)


async def add(
    arguments: Mapping[str, JsonValue],
    _context: ToolExecutionContext,
) -> JsonValue:
    left = arguments.get("a")
    right = arguments.get("b")
    if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
        raise ValueError("a and b must be numbers")
    return left + right


async def run() -> int:
    try:
        config = OpenAICompatibleConfig.from_env()
    except OpenAICompatibleConfigurationError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2

    session = Session("real-llm-session")
    agent = Agent.create(
        agent_id="real-llm-agent",
        session=session,
        capabilities={"math.add"},
        capability_bounding_set={"math.add"},
    )
    tools = ToolRegistry()
    tools.register(
        ToolDefinition(
            schema=ToolSchema(
                name="math.add",
                description="Add two numbers using the AgentKernel tool boundary.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "a": {"type": "number"},
                        "b": {"type": "number"},
                    },
                    "required": ["a", "b"],
                    "additionalProperties": False,
                },
            ),
            handler=add,
            required_capability="math.add",
        )
    )
    loop = DefaultAgentLoop(
        llm=OpenAICompatibleLLM(config),
        tools=tools,
        prompt=PromptService(
            "You are validating an agent runtime. Use the provided math.add tool "
            "for the requested arithmetic; do not calculate it directly."
        ),
    )
    try:
        answer = await loop.run(
            agent,
            "请使用 math.add 工具计算 20 + 22，不要自己直接计算。",
        )
    except OpenAICompatibleError as error:
        print(f"Provider error: {error}", file=sys.stderr)
        return 1

    print("Final Answer")
    print(answer)
    print("\nSession Event Log")
    for event in session.events:
        print(json.dumps(event.as_dict(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
