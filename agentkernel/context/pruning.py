"""Deterministic model-visible Tool Result pruning."""

from __future__ import annotations

from dataclasses import dataclass, replace

from ..truncation import retain_head_tail
from .model import ContextPage, ContextPageKind, ToolResultPruning
from .tokens import ApproximateTokenEstimator, TokenEstimator


@dataclass(frozen=True, slots=True)
class ToolResultPrunerConfig:
    """Token-estimate budgets for head/middle/tail pruning."""

    threshold_tokens: int = 2_048
    head_tokens: int = 1_024
    tail_tokens: int = 512

    def __post_init__(self) -> None:
        for name in ("threshold_tokens", "head_tokens", "tail_tokens"):
            value = getattr(self, name)
            minimum = 1 if name == "threshold_tokens" else 0
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise ValueError(f"{name} must be an integer >= {minimum}")
        if self.head_tokens + self.tail_tokens >= self.threshold_tokens:
            raise ValueError("head_tokens + tail_tokens must be below threshold_tokens")


class ToolResultPruner:
    """Retain a Tool Result's beginning and error-rich tail without changing Session."""

    STRATEGY = "head+omission-marker+tail/v1"

    def __init__(
        self,
        config: ToolResultPrunerConfig | None = None,
        *,
        estimator: TokenEstimator | None = None,
    ) -> None:
        self.config = config or ToolResultPrunerConfig()
        self.estimator = estimator or ApproximateTokenEstimator()

    def prune(self, page: ContextPage) -> ContextPage:
        """Return a smaller page or the exact original when pruning is unnecessary."""

        if page.kind is not ContextPageKind.TOOL_RESULT:
            return page
        if page.token_cost <= self.config.threshold_tokens:
            return page
        if page.message is None or page.message.tool_call_id is None:
            return page

        text = page.message.content
        head_chars = _characters_for_tokens(
            text, self.config.head_tokens, self.estimator
        )
        tail_chars = _characters_for_tokens(
            text, self.config.tail_tokens, self.estimator
        )
        if head_chars + tail_chars >= len(text):
            return page
        omitted_text = text[head_chars : len(text) - tail_chars]
        omitted_tokens = self.estimator.count_text(omitted_text)
        omitted_lines = omitted_text.count("\n")
        marker = (
            f"\n\n... omitted {omitted_tokens} tokens / "
            f"{omitted_lines} lines ...\n\n"
        )
        content = retain_head_tail(text, head_chars, tail_chars, marker)
        retained_cost = self.estimator.count_text(content)
        if retained_cost >= page.token_cost:
            return page

        message = replace(page.message, content=content)
        return replace(
            page,
            content=message.content,
            message=message,
            token_cost=retained_cost,
            pruning=ToolResultPruning(
                source_page_id=page.page_id,
                original_token_cost=page.token_cost,
                retained_token_cost=retained_cost,
                strategy=self.STRATEGY,
            ),
        )


def _characters_for_tokens(
    text: str, tokens: int, estimator: TokenEstimator
) -> int:
    if tokens <= 0:
        return 0
    low = 0
    high = len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if estimator.count_text(text[:middle]) <= tokens:
            low = middle
        else:
            high = middle - 1
    return low
