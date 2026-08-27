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
SECRET_CONFIG_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "token",
    "secret",
    "minicode_llm_api_key",
}
T = TypeVar("T")


@dataclass(frozen=True)
class MiniCodeProjectConfig:
    """Non-secret project-local MiniCode configuration."""

    path: Path | None = None
    model: str | None = None
    base_url: str | None = None
    model_name: str | None = None
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

    The project file is allowed to contain non-secret runtime defaults only.
    Provider secrets remain environment-only so they do not enter repository
    files, traces, or session artifacts.
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
        max_turns=_first_present(_optional_int(raw, "max_turns"), _optional_int(defaults, "max_turns")),
        timeout_ms=_first_present(_optional_int(raw, "timeout_ms"), _optional_int(defaults, "timeout_ms")),
        approve=_first_present(_optional_approval(raw, "approve"), _optional_approval(defaults, "approve")),
        allow_network=_first_present(_optional_bool(raw, "allow_network"), _optional_bool(defaults, "allow_network")),
    )


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
            if _looks_secret_key(str(key)):
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
