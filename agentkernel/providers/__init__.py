"""Optional LLM provider adapters outside the trusted Kernel mechanisms."""

from .openai_compatible import (
    OpenAICompatibleConfig,
    OpenAICompatibleConfigurationError,
    OpenAICompatibleError,
    OpenAICompatibleHTTPError,
    OpenAICompatibleLLM,
    OpenAICompatibleProtocolError,
    OpenAICompatibleTransportError,
)

__all__ = [
    "OpenAICompatibleConfig",
    "OpenAICompatibleConfigurationError",
    "OpenAICompatibleError",
    "OpenAICompatibleHTTPError",
    "OpenAICompatibleLLM",
    "OpenAICompatibleProtocolError",
    "OpenAICompatibleTransportError",
]
