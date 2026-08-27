"""MiniCode application harness package.

MiniCode is intentionally separate from :mod:`agentkernel`: it owns the coding
agent harness while AgentKernel owns durable runtime mechanisms.
"""

from .config import ApprovalMode, MiniCodeConfig
from .errors import MiniCodeError
from .loop import MiniCodeAgentLoop, MiniCodeRunResult, MiniCodeRunStatus
from .model import (
    MiniCodeModelRequest,
    MiniCodeModelResponse,
    ModelAdapter,
    ModelAdapterError,
    OpenAICompatibleAdapter,
    OpenAICompatibleConfig,
    ScriptedModelAdapter,
    redact_secret,
    scripted_response,
)
from .trace import TraceEvent, TraceRecorder, render_session_trace
from .workspace import NormalizedPath, WorkspaceIdentity, discover_workspace

__all__ = [
    "ApprovalMode",
    "MiniCodeAgentLoop",
    "MiniCodeConfig",
    "MiniCodeError",
    "MiniCodeModelRequest",
    "MiniCodeModelResponse",
    "MiniCodeRunResult",
    "MiniCodeRunStatus",
    "ModelAdapter",
    "ModelAdapterError",
    "NormalizedPath",
    "OpenAICompatibleAdapter",
    "OpenAICompatibleConfig",
    "ScriptedModelAdapter",
    "TraceEvent",
    "TraceRecorder",
    "WorkspaceIdentity",
    "discover_workspace",
    "redact_secret",
    "render_session_trace",
    "scripted_response",
]
