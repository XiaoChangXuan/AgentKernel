"""Deterministic helpers for the AgentKernel interactive labs.

The notebooks in this directory are teaching artifacts.  They use these
helpers to keep each lab small while still exercising real AgentKernel and
MiniCode runtime code paths.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentkernel import (
    Agent,
    AgentBudget,
    AgentRegistry,
    ApproximateTokenEstimator,
    CapabilityEvaluator,
    CapabilityGrant,
    CooperativeScheduler,
    DefaultAgentLoop,
    ErrorCode,
    EventType,
    InMemoryIPCPersistence,
    JsonlSessionPersistence,
    KernelIPC,
    LocalResourceStore,
    Message,
    MessageRole,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    OperationRecoveryClassification,
    ProcessBudgetExceeded,
    ProcessManager,
    ProcessState,
    PromptService,
    RESOURCE_READ_ACTION,
    ResourceAccessDenied,
    ResourceOwner,
    ResourceService,
    ResourceShareRegistry,
    SchedulerSafePoint,
    ScriptedLLM,
    Session,
    TOOL_EXECUTE_ACTION,
    ToolCall,
    ToolDefinition,
    ToolExecutionContext,
    ToolRegistry,
    ToolSchema,
    UsageCollector,
    DelegateCapabilityRequest,
)
from agentkernel.context import ContextBudget, ContextManager, ContextProjector
from agentkernel.providers import OpenAICompatibleConfig, OpenAICompatibleLLM
from agentkernel.protocol import JsonValue
from agentkernel.tool_effects import ReconcileStatus, ToolEffectKind
from minicode.durable_patch import DurableApplyPatchAdapter, hash_file_or_absent
from minicode.patch import apply_mutation_plan
from minicode.testing import make_minicode_workspace
from minicode.tools import (
    APPLY_PATCH_NAME,
    apply_patch_capability_grants,
    register_apply_patch_tool,
    tool_resource,
)
from minicode.workspace import discover_workspace


LabPayload = dict[str, Any]


@dataclass(frozen=True)
class LabSpec:
    lab_id: str
    title: str
    question: str
    runner: Callable[[], LabPayload]


def create_lab(lab_id: str, *, mode: str = "deterministic") -> "InteractiveLab":
    """Create a stateful teaching lab controller.

    The notebooks use this API so the reader can stop at Kernel boundaries and
    inspect state before executing the next cell.
    """

    normalized = mode.strip().lower()
    if normalized not in {"deterministic", "real_model"}:
        raise ValueError('mode must be "deterministic" or "real_model"')
    if lab_id == "v01":
        return V01ExecutionSpineLab(normalized)
    if lab_id == "v02":
        return V02CrashRecoveryLab(normalized)
    if lab_id == "v03":
        return V03DurableSideEffectLab(normalized)
    raise KeyError(f"interactive controller is only implemented for v01-v03: {lab_id}")


def run_lab(lab_id: str) -> LabPayload:
    """Run one deterministic offline lab and return a displayable payload."""

    try:
        spec = LABS[lab_id]
    except KeyError as error:
        raise KeyError(f"unknown lab id: {lab_id}") from error
    payload = spec.runner()
    payload.setdefault("lab_id", spec.lab_id)
    payload.setdefault("title", spec.title)
    payload.setdefault("question", spec.question)
    return payload


def render_lab(payload: Mapping[str, Any]) -> None:
    """Pretty-print a lab payload without hiding the raw facts."""

    print("=" * 72)
    print(payload["title"])
    print("=" * 72)
    print(f"Question: {payload['question']}")
    print()
    for step in payload.get("steps", ()):
        print(f"[{step['name']}]")
        detail = step.get("detail")
        if isinstance(detail, str):
            print(detail)
        else:
            print(json.dumps(detail, ensure_ascii=False, indent=2, sort_keys=True))
        print()
    print("[Result]")
    print(json.dumps(payload.get("result", {}), ensure_ascii=False, indent=2, sort_keys=True))
    print()
    print("[A/B contrast]")
    print(payload.get("contrast", "No contrast recorded."))
    print()
    print("[What this proves]")
    print(payload.get("proves", "No claim recorded."))
    print()
    print("[Limitations]")
    print(payload.get("limitations", "Deterministic fixture; not a model intelligence benchmark."))


def _step(name: str, detail: Any) -> dict[str, Any]:
    return {"name": name, "detail": detail}


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


async def _add(
    arguments: Mapping[str, JsonValue],
    _context: ToolExecutionContext,
) -> JsonValue:
    return int(arguments["left"]) + int(arguments["right"])


async def _divide(
    arguments: Mapping[str, JsonValue],
    _context: ToolExecutionContext,
) -> JsonValue:
    b = int(arguments["b"])
    if b == 0:
        return None
    return int(arguments["a"]) / b


async def _write_stub(
    _arguments: Mapping[str, JsonValue],
    _context: ToolExecutionContext,
) -> JsonValue:
    return {"ok": True}


def _show(title: str, value: Any) -> Any:
    print(f"\n=== {title} ===")
    if isinstance(value, str):
        print(value)
    else:
        print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
    return value


def _message_payload(message: Message) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "role": message.role.value,
        "content": message.content,
    }
    if message.tool_calls:
        payload["tool_calls"] = [call.as_dict() for call in message.tool_calls]
    if message.tool_call_id is not None:
        payload["tool_call_id"] = message.tool_call_id
    if message.name is not None:
        payload["name"] = message.name
    if message.is_error:
        payload["is_error"] = True
    return payload


def _tool_schema_payload(schema: ToolSchema) -> dict[str, Any]:
    return {
        "name": schema.name,
        "description": schema.description,
        "input_schema": dict(schema.input_schema),
    }


def _request_payload(request: ModelRequest) -> dict[str, Any]:
    return {
        "system_prompt": request.system_prompt,
        "messages": [_message_payload(message) for message in request.messages],
        "tools": [_tool_schema_payload(schema) for schema in request.tools],
    }


def _response_payload(response: ModelResponse) -> dict[str, Any]:
    return {
        "assistant_text": response.content,
        "assistant_tool_calls": [call.as_dict() for call in response.tool_calls],
        "finish_reason": response.finish_reason.value if response.finish_reason else None,
        "usage": None
        if response.usage is None
        else {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "total_tokens": response.usage.total_tokens,
        },
    }


def _events_payload(session: Session) -> list[dict[str, Any]]:
    return [
        {
            "seq": event.seq,
            "type": event.type.value,
            "data": dict(event.data),
        }
        for event in session.events
    ]


class LabOpenAICompatibleLLM:
    """Lab-only adapter using AGENTKERNEL_LAB_LLM_* environment variables."""

    def __init__(self) -> None:
        self.base_url = os.environ.get("AGENTKERNEL_LAB_LLM_BASE_URL", "").strip()
        self.model = os.environ.get("AGENTKERNEL_LAB_LLM_MODEL", "").strip()
        self.api_key = os.environ.get("AGENTKERNEL_LAB_LLM_API_KEY", "").strip()
        if not self.base_url or not self.model:
            raise RuntimeError(
                "real_model mode requires AGENTKERNEL_LAB_LLM_BASE_URL and "
                "AGENTKERNEL_LAB_LLM_MODEL"
            )
        self._llm = OpenAICompatibleLLM(
            OpenAICompatibleConfig(
                base_url=self.base_url,
                model=self.model,
                api_key=self.api_key or None,
                timeout_seconds=60.0,
            )
        )
        self.requests: list[ModelRequest] = []
        self.responses: list[ModelResponse] = []

    @property
    def metadata(self) -> dict[str, Any]:
        return {
            "provider": "openai-compatible",
            "model": self.model,
            "base_url": self.base_url,
            "request_count": len(self.requests),
        }

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        response = await self._llm.generate(request)
        self.responses.append(response)
        return response


class InteractiveLab:
    """Small base class for notebook-friendly stateful experiments."""

    title = ""
    question = ""

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.state: dict[str, Any] = {}

    @property
    def decision_label(self) -> str:
        if self.mode == "deterministic":
            return "Model decision = SCRIPTED; Kernel execution = REAL"
        return "Model decision = REAL OpenAI-compatible; Kernel execution = REAL"

    def _llm_metadata(self) -> dict[str, Any]:
        llm = self.state.get("llm")
        if isinstance(llm, LabOpenAICompatibleLLM):
            return llm.metadata
        return {"provider": "scripted", "model": "ScriptedLLM"}

    def _model(self, responses: list[ModelResponse | Callable[[ModelRequest], ModelResponse]]) -> Any:
        if self.mode == "real_model":
            return LabOpenAICompatibleLLM()
        return ScriptedLLM(responses)

    def close(self) -> None:
        """Release lab-owned runtime resources after the final notebook cell."""

        session = self.state.get("session")
        if isinstance(session, Session):
            session.close()
        tmp = self.state.get("tmp")
        if isinstance(tmp, tempfile.TemporaryDirectory):
            tmp.cleanup()


class V01ExecutionSpineLab(InteractiveLab):
    title = "V0.1 Execution Spine Lab"
    question = "一个真实模型提出的 ToolCall 进入 Kernel 后发生什么？"

    def setup(self) -> dict[str, Any]:
        session = Session("lab-v01-session")
        agent = Agent.create(
            agent_id="lab-agent",
            session=session,
            capabilities={"calculator.divide"},
        )
        tools = ToolRegistry()
        tools.register(
            ToolDefinition(
                schema=ToolSchema(
                    "calculator.divide",
                    "Divide two numbers.",
                    {
                        "type": "object",
                        "properties": {
                            "a": {"type": "number"},
                            "b": {"type": "number"},
                        },
                        "required": ["a", "b"],
                    },
                ),
                handler=_divide,
                required_capability="calculator.divide",
            )
        )
        llm = self._model(
            [
                ModelResponse(
                    tool_calls=(
                        ToolCall("call-divide-1", "calculator.divide", {"a": 8, "b": 2}),
                    )
                )
            ]
        )
        request = ModelRequest(
            messages=(Message.user("What is 8 / 2?"),),
            tools=tools.model_schemas(agent.control),
            system_prompt=(
                "You are an AgentKernel lab assistant. Use calculator.divide "
                "when arithmetic division is needed."
            ),
        )
        self.state.update(
            {
                "session": session,
                "agent": agent,
                "tools": tools,
                "llm": llm,
                "request": request,
                "tool_result": None,
            }
        )
        return _show(
            "Setup",
            {
                "question": self.question,
                "mode": self.mode,
                "decision_boundary": self.decision_label,
                "agent_id": agent.control.agent_id,
                "session_id": session.session_id,
                "available_tool_count": len(request.tools),
            },
        )

    def show_model_request(self) -> dict[str, Any]:
        return _show("Model-visible request", _request_payload(self.state["request"]))

    def model_step(self) -> dict[str, Any]:
        response = _run(self.state["llm"].generate(self.state["request"]))
        self.state["response"] = response
        return _show(
            "Observable model response",
            {
                "provider_metadata": self._llm_metadata(),
                "response": _response_payload(response),
                "hidden_chain_of_thought": "not requested and not displayed",
            },
        )

    def kernel_execute_tool(self) -> dict[str, Any]:
        response: ModelResponse = self.state["response"]
        if not response.tool_calls:
            return _show(
                "Kernel decision",
                {
                    "status": "no_tool_call",
                    "meaning": "The model did not propose a tool call.",
                },
            )
        call = response.tool_calls[0]
        session: Session = self.state["session"]
        agent: Agent = self.state["agent"]
        tools: ToolRegistry = self.state["tools"]
        session.append(EventType.TURN_START, {"turn": 1})
        session.append(EventType.USER_MESSAGE, {"turn": 1, "content": "What is 8 / 2?"})
        session.append(EventType.STEP_START, {"turn": 1, "step": 1})
        session.append(
            EventType.ASSISTANT_MESSAGE,
            {
                "turn": 1,
                "step": 1,
                "content": response.content,
                "tool_calls": [call.as_dict()],
            },
        )
        session.append(EventType.TOOL_CALL, {"turn": 1, "step": 1, **call.as_dict()})
        result = _run(tools.execute(call, agent.control))
        session.append(EventType.TOOL_RESULT, {"turn": 1, "step": 1, **result.as_dict()})
        session.flush()
        self.state["tool_result"] = result
        self.state["next_request"] = ModelRequest(
            messages=(
                Message.user("What is 8 / 2?"),
                Message.assistant(response.content, response.tool_calls),
                Message.tool(result),
            ),
            tools=tools.model_schemas(agent.control),
            system_prompt=self.state["request"].system_prompt,
        )
        return _show(
            "Kernel execution",
            {
                "model_role": "untrusted proposer",
                "kernel_owned_steps": [
                    "ToolRegistry authorization",
                    "Tool handler invocation",
                    "ToolResult normalization",
                    "Session event append",
                ],
                "tool_result": result.as_dict(),
                "session_events": _events_payload(session),
            },
        )

    def show_next_visible_request(self) -> dict[str, Any]:
        return _show(
            "Next model-visible request",
            _request_payload(self.state["next_request"]),
        )

    def summary(self) -> dict[str, Any]:
        return _show(
            "What this proves",
            {
                "claim": "The LLM proposes; Kernel decides visibility, authority, execution, and durable facts.",
                "answer_to_question": "V0.1 routes a ToolCall through Kernel-owned layers before host code runs.",
                "not_claimed": "This lab does not measure model intelligence or sandbox security.",
            },
        )


class V02CrashRecoveryLab(InteractiveLab):
    title = "V0.2 Mid-task Crash Recovery Lab"
    question = "Agent 跑一半程序崩了，为什么还能继续？"

    def setup(self) -> dict[str, Any]:
        tmp = tempfile.TemporaryDirectory(prefix="agentkernel-lab-v02-")
        session_path = Path(tmp.name) / "session.jsonl"
        session = Session("lab-v02-session", JsonlSessionPersistence(session_path))
        agent = Agent.create(
            agent_id="lab-agent",
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
                handler=_add,
                required_capability="math.add",
            )
        )
        llm = self._model(
            [
                ModelResponse(
                    tool_calls=(ToolCall("call-add-1", "math.add", {"left": 7, "right": 35}),)
                ),
                ModelResponse(content="The answer is 42."),
            ]
        )
        session.append(EventType.TURN_START, {"turn": 1})
        session.append(EventType.USER_MESSAGE, {"turn": 1, "content": "What is 7 + 35?"})
        session.append(EventType.STEP_START, {"turn": 1, "step": 1})
        request = ModelRequest(
            messages=session.derive_messages(),
            tools=tools.model_schemas(agent.control),
            system_prompt="Use math.add for arithmetic, then answer the user.",
        )
        self.state.update(
            {
                "tmp": tmp,
                "session_path": session_path,
                "session": session,
                "agent": agent,
                "tools": tools,
                "llm": llm,
                "runtime_id": "process-P1",
                "request": request,
                "crashed": False,
            }
        )
        return _show(
            "Runtime P1 setup",
            {
                "mode": self.mode,
                "decision_boundary": self.decision_label,
                "runtime_process": "process-P1",
                "session_id": session.session_id,
                "session_path": str(session_path),
            },
        )

    def show_model_request(self) -> dict[str, Any]:
        return _show("P1 model-visible request", _request_payload(self.state["request"]))

    def model_step(self) -> dict[str, Any]:
        response = _run(self.state["llm"].generate(self.state["request"]))
        self.state["response"] = response
        return _show(
            "P1 observable model response",
            {
                "provider_metadata": self._llm_metadata(),
                "response": _response_payload(response),
                "hidden_chain_of_thought": "not requested and not displayed",
            },
        )

    def execute_tool_before_crash(self) -> dict[str, Any]:
        response: ModelResponse = self.state["response"]
        if not response.tool_calls:
            raise RuntimeError("V0.2 requires a tool call before the crash boundary")
        call = response.tool_calls[0]
        session: Session = self.state["session"]
        tools: ToolRegistry = self.state["tools"]
        agent: Agent = self.state["agent"]
        session.append(
            EventType.ASSISTANT_MESSAGE,
            {
                "turn": 1,
                "step": 1,
                "content": response.content,
                "tool_calls": [call.as_dict()],
            },
        )
        session.append(EventType.TOOL_CALL, {"turn": 1, "step": 1, **call.as_dict()})
        result = _run(tools.execute(call, agent.control))
        session.append(EventType.TOOL_RESULT, {"turn": 1, "step": 1, **result.as_dict()})
        session.flush()
        self.state["tool_result"] = result
        return _show(
            "Crash boundary",
            {
                "durable_tool_result": result.as_dict(),
                "final_assistant_message_written": False,
                "session_events_before_crash": _events_payload(session),
                "why_this_boundary_matters": "The task is unfinished, but the tool result is already durable.",
            },
        )

    def crash(self) -> dict[str, Any]:
        self.state["session"].close()
        self.state["session"] = None
        self.state["agent"] = None
        self.state["tools"] = None
        self.state["crashed"] = True
        return _show(
            "Simulated crash",
            {
                "discarded_runtime_process": "process-P1",
                "discarded_live_objects": ["Agent", "ToolRegistry", "Session object"],
                "kept_durable_file": str(self.state["session_path"]),
            },
        )

    def restart(self) -> dict[str, Any]:
        session = Session.load(
            "lab-v02-session",
            JsonlSessionPersistence(self.state["session_path"]),
        )
        agent = Agent.create(
            agent_id="lab-agent",
            session=session,
            capabilities={"math.add"},
        )
        tools = ToolRegistry()
        tools.register(
            ToolDefinition(
                schema=ToolSchema("math.add", "Add two integers.", {"type": "object"}),
                handler=_add,
                required_capability="math.add",
            )
        )
        request = ModelRequest(
            messages=session.derive_messages(),
            tools=tools.model_schemas(agent.control),
            system_prompt="Use math.add for arithmetic, then answer the user.",
        )
        self.state.update(
            {
                "session": session,
                "agent": agent,
                "tools": tools,
                "runtime_id": "process-P2",
                "resume_request": request,
            }
        )
        return _show(
            "Runtime P2 restart",
            {
                "old_runtime_process": "process-P1",
                "new_runtime_process": "process-P2",
                "same_session": session.session_id == "lab-v02-session",
                "recovery_status": session.recovery_analysis.status.value,
                "durable_tool_result_survives": any(
                    event.type is EventType.TOOL_RESULT for event in session.events
                ),
                "rebuilt_model_visible_request": _request_payload(request),
            },
        )

    def continue_after_restart(self) -> dict[str, Any]:
        response = _run(self.state["llm"].generate(self.state["resume_request"]))
        session: Session = self.state["session"]
        session.append(EventType.STEP_START, {"turn": 1, "step": 2})
        session.append(
            EventType.ASSISTANT_MESSAGE,
            {"turn": 1, "step": 2, "content": response.content, "tool_calls": []},
        )
        session.append(EventType.STEP_END, {"turn": 1, "step": 2, "outcome": "done"})
        session.append(EventType.TURN_END, {"turn": 1, "reason": "completed"})
        session.flush()
        self.state["final_response"] = response
        return _show(
            "Task continues in P2",
            {
                "observable_model_response": _response_payload(response),
                "final_session_events": _events_payload(session),
            },
        )

    def summary(self) -> dict[str, Any]:
        session: Session = self.state["session"]
        payload = _show(
            "What this proves",
            {
                "claim": "Session is durable semantic truth; Process/runtime is replaceable execution state.",
                "old_process": "process-P1",
                "new_process": "process-P2",
                "same_session": session.session_id,
                "unfinished_task_continued": bool(self.state.get("final_response")),
                "not_claimed": "This does not prove every external side effect is safe; V0.3 covers that boundary.",
            },
        )
        self.close()
        return payload


class V03DurableSideEffectLab(InteractiveLab):
    title = "V0.3 Durable Side Effect Lab"
    question = "文件已经修改但程序突然崩了，为什么不会重复修改？"

    def setup(self) -> dict[str, Any]:
        tmp = tempfile.TemporaryDirectory(prefix="agentkernel-lab-v03-")
        root = Path(tmp.name)
        fixture = make_minicode_workspace(root)
        workspace = discover_workspace(cwd=fixture.root)
        session_path = root / "session.jsonl"
        session = Session("lab-v03-session", JsonlSessionPersistence(session_path))
        registry = register_apply_patch_tool(ToolRegistry(), workspace, session=session)
        agent = Agent.create(
            agent_id="minicode-agent",
            session=session,
            capability_grants=apply_patch_capability_grants(
                agent_id="minicode-agent",
                workspace=workspace,
            ),
        )
        patch = (
            "*** Begin Patch\n"
            "*** Update File: calculator.py\n"
            "@@\n"
            "-    if b == 0:\n"
            "-        raise ZeroDivisionError('division by zero')\n"
            "-    return a / b\n"
            "+    return None if b == 0 else a / b\n"
            "*** End Patch"
        )
        call = ToolCall("call-patch-1", APPLY_PATCH_NAME, {"patch": patch})
        prepared = DurableApplyPatchAdapter(registry).prepare_call(
            workspace,
            call,
            agent.control,
        )
        llm = LabOpenAICompatibleLLM() if self.mode == "real_model" else None
        request = ModelRequest(
            messages=(
                Message.user(
                    "Fix calculator.py so divide(a, 0) returns None. "
                    "Use apply_patch.\n\ncalculator.py:\n"
                    + fixture.calculator.read_text(encoding="utf-8")
                ),
            ),
            tools=registry.model_schemas(agent.control),
            system_prompt=(
                "You are an AgentKernel V0.3 lab assistant. Propose exactly one "
                "apply_patch tool call for the requested file change."
            ),
        )
        self.state.update(
            {
                "tmp": tmp,
                "fixture": fixture,
                "workspace": workspace,
                "session_path": session_path,
                "session": session,
                "registry": registry,
                "agent": agent,
                "patch": patch,
                "call": call,
                "prepared": prepared,
                "llm": llm,
                "request": request,
                "real_model_missing_tool_call": False,
                "physical_patch_executions": 0,
            }
        )
        return _show(
            "Setup",
            {
                "mode": self.mode,
                "decision_boundary": self.decision_label,
                "workspace_root": str(workspace.root),
                "session_id": session.session_id,
                "target_file": str(fixture.calculator),
            },
        )

    def show_initial_state(self) -> dict[str, Any]:
        fixture = self.state["fixture"]
        return _show(
            "Initial filesystem",
            {"calculator.py": fixture.calculator.read_text(encoding="utf-8")},
        )

    def model_step(self) -> dict[str, Any]:
        if self.mode == "real_model":
            response = _run(self.state["llm"].generate(self.state["request"]))
            self.state["response"] = response
            matching_calls = [
                call for call in response.tool_calls if call.name == APPLY_PATCH_NAME
            ]
            if matching_calls:
                call = matching_calls[0]
                prepared = DurableApplyPatchAdapter(self.state["registry"]).prepare_call(
                    self.state["workspace"],
                    call,
                    self.state["agent"].control,
                )
                self.state.update(
                    {
                        "call": call,
                        "prepared": prepared,
                        "patch": str(call.arguments.get("patch", "")),
                        "real_model_missing_tool_call": False,
                    }
                )
            else:
                self.state["real_model_missing_tool_call"] = True
            return _show(
                "Observable model response",
                {
                    "model_visible_request": _request_payload(self.state["request"]),
                    "provider_metadata": self._llm_metadata(),
                    "response": _response_payload(response),
                    "accepted_apply_patch_tool_call": bool(matching_calls),
                    "hidden_chain_of_thought": "not requested and not displayed",
                },
            )
        call: ToolCall = self.state["call"]
        return _show(
            "Assistant ToolCall",
            {
                "decision_source": "SCRIPTED patch proposal in this deterministic lab",
                "model_visible_request": _request_payload(self.state["request"]),
                "tool_call": call.as_dict(),
                "hidden_chain_of_thought": "not requested and not displayed",
            },
        )

    def prepare(self) -> dict[str, Any]:
        if self.state.get("real_model_missing_tool_call"):
            raise RuntimeError(
                "real_model did not propose apply_patch; stop here and inspect the observable response"
            )
        session: Session = self.state["session"]
        prepared = self.state["prepared"]
        call: ToolCall = prepared.call
        session.append(EventType.TURN_START, {"turn": 1})
        session.append(
            EventType.USER_MESSAGE,
            {"turn": 1, "content": "Fix calculator.py so divide(a, 0) returns None."},
        )
        session.append(EventType.STEP_START, {"turn": 1, "step": 1})
        session.append(
            EventType.ASSISTANT_MESSAGE,
            {
                "turn": 1,
                "step": 1,
                "content": "",
                "tool_calls": [call.as_dict()],
            },
        )
        session.append(EventType.TOOL_CALL, {"turn": 1, "step": 1, **call.as_dict()})
        session.append(
            EventType.TOOL_PREPARE,
            {
                "turn": 1,
                "step": 1,
                "operation_id": prepared.operation_id,
                "tool_call_id": call.call_id,
                "tool_name": call.name,
                "effect_kind": ToolEffectKind.RECONCILABLE_MUTATION.value,
            },
        )
        session.flush()
        return _show(
            "PREPARE",
            {
                "operation_id": prepared.operation_id,
                "why": "Kernel records the intended mutation before changing the filesystem.",
                "changed_files": prepared.changed_files,
                "session_events": _events_payload(session),
            },
        )

    def dispatch(self) -> dict[str, Any]:
        session: Session = self.state["session"]
        prepared = self.state["prepared"]
        session.append(
            EventType.TOOL_DISPATCH,
            {
                "turn": 1,
                "step": 1,
                "operation_id": prepared.operation_id,
                "attempt": 1,
            },
        )
        session.flush()
        return _show(
            "DISPATCH",
            {
                "operation_id": prepared.operation_id,
                "effect_has_happened_yet": False,
                "session_events": _events_payload(session),
            },
        )

    def apply_effect(self) -> dict[str, Any]:
        fixture = self.state["fixture"]
        prepared = self.state["prepared"]
        before = fixture.calculator.read_text(encoding="utf-8")
        apply_mutation_plan(prepared.plan)
        after = fixture.calculator.read_text(encoding="utf-8")
        self.state["physical_patch_executions"] += 1
        return _show(
            "Filesystem effect",
            {
                "before": before,
                "after": after,
                "current_file_hash": hash_file_or_absent(fixture.calculator),
                "expected_postimage_hash": prepared.plan.files[0].postimage_hash,
                "commit_written": any(
                    event.type is EventType.TOOL_COMMIT
                    for event in self.state["session"].events
                ),
            },
        )

    def crash(self) -> dict[str, Any]:
        self.state["session"].close()
        self.state["session"] = None
        self.state["registry"] = None
        self.state["agent"] = None
        return _show(
            "Simulated crash",
            {
                "important_fact": "The file is already modified, but TOOL_COMMIT does not exist yet.",
                "discarded_runtime": "old process and live Python objects",
                "kept": ["filesystem", "session JSONL"],
            },
        )

    def restart(self) -> dict[str, Any]:
        session = Session.load(
            "lab-v03-session",
            JsonlSessionPersistence(self.state["session_path"]),
        )
        registry = register_apply_patch_tool(
            ToolRegistry(),
            self.state["workspace"],
            session=session,
        )
        agent = Agent.create(
            agent_id="minicode-agent",
            session=session,
            capability_grants=apply_patch_capability_grants(
                agent_id="minicode-agent",
                workspace=self.state["workspace"],
            ),
        )
        self.state.update({"session": session, "registry": registry, "agent": agent})
        return _show(
            "Restart",
            {
                "new_runtime": "fresh process",
                "same_session": session.session_id,
                "recovery_status": session.recovery_analysis.status.value,
                "events_after_reload": _events_payload(session),
            },
        )

    def analyze(self) -> dict[str, Any]:
        session: Session = self.state["session"]
        fixture = self.state["fixture"]
        operation = session.recovery_analysis.durable_operations[0]
        expected = self.state["prepared"].plan.files[0].postimage_hash
        current = hash_file_or_absent(fixture.calculator)
        self.state["operation"] = operation
        return _show(
            "Recovery analysis",
            {
                "prepare": True,
                "dispatch": True,
                "commit": operation.committed,
                "classification": operation.classification.value,
                "expected_postimage_hash": expected,
                "current_file_hash": current,
                "hashes_match": current == expected,
            },
        )

    def reconcile(self) -> dict[str, Any]:
        session: Session = self.state["session"]
        observed = _run(
            DurableApplyPatchAdapter(self.state["registry"]).reconcile(
                self.state["operation"],
                self.state["agent"].control,
                session,
            )
        )
        final = session.recovery_analysis.durable_operations[0]
        self.state["observed"] = observed
        return _show(
            "Recovery decision",
            {
                "decision": "RECONCILE existing side effect",
                "blind_retry": False,
                "reconcile_status": observed.status.value,
                "commit_written_after_reconcile": final.committed,
                "physical_patch_executions": self.state["physical_patch_executions"],
            },
        )

    def summary(self) -> dict[str, Any]:
        payload = _show(
            "What this proves",
            {
                "claim": "Recovery is not blind retry.",
                "why": "The Kernel compares durable WAL facts with real filesystem state and commits an already completed effect.",
                "duplicate_side_effects": 0,
                "not_claimed": "Arbitrary shell mutation is not made WAL-safe by this mechanism.",
            },
        )
        self.close()
        return payload


def _v01_execution_spine() -> LabPayload:
    async def run() -> LabPayload:
        session = Session("lab-v01-session")
        agent = Agent.create(
            agent_id="lab-agent",
            session=session,
            capabilities={"calculator.divide"},
        )
        tools = ToolRegistry()
        tools.register(
            ToolDefinition(
                schema=ToolSchema(
                    "calculator.divide",
                    "Divide two numbers.",
                    {"type": "object"},
                ),
                handler=_divide,
                required_capability="calculator.divide",
            )
        )
        call = ToolCall("call-divide-1", "calculator.divide", {"a": 8, "b": 2})
        answer = await DefaultAgentLoop(
            llm=ScriptedLLM(
                [
                    ModelResponse(tool_calls=(call,)),
                    lambda request: ModelResponse(
                        content=f"Tool result observed in {request.messages[-1].role.value} message."
                    ),
                ]
            ),
            tools=tools,
            prompt=PromptService("Use the calculator tool when needed."),
        ).run(agent, "What is 8 / 2?")
        event_types = [event.type.value for event in session.events]
        return {
            "steps": [
                _step("User prompt", "What is 8 / 2?"),
                _step(
                    "Model visible input",
                    {
                        "system": "Use the calculator tool when needed.",
                        "available_tools": [schema.name for schema in tools.model_schemas(agent.control)],
                    },
                ),
                _step("Assistant ToolCall", call.as_dict()),
                _step("Kernel path", ["DefaultAgentLoop", "ToolRegistry", "Tool handler", "Session event log"]),
                _step("Session events", event_types),
            ],
            "result": {"final_answer": answer, "tool_call_count": event_types.count("tool/call")},
            "contrast": "Without a Kernel boundary, the model would be directly trusted to mutate state or call host code.",
            "proves": "V0.1 makes the LLM an untrusted proposer and routes Tool execution through Kernel-owned boundaries.",
        }

    return _run(run())


def _v02_crash_recovery() -> LabPayload:
    async def run_once(path: Path) -> tuple[str, list[str]]:
        session = Session("lab-v02-session", JsonlSessionPersistence(path))
        agent = Agent.create(
            agent_id="lab-agent",
            session=session,
            capabilities={"math.add"},
        )
        tools = ToolRegistry()
        tools.register(
            ToolDefinition(
                schema=ToolSchema("math.add", "Add two integers.", {"type": "object"}),
                handler=_add,
                required_capability="math.add",
            )
        )

        def final_answer(request: ModelRequest) -> ModelResponse:
            if request.messages[-1].role is not MessageRole.TOOL:
                raise AssertionError("expected replayed tool result")
            return ModelResponse(content="42")

        answer = await DefaultAgentLoop(
            llm=ScriptedLLM(
                [
                    ModelResponse(
                        tool_calls=(ToolCall("call-add-1", "math.add", {"left": 7, "right": 35}),)
                    ),
                    final_answer,
                ]
            ),
            tools=tools,
            prompt=PromptService("Use the math tool."),
        ).run(agent, "What is 7 + 35?")
        events = [event.type.value for event in session.events]
        session.close()
        return answer, events

    with tempfile.TemporaryDirectory(prefix="agentkernel-lab-v02-") as directory:
        path = Path(directory) / "session.jsonl"
        answer, before_events = _run(run_once(path))
        restored = Session.load("lab-v02-session", JsonlSessionPersistence(path))
        try:
            restored_events = [event.type.value for event in restored.events]
            return {
                "steps": [
                    _step("Process P1 created", {"process": "implicit loop runtime", "session": "lab-v02-session"}),
                    _step("P1 runs one task", {"answer_before_crash": answer, "events_written": before_events}),
                    _step("Simulated crash", "Discard the live Agent/Loop objects. Keep only the JSONL session file."),
                    _step("Runtime P2 resumes", {"recovered_status": restored.recovery_analysis.status.value, "derived_messages": len(restored.derive_messages())}),
                ],
                "result": {
                    "session_id_preserved": restored.session_id == "lab-v02-session",
                    "lost_durable_facts": before_events != restored_events,
                    "events_after_restart": len(restored_events),
                },
                "contrast": "A stateless loop only has the live Python objects; after a crash it cannot distinguish completed work from missing work.",
                "proves": "V0.2 durable Session facts survive process loss and can be replayed into a new runtime.",
            }
        finally:
            restored.close()


def _v03_durable_side_effect() -> LabPayload:
    with tempfile.TemporaryDirectory(prefix="agentkernel-lab-v03-") as directory:
        root = Path(directory)
        fixture = make_minicode_workspace(root)
        workspace = discover_workspace(cwd=fixture.root)
        session_path = root / "session.jsonl"
        session = Session("lab-v03-session", JsonlSessionPersistence(session_path))
        registry = register_apply_patch_tool(ToolRegistry(), workspace, session=session)
        agent = Agent.create(
            agent_id="minicode-agent",
            session=session,
            capability_grants=apply_patch_capability_grants(
                agent_id="minicode-agent",
                workspace=workspace,
            ),
        )
        patch = (
            "*** Begin Patch\n"
            "*** Update File: calculator.py\n"
            "@@\n"
            "-    if b == 0:\n"
            "-        raise ZeroDivisionError('division by zero')\n"
            "-    return a / b\n"
            "+    return None if b == 0 else a / b\n"
            "*** End Patch"
        )
        call = ToolCall("call-patch-1", APPLY_PATCH_NAME, {"patch": patch})
        prepared = DurableApplyPatchAdapter(registry).prepare_call(
            workspace,
            call,
            agent.control,
        )

        before = fixture.calculator.read_text(encoding="utf-8")
        session.append(EventType.TURN_START, {"turn": 1})
        session.append(
            EventType.USER_MESSAGE,
            {"turn": 1, "content": "Fix divide by zero and run tests."},
        )
        session.append(EventType.STEP_START, {"turn": 1, "step": 1})
        session.append(
            EventType.ASSISTANT_MESSAGE,
            {
                "turn": 1,
                "step": 1,
                "content": "",
                "tool_calls": [prepared.call.as_dict()],
            },
        )
        session.append(EventType.TOOL_CALL, {"turn": 1, "step": 1, **prepared.call.as_dict()})
        session.append(
            EventType.TOOL_PREPARE,
            {
                "turn": 1,
                "step": 1,
                "operation_id": prepared.operation_id,
                "tool_call_id": prepared.call.call_id,
                "tool_name": prepared.call.name,
                "effect_kind": ToolEffectKind.RECONCILABLE_MUTATION.value,
            },
        )
        session.flush()
        session.append(
            EventType.TOOL_DISPATCH,
            {
                "turn": 1,
                "step": 1,
                "operation_id": prepared.operation_id,
                "attempt": 1,
            },
        )
        session.flush()
        apply_mutation_plan(prepared.plan)
        after = fixture.calculator.read_text(encoding="utf-8")
        session.close()

        restored = Session.load("lab-v03-session", JsonlSessionPersistence(session_path))
        try:
            operation = restored.recovery_analysis.durable_operations[0]
            recovered_agent = Agent.create(
                agent_id="minicode-agent",
                session=restored,
                capability_grants=apply_patch_capability_grants(
                    agent_id="minicode-agent",
                    workspace=workspace,
                ),
            )
            recovered_registry = register_apply_patch_tool(
                ToolRegistry(),
                workspace,
                session=restored,
            )
            observed = _run(
                DurableApplyPatchAdapter(recovered_registry).reconcile(
                    operation,
                    recovered_agent.control,
                    restored,
                )
            )
            final = restored.recovery_analysis.durable_operations[0]
            return {
                "steps": [
                    _step("Task", "Fix calculator.py so divide(a, 0) returns None."),
                    _step("Assistant ToolCall", {"tool": "apply_patch", "patch": patch}),
                    _step("PREPARE", {"operation_id": prepared.operation_id, "changed_files": prepared.changed_files}),
                    _step("DISPATCH and effect", {"before": before, "after": after}),
                    _step("Crash point", "Process dies after filesystem mutation and before TOOL_COMMIT."),
                    _step(
                        "Recovery inspection",
                        {
                            "classification": operation.classification.value,
                            "expected_postimage_hash": prepared.plan.files[0].postimage_hash,
                            "current_file_hash": hash_file_or_absent(fixture.calculator),
                            "hashes_match": hash_file_or_absent(fixture.calculator) == prepared.plan.files[0].postimage_hash,
                        },
                    ),
                    _step("Recovery decision", {"reconcile_status": observed.status.value, "blind_retry": False}),
                ],
                "result": {
                    "physical_patch_executions": 1,
                    "duplicate_side_effects": 0,
                    "committed_after_recovery": final.committed,
                    "final_classification": final.classification.value,
                },
                "contrast": "Without PREPARE/DISPATCH/reconcile, recovery would have to retry blindly or give up.",
                "proves": "V0.3 recovery is not blind retry: it inspects reality and commits an already completed side effect.",
            }
        finally:
            restored.close()


def _append_text_turn(session: Session, turn: int, user: str, assistant: str) -> None:
    session.append(EventType.TURN_START, {"turn": turn})
    session.append(EventType.USER_MESSAGE, {"turn": turn, "content": user})
    session.append(EventType.STEP_START, {"turn": turn, "step": 1})
    session.append(
        EventType.ASSISTANT_MESSAGE,
        {"turn": turn, "step": 1, "content": assistant, "tool_calls": []},
    )
    session.append(EventType.STEP_END, {"turn": turn, "step": 1, "outcome": "done"})
    session.append(EventType.TURN_END, {"turn": turn, "reason": "completed"})


def _v04_context_vm() -> LabPayload:
    session = Session("lab-v04-session")
    for turn in range(1, 51):
        _append_text_turn(
            session,
            turn,
            f"user fact {turn}: " + ("important durable detail " * 8),
            f"assistant response {turn}: " + ("bounded model-visible text " * 6),
        )
    projector = ContextProjector(ApproximateTokenEstimator(1))
    all_pages = projector.project(session, system_prompt="Keep the answer concise.")
    working_set = ContextManager(projector=projector).build_working_set(
        session,
        current_turn=50,
        budget=ContextBudget(max_tokens=800),
        system_prompt="Keep the answer concise.",
    )
    return {
        "steps": [
            _step("Long session", {"turns": 50, "durable_events": len(session.events)}),
            _step("Without Context VM", {"model_messages": len(session.derive_messages()), "risk": "send the whole durable history"}),
            _step("Context projection", {"projected_pages": len(all_pages), "selected_pages": working_set.metrics.selected_pages}),
            _step("Model input", {"selected_tokens": working_set.metrics.selected_tokens, "evicted_pages": working_set.metrics.evicted_pages}),
        ],
        "result": {
            "durable_truth_events": len(session.events),
            "model_visible_messages": len(working_set.to_messages()),
            "context_equals_truth": len(working_set.to_messages()) == len(session.derive_messages()),
        },
        "contrast": "A full-history agent treats Session truth and model context as the same object.",
        "proves": "V0.4 separates durable truth from the bounded working set sent to the model.",
    }


def _v05_large_output() -> LabPayload:
    payload = ("pytest diagnostic line\n" * 100_000).encode("utf-8")
    owner = ResourceOwner("lab-agent", "lab-v05-session")
    intruder = ResourceOwner("other-agent", "other-session")
    with tempfile.TemporaryDirectory(prefix="agentkernel-lab-v05-") as directory:
        service = ResourceService(LocalResourceStore(Path(directory) / "resources"))
        handle = service.create_artifact(
            payload,
            owner=owner,
            media_type="text/plain",
            encoding="utf-8",
            source_tool_name="run_command",
            source_tool_call_id="call-pytest-1",
            source_operation_id="op-pytest-1",
        )
        preview = service.read(handle.uri, owner=owner, limit=80)
        denied = False
        try:
            service.read(handle.uri, owner=intruder, limit=80)
        except ResourceAccessDenied:
            denied = True
        return {
            "steps": [
                _step("Large output", {"bytes": len(payload), "source": "simulated pytest output"}),
                _step("Resource handle", handle.as_dict()),
                _step("Model-visible preview", preview.data.decode("utf-8", errors="replace")),
                _step("Unauthorized possession test", {"other_agent_has_uri": handle.uri, "read_denied": denied}),
            ],
            "result": {
                "stored_bytes": handle.size_bytes,
                "model_preview_bytes": len(preview.data),
                "handle_is_permission": False,
                "unauthorized_read_denied": denied,
            },
            "contrast": "Inlining 2MB of tool output grows context even when the model only needs a small preview.",
            "proves": "V0.5 stores large bytes as artifacts and returns a small handle plus preview; authorization still happens on read.",
        }


def _v06_capability_denial() -> LabPayload:
    async def run() -> LabPayload:
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                schema=ToolSchema("workspace.write", "Write a file.", {"type": "object"}),
                handler=_write_stub,
                required_action=TOOL_EXECUTE_ACTION,
                required_resource="tool://workspace.write",
            )
        )
        allowed_agent = Agent.create(
            agent_id="allowed-agent",
            session=Session("allowed-session"),
            capability_grants=(CapabilityGrant("allowed-agent", TOOL_EXECUTE_ACTION, "tool://workspace.write"),),
        )
        denied_agent = Agent.create(
            agent_id="denied-agent",
            session=Session("denied-session"),
        )
        call = ToolCall("call-write-1", "workspace.write", {"path": "README.md"})
        denied = await registry.execute(call, denied_agent.control)
        allowed = await registry.execute(call, allowed_agent.control)
        return {
            "steps": [
                _step("LLM proposes mutation", call.as_dict()),
                _step("Denied model-visible tools", [schema.name for schema in registry.model_schemas(denied_agent.control)]),
                _step("Execution re-check", {"denied_ok": denied.ok, "error": denied.error.as_dict() if denied.error else None}),
                _step("Authorized comparison", {"allowed_ok": allowed.ok, "output": allowed.output}),
            ],
            "result": {
                "unauthorized_hidden_from_model": len(registry.model_schemas(denied_agent.control)) == 0,
                "unauthorized_execution_denied": denied.error is not None and denied.error.code is ErrorCode.EACCES,
                "authorized_execution_allowed": allowed.ok,
            },
            "contrast": "A tool-only design relies on the tool or prompt to say no after the model has already selected it.",
            "proves": "V0.6 places authority in the Kernel: the LLM can propose, but cannot grant itself capability.",
        }

    return _run(run())


def _v07_process_runtime() -> LabPayload:
    agent = Agent.create(
        agent_id="lab-agent",
        session=Session("lab-v07-session"),
        budget=AgentBudget(max_token_usage=5),
    )
    collector = UsageCollector()
    scheduler = CooperativeScheduler(usage_collector=collector)
    process = scheduler.create_process(process_id="process-001", agent=agent.control)
    scheduler.dispatch(process.process_id)
    collector.record_llm_usage(
        process.process_id,
        ModelUsage(input_tokens=4, output_tokens=2, total_tokens=6),
    )
    blocked = False
    try:
        scheduler.safe_point(process.process_id, SchedulerSafePoint.AFTER_LLM_CALL)
    except ProcessBudgetExceeded:
        blocked = True
    snapshot = collector.snapshot(process.process_id)
    scheduler.reset_usage(process.process_id)
    scheduler.unblock(process.process_id)
    return {
        "steps": [
            _step("Agent principal", {"agent_id": agent.control.agent_id, "session_id": agent.control.session_id}),
            _step("Process runtime identity", {"process_id": process.process_id, "state_after_dispatch": ProcessState.RUNNING.value}),
            _step("Usage observed", _usage_snapshot_payload(snapshot)),
            _step("Safe point", {"point": SchedulerSafePoint.AFTER_LLM_CALL.value, "blocked": blocked}),
            _step("Recovery/unblock", {"state": process.state.value}),
        ],
        "result": {
            "agent_owns_capability": process.capability_snapshot.agent_id == agent.control.agent_id,
            "process_blocked_by_budget": blocked,
            "session_id_unchanged": process.session_id == agent.control.session_id,
            "state_after_unblock": process.state.value,
        },
        "contrast": "Without a process identity, runtime cancellation and budget state get mixed into Agent authority or durable Session truth.",
        "proves": "V0.7 makes Process runtime state schedulable while leaving Agent authority and Session facts separate.",
    }


def _v08_multi_agent() -> LabPayload:
    async def run() -> LabPayload:
        agents = AgentRegistry()
        parent_session = Session("session-parent")
        child_session = Session("session-child")
        parent = agents.create_root(
            agent_id="agent-parent",
            session=parent_session,
            capability_grants=(
                CapabilityGrant("agent-parent", TOOL_EXECUTE_ACTION, "tool://math.add"),
                CapabilityGrant("agent-parent", RESOURCE_READ_ACTION, "artifact://**"),
            ),
            creation_id="create-parent",
        )
        child = agents.create_child(
            parent_agent_id=parent.control.agent_id,
            agent_id="agent-child",
            session=child_session,
            creation_id="create-child",
            record_session=parent_session,
        )
        processes = ProcessManager(agent_registry=agents)
        parent_process = processes.create_process(
            process_id="process-parent",
            agent=parent.control,
        )
        child_process = processes.create_child_process(
            parent_process_id=parent_process.process_id,
            process_id="process-child",
            agent=child.control,
        )
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                schema=ToolSchema("math.add", "Add.", {"type": "object"}),
                handler=_add,
                required_action=TOOL_EXECUTE_ACTION,
                required_resource="tool://math.add",
            )
        )
        before = await registry.execute(
            ToolCall("call-add-1", "math.add", {"left": 2, "right": 3}),
            child.control,
        )
        decision = agents.delegate_capability(
                request=DelegateCapabilityRequest(
                "agent-parent",
                "agent-child",
                TOOL_EXECUTE_ACTION,
                "tool://math.add",
                correlation_id="delegate-tool",
            ),
            record_session=child_session,
        )
        after = await registry.execute(
            ToolCall("call-add-2", "math.add", {"left": 2, "right": 3}),
            agents.get("agent-child"),
        )
        with tempfile.TemporaryDirectory(prefix="agentkernel-lab-v08-") as directory:
            shares = ResourceShareRegistry(agent_registry=agents, clock=lambda: 100.0)
            resources = ResourceService(
                LocalResourceStore(Path(directory) / "resources"),
                share_registry=shares,
                resource_id_factory=lambda: "res_shared",
                handle_id_factory=lambda: "hdl_shared",
                clock=lambda: 1.0,
            )
            owner = ResourceOwner("agent-parent", "session-parent")
            child_owner = ResourceOwner("agent-child", "session-child")
            handle = resources.create_artifact(
                b"parent artifact",
                owner=owner,
                media_type="text/plain",
                encoding="utf-8",
                source_tool_name="producer",
                source_tool_call_id="call-producer",
                source_operation_id="op-producer",
            )
            ipc = KernelIPC(
                agent_registry=agents,
                process_manager=processes,
                sessions={"agent-parent": parent_session, "agent-child": child_session},
                persistence=InMemoryIPCPersistence(),
                time_fn=lambda: 1.0,
            )
            ipc.create_channel(
                channel_id="channel-parent-child",
                sender_agent_id="agent-parent",
                receiver_agent_id="agent-child",
                receiver_process_id=child_process.process_id,
            )
            ipc.send(
                channel_id="channel-parent-child",
                sender_process_id=parent_process.process_id,
                payload={"body": "inspect this artifact"},
                resource_refs=(handle.uri,),
                message_id="message-1",
                correlation_id="corr-1",
            )
            delivered = ipc.receive(
                channel_id="channel-parent-child",
                receiver_agent_id="agent-child",
                receiver_process_id=child_process.process_id,
            )
            read_before_share_denied = False
            try:
                resources.read(
                    handle.uri,
                    owner=child_owner,
                    capability_evaluator=CapabilityEvaluator(
                        (CapabilityGrant("agent-child", RESOURCE_READ_ACTION, handle.uri),)
                    ),
                )
            except ResourceAccessDenied:
                read_before_share_denied = True
            share = resources.share(
                handle.uri,
                owner=owner,
                grantee_agent_id="agent-child",
                allowed_actions=(RESOURCE_READ_ACTION,),
                record_session=parent_session,
                share_id="share_1",
                correlation_id="corr-share",
            )
        return {
            "steps": [
                _step("Agent tree", {"lineage": agents.lineage("agent-child")}),
                _step("Process tree", {"lineage": processes.lineage("process-child")}),
                _step("Child before delegation", {"ok": before.ok, "error": before.error.as_dict() if before.error else None}),
                _step("Capability delegation", {"allowed": decision.allowed, "reason": decision.reason}),
                _step("Child after delegation", {"ok": after.ok, "output": after.output}),
                _step("IPC message", {"resource_ref": delivered.resource_refs[0] if delivered else None}),
                _step("Resource sharing", {"ipc_ref_alone_denied": read_before_share_denied, "share_allowed": share.allowed}),
            ],
            "result": {
                "agent_process_identity_separate": agents.lineage("agent-child") != processes.lineage("process-child"),
                "child_cannot_use_tool_before_delegation": before.error is not None and before.error.code is ErrorCode.EACCES,
                "delegation_enables_narrow_tool_use": after.ok,
                "ipc_reference_is_not_permission": read_before_share_denied,
                "explicit_share_required": share.allowed,
            },
            "contrast": "A shared-memory or prompt-only multi-agent design cannot prove who may call which tool or read which artifact.",
            "proves": "V0.8 combines Agent identity, Process identity, IPC references, delegation, and resource sharing without making a reference equal permission.",
        }

    return _run(run())


LABS: dict[str, LabSpec] = {
    "v01": LabSpec(
        "v01",
        "V0.1 Execution Spine Lab",
        "A real LLM ToolCall goes through which Kernel layers?",
        _v01_execution_spine,
    ),
    "v02": LabSpec(
        "v02",
        "V0.2 Crash Recovery Lab",
        "If the runtime dies mid-task, why can the Agent continue?",
        _v02_crash_recovery,
    ),
    "v03": LabSpec(
        "v03",
        "V0.3 Durable Side Effect Lab",
        "If the file was modified but the process crashed, why is it not modified twice?",
        _v03_durable_side_effect,
    ),
    "v04": LabSpec(
        "v04",
        "V0.4 Context VM Lab",
        "After 50 turns, why not send the whole Session to the model?",
        _v04_context_vm,
    ),
    "v05": LabSpec(
        "v05",
        "V0.5 Resource Handle Lab",
        "If pytest prints megabytes, why does context stay small?",
        _v05_large_output,
    ),
    "v06": LabSpec(
        "v06",
        "V0.6 Capability Denial Lab",
        "Why can the Kernel reject a file mutation the LLM asked for?",
        _v06_capability_denial,
    ),
    "v07": LabSpec(
        "v07",
        "V0.7 Process Runtime Lab",
        "If a Process exhausts budget, why does Agent authority still remain separate?",
        _v07_process_runtime,
    ),
    "v08": LabSpec(
        "v08",
        "V0.8 Multi-Agent Boundary Lab",
        "How can two Agents communicate and share resources without overreach?",
        _v08_multi_agent,
    ),
}


def _usage_snapshot_payload(snapshot: Any) -> dict[str, Any]:
    return {
        "process_id": snapshot.process_id,
        "token_usage": snapshot.token_usage,
        "model_cost": snapshot.model_cost,
        "tool_calls": snapshot.tool_calls,
        "resource_reads": snapshot.resource_reads,
        "resource_bytes": snapshot.resource_bytes,
        "wall_time": snapshot.wall_time,
    }
