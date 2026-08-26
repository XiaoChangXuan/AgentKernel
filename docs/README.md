# AgentKernel Documentation

This file is the navigation entrypoint for AgentKernel documentation. Historical
documents are retained for design history; they are not necessarily current
authoritative specifications.

## Start Here

1. [../README.md](../README.md)
2. [ARCHITECTURE.md](ARCHITECTURE.md)
3. [AGENTKERNEL_EVALUATION_STRATEGY.md](AGENTKERNEL_EVALUATION_STRATEGY.md)
4. [RUNTIMEBENCH_V0.7_DESIGN.md](RUNTIMEBENCH_V0.7_DESIGN.md)
5. [V0.7_RUNTIMEBENCH_REVIEW.md](V0.7_RUNTIMEBENCH_REVIEW.md)
6. [V0.7_RELEASE.md](V0.7_RELEASE.md)

## Current / Canonical

- [ARCHITECTURE.md](ARCHITECTURE.md): implemented runtime architecture.
- [V0.7_RELEASE.md](V0.7_RELEASE.md): V0.7 public release summary.
- [V0.7_RUNTIMEBENCH_REVIEW.md](V0.7_RUNTIMEBENCH_REVIEW.md): current evidence,
  claims, and limitations.
- [RUNTIMEBENCH_V0.7_DESIGN.md](RUNTIMEBENCH_V0.7_DESIGN.md): RuntimeBench
  family design and schema.
- [AGENTKERNEL_EVALUATION_STRATEGY.md](AGENTKERNEL_EVALUATION_STRATEGY.md):
  evaluation positioning and claim discipline.

## Current Architecture

- [ARCHITECTURE.md](ARCHITECTURE.md)
- [KERNEL_ARCHITECTURE_REVIEW.md](KERNEL_ARCHITECTURE_REVIEW.md)
- [V0.5_RESOURCE_ARCHITECTURE.md](V0.5_RESOURCE_ARCHITECTURE.md)
- [V0.5_RESOURCE_ARCHITECTURE_REVIEW.md](V0.5_RESOURCE_ARCHITECTURE_REVIEW.md)
- [V0.6_CAPABILITY_ARCHITECTURE_DESIGN.md](V0.6_CAPABILITY_ARCHITECTURE_DESIGN.md)
- [V0.6_CAPABILITY_FREEZE_REVIEW.md](V0.6_CAPABILITY_FREEZE_REVIEW.md)
- [V0.6_CAPABILITY_FINAL_REVIEW.md](V0.6_CAPABILITY_FINAL_REVIEW.md)
- [V0.7_PROCESS_ARCHITECTURE_REVIEW.md](V0.7_PROCESS_ARCHITECTURE_REVIEW.md)
- [V0.7_RUNTIME_ARCHITECTURE_REVIEW.md](V0.7_RUNTIME_ARCHITECTURE_REVIEW.md)
- [V0.7_RELEASE_REVIEW.md](V0.7_RELEASE_REVIEW.md)

## Current Evaluation

- [AGENTKERNEL_EVALUATION_STRATEGY.md](AGENTKERNEL_EVALUATION_STRATEGY.md):
  why AgentKernel is evaluated and which claims are allowed.
- [RUNTIMEBENCH_V0.7_DESIGN.md](RUNTIMEBENCH_V0.7_DESIGN.md): how RuntimeBench
  is structured, including B1 through B8.
- [V0.7_RUNTIMEBENCH_IMPLEMENTATION.md](V0.7_RUNTIMEBENCH_IMPLEMENTATION.md):
  how the current RuntimeBench runner is implemented.
- [V0.7_RUNTIMEBENCH_REVIEW.md](V0.7_RUNTIMEBENCH_REVIEW.md): current measured
  evidence and limitations.
- [V0.7_RUNTIME_BENCHMARK.md](V0.7_RUNTIME_BENCHMARK.md): earlier V0.7 runtime
  primitive benchmark report.
- [benchmark/RESULTS.md](benchmark/RESULTS.md): historical benchmark report
  location retained for compatibility.

## Implementation Notes

- [V0.6_CAPABILITY_IMPLEMENTATION.md](V0.6_CAPABILITY_IMPLEMENTATION.md)
- [V0.6_CAPABILITY_ENFORCEMENT_IMPLEMENTATION.md](V0.6_CAPABILITY_ENFORCEMENT_IMPLEMENTATION.md)
- [V0.6_CAPABILITY_DURABLE_IMPLEMENTATION.md](V0.6_CAPABILITY_DURABLE_IMPLEMENTATION.md)
- [V0.7_PROCESS_CORE_IMPLEMENTATION.md](V0.7_PROCESS_CORE_IMPLEMENTATION.md)
- [V0.7_SCHEDULER_IMPLEMENTATION.md](V0.7_SCHEDULER_IMPLEMENTATION.md)
- [V0.7_RESOURCE_ACCOUNTING.md](V0.7_RESOURCE_ACCOUNTING.md)
- [V0.7_RUNTIMEBENCH_IMPLEMENTATION.md](V0.7_RUNTIMEBENCH_IMPLEMENTATION.md)

## Historical / Research Notes

These documents are useful design history. They may contain earlier framing,
open questions, or pre-freeze wording.

- [AGENT_RUNTIME_POSITIONING_REVIEW.md](AGENT_RUNTIME_POSITIONING_REVIEW.md)
- [AGENT_RUNTIME_COMPARISON.md](AGENT_RUNTIME_COMPARISON.md)
- [AGENT_RUNTIME_BENCHMARK_DESIGN.md](AGENT_RUNTIME_BENCHMARK_DESIGN.md)
- [ARCHITECTURE_FREEZE_REVIEW.md](ARCHITECTURE_FREEZE_REVIEW.md)
- [ARCHITECTURE_REVIEW_AFTER_V0_5.md](ARCHITECTURE_REVIEW_AFTER_V0_5.md)
- [CONTEXT_COMPACTION_RESEARCH.md](CONTEXT_COMPACTION_RESEARCH.md)
- [CONTEXT_PROVIDER_RECOVERY_RESEARCH.md](CONTEXT_PROVIDER_RECOVERY_RESEARCH.md)
- [RESOURCE_LAYER_RESEARCH.md](RESOURCE_LAYER_RESEARCH.md)
- [IMPLEMENTATION_BLUEPRINT.md](IMPLEMENTATION_BLUEPRINT.md)

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
