"""Deterministic offline fixtures shared by benchmark runners."""

from __future__ import annotations

import math


MIB = 1024 * 1024
TOKEN_BYTES = 4


def mib(value: int) -> int:
    return value * MIB


def estimate_tokens_from_bytes(byte_count: int) -> int:
    return math.ceil(byte_count / TOKEN_BYTES)


def repeated_ascii_payload(size_bytes: int, fill: bytes = b"x") -> bytes:
    """Return a deterministic byte payload without network or randomness."""

    if size_bytes < 0:
        raise ValueError("size_bytes must be non-negative")
    if not fill:
        raise ValueError("fill must not be empty")
    repeats, remainder = divmod(size_bytes, len(fill))
    return fill * repeats + fill[:remainder]


def retained_by_head_tail(
    *,
    size_bytes: int,
    offset: int,
    head_bytes: int,
    tail_bytes: int,
) -> bool:
    if offset < head_bytes:
        return True
    return offset >= max(0, size_bytes - tail_bytes)
