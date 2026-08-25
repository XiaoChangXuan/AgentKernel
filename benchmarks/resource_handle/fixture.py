"""Fixtures for Resource Handle runtime benchmarks."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ResourceCase:
    name: str
    size_bytes: int
    required_offsets: tuple[int, ...]


def make_resource_case(size_bytes: int) -> ResourceCase:
    """Create a large-output case with facts at head, middle, and tail."""

    if size_bytes < 3:
        raise ValueError("resource benchmark case must be at least 3 bytes")
    return ResourceCase(
        name=f"{size_bytes // (1024 * 1024)}MB_tool_result",
        size_bytes=size_bytes,
        required_offsets=(0, size_bytes // 2, size_bytes - 1),
    )
