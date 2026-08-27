"""V0.7 tutorial: Process runtime separates scheduling from Agent authority.

Run from the repository root:

    python examples/tutorials/v0_7_process_runtime.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from agentkernel import (  # noqa: E402
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
        agent_id="tutorial-agent",
        session=Session("tutorial-v0-7-session"),
        budget=AgentBudget(max_token_usage=5),
    )
    collector = UsageCollector()
    scheduler = CooperativeScheduler(usage_collector=collector)
    process = scheduler.create_process(
        process_id="tutorial-process",
        agent=agent.control,
    )

    scheduler.dispatch(process.process_id)
    collector.record_llm_usage(
        process.process_id,
        ModelUsage(input_tokens=4, output_tokens=2, total_tokens=6),
    )

    blocked = False
    try:
        scheduler.safe_point(process.process_id, SchedulerSafePoint.AFTER_LLM_CALL)
    except ProcessBudgetExceeded:
        blocked = True

    snapshot = collector.snapshot(process.process_id)
    scheduler.reset_usage(process.process_id)
    scheduler.unblock(process.process_id)

    print("V0.7 Process Runtime")
    print(f"agent_id={agent.control.agent_id}")
    print(f"process_id={process.process_id}")
    print(f"capability_principal={process.capability_snapshot.agent_id}")
    print(f"budget_blocked={blocked}")
    print(f"blocked_state={ProcessState.BLOCKED.value}")
    print(f"observed_tokens={snapshot.token_usage}")
    print(f"after_unblock_state={process.state.value}")
    print()
    print("本实验验证什么 / WHAT THIS DEMONSTRATES")
    print("- Agent identity remains the capability principal.")
    print("- Process identity owns runtime scheduling state.")
    print("- Usage accounting can block a process at a safe point.")
    print()
    print("本实验不证明什么 / WHAT THIS DOES NOT DEMONSTRATE")
    print("- It does not use a real model provider.")
    print("- It does not implement preemptive scheduling.")
    print("- It does not make accounting a durable billing ledger.")


if __name__ == "__main__":
    main()
