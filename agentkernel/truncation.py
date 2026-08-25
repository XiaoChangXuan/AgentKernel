"""Shared deterministic head/marker/tail projection helpers."""

from __future__ import annotations


def retain_head_tail(text: str, head_chars: int, tail_chars: int, marker: str) -> str:
    """Return a deterministic projection without mutating the source text."""

    if head_chars < 0 or tail_chars < 0:
        raise ValueError("head_chars and tail_chars must be non-negative")
    if head_chars + tail_chars >= len(text):
        return text
    tail = text[len(text) - tail_chars :] if tail_chars else ""
    return text[:head_chars] + marker + tail


def retain_utf8_head_tail(
    data: bytes,
    head_bytes: int,
    tail_bytes: int,
    marker: str,
) -> str:
    """Project byte-bounded UTF-8 edges, replacing only split/invalid code points."""

    if head_bytes < 0 or tail_bytes < 0:
        raise ValueError("head_bytes and tail_bytes must be non-negative")
    if head_bytes + tail_bytes >= len(data):
        return data.decode("utf-8", errors="replace")
    head = data[:head_bytes].decode("utf-8", errors="replace")
    tail = data[len(data) - tail_bytes :].decode("utf-8", errors="replace") if tail_bytes else ""
    return head + marker + tail
