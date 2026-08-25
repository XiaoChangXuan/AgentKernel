"""Minimal prompt and tool-schema assembly service."""

from __future__ import annotations

from dataclasses import dataclass

from .agent import AgentControlBlock
from .protocol import ToolSchema
from .tools import ToolRegistry


@dataclass(frozen=True, slots=True)
class PromptAssembly:
    """Model-visible prompt inputs assembled for one step."""

    system_prompt: str | None
    tools: tuple[ToolSchema, ...]


class PromptService:
    """Assemble current prompt inputs without owning conversation history."""

    def __init__(self, system_prompt: str | None = None) -> None:
        self._system_prompt = system_prompt

    def assemble(
        self,
        agent: AgentControlBlock,
        tools: ToolRegistry,
    ) -> PromptAssembly:
        """Build a fresh model projection for one step."""

        return PromptAssembly(
            system_prompt=self._system_prompt,
            tools=tools.model_schemas(agent),
        )

