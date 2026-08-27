from __future__ import annotations

import pytest

from agentkernel import Message, ModelUsage, ToolCall, ToolSchema
from minicode.model import (
    MiniCodeModelRequest,
    ModelAdapterError,
    OpenAICompatibleAdapter,
    OpenAICompatibleConfig,
    ScriptedModelAdapter,
    redact_secret,
    scripted_response,
)


def _request() -> MiniCodeModelRequest:
    return MiniCodeModelRequest(
        messages=(Message.user("fix it"),),
        tools=(ToolSchema("read_file", "Read a file.", {"type": "object"}),),
        system_prompt="system",
    )


def test_scripted_adapter_returns_queued_tool_call_and_records_request():
    call = ToolCall("call-1", "read_file", {"path": "calculator.py"})
    adapter = ScriptedModelAdapter(
        [
            scripted_response(
                text="checking",
                tool_calls=(call,),
                usage=ModelUsage(input_tokens=3, output_tokens=2, total_tokens=5),
            )
        ]
    )

    response = adapter.complete(_request())

    assert response.tool_calls == (call,)
    assert response.finish_reason == "tool_calls"
    assert response.usage is not None
    assert response.usage.total_tokens == 5
    assert len(adapter.requests) == 1


def test_scripted_adapter_exhaustion_is_model_error():
    adapter = ScriptedModelAdapter()

    with pytest.raises(ModelAdapterError) as error:
        adapter.complete(_request())

    assert error.value.code == "script_exhausted"


def test_openai_compatible_adapter_requires_opt_in_without_client():
    adapter = OpenAICompatibleAdapter(
        OpenAICompatibleConfig(base_url="https://example.invalid", model="demo")
    )

    with pytest.raises(ModelAdapterError) as error:
        adapter.complete(_request())

    assert error.value.code == "provider_not_configured"


def test_openai_compatible_adapter_parses_tool_calls_and_usage():
    def client(payload):
        assert payload["model"] == "demo"
        assert payload["messages"][0]["role"] == "system"
        return {
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {
                                    "name": "read_file",
                                    "arguments": '{"path":"calculator.py"}',
                                },
                            }
                        ],
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 7,
                "completion_tokens": 4,
                "total_tokens": 11,
            },
        }

    adapter = OpenAICompatibleAdapter(
        OpenAICompatibleConfig(base_url="", model="demo"),
        client=client,
    )

    response = adapter.complete(_request())

    assert response.tool_calls == (ToolCall("call-1", "read_file", {"path": "calculator.py"}),)
    assert response.usage is not None
    assert response.usage.total_tokens == 11
    assert response.provider_latency_ms is not None


def test_redact_secret():
    assert redact_secret("sk-test") == "<redacted>"
    assert redact_secret("") == ""
    assert redact_secret(None) is None
