# AgentKernel Documentation

This file is the navigation entrypoint for AgentKernel documentation.
Historical and research notes are retained for design evolution; they are not
necessarily current authoritative specifications.

## Start Here

1. [Project README](../README.md)
2. [Current Architecture](ARCHITECTURE.md)
3. [Evaluation Strategy](evaluation/AGENTKERNEL_EVALUATION_STRATEGY.md)
4. [RuntimeBench V0.7 Design](evaluation/RUNTIMEBENCH_V0.7_DESIGN.md)
5. [RuntimeBench Evidence Review](evaluation/V0.7_RUNTIMEBENCH_REVIEW.md)
6. [V0.7 Release](releases/V0.7_RELEASE.md)

## Canonical Documents

- [Current architecture](ARCHITECTURE.md): implemented runtime architecture.
- [Current evaluation strategy](evaluation/AGENTKERNEL_EVALUATION_STRATEGY.md):
  evaluated claims and claim discipline.
- [RuntimeBench V0.7 design](evaluation/RUNTIMEBENCH_V0.7_DESIGN.md):
  benchmark structure, schema, fixtures, and methodology.
- [RuntimeBench V0.7 evidence review](evaluation/V0.7_RUNTIMEBENCH_REVIEW.md):
  measured V0.7 evidence and limitations.
- [B6 long-horizon evidence](evaluation/V0.7_LONG_HORIZON_RUNTIMEBENCH.md):
  deterministic long-horizon RuntimeBench evidence.
- [V0.7 release](releases/V0.7_RELEASE.md): public release summary.

## Current Architecture

- [Kernel Architecture Review](architecture/KERNEL_ARCHITECTURE_REVIEW.md)
- [Architecture Freeze Review](architecture/ARCHITECTURE_FREEZE_REVIEW.md)
- [V0.5 Resource Architecture](architecture/V0.5_RESOURCE_ARCHITECTURE.md)
- [V0.5 Resource Architecture Review](architecture/V0.5_RESOURCE_ARCHITECTURE_REVIEW.md)
- [V0.6 Capability Architecture Design](architecture/V0.6_CAPABILITY_ARCHITECTURE_DESIGN.md)
- [V0.6 Capability Freeze Review](architecture/V0.6_CAPABILITY_FREEZE_REVIEW.md)
- [V0.6 Capability Final Review](architecture/V0.6_CAPABILITY_FINAL_REVIEW.md)
- [V0.7 Process Architecture Review](architecture/V0.7_PROCESS_ARCHITECTURE_REVIEW.md)
- [V0.7 Runtime Architecture Review](architecture/V0.7_RUNTIME_ARCHITECTURE_REVIEW.md)
- [V0.8 Multi-Agent Architecture Review](architecture/V0.8_MULTI_AGENT_ARCHITECTURE_REVIEW.md)
- [V0.8 Multi-Agent Design Freeze](architecture/V0.8_MULTI_AGENT_DESIGN_FREEZE.md)

## Evaluation

- [AgentKernel Evaluation Strategy](evaluation/AGENTKERNEL_EVALUATION_STRATEGY.md):
  what claims are evaluated.
- [RuntimeBench V0.7 Design](evaluation/RUNTIMEBENCH_V0.7_DESIGN.md):
  benchmark structure and methodology.
- [RuntimeBench V0.7 Implementation](evaluation/V0.7_RUNTIMEBENCH_IMPLEMENTATION.md):
  RuntimeBench runner implementation record.
- [RuntimeBench V0.7 Review](evaluation/V0.7_RUNTIMEBENCH_REVIEW.md):
  actual evidence and limitations.
- [V0.7 Long-Horizon RuntimeBench](evaluation/V0.7_LONG_HORIZON_RUNTIMEBENCH.md):
  B6 composition evidence.

## Implementation Notes

- [V0.6 Capability Implementation](implementation/V0.6_CAPABILITY_IMPLEMENTATION.md)
- [V0.6 Capability Enforcement Implementation](implementation/V0.6_CAPABILITY_ENFORCEMENT_IMPLEMENTATION.md)
- [V0.6 Capability Durable Implementation](implementation/V0.6_CAPABILITY_DURABLE_IMPLEMENTATION.md)
- [V0.7 Process Core Implementation](implementation/V0.7_PROCESS_CORE_IMPLEMENTATION.md)
- [V0.7 Scheduler Implementation](implementation/V0.7_SCHEDULER_IMPLEMENTATION.md)
- [V0.7 Resource Accounting](implementation/V0.7_RESOURCE_ACCOUNTING.md)
- [V0.8 Agent Registry Implementation](implementation/V0.8_AGENT_REGISTRY_IMPLEMENTATION.md)
- [V0.8 Process Tree Implementation](implementation/V0.8_PROCESS_TREE_IMPLEMENTATION.md)
- [V0.8 Capability Delegation Implementation](implementation/V0.8_CAPABILITY_DELEGATION_IMPLEMENTATION.md)
- [V0.8 Kernel IPC Implementation](implementation/V0.8_KERNEL_IPC_IMPLEMENTATION.md)
- [V0.8 Resource Sharing Implementation](implementation/V0.8_RESOURCE_SHARING_IMPLEMENTATION.md)

## Releases

- [V0.4 Release](releases/V0.4_RELEASE.md)
- [V0.7 Release](releases/V0.7_RELEASE.md)
- [V0.7 Release Freeze Review](releases/V0.7_RELEASE_REVIEW.md)

## Research / Historical Notes

These documents preserve architecture evolution, earlier benchmark framing, and
comparison research. They may contain superseded paths, earlier assumptions, or
pre-freeze language, so they should not be treated as current specifications
without checking the canonical documents above.

- [Agent Runtime Positioning Review](research/AGENT_RUNTIME_POSITIONING_REVIEW.md)
- [Agent Runtime Comparison](research/AGENT_RUNTIME_COMPARISON.md)
- [Agent Runtime Benchmark Design](research/AGENT_RUNTIME_BENCHMARK_DESIGN.md)
- [Architecture Review After V0.5](research/ARCHITECTURE_REVIEW_AFTER_V0_5.md)
- [Context Compaction Research](research/CONTEXT_COMPACTION_RESEARCH.md)
- [Context Provider Recovery Research](research/CONTEXT_PROVIDER_RECOVERY_RESEARCH.md)
- [Resource Layer Research](research/RESOURCE_LAYER_RESEARCH.md)
- [Implementation Blueprint](research/IMPLEMENTATION_BLUEPRINT.md)
- [V0.7 Runtime Benchmark](research/V0.7_RUNTIME_BENCHMARK.md)
- [Historical Agent Runtime Benchmark](research/AGENT_RUNTIME_BENCHMARK.md)
- [Historical Agent Runtime Benchmark Results](research/AGENT_RUNTIME_BENCHMARK_RESULTS.md)

## Benchmark Result Files

Canonical V0.7 result:

```text
../benchmarks/results/runtimebench_v0.7.json
```

Leaf and historical result files remain under:

```text
../benchmarks/results/
```

Use leaf benchmark results for mechanism debugging. Use RuntimeBench for release
claims.
