"""Context VM values, budgets, metrics, and protocol validation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ..protocol import Message, MessageRole


class ContextPageKind(StrEnum):
    """Model-facing page kinds supported by the Context VM vocabulary."""

    SYSTEM = "system"
    USER_MESSAGE = "user_message"
    ASSISTANT_MESSAGE = "assistant_message"
    TOOL_RESULT = "tool_result"
    SUMMARY = "summary"


class ContextTemperature(StrEnum):
    """Policy-assigned likelihood that a page belongs in physical context."""

    PINNED = "pinned"
    HOT = "hot"
    WARM = "warm"
    COLD = "cold"


class ContextTrustLabel(StrEnum):
    """Data-origin label reserved for later context security policy."""

    KERNEL = "kernel"
    USER = "user"
    TOOL = "tool"
    EXTERNAL = "external"


class ContextBudgetExceeded(RuntimeError):
    """Mandatory Context Pages cannot fit in the physical input budget."""

    def __init__(self, *, required_tokens: int, available_tokens: int) -> None:
        self.required_tokens = required_tokens
        self.available_tokens = available_tokens
        super().__init__(
            f"mandatory context requires {required_tokens} tokens, "
            f"but only {available_tokens} input tokens are available"
        )


class ContextPageNotFound(LookupError):
    """A requested or manually pinned page is absent from the projection."""


class ContextProtocolError(RuntimeError):
    """Selected pages cannot form a legal provider-neutral message history."""


@dataclass(frozen=True, slots=True)
class ContextBudget:
    """One model call's physical context limit and output reservation."""

    max_tokens: int
    reserved_output_tokens: int = 0

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_tokens, bool)
            or not isinstance(self.max_tokens, int)
            or self.max_tokens < 1
        ):
            raise ValueError("max_tokens must be a positive integer")
        if (
            isinstance(self.reserved_output_tokens, bool)
            or not isinstance(self.reserved_output_tokens, int)
            or self.reserved_output_tokens < 0
        ):
            raise ValueError("reserved_output_tokens must be a non-negative integer")
        if self.reserved_output_tokens > self.max_tokens:
            raise ValueError("reserved_output_tokens cannot exceed max_tokens")

    @property
    def available_input_tokens(self) -> int:
        return self.max_tokens - self.reserved_output_tokens


@dataclass(frozen=True, slots=True)
class ContextPage:
    """Immutable projection of model-relevant durable or host prompt state."""

    page_id: str
    kind: ContextPageKind
    content: str
    token_cost: int
    priority: int
    temperature: ContextTemperature
    pinned: bool
    trust_label: ContextTrustLabel
    created_seq: int
    turn: int | None
    dependencies: tuple[str, ...] = ()
    atomic_group: str | None = None
    message: Message | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.page_id, str) or not self.page_id:
            raise ValueError("context page_id must be a non-empty string")
        object.__setattr__(self, "kind", ContextPageKind(self.kind))
        object.__setattr__(self, "temperature", ContextTemperature(self.temperature))
        object.__setattr__(self, "trust_label", ContextTrustLabel(self.trust_label))
        if not isinstance(self.content, str):
            raise TypeError("context page content must be text")
        if (
            isinstance(self.token_cost, bool)
            or not isinstance(self.token_cost, int)
            or self.token_cost < 0
        ):
            raise ValueError("context page token_cost must be non-negative")
        if isinstance(self.priority, bool) or not isinstance(self.priority, int):
            raise TypeError("context page priority must be an integer")
        if not isinstance(self.pinned, bool):
            raise TypeError("context page pinned must be a boolean")
        if self.pinned is not (self.temperature is ContextTemperature.PINNED):
            raise ValueError("pinned and PINNED temperature must agree")
        if (
            isinstance(self.created_seq, bool)
            or not isinstance(self.created_seq, int)
            or self.created_seq < 0
        ):
            raise ValueError("context page created_seq must be non-negative")
        if self.turn is not None and (
            isinstance(self.turn, bool)
            or not isinstance(self.turn, int)
            or self.turn < 1
        ):
            raise ValueError("context page turn must be positive when present")
        dependencies = tuple(self.dependencies)
        if any(not isinstance(item, str) or not item for item in dependencies):
            raise ValueError("context page dependencies must be non-empty strings")
        if len(set(dependencies)) != len(dependencies):
            raise ValueError("context page dependencies must be unique")
        if self.page_id in dependencies:
            raise ValueError("context page cannot depend on itself")
        object.__setattr__(self, "dependencies", dependencies)
        if self.atomic_group is not None and (
            not isinstance(self.atomic_group, str) or not self.atomic_group
        ):
            raise ValueError("atomic_group must be a non-empty string when present")
        if self.kind is ContextPageKind.SYSTEM:
            if self.message is not None:
                raise ValueError("SYSTEM page must use content, not a Message")
        elif self.kind is not ContextPageKind.SUMMARY and self.message is None:
            raise ValueError("message page kinds require a Message")
        elif self.message is not None:
            expected_roles = {
                ContextPageKind.USER_MESSAGE: MessageRole.USER,
                ContextPageKind.ASSISTANT_MESSAGE: MessageRole.ASSISTANT,
                ContextPageKind.TOOL_RESULT: MessageRole.TOOL,
            }
            expected_role = expected_roles.get(self.kind)
            if expected_role is not None and self.message.role is not expected_role:
                raise ValueError("context page kind does not match Message role")
            if self.content != self.message.content:
                raise ValueError("context page content must match Message content")


@dataclass(frozen=True, slots=True)
class ContextMetrics:
    projected_pages: int
    selected_pages: int
    evicted_pages: int
    pinned_pages: int
    projected_tokens: int
    selected_tokens: int
    evicted_tokens: int
    budget_tokens: int

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"context metric {name} must be non-negative")
        if self.projected_pages != self.selected_pages + self.evicted_pages:
            raise ValueError("projected page metric must equal selected plus evicted")
        if self.projected_tokens != self.selected_tokens + self.evicted_tokens:
            raise ValueError("projected token metric must equal selected plus evicted")


@dataclass(frozen=True, slots=True)
class ContextWorkingSet:
    """One budgeted, causally ordered physical model context."""

    pages: tuple[ContextPage, ...]
    evicted_pages: tuple[ContextPage, ...]
    budget: ContextBudget
    metrics: ContextMetrics

    def __post_init__(self) -> None:
        object.__setattr__(self, "pages", tuple(self.pages))
        object.__setattr__(self, "evicted_pages", tuple(self.evicted_pages))
        selected_ids = [page.page_id for page in self.pages]
        evicted_ids = [page.page_id for page in self.evicted_pages]
        if len(set(selected_ids)) != len(selected_ids):
            raise ValueError("working set selected page identities must be unique")
        if len(set(evicted_ids)) != len(evicted_ids):
            raise ValueError("working set evicted page identities must be unique")
        if set(selected_ids) & set(evicted_ids):
            raise ValueError("selected and evicted pages must be disjoint")
        if [page.created_seq for page in self.pages] != sorted(
            page.created_seq for page in self.pages
        ):
            raise ValueError("working set selected pages must be in causal order")
        if [page.created_seq for page in self.evicted_pages] != sorted(
            page.created_seq for page in self.evicted_pages
        ):
            raise ValueError("working set evicted pages must be in causal order")
        if sum(page.kind is ContextPageKind.SYSTEM for page in self.pages) > 1:
            raise ValueError("working set cannot contain multiple SYSTEM pages")
        selected_tokens = sum(page.token_cost for page in self.pages)
        if selected_tokens > self.budget.available_input_tokens:
            raise ContextBudgetExceeded(
                required_tokens=selected_tokens,
                available_tokens=self.budget.available_input_tokens,
            )
        if (
            self.metrics.selected_pages != len(self.pages)
            or self.metrics.evicted_pages != len(self.evicted_pages)
            or self.metrics.selected_tokens != selected_tokens
            or self.metrics.evicted_tokens
            != sum(page.token_cost for page in self.evicted_pages)
            or self.metrics.pinned_pages
            != sum(page.pinned for page in (*self.pages, *self.evicted_pages))
            or self.metrics.budget_tokens != self.budget.available_input_tokens
        ):
            raise ValueError("working set metrics do not match its Pages and budget")

    @property
    def system_prompt(self) -> str | None:
        systems = [
            page.content
            for page in self.pages
            if page.kind is ContextPageKind.SYSTEM
        ]
        if len(systems) > 1:
            raise ContextProtocolError("working set contains multiple SYSTEM pages")
        return systems[0] if systems else None

    def to_messages(self) -> tuple[Message, ...]:
        messages = tuple(
            page.message for page in self.pages if page.message is not None
        )
        _validate_tool_protocol(messages)
        return messages


def _validate_tool_protocol(messages: tuple[Message, ...]) -> None:
    outstanding: set[str] = set()
    seen_calls: set[str] = set()
    for message in messages:
        if message.tool_calls:
            if outstanding:
                raise ContextProtocolError(
                    "assistant tool calls cannot appear before prior results"
                )
            for call in message.tool_calls:
                if call.call_id in seen_calls:
                    raise ContextProtocolError(
                        f"duplicate selected tool call: {call.call_id}"
                    )
                seen_calls.add(call.call_id)
                outstanding.add(call.call_id)
        if message.tool_call_id is not None:
            if message.tool_call_id not in outstanding:
                raise ContextProtocolError(
                    f"orphan selected tool result: {message.tool_call_id}"
                )
            outstanding.remove(message.tool_call_id)
    if outstanding:
        raise ContextProtocolError(
            f"selected assistant tool calls lack results: {sorted(outstanding)}"
        )
