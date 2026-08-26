"""Minimal offline V0.7 Process Runtime example."""

from __future__ import annotations

import sys
from pathlib import Path

# Keep the documented source-checkout command runnable without installing first.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from agentkernel import (
    Agent,
    AgentBudget,
    CooperativeScheduler,
    ModelUsage,
    ProcessBudgetExceeded,
    ProcessState,
    SchedulerSafePoint,
    Session,
    UsageCollector,
)


def main() -> None:
    agent = Agent.create(
        agent_id="example-agent",
        session=Session("example-session"),
        budget=AgentBudget(max_token_usage=5),
    )
    collector = UsageCollector()
    scheduler = CooperativeScheduler(usage_collector=collector)

    process = scheduler.create_process(
        process_id="example-process",
        agent=agent.control,
    )
    print(f"created: {process.process_id} state={process.state}")

    scheduler.dispatch(process.process_id)
    print(f"dispatch: state={process.state}")

    collector.record_llm_usage(
        process.process_id,
        ModelUsage(input_tokens=4, output_tokens=2, total_tokens=6),
    )

    try:
        scheduler.safe_point(
            process.process_id,
            SchedulerSafePoint.AFTER_LLM_CALL,
        )
    except ProcessBudgetExceeded as error:
        print(
            "blocked: "
            f"limit={error.exceeded.limit} "
            f"usage={error.exceeded.usage} "
            f"max={error.exceeded.maximum} "
            f"state={process.state}"
        )

    collector.reset_process(process.process_id)
    scheduler.unblock(process.process_id)
    print(f"unblocked: state={process.state}")

    scheduler.dispatch(process.process_id)
    collector.record_llm_usage(
        process.process_id,
        ModelUsage(input_tokens=1, output_tokens=1, total_tokens=2),
    )
    scheduler.safe_point(process.process_id, SchedulerSafePoint.AFTER_LLM_CALL)
    scheduler.yield_process(process.process_id, ProcessState.READY)

    snapshot = collector.snapshot(process.process_id)
    print(
        "resumed: "
        f"state={process.state} "
        f"tokens={snapshot.token_usage} "
        f"tool_calls={snapshot.tool_calls}"
    )


if __name__ == "__main__":
    main()
