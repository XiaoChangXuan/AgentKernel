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
