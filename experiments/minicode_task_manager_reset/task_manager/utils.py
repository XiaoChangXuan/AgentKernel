from __future__ import annotations


def normalize_title(value: str) -> str:
    """Normalize user-facing task titles."""

    return " ".join(value.encode("ascii", "ignore").decode("ascii").split())


def parse_tags(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(part.strip().lower() for part in value.split(",") if part.strip())
