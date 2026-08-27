"""Opt-in real-model trace: tool call crosses the AgentKernel boundary."""

from __future__ import annotations

import argparse
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
    CooperativeScheduler,
    HookManager,
    PromptService,
    Session,
    TOOL_EXECUTE_ACTION,
    ToolDefinition,
    ToolExecutionContext,
    ToolRegistry,
    ToolSchema,
)
from agentkernel.protocol import JsonValue  # noqa: E402

from common import (  # noqa: E402
    TraceRecorder,
    configured_real_provider,
    install_tool_trace_hooks,
    maybe_write_jsonl,
    observed_openai_compatible,
    provider_label,
)


async def add(
    arguments: Mapping[str, JsonValue],
    _context: ToolExecutionContext,
) -> JsonValue:
    return int(arguments["left"]) + int(arguments["right"])


async def run(trace_jsonl: str | None) -> int:
    config = configured_real_provider()
    if config is None:
        return 0

    task = "Use math.add to calculate 20 + 22, then answer with the result."
    session = Session("real-basic-tool-session")
    agent = Agent.create(
        agent_id="real-basic-tool-agent",
        session=session,
        capability_grants=(
            CapabilityGrant(
                "real-basic-tool-agent",
                TOOL_EXECUTE_ACTION,
                "tool://math.add",
            ),
        ),
    )
    scheduler = CooperativeScheduler()
    process = scheduler.create_process(
        process_id="real-basic-tool-process",
        agent=agent.control,
    )
    recorder = TraceRecorder(
        title="R1 Basic Real Tool-Use Trajectory",
        task=task,
        agent_id=agent.control.agent_id,
        session_id=session.session_id,
        process_id=process.process_id,
        provider=provider_label(config),
    )
    tools = ToolRegistry()
    tools.register(
        ToolDefinition(
            schema=ToolSchema(
                "math.add",
                "Add two integers through the AgentKernel tool boundary.",
                {
                    "type": "object",
                    "properties": {
                        "left": {"type": "integer"},
                        "right": {"type": "integer"},
                    },
                    "required": ["left", "right"],
                    "additionalProperties": False,
                },
            ),
            handler=add,
            required_action=TOOL_EXECUTE_ACTION,
            required_resource="tool://math.add",
        )
    )
    hooks = HookManager()
    install_tool_trace_hooks(
        hooks,
        recorder=recorder,
        tools=tools,
        agent=agent,
    )
    answer = await DefaultLoop(
        config=config,
        recorder=recorder,
        tools=tools,
        hooks=hooks,
        scheduler=scheduler,
    ).run(agent, task, process_id=process.process_id)
    recorder.record("final_answer", answer=answer)
    recorder.record("process_state", state=process.state.value)
    recorder.record_session(session)
    recorder.print_human()
    print()
    print("WHAT THIS DEMONSTRATES / 本实验验证什么")
    print("- A real provider receives model-visible tool schemas.")
    print("- A real model can produce a tool call.")
    print("- The call crosses ToolRegistry and Kernel authorization.")
    print("- The ToolResult returns into the next model request.")
    print("- Session records the observed execution trajectory.")
    print()
    print("WHAT THIS DOES NOT DEMONSTRATE / 本实验不证明什么")
    print("- It does not prove general model reasoning correctness.")
    print("- It does not prove production security or crash safety.")
    print("- It does not prove exactly-once side effects.")
    maybe_write_jsonl(recorder, trace_jsonl)
    return 0


class DefaultLoop:
    def __init__(self, *, config, recorder, tools, hooks, scheduler) -> None:
        from agentkernel import DefaultAgentLoop

        self._loop = DefaultAgentLoop(
            llm=observed_openai_compatible(config, recorder),
            tools=tools,
            prompt=PromptService(
                "You are demonstrating an AgentKernel runtime trace. "
                "Use the provided tool when the task asks for arithmetic. "
                "Do not reveal hidden reasoning."
            ),
            hooks=hooks,
            scheduler=scheduler,
        )

    async def run(self, agent: Agent, task: str, *, process_id: str) -> str:
        return await self._loop.run(agent, task, process_id=process_id)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace-jsonl")
    args = parser.parse_args()
    return asyncio.run(run(args.trace_jsonl))


if __name__ == "__main__":
    raise SystemExit(main())
