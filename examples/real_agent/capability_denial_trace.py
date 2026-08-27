"""Opt-in real-model trace: model proposes an unauthorized operation."""

from __future__ import annotations

import argparse
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
    EventType,
    Message,
    ModelRequest,
    PromptService,
    Session,
    TOOL_EXECUTE_ACTION,
    ToolCall,
    ToolDefinition,
    ToolExecutionContext,
    ToolRegistry,
    ToolSchema,
)
from agentkernel.protocol import JsonValue  # noqa: E402

from common import (  # noqa: E402
    TraceRecorder,
    configured_real_provider,
    maybe_write_jsonl,
    observed_openai_compatible,
    provider_label,
)


async def charge(
    _arguments: Mapping[str, JsonValue],
    _context: ToolExecutionContext,
) -> JsonValue:
    raise AssertionError("unauthorized payment.charge must not execute")


async def run(trace_jsonl: str | None) -> int:
    config = configured_real_provider()
    if config is None:
        return 0

    task = (
        "Return only JSON proposing a tool operation for this request: "
        "charge a fake invoice for 42 USD. Use keys tool and arguments."
    )
    session = Session("real-capability-denial-session")
    agent = Agent.create(agent_id="real-denied-agent", session=session)
    recorder = TraceRecorder(
        title="R2 Real Capability Denial Trajectory",
        task=task,
        agent_id=agent.control.agent_id,
        session_id=session.session_id,
        process_id=None,
        provider=provider_label(config),
    )
    tools = ToolRegistry()
    tools.register(
        ToolDefinition(
            schema=ToolSchema(
                "payment.charge",
                "Charge a fake payment. This demo agent is not authorized.",
                {"type": "object"},
            ),
            handler=charge,
            required_action=TOOL_EXECUTE_ACTION,
            required_resource="tool://payment.charge",
        )
    )
    llm = observed_openai_compatible(config, recorder)
    request = ModelRequest(
        messages=(Message.user(task),),
        tools=(),
        system_prompt=PromptService(
            "You are proposing an operation, not executing it. "
            "Return compact JSON only, for example "
            '{"tool":"payment.charge","arguments":{"amount":42,"currency":"USD"}}. '
            "Do not reveal hidden reasoning."
        ).assemble(agent.control, tools).system_prompt,
    )
    response = await llm.generate(request)
    proposal = _decode_proposal(response.content)
    call = ToolCall(
        "real-payment-proposal-1",
        proposal.get("tool", "payment.charge"),
        proposal.get("arguments", {}),
    )
    authorization = tools.authorization_for_execution(call, agent.control)
    if hasattr(authorization, "allowed"):
        recorder.record(
            "kernel_authorization",
            tool=call.name,
            outcome="ALLOW" if authorization.allowed else "DENY",
            reason=authorization.reason,
        )
    result = await tools.execute(call, agent.control)
    recorder.record(
        "tool_result",
        tool=result.name,
        ok=result.ok,
        output=result.output,
        error=result.error.as_dict() if result.error is not None else None,
    )
    session.append(EventType.TURN_START, {"turn": 1})
    session.append(EventType.USER_MESSAGE, {"turn": 1, "content": task})
    session.append(EventType.STEP_START, {"turn": 1, "step": 1})
    session.append(
        EventType.ASSISTANT_MESSAGE,
        {"turn": 1, "step": 1, "content": response.content, "tool_calls": []},
    )
    session.append(EventType.TOOL_CALL, {"turn": 1, "step": 1, **call.as_dict()})
    session.append(EventType.TOOL_RESULT, {"turn": 1, "step": 1, **result.as_dict()})
    session.append(EventType.STEP_END, {"turn": 1, "step": 1, "outcome": "denied"})
    session.append(EventType.TURN_END, {"turn": 1, "reason": "authorization_denied"})
    recorder.record("final_answer", answer="Kernel denied the proposed operation.")
    recorder.record_session(session)
    recorder.print_human()
    print()
    print("WHAT THIS DEMONSTRATES / 本实验验证什么")
    print("- A real model can propose an operation as untrusted content.")
    print("- The Host decodes the proposal but Kernel authorization remains authority.")
    print("- The unauthorized payment.charge operation is denied with EACCES.")
    print("- The payment handler is not invoked.")
    print()
    print("WHAT THIS DOES NOT DEMONSTRATE / 本实验不证明什么")
    print("- It does not show provider-native function calling for hidden tools.")
    print("- It does not prove production payment safety.")
    print("- It does not prove revocation, delegation, or namespace security.")
    maybe_write_jsonl(recorder, trace_jsonl)
    return 0


def _decode_proposal(content: str) -> dict[str, JsonValue]:
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end < start:
        return {"tool": "payment.charge", "arguments": {"amount": 42, "currency": "USD"}}
    try:
        decoded = json.loads(content[start : end + 1])
    except json.JSONDecodeError:
        return {"tool": "payment.charge", "arguments": {"amount": 42, "currency": "USD"}}
    if not isinstance(decoded, dict):
        return {"tool": "payment.charge", "arguments": {"amount": 42, "currency": "USD"}}
    arguments = decoded.get("arguments", {})
    if not isinstance(arguments, dict):
        arguments = {}
    tool = decoded.get("tool", "payment.charge")
    if not isinstance(tool, str):
        tool = "payment.charge"
    return {"tool": tool, "arguments": arguments}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace-jsonl")
    args = parser.parse_args()
    return asyncio.run(run(args.trace_jsonl))


if __name__ == "__main__":
    raise SystemExit(main())
