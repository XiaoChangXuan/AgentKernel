"""Opt-in real-model trace: large ToolResult becomes a ResourceHandle."""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from agentkernel import (  # noqa: E402
    Agent,
    CooperativeScheduler,
    DurableToolExecutor,
    HookManager,
    LocalResourceStore,
    PromptService,
    Session,
    ThresholdExternalizationPolicy,
    ToolDefinition,
    ToolExecutionContext,
    ToolRegistry,
    ToolResultExternalizer,
    ToolSchema,
)
from agentkernel.protocol import JsonValue  # noqa: E402
from agentkernel.resources import ResourceService  # noqa: E402

from common import (  # noqa: E402
    TraceRecorder,
    configured_real_provider,
    install_tool_trace_hooks,
    maybe_write_jsonl,
    observed_openai_compatible,
    provider_label,
)


async def collect_logs(
    _arguments: Mapping[str, JsonValue],
    _context: ToolExecutionContext,
) -> JsonValue:
    return "\n".join(f"diagnostic line {index:04d}: ok" for index in range(900))


async def run(trace_jsonl: str | None) -> int:
    config = configured_real_provider()
    if config is None:
        return 0

    task = (
        "Call logs.collect once. Then explain whether the result was returned "
        "inline or as an artifact handle."
    )
    session = Session("real-resource-handle-session")
    agent = Agent.create(
        agent_id="real-resource-agent",
        session=session,
        capabilities={"logs.collect"},
    )
    scheduler = CooperativeScheduler()
    process = scheduler.create_process(
        process_id="real-resource-process",
        agent=agent.control,
    )
    recorder = TraceRecorder(
        title="R3 Real ResourceHandle Trajectory",
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
                "logs.collect",
                "Collect a large deterministic diagnostic log.",
                {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            ),
            handler=collect_logs,
            required_capability="logs.collect",
        )
    )
    hooks = HookManager()
    install_tool_trace_hooks(
        hooks,
        recorder=recorder,
        tools=tools,
        agent=agent,
    )
    with tempfile.TemporaryDirectory(prefix="agentkernel-real-resource-") as directory:
        resources = ResourceService(LocalResourceStore(Path(directory) / "resources"))
        externalizer = ToolResultExternalizer(
            resources,
            ThresholdExternalizationPolicy(
                threshold_bytes=1_000,
                preview_head_bytes=320,
                preview_tail_bytes=220,
            ),
        )
        from agentkernel import DefaultAgentLoop

        answer = await DefaultAgentLoop(
            llm=observed_openai_compatible(config, recorder),
            tools=tools,
            prompt=PromptService(
                "You are demonstrating AgentKernel ResourceHandle behavior. "
                "Use logs.collect exactly once, then answer from the observation. "
                "Do not reveal hidden reasoning."
            ),
            hooks=hooks,
            scheduler=scheduler,
            tool_executor=DurableToolExecutor(tools, result_processor=externalizer),
            resource_metrics=(resources.metrics,),
        ).run(agent, task, process_id=process.process_id)
    recorder.record("final_answer", answer=answer)
    recorder.record("process_state", state=process.state.value)
    recorder.record_session(session)
    recorder.print_human()
    print()
    print("WHAT THIS DEMONSTRATES / 本实验验证什么")
    print("- A real model can request a large-output tool.")
    print("- AgentKernel can externalize the exact output into ResourceService.")
    print("- The model-visible ToolResult contains a bounded preview and handle.")
    print("- Session records the observed handle-bearing ToolResult.")
    print()
    print("WHAT THIS DOES NOT DEMONSTRATE / 本实验不证明什么")
    print("- It does not prove storage durability beyond this temporary demo store.")
    print("- It does not prove semantic summary quality.")
    print("- It does not prove production data security.")
    maybe_write_jsonl(recorder, trace_jsonl)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace-jsonl")
    args = parser.parse_args()
    return asyncio.run(run(args.trace_jsonl))


if __name__ == "__main__":
    raise SystemExit(main())
