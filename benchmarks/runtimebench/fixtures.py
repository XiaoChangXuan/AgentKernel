"""RuntimeBench V0.7 fixture declarations."""

from __future__ import annotations

from benchmarks.runtimebench.schema import FixtureSpec


def crash_fixture() -> FixtureSpec:
    return FixtureSpec(
        fixture_id="session_crash_prefixes",
        parameters={
            "crash_points": [
                "after_user_message",
                "after_step_start",
                "after_tool_call",
                "after_tool_dispatch",
                "before_commit",
                "after_result",
                "process_crash_recovery",
            ],
        },
    )


def side_effect_fixture() -> FixtureSpec:
    return FixtureSpec(
        fixture_id="fake_payment_success_then_crash",
        parameters={
            "external_service": "fake_payment",
            "crash_point": "after_external_success_before_commit",
        },
    )


def context_truth_fixture() -> FixtureSpec:
    return FixtureSpec(
        fixture_id="large_resource_and_1000_turn_context",
        parameters={
            "resource_sizes_mib": [10, 100, 500],
            "context_turns": 1000,
            "marker_positions": ["HEAD", "MIDDLE", "TAIL"],
        },
    )


def capability_attack_fixture() -> FixtureSpec:
    return FixtureSpec(
        fixture_id="capability_boundary_attacks",
        parameters={
            "cases": [
                "unauthorized_tool",
                "unauthorized_resource_read",
                "unauthorized_payment_dispatch",
                "crash_after_prepare",
                "legacy_tool",
            ],
        },
    )


def resource_governance_fixture() -> FixtureSpec:
    return FixtureSpec(
        fixture_id="process_resource_budget_pressure",
        parameters={
            "budget_cases": [
                "token_budget",
                "tool_budget",
                "resource_budget",
                "wall_time_budget",
                "unblock_recovery",
            ],
            "safe_points": [
                "llm_call.after",
                "tool_call.after",
                "step_start.before",
            ],
        },
    )


def long_horizon_fixture() -> FixtureSpec:
    return FixtureSpec(
        fixture_id="long_horizon_runtime_stability",
        parameters={
            "profiles": [100, 500, 1000],
            "mechanisms": [
                "session_event_log",
                "recovery_replay",
                "process_recovery_mapping",
                "context_working_set",
                "resource_handle_restart_read",
                "durable_wal_reconcile",
                "capability_denial",
                "usage_accounting",
                "scheduler_budget_blocking",
            ],
            "crash_point": "after_durable_dispatch_before_commit",
            "offline": True,
        },
    )


def boundary_isolation_fixture() -> FixtureSpec:
    return FixtureSpec(
        fixture_id="kernel_object_boundary_invariants",
        parameters={
            "invariants": [
                "agent_is_not_process",
                "process_is_not_session",
                "context_is_not_truth",
                "accounting_is_not_authority",
                "resource_store_is_not_authorization",
            ],
        },
    )
