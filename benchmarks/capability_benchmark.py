"""Validate the V0.6 Phase 1 capability evaluator behavior."""

from __future__ import annotations

import argparse

from agentkernel import AuthorizationRequest, CapabilityEvaluator, CapabilityGrant

from benchmarks.common.metrics import BenchmarkRecord, Timer
from benchmarks.common.reporter import print_json_records, write_json_records


BENCHMARK = "capability_core"


def run() -> list[BenchmarkRecord]:
    """Run deterministic capability-core validation cases."""

    records: list[BenchmarkRecord] = []
    records.append(
        _case(
            case="basic_authorization",
            grants=[
                CapabilityGrant(
                    subject="agent-a",
                    action="resource.read",
                    resource_scope="artifact://project-a/**",
                )
            ],
            request=AuthorizationRequest(
                agent_id="agent-a",
                action="resource.read",
                resource="artifact://project-a/file.txt",
            ),
            expected_allowed=True,
        )
    )
    records.append(
        _case(
            case="scope_isolation",
            grants=[
                CapabilityGrant(
                    subject="agent-a",
                    action="resource.read",
                    resource_scope="artifact://project-a/**",
                )
            ],
            request=AuthorizationRequest(
                agent_id="agent-a",
                action="resource.read",
                resource="artifact://project-b/file.txt",
            ),
            expected_allowed=False,
        )
    )
    records.append(
        _case(
            case="action_isolation",
            grants=[
                CapabilityGrant(
                    subject="agent-a",
                    action="resource.read",
                    resource_scope="artifact://project-a/**",
                )
            ],
            request=AuthorizationRequest(
                agent_id="agent-a",
                action="resource.write",
                resource="artifact://project-a/file.txt",
            ),
            expected_allowed=False,
        )
    )
    records.append(
        _case(
            case="multiple_grant_project_a",
            grants=[
                CapabilityGrant(
                    subject="agent-a",
                    action="resource.read",
                    resource_scope="artifact://project-a/**",
                ),
                CapabilityGrant(
                    subject="agent-a",
                    action="resource.read",
                    resource_scope="artifact://project-b/**",
                ),
            ],
            request=AuthorizationRequest(
                agent_id="agent-a",
                action="resource.read",
                resource="artifact://project-a/file.txt",
            ),
            expected_allowed=True,
        )
    )
    records.append(
        _case(
            case="multiple_grant_project_b",
            grants=[
                CapabilityGrant(
                    subject="agent-a",
                    action="resource.read",
                    resource_scope="artifact://project-a/**",
                ),
                CapabilityGrant(
                    subject="agent-a",
                    action="resource.read",
                    resource_scope="artifact://project-b/**",
                ),
            ],
            request=AuthorizationRequest(
                agent_id="agent-a",
                action="resource.read",
                resource="artifact://project-b/file.txt",
            ),
            expected_allowed=True,
        )
    )
    return records


def _case(
    *,
    case: str,
    grants: list[CapabilityGrant],
    request: AuthorizationRequest,
    expected_allowed: bool,
) -> BenchmarkRecord:
    timer = Timer()
    decision = CapabilityEvaluator(grants).authorize(request)
    latency_ms = timer.elapsed_ms()
    matched_grant = decision.matched_grant
    success = decision.allowed is expected_allowed
    return BenchmarkRecord(
        benchmark=BENCHMARK,
        case=case,
        strategy="phase_1_evaluator",
        metrics={
            "agent_id": request.agent_id,
            "action": request.action,
            "resource": request.resource,
            "grant_count": len(grants),
            "expected_allowed": expected_allowed,
            "actual_allowed": decision.allowed,
            "reason": decision.reason,
            "matched_scope": (
                matched_grant.resource_scope if matched_grant is not None else None
            ),
            "latency_ms": latency_ms,
            "success": success,
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="capability.json")
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()

    records = run()
    if not all(record.metrics["success"] for record in records):
        raise SystemExit("capability benchmark validation failed")
    if not args.no_write:
        write_json_records(args.output, records)
    print_json_records(records)


if __name__ == "__main__":
    main()
