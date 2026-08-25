from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from agentkernel import (
    Agent,
    DefaultAgentLoop,
    EventType,
    ModelRequest,
    PromptService,
    Session,
    ToolCall,
    ToolDefinition,
    ToolExecutionContext,
    ToolRegistry,
    ToolResult,
    ToolSchema,
)
from agentkernel.protocol import JsonValue, Message
from agentkernel.providers import (
    OpenAICompatibleConfig,
    OpenAICompatibleConfigurationError,
    OpenAICompatibleHTTPError,
    OpenAICompatibleLLM,
    OpenAICompatibleProtocolError,
)


class FakeChatServer(ThreadingHTTPServer):
    responses: list[tuple[int, object]]
    requests: list[dict[str, Any]]
    authorization_headers: list[str | None]


class FakeChatHandler(BaseHTTPRequestHandler):
    server: FakeChatServer

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        self.server.requests.append({"path": self.path, "body": body})
        self.server.authorization_headers.append(self.headers.get("Authorization"))
        status, response = self.server.responses.pop(0)
        encoded = (
            response
            if isinstance(response, bytes)
            else json.dumps(response).encode("utf-8")
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format: str, *_args: object) -> None:
        pass


@contextmanager
def fake_chat_api(*responses: object) -> Iterator[FakeChatServer]:
    server = FakeChatServer(("127.0.0.1", 0), FakeChatHandler)
    server.responses = [
        response if isinstance(response, tuple) else (200, response)
        for response in responses
    ]
    server.requests = []
    server.authorization_headers = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def tool_call_response(*calls: tuple[str, str, dict[str, JsonValue]]) -> dict[str, Any]:
    return {
        "id": "completion-1",
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(arguments),
                            },
                        }
                        for call_id, name, arguments in calls
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
    }


def final_response(content: str) -> dict[str, Any]:
    return {
        "id": "completion-2",
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
    }


async def add(
    arguments: Mapping[str, JsonValue],
    _context: ToolExecutionContext,
) -> JsonValue:
    return int(arguments["a"]) + int(arguments["b"])


def build_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            schema=ToolSchema(
                name="math.add",
                description="Add two numbers.",
                input_schema={
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
    return registry


def provider_for(
    server: FakeChatServer,
    api_key: str | None = "test-secret",
) -> OpenAICompatibleLLM:
    host, port = server.server_address
    return OpenAICompatibleLLM(
        OpenAICompatibleConfig(
            base_url=f"http://{host}:{port}/v1",
            model="test-model",
            api_key=api_key,
        )
    )


def test_real_http_round_trip_handles_multiple_tool_calls_and_results() -> None:
    with fake_chat_api(
        tool_call_response(
            ("call-1", "math.add", {"a": 20, "b": 22}),
            ("call-2", "math.add", {"a": 1, "b": 2}),
        ),
        final_response("The results are 42 and 3."),
    ) as server:
        session = Session("session-1")
        agent = Agent.create(
            agent_id="agent-1",
            session=session,
            capabilities={"math.add"},
        )
        loop = DefaultAgentLoop(
            llm=provider_for(server),
            tools=build_registry(),
            prompt=PromptService("Use math.add."),
        )

        answer = asyncio.run(loop.run(agent, "Calculate two sums."))

    assert answer == "The results are 42 and 3."
    assert [request["path"] for request in server.requests] == [
        "/v1/chat/completions",
        "/v1/chat/completions",
    ]
    assert server.authorization_headers == ["Bearer test-secret", "Bearer test-secret"]
    first_body = server.requests[0]["body"]
    assert first_body["model"] == "test-model"
    assert first_body["messages"] == [
        {"role": "system", "content": "Use math.add."},
        {"role": "user", "content": "Calculate two sums."},
    ]
    assert first_body["tool_choice"] == "auto"
    assert first_body["tools"][0]["function"].keys() == {
        "name",
        "description",
        "parameters",
    }
    second_messages = server.requests[1]["body"]["messages"]
    assert [message["role"] for message in second_messages] == [
        "system",
        "user",
        "assistant",
        "tool",
        "tool",
    ]
    assert [message["tool_call_id"] for message in second_messages[-2:]] == [
        "call-1",
        "call-2",
    ]
    assert [json.loads(message["content"])["output"] for message in second_messages[-2:]] == [
        42,
        3,
    ]
    assert sum(event.type is EventType.TOOL_CALL for event in session.events) == 2
    assert sum(event.type is EventType.TOOL_RESULT for event in session.events) == 2
    assert [event.type for event in session.events] == [
        EventType.TURN_START,
        EventType.USER_MESSAGE,
        EventType.STEP_START,
        EventType.ASSISTANT_MESSAGE,
        EventType.TOOL_CALL,
        EventType.TOOL_RESULT,
        EventType.TOOL_CALL,
        EventType.TOOL_RESULT,
        EventType.STEP_END,
        EventType.STEP_START,
        EventType.ASSISTANT_MESSAGE,
        EventType.STEP_END,
        EventType.TURN_END,
    ]


def test_unadvertised_real_http_tool_call_still_gets_eacces() -> None:
    with fake_chat_api(
        tool_call_response(("call-1", "math.add", {"a": 20, "b": 22})),
        final_response("The tool was denied."),
    ) as server:
        session = Session("session-1")
        agent = Agent.create(agent_id="agent-1", session=session)
        loop = DefaultAgentLoop(
            llm=provider_for(server, api_key=None),
            tools=build_registry(),
            prompt=PromptService(),
        )

        answer = asyncio.run(loop.run(agent, "Try the tool."))

    assert answer == "The tool was denied."
    assert "tools" not in server.requests[0]["body"]
    assert server.authorization_headers == [None, None]
    tool_message = server.requests[1]["body"]["messages"][-1]
    assert tool_message["role"] == "tool"
    assert tool_message["tool_call_id"] == "call-1"
    assert json.loads(tool_message["content"])["error"]["code"] == "EACCES"
    result_event = next(
        event for event in session.events if event.type is EventType.TOOL_RESULT
    )
    assert result_event.data["error"]["code"] == "EACCES"  # type: ignore[index]


def test_assistant_tool_call_and_tool_result_convert_losslessly() -> None:
    call = ToolCall("call-1", "math.add", {"a": 20, "b": 22})
    request = ModelRequest(
        messages=(
            Message.user("Calculate."),
            Message.assistant("", (call,)),
            Message.tool(ToolResult.success(call, 42)),
        )
    )
    with fake_chat_api(final_response("42")) as server:
        response = asyncio.run(provider_for(server).generate(request))

    assert response.content == "42"
    messages = server.requests[0]["body"]["messages"]
    assert messages[1]["content"] is None
    assert messages[1]["tool_calls"][0]["id"] == "call-1"
    assert json.loads(messages[1]["tool_calls"][0]["function"]["arguments"]) == {
        "a": 20,
        "b": 22,
    }
    assert messages[2]["tool_call_id"] == "call-1"


def test_invalid_tool_arguments_raise_provider_protocol_error() -> None:
    response = tool_call_response(("call-1", "math.add", {}))
    response["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = "{bad"
    with fake_chat_api(response) as server:
        with pytest.raises(OpenAICompatibleProtocolError, match="invalid JSON arguments"):
            asyncio.run(
                provider_for(server).generate(
                    ModelRequest(messages=(Message.user("Calculate."),))
                )
            )


def test_configuration_reports_required_environment_variables() -> None:
    with pytest.raises(OpenAICompatibleConfigurationError) as captured:
        OpenAICompatibleConfig.from_env({})

    message = str(captured.value)
    assert "AGENTKERNEL_LLM_BASE_URL" in message
    assert "AGENTKERNEL_LLM_MODEL" in message
    assert "AGENTKERNEL_LLM_API_KEY" not in message


def test_configuration_repr_does_not_expose_api_key() -> None:
    secret = "super-secret-value"
    config = OpenAICompatibleConfig(
        base_url="http://127.0.0.1:8000/v1",
        model="test-model",
        api_key=secret,
    )

    assert secret not in repr(config)


def test_http_error_redacts_api_key() -> None:
    secret = "super-secret-value"
    with fake_chat_api(
        (401, {"error": {"message": f"bad credential {secret}"}})
    ) as server:
        with pytest.raises(OpenAICompatibleHTTPError) as captured:
            asyncio.run(
                provider_for(server, api_key=secret).generate(
                    ModelRequest(messages=(Message.user("Hello"),))
                )
            )

    assert captured.value.status == 401
    assert secret not in str(captured.value)
    assert "***" in str(captured.value)
