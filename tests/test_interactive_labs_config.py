from __future__ import annotations

import json

import pytest

from agentkernel import ModelResponse, ToolCall
from labs import create_lab
from labs.kernel_labs import LabOpenAICompatibleLLM, _load_lab_llm_config
from minicode.tools import APPLY_PATCH_NAME


_LAB_MODEL_ENV = (
    "AGENTKERNEL_LAB_LLM_BASE_URL",
    "AGENTKERNEL_LAB_LLM_MODEL",
    "AGENTKERNEL_LAB_LLM_API_KEY",
    "AGENTKERNEL_LAB_LLM_TIMEOUT_MS",
    "MINICODE_LLM_BASE_URL",
    "MINICODE_LLM_MODEL",
    "MINICODE_LLM_API_KEY",
    "AGENTKERNEL_LLM_BASE_URL",
    "AGENTKERNEL_LLM_MODEL",
    "AGENTKERNEL_LLM_API_KEY",
    "MINICODE_ALLOW_NETWORK",
    "MINICODE_TIMEOUT_MS",
)


def _clear_model_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _LAB_MODEL_ENV:
        monkeypatch.delenv(name, raising=False)


def _write_project_config(root, payload: dict[str, object]) -> None:
    config_dir = root / ".minicode"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


def test_real_model_lab_reads_minicode_project_config(tmp_path, monkeypatch):
    _clear_model_env(monkeypatch)
    (tmp_path / "agentkernel").mkdir()
    (tmp_path / "labs").mkdir()
    _write_project_config(
        tmp_path,
        {
            "model": "openai-compatible",
            "allow_network": True,
            "openai_compatible": {
                "base_url": "https://project-provider.example/v1",
                "model": "project-model",
                "api_key": "<test-project-token>",
            },
            "defaults": {"timeout_ms": 12_000},
        },
    )
    monkeypatch.chdir(tmp_path)

    config = _load_lab_llm_config()
    llm = LabOpenAICompatibleLLM()

    assert config.base_url == "https://project-provider.example/v1"
    assert config.model == "project-model"
    assert config.api_key == "<test-project-token>"
    assert config.timeout_seconds == 12.0
    assert config.allow_network is True
    assert config.source.endswith(".minicode\\config.json") or config.source.endswith(
        ".minicode/config.json"
    )
    assert llm.metadata["api_key_configured"] is True
    assert "<test-project-token>" not in json.dumps(llm.metadata)


def test_real_model_lab_requires_network_opt_in_for_project_config(tmp_path, monkeypatch):
    _clear_model_env(monkeypatch)
    (tmp_path / "agentkernel").mkdir()
    (tmp_path / "labs").mkdir()
    _write_project_config(
        tmp_path,
        {
            "model": "openai-compatible",
            "allow_network": False,
            "openai_compatible": {
                "base_url": "https://project-provider.example/v1",
                "model": "project-model",
                "api_key": "<test-project-token>",
            },
        },
    )
    monkeypatch.chdir(tmp_path)

    with pytest.raises(RuntimeError, match="network opt-in"):
        _load_lab_llm_config()


def test_real_model_lab_env_overrides_project_config(tmp_path, monkeypatch):
    _clear_model_env(monkeypatch)
    (tmp_path / "agentkernel").mkdir()
    (tmp_path / "labs").mkdir()
    _write_project_config(
        tmp_path,
        {
            "model": "openai-compatible",
            "allow_network": True,
            "openai_compatible": {
                "base_url": "https://project-provider.example/v1",
                "model": "project-model",
                "api_key": "<test-project-token>",
            },
        },
    )
    monkeypatch.setenv("AGENTKERNEL_LAB_LLM_BASE_URL", "https://lab-provider.example/v1")
    monkeypatch.setenv("AGENTKERNEL_LAB_LLM_MODEL", "lab-model")
    monkeypatch.setenv("AGENTKERNEL_LAB_LLM_API_KEY", "<test-lab-token>")
    monkeypatch.setenv("AGENTKERNEL_LAB_LLM_TIMEOUT_MS", "9000")
    monkeypatch.chdir(tmp_path)

    config = _load_lab_llm_config()

    assert config.base_url == "https://lab-provider.example/v1"
    assert config.model == "lab-model"
    assert config.api_key == "<test-lab-token>"
    assert config.timeout_seconds == 9.0
    assert config.allow_network is True
    assert config.source == "env:AGENTKERNEL_LAB_LLM_*"


def test_partial_lab_env_falls_back_to_project_config(tmp_path, monkeypatch):
    _clear_model_env(monkeypatch)
    (tmp_path / "agentkernel").mkdir()
    (tmp_path / "labs").mkdir()
    _write_project_config(
        tmp_path,
        {
            "model": "openai-compatible",
            "allow_network": True,
            "openai_compatible": {
                "base_url": "https://project-provider.example/v1",
                "model": "project-model",
                "api_key": "<test-project-token>",
            },
        },
    )
    monkeypatch.setenv("AGENTKERNEL_LAB_LLM_API_KEY", "<partial-lab-token>")
    monkeypatch.chdir(tmp_path)

    config = _load_lab_llm_config()

    assert config.base_url == "https://project-provider.example/v1"
    assert config.model == "project-model"
    assert config.api_key == "<test-project-token>"
    assert config.source.endswith(".minicode\\config.json") or config.source.endswith(
        ".minicode/config.json"
    )


def test_v03_real_model_invalid_patch_is_observable(monkeypatch):
    class FakeLabLLM:
        @property
        def metadata(self):
            return {
                "provider": "openai-compatible",
                "model": "fake",
                "config_source": "test",
            }

        async def generate(self, request):
            return ModelResponse(
                tool_calls=(
                    ToolCall(
                        "call-bad-patch",
                        APPLY_PATCH_NAME,
                        {"patch": "not a codex patch"},
                    ),
                )
            )

    monkeypatch.setattr("labs.kernel_labs.LabOpenAICompatibleLLM", FakeLabLLM)
    lab = create_lab("v03", mode="real_model")
    try:
        lab.setup()
        payload = lab.model_step()
    finally:
        lab.close()

    assert payload["accepted_apply_patch_tool_call"] is False
    assert payload["patch_parse_error"]["code"] == "invalid_patch"


@pytest.mark.parametrize(
    ("lab_id", "methods"),
    (
        (
            "v04",
            (
                "setup",
                "show_session_truth",
                "build_working_set",
                "show_model_request",
                "model_step",
                "summary",
            ),
        ),
        (
            "v05",
            (
                "setup",
                "show_large_output",
                "externalize_output",
                "show_model_request",
                "model_step",
                "authorized_read",
                "unauthorized_read",
                "summary",
            ),
        ),
        (
            "v06",
            (
                "setup",
                "show_model_request",
                "model_step",
                "forced_unauthorized_execution",
                "authorized_comparison",
                "summary",
            ),
        ),
        (
            "v07",
            (
                "setup",
                "dispatch",
                "show_model_request",
                "model_step",
                "safe_point_budget_check",
                "recover_after_budget_pause",
                "summary",
            ),
        ),
        (
            "v08",
            (
                "setup",
                "show_model_request",
                "model_step",
                "child_before_delegation",
                "delegate_and_execute",
                "ipc_resource_reference",
                "share_and_read",
                "summary",
            ),
        ),
    ),
)
def test_v04_to_v08_interactive_labs_run_deterministically(lab_id, methods):
    lab = create_lab(lab_id, mode="deterministic")
    try:
        results = [getattr(lab, method)() for method in methods]
    finally:
        lab.close()

    assert results[-1]["claim"]


@pytest.mark.parametrize(
    ("lab_id", "methods"),
    (
        (
            "v04",
            (
                "setup",
                "show_session_truth",
                "build_working_set",
                "show_model_request",
                "model_step",
                "summary",
            ),
        ),
        (
            "v05",
            (
                "setup",
                "show_large_output",
                "externalize_output",
                "show_model_request",
                "model_step",
                "authorized_read",
                "unauthorized_read",
                "summary",
            ),
        ),
        (
            "v06",
            (
                "setup",
                "show_model_request",
                "model_step",
                "forced_unauthorized_execution",
                "authorized_comparison",
                "summary",
            ),
        ),
        (
            "v07",
            (
                "setup",
                "dispatch",
                "show_model_request",
                "model_step",
                "safe_point_budget_check",
                "recover_after_budget_pause",
                "summary",
            ),
        ),
        (
            "v08",
            (
                "setup",
                "show_model_request",
                "model_step",
                "child_before_delegation",
                "delegate_and_execute",
                "ipc_resource_reference",
                "share_and_read",
                "summary",
            ),
        ),
    ),
)
def test_v04_to_v08_real_model_path_is_observable_without_network(
    lab_id,
    methods,
    monkeypatch,
):
    class FakeLabLLM:
        @property
        def metadata(self):
            return {
                "provider": "openai-compatible",
                "model": "fake-real-model",
                "config_source": "test",
                "api_key_configured": True,
            }

        async def generate(self, request):
            return ModelResponse(
                content=(
                    "This fake provider response proves the real_model path records "
                    "provider metadata without depending on external network in tests."
                )
            )

    monkeypatch.setattr("labs.kernel_labs.LabOpenAICompatibleLLM", FakeLabLLM)
    lab = create_lab(lab_id, mode="real_model")
    try:
        results = [getattr(lab, method)() for method in methods]
    finally:
        lab.close()

    model_step_payloads = [
        payload for payload in results if payload.get("provider_metadata", {}).get("model")
    ]
    assert model_step_payloads
    assert model_step_payloads[0]["provider_metadata"]["model"] == "fake-real-model"
