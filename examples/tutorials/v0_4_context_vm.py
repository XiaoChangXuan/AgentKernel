"""V0.4 tutorial: Context VM projects durable truth into a bounded working set.

Run from the repository root:

    python examples/tutorials/v0_4_context_vm.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from agentkernel import (  # noqa: E402
    ApproximateTokenEstimator,
    ContextBudget,
    ContextManager,
    ContextProjector,
    EventType,
    Session,
)


def append_text_turn(
    session: Session,
    turn: int,
    user_content: str,
    assistant_content: str,
) -> None:
    session.append(EventType.TURN_START, {"turn": turn})
    session.append(EventType.USER_MESSAGE, {"turn": turn, "content": user_content})
    session.append(EventType.STEP_START, {"turn": turn, "step": 1})
    session.append(
        EventType.ASSISTANT_MESSAGE,
        {
            "turn": turn,
            "step": 1,
            "content": assistant_content,
            "tool_calls": [],
        },
    )
    session.append(EventType.STEP_END, {"turn": turn, "step": 1, "outcome": "done"})
    session.append(EventType.TURN_END, {"turn": turn, "reason": "completed"})


def main() -> None:
    session = Session("tutorial-v0-4-session")
    for turn in range(1, 8):
        append_text_turn(
            session,
            turn,
            f"user fact {turn}: " + ("durable text " * 14),
            f"assistant response {turn}: " + ("model-visible projection " * 10),
        )

    projector = ContextProjector(ApproximateTokenEstimator(1))
    pages = projector.project(session, system_prompt="Keep the answer concise.")
    working_set = ContextManager(projector=projector).build_working_set(
        session,
        current_turn=7,
        budget=ContextBudget(max_tokens=420),
        system_prompt="Keep the answer concise.",
    )

    durable_messages = session.derive_messages()
    model_messages = working_set.to_messages()

    print("V0.4 Context VM")
    print(f"durable_events={len(session.events)}")
    print(f"durable_messages={len(durable_messages)}")
    print(f"projected_pages={len(pages)}")
    print(f"selected_pages={working_set.metrics.selected_pages}")
    print(f"evicted_pages={working_set.metrics.evicted_pages}")
    print(f"selected_tokens={working_set.metrics.selected_tokens}")
    print(f"model_messages={len(model_messages)}")
    print(f"context_equals_truth={len(model_messages) == len(durable_messages)}")


if __name__ == "__main__":
    main()
