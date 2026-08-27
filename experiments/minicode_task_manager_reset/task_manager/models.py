from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .utils import normalize_title


@dataclass(slots=True)
class Task:
    id: str
    title: str
    description: str = ""
    due_date: str | None = None
    priority: int = 3
    completed: bool = False
    tags: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        self.id = self.id.strip()
        self.title = normalize_title(self.title)
        self.description = self.description.strip()
        self.tags = tuple(tag.strip().lower() for tag in self.tags if tag.strip())
        if not self.id:
            raise ValueError("task id is required")
        if not (1 <= self.priority <= 5):
            raise ValueError("priority must be between 1 and 5")

    def complete(self) -> None:
        self.completed = True

    def update(
        self,
        *,
        title: str | None = None,
        description: str | None = None,
        due_date: str | None = None,
        priority: int | None = None,
        tags: tuple[str, ...] | None = None,
    ) -> None:
        if title is not None:
            self.title = normalize_title(title)
        if description is not None:
            self.description = description.strip()
        if due_date is not None:
            self.due_date = due_date
        if priority is not None:
            if not (1 <= priority <= 5):
                raise ValueError("priority must be between 1 and 5")
            self.priority = priority
        if tags is not None:
            self.tags = tuple(tag.strip().lower() for tag in tags if tag.strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "due_date": self.due_date,
            "priority": self.priority,
            "completed": self.completed,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Task":
        return cls(
            id=str(payload["id"]),
            title=str(payload["title"]),
            description=str(payload.get("description", "")),
            due_date=payload.get("due_date"),
            priority=int(payload.get("priority", 3)),
            completed=bool(payload.get("completed", False)),
            tags=tuple(str(tag) for tag in payload.get("tags", ())),
        )
