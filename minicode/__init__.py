"""MiniCode application harness package.

MiniCode is intentionally separate from :mod:`agentkernel`. Phase 2A exposes
workspace/configuration primitives only; coding tools and model loops arrive in
later phases.
"""

from .config import ApprovalMode, MiniCodeConfig
from .errors import MiniCodeError
from .workspace import NormalizedPath, WorkspaceIdentity, discover_workspace

__all__ = [
    "ApprovalMode",
    "MiniCodeConfig",
    "MiniCodeError",
    "NormalizedPath",
    "WorkspaceIdentity",
    "discover_workspace",
]
