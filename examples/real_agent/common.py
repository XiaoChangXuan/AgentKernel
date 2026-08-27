"""Shared helpers for opt-in real-provider execution trace demos."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agentkernel import (
    Agent,
    HookEvent,
    HookManager,
    HookPoint,
    LLMService,
    ModelRequest,
    ModelResponse,
    Session,
    ToolRegistry,
    ToolResult,
)
from agentkernel.providers import (
    OpenAICompatibleConfig,
    OpenAICompatibleConfigurationError,
    OpenAICompatibleLLM,
)

RUN_FLAG = "AGENTKERNEL_RUN_REAL_MODEL"


class TraceRecorder:
    """Collect human-readable runtime facts without private model reasoning."""

    def __init__(
        self,
        *,
        title: str,
        task: str,
        agent_id: str,
        session_id: str,
        process_id: str | None,
        provider: str,
    ) -> None:
        self.title = title
        self.task = task
        self.agent_id = agent_id
        self.session_id = session_id
        self.process_id = process_id
        self.provider = provider
        self.events: list[dict[str, Any]] = []

    def record(self, event_type: str, **payload: Any) -> None:
        self.events.append(
            {
                "sequence": len(self.events) + 1,
                "event_type": event_type,
                **_safe_payload(payload),
            }
        )

    def record_session(self, session: Session) -> None:
        for event in session.events:
            self.record(
                "session_event",
                session_id=session.session_id,
                type=event.type.value,
                data=event.data,
            )

    def print_human(self) -> None:
        print(self.title)
        print("=" * len(self.title))
        print(f"Task: {self.task}")
        print(f"Agent identity: {self.agent_id}")
        print(f"Process identity: {self.process_id or 'none'}")
        print(f"Session identity: {self.session_id}")
        print(f"Model/provider: {self.provider}")
        print()

        step = 0
        for event in self.events:
            event_type = event["event_type"]
            if event_type == "model_request":
                step += 1
                print(f"STEP {step}")
                print("Model Request summary:")
                print(f"  messages: {event['message_roles']}")
                print(f"  latest user: {event.get('latest_user', '')}")
                print(
                    "  model-visible tools: "
                    + (", ".join(event["visible_tools"]) or "none")
                )
            elif event_type == "model_response":
                print("Model Response:")
                if event.get("tool_calls"):
                    for call in event["tool_calls"]:
                        print(
                            f"  tool call: {call['name']}({call['arguments']})"
                        )
                else:
                    print(f"  content: {event.get('content', '')}")
                print()
            elif event_type == "kernel_authorization":
                print("Kernel authorization:")
                print(
                    f"  {event['outcome']} {event['tool']} "
                    f"reason={event['reason']}"
                )
            elif event_type == "tool_result":
                print("Tool returned:")
                print(f"  ok: {event['ok']}")
                print(f"  output: {event.get('output', event.get('error'))}")
                print()
            elif event_type == "process_state":
                print("Process state:")
                print(f"  {event['state']}")
                print()
            elif event_type == "resource_handle":
                print("ResourceHandle:")
                print(f"  uri: {event['uri']}")
                print(f"  bytes: {event['size_bytes']}")
                print()
            elif event_type == "final_answer":
                print("Final Answer:")
                print(f"  {event['answer']}")
                print()

        print("Session facts appended:")
        for event in self.events:
            if event["event_type"] != "session_event":
                continue
            print(f"  {event['type']}: {event['data']}")

    def write_jsonl(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as stream:
            for event in self.events:
                stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True))
                stream.write("\n")


class ObservedLLM(LLMService):
    """Wrap a provider and record request/response summaries."""

    def __init__(
        self,
        provider: LLMService,
        recorder: TraceRecorder,
    ) -> None:
        self._provider = provider
        self._recorder = recorder

    @property
    def token_accounting(self):
        return self._provider.token_accounting

    @property
    def context_limits(self):
        return self._provider.context_limits

    async def generate(self, request: ModelRequest) -> ModelResponse:
        latest_user = ""
        for message in reversed(request.messages):
            if message.role.value == "user":
                latest_user = message.content
                break
        self._recorder.record(
            "model_request",
            message_roles=[message.role.value for message in request.messages],
            latest_user=_clip(latest_user),
            visible_tools=[tool.name for tool in request.tools],
        )
        response = await self._provider.generate(request)
        self._recorder.record(
            "model_response",
            content=_clip(response.content),
            tool_calls=[
                {
                    "call_id": call.call_id,
                    "name": call.name,
                    "arguments": dict(call.arguments),
                }
                for call in response.tool_calls
            ],
        )
        return response


def configured_real_provider() -> OpenAICompatibleConfig | None:
    """Return explicit provider config, or print a safe skip message."""

    if os.environ.get(RUN_FLAG) != "1":
        print("SKIPPED: real-model demos are opt-in.")
        print(f"Set {RUN_FLAG}=1 plus AGENTKERNEL_LLM_BASE_URL and")
        print("AGENTKERNEL_LLM_MODEL to run this demo.")
        print("Optional: AGENTKERNEL_LLM_API_KEY.")
        return None
    try:
        return OpenAICompatibleConfig.from_env()
    except OpenAICompatibleConfigurationError as error:
        print(f"SKIPPED: provider configuration is incomplete: {error}")
        return None


def observed_openai_compatible(
    config: OpenAICompatibleConfig,
    recorder: TraceRecorder,
) -> ObservedLLM:
    return ObservedLLM(OpenAICompatibleLLM(config), recorder)


def install_tool_trace_hooks(
    hooks: HookManager,
    *,
    recorder: TraceRecorder,
    tools: ToolRegistry,
    agent: Agent,
) -> None:
    def before_tool(event: HookEvent) -> None:
        if event.tool_call is None:
            return
        authorization = tools.authorization_for_execution(
            event.tool_call,
            agent.control,
        )
        if isinstance(authorization, ToolResult):
            recorder.record(
                "kernel_authorization",
                tool=event.tool_call.name,
                outcome="DENY",
                reason=authorization.error.message if authorization.error else "error",
            )
            return
        recorder.record(
            "kernel_authorization",
            tool=event.tool_call.name,
            outcome="ALLOW" if authorization.allowed else "DENY",
            reason=authorization.reason,
            matched_grant=authorization.decision.matched_grant,
        )

    def after_tool(event: HookEvent) -> None:
        if event.tool_result is None:
            return
        output = event.tool_result.output
        recorder.record(
            "tool_result",
            tool=event.tool_result.name,
            ok=event.tool_result.ok,
            output=output,
            error=event.tool_result.error.as_dict()
            if event.tool_result.error is not None
            else None,
        )
        _record_resource_handles(recorder, output)

    hooks.subscribe(HookPoint.BEFORE_TOOL, before_tool)
    hooks.subscribe(HookPoint.AFTER_TOOL, after_tool)


def maybe_write_jsonl(recorder: TraceRecorder, value: str | None) -> None:
    if not value:
        return
    path = Path(value)
    recorder.write_jsonl(path)
    print()
    print(f"Machine-readable trace written: {path}")


def provider_label(config: OpenAICompatibleConfig) -> str:
    return f"openai-compatible:{config.model}@{config.base_url}"


def _record_resource_handles(recorder: TraceRecorder, value: Any) -> None:
    if isinstance(value, Mapping):
        resource = value.get("resource")
        if isinstance(resource, Mapping):
            uri = resource.get("uri")
            size_bytes = resource.get("size_bytes")
            if isinstance(uri, str):
                recorder.record(
                    "resource_handle",
                    uri=uri,
                    size_bytes=size_bytes,
                )
        for child in value.values():
            _record_resource_handles(recorder, child)
    elif isinstance(value, list):
        for child in value:
            _record_resource_handles(recorder, child)


def _safe_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {key: _safe_value(value) for key, value in payload.items()}


def _safe_value(value: Any) -> Any:
    if hasattr(value, "as_dict"):
        return _safe_value(value.as_dict())
    if isinstance(value, str):
        return _clip(value)
    if isinstance(value, Mapping):
        return {str(key): _safe_value(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_value(child) for child in value]
    return value


def _clip(value: str, limit: int = 700) -> str:
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[: limit - 20] + " ... [truncated]"
