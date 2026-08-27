from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeVar

from .errors import MiniCodeError


ApprovalMode = Literal["never", "on-mutation", "always"]
MINICODE_CONFIG_DIR = ".minicode"
MINICODE_CONFIG_FILE = "config.json"
MINICODE_ENV_FILE = ".env"
ENV_FILE_KEYS = {
    "AGENTKERNEL_LLM_API_KEY",
    "AGENTKERNEL_LLM_BASE_URL",
    "AGENTKERNEL_LLM_MODEL",
    "MINICODE_ALLOW_NETWORK",
    "MINICODE_APPROVE",
    "MINICODE_LLM_API_KEY",
    "MINICODE_LLM_BASE_URL",
    "MINICODE_LLM_MODEL",
    "MINICODE_MAX_TURNS",
    "MINICODE_MODEL",
    "MINICODE_TIMEOUT_MS",
}
SECRET_CONFIG_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "token",
    "secret",
    "minicode_llm_api_key",
}
ALLOWED_SECRET_CONFIG_PATHS = {"openai_compatible.api_key"}
T = TypeVar("T")


@dataclass(frozen=True)
class MiniCodeProjectConfig:
    """Project-local MiniCode configuration."""

    path: Path | None = None
    model: str | None = None
    base_url: str | None = None
    model_name: str | None = None
    api_key: str | None = None
    max_turns: int | None = None
    timeout_ms: int | None = None
    approve: ApprovalMode | None = None
    allow_network: bool | None = None


@dataclass(frozen=True)
class MiniCodeConfig:
    """Small Phase 2A configuration object for future CLI/runtime wiring."""

    workspace: Path | None = None
    task_cwd: Path | None = None
    approve: ApprovalMode = "on-mutation"
    trace_jsonl: Path | None = None
    model: str = "scripted"
    max_turns: int = 20
    timeout_ms: int = 30_000
    no_network: bool = True

    def validate(self) -> None:
        if self.approve not in {"never", "on-mutation", "always"}:
            raise MiniCodeError(
                code="invalid_configuration",
                message=f"Unsupported approval mode: {self.approve}",
                retryable=False,
            )
        if self.max_turns <= 0:
            raise MiniCodeError(
                code="invalid_configuration",
                message="max_turns must be greater than zero",
                retryable=False,
            )
        if self.timeout_ms <= 0:
            raise MiniCodeError(
                code="invalid_configuration",
                message="timeout_ms must be greater than zero",
                retryable=False,
            )


def load_project_config(
    workspace_root: Path,
    *,
    explicit_config: Path | None = None,
) -> MiniCodeProjectConfig:
    """Load ``.minicode/config.json`` from a workspace.

    The project file is allowed to contain runtime defaults and, for local
    convenience, an optional OpenAI-compatible API key. Real keys should stay
    out of committed files.
    """

    path = _resolve_project_config_path(workspace_root, explicit_config)
    if path is None:
        return MiniCodeProjectConfig()

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise MiniCodeError(
            code="config_not_found",
            message=f"could not read MiniCode config: {path}",
            retryable=False,
        ) from exc
    except json.JSONDecodeError as exc:
        raise MiniCodeError(
            code="invalid_project_config",
            message=f"MiniCode config is not valid JSON: {exc}",
            retryable=False,
        ) from exc
    if not isinstance(raw, dict):
        raise MiniCodeError(
            code="invalid_project_config",
            message="MiniCode config must be a JSON object",
            retryable=False,
        )
    _reject_secret_config(raw)
    defaults = _optional_object(raw, "defaults")
    provider = _optional_object(raw, "openai_compatible")

    return MiniCodeProjectConfig(
        path=path,
        model=_optional_str(raw, "model"),
        base_url=_optional_str(provider, "base_url"),
        model_name=_optional_str(provider, "model") or _optional_str(provider, "model_name"),
        api_key=_optional_secret_str(provider, "api_key"),
        max_turns=_first_present(_optional_int(raw, "max_turns"), _optional_int(defaults, "max_turns")),
        timeout_ms=_first_present(_optional_int(raw, "timeout_ms"), _optional_int(defaults, "timeout_ms")),
        approve=_first_present(_optional_approval(raw, "approve"), _optional_approval(defaults, "approve")),
        allow_network=_first_present(_optional_bool(raw, "allow_network"), _optional_bool(defaults, "allow_network")),
    )


def load_environment_files(workspace_root: Path) -> dict[str, str]:
    """Read non-committed MiniCode env files as local configuration defaults.

    A local ``.env`` can hold provider variables without overriding the process
    environment. Values returned from env files never mutate ``os.environ``;
    real keys should stay out of committed files.
    """

    root = workspace_root.expanduser().resolve(strict=True)
    values: dict[str, str] = {}
    for candidate in (root / MINICODE_ENV_FILE, root / MINICODE_CONFIG_DIR / MINICODE_ENV_FILE):
        if not candidate.exists():
            continue
        for key, value in _load_environment_file(candidate).items():
            values.setdefault(key, value)
    return values


def _load_environment_file(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise MiniCodeError(
            code="env_not_found",
            message=f"could not read MiniCode env file: {path}",
            retryable=False,
        ) from exc
    values: dict[str, str] = {}
    for raw_line in lines:
        parsed = _parse_env_line(raw_line)
        if parsed is None:
            continue
        key, value = parsed
        if key not in ENV_FILE_KEYS:
            continue
        values[key] = value
    return values


def _parse_env_line(raw_line: str) -> tuple[str, str] | None:
    line = raw_line.strip()
    if not line or line.startswith("#"):
        return None
    if line.startswith("export "):
        line = line[len("export ") :].lstrip()
    if "=" not in line:
        raise MiniCodeError(
            code="invalid_env_file",
            message="MiniCode env lines must use KEY=VALUE syntax",
            retryable=False,
        )
    key, value = line.split("=", 1)
    key = key.strip()
    if not key or any(char.isspace() for char in key):
        raise MiniCodeError(
            code="invalid_env_file",
            message=f"MiniCode env key is invalid: {key!r}",
            retryable=False,
        )
    return key, _unquote_env_value(value.strip())


def _unquote_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _resolve_project_config_path(workspace_root: Path, explicit_config: Path | None) -> Path | None:
    root = workspace_root.expanduser().resolve(strict=True)
    if explicit_config is None:
        candidate = root / MINICODE_CONFIG_DIR / MINICODE_CONFIG_FILE
        return candidate if candidate.exists() else None

    candidate = explicit_config.expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve(strict=False)
    if not _contains(root, resolved):
        raise MiniCodeError(
            code="outside_workspace",
            message=f"MiniCode config path escapes workspace: {explicit_config}",
            retryable=False,
        )
    if not resolved.exists():
        raise MiniCodeError(
            code="config_not_found",
            message=f"MiniCode config does not exist: {explicit_config}",
            retryable=False,
        )
    return resolved


def _optional_object(raw: dict[str, object], key: str) -> dict[str, object]:
    value = raw.get(key)
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    raise MiniCodeError(
        code="invalid_project_config",
        message=f"MiniCode config field {key!r} must be an object",
        retryable=False,
    )


def _optional_str(raw: dict[str, object], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise MiniCodeError(
        code="invalid_project_config",
        message=f"MiniCode config field {key!r} must be a non-empty string",
        retryable=False,
    )


def _optional_secret_str(raw: dict[str, object], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    raise MiniCodeError(
        code="invalid_project_config",
        message=f"MiniCode config field {key!r} must be a string",
        retryable=False,
    )


def _optional_int(raw: dict[str, object], key: str) -> int | None:
    value = raw.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise MiniCodeError(
            code="invalid_project_config",
            message=f"MiniCode config field {key!r} must be a positive integer",
            retryable=False,
        )
    return value


def _optional_bool(raw: dict[str, object], key: str) -> bool | None:
    value = raw.get(key)
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    raise MiniCodeError(
        code="invalid_project_config",
        message=f"MiniCode config field {key!r} must be a boolean",
        retryable=False,
    )


def _optional_approval(raw: dict[str, object], key: str) -> ApprovalMode | None:
    value = _optional_str(raw, key)
    if value is None:
        return None
    if value in {"never", "on-mutation", "always"}:
        return value
    raise MiniCodeError(
        code="invalid_project_config",
        message=f"MiniCode config field {key!r} has unsupported approval mode: {value}",
        retryable=False,
    )


def _reject_secret_config(value: object, *, path: str = "") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if _looks_secret_key(str(key)) and child_path not in ALLOWED_SECRET_CONFIG_PATHS:
                raise MiniCodeError(
                    code="config_contains_secret",
                    message=f"MiniCode config must not contain secrets: {child_path}",
                    retryable=False,
                )
            _reject_secret_config(child, path=child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_secret_config(child, path=f"{path}[{index}]")


def _looks_secret_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return normalized in SECRET_CONFIG_KEYS or normalized.endswith("_api_key")


def _first_present(first: T | None, second: T | None) -> T | None:
    return first if first is not None else second


def _contains(root: Path, candidate: Path) -> bool:
    try:
        common = os.path.commonpath([os.path.normcase(str(root)), os.path.normcase(str(candidate))])
    except ValueError:
        return False
    return common == os.path.normcase(str(root))
