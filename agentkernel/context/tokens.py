"""Replaceable token estimation for Context Pages."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@runtime_checkable
class TokenEstimator(Protocol):
    """Replaceable text token-cost estimator."""

    def count_text(self, text: str) -> int: ...


@dataclass(frozen=True, slots=True)
class ApproximateTokenEstimator:
    """Offline deterministic Unicode-code-point approximation."""

    characters_per_token: int = 4

    def __post_init__(self) -> None:
        if (
            isinstance(self.characters_per_token, bool)
            or not isinstance(self.characters_per_token, int)
            or self.characters_per_token < 1
        ):
            raise ValueError("characters_per_token must be a positive integer")

    def count_text(self, text: str) -> int:
        if not isinstance(text, str):
            raise TypeError("token estimator input must be text")
        if not text:
            return 0
        return math.ceil(len(text) / self.characters_per_token)
