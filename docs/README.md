# AgentKernel Documentation

This file is the navigation entrypoint for AgentKernel documentation.
Historical and research notes are retained for design evolution; they are not
necessarily current authoritative specifications.

## Start Here

1. [Project README](../README.md)
2. [中文入门指南](getting-started/AGENTKERNEL_GUIDE.zh-CN.md)
3. [English newcomer guide](getting-started/AGENTKERNEL_GUIDE.en.md)
4. [Teaching and trace guide](getting-started/TEACHING_AND_TRACE.md)
5. [Current Architecture](ARCHITECTURE.md)
6. [V0.8 Multi-Agent RuntimeBench](evaluation/V0.8_MULTI_AGENT_RUNTIMEBENCH.md)
7. [V0.8 Release Review](releases/V0.8_RELEASE_REVIEW.md)
8. [V0.8 Release Notes](releases/V0.8_RELEASE_NOTES.md)
9. [Evaluation Strategy](evaluation/AGENTKERNEL_EVALUATION_STRATEGY.md)

## Quick Tutorials

Run these from the repository root:

```bash
python examples/tutorials/v0_1_agent_spine.py
python examples/tutorials/v0_2_recovery.py
python examples/tutorials/v0_3_durable_side_effect.py
python examples/tutorials/v0_4_context_vm.py
python examples/tutorials/v0_5_resource_handle.py
python examples/tutorials/v0_6_capability_core.py
python examples/tutorials/v0_7_process_runtime.py
python examples/tutorials/v0_8_multi_agent_runtime.py
```

They introduce the runtime spine, durable Session recovery, Durable Tool WAL
reconciliation, Context VM, Resource Handle, Capability enforcement, Process
Runtime, and Multi-Agent runtime boundaries without real API keys or network
calls.

Each tutorial prints a short "本实验验证什么 / WHAT THIS DEMONSTRATES" and
"本实验不证明什么 / WHAT THIS DOES NOT DEMONSTRATE" section so the runnable
example cannot be mistaken for a real-model evaluation.

## Canonical Documents

- [中文入门指南](getting-started/AGENTKERNEL_GUIDE.zh-CN.md): problem-driven
  newcomer guide for AgentKernel V0.8.
- [English newcomer guide](getting-started/AGENTKERNEL_GUIDE.en.md):
  English version of the same onboarding path.
- [Teaching and trace guide](getting-started/TEACHING_AND_TRACE.md):
  distinction between deterministic tutorials, RuntimeBench evidence, and
  opt-in real-model traces.
- [Current architecture](ARCHITECTURE.md): implemented V0.8 runtime architecture.
- [V0.8 release review](releases/V0.8_RELEASE_REVIEW.md): release freeze audit,
  claim boundary, evidence provenance, and validation record.
- [V0.8 release notes](releases/V0.8_RELEASE_NOTES.md): alpha release summary.
- [V0.8 Multi-Agent RuntimeBench](evaluation/V0.8_MULTI_AGENT_RUNTIMEBENCH.md):
  B1-B8 evidence, B8 M1-M10 evidence, and limitations.
- [Current evaluation strategy](evaluation/AGENTKERNEL_EVALUATION_STRATEGY.md):
  evaluated claims and claim discipline.

## Architecture

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
- [V0.8 Multi-Agent RuntimeBench](evaluation/V0.8_MULTI_AGENT_RUNTIMEBENCH.md):
  current B1-B8 evidence.
- [RuntimeBench V0.7 Design](evaluation/RUNTIMEBENCH_V0.7_DESIGN.md):
  benchmark structure and methodology for the V0.7 single-agent baseline.
- [RuntimeBench V0.7 Implementation](evaluation/V0.7_RUNTIMEBENCH_IMPLEMENTATION.md):
  V0.7 RuntimeBench runner implementation record.
- [RuntimeBench V0.7 Review](evaluation/V0.7_RUNTIMEBENCH_REVIEW.md):
  V0.7 evidence and limitations.
- [V0.7 Long-Horizon RuntimeBench](evaluation/V0.7_LONG_HORIZON_RUNTIMEBENCH.md):
  B6 single-agent composition evidence.

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
- [V0.8 Runtime Isolation Implementation](implementation/V0.8_RUNTIME_ISOLATION_IMPLEMENTATION.md)
- [V0.8 Multi-Agent Recovery Implementation](implementation/V0.8_MULTI_AGENT_RECOVERY_IMPLEMENTATION.md)

## Releases

- [V0.4 Release](releases/V0.4_RELEASE.md)
- [V0.7 Release](releases/V0.7_RELEASE.md)
- [V0.7 Release Freeze Review](releases/V0.7_RELEASE_REVIEW.md)
- [V0.8 Release Review](releases/V0.8_RELEASE_REVIEW.md)
- [V0.8 Release Notes](releases/V0.8_RELEASE_NOTES.md)

## Research / Historical Notes

These documents preserve architecture evolution, earlier benchmark framing, and
comparison research. They may contain superseded paths, earlier assumptions, or
pre-freeze language, so they should not be treated as current specifications
without checking the canonical documents above.

- [Agent Runtime Positioning Review](research/AGENT_RUNTIME_POSITIONING_REVIEW.md)
- [Agent Runtime Comparison](research/AGENT_RUNTIME_COMPARISON.md)
- [Agent Runtime Design Comparison](research/AGENT_RUNTIME_DESIGN_COMPARISON.md)
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

Canonical V0.8 result:

```text
../benchmarks/results/runtimebench_v0.8.json
```

Historical V0.7 result:

```text
../benchmarks/results/runtimebench_v0.7.json
```

Leaf and historical result files remain under:

```text
../benchmarks/results/
```

Use leaf benchmark results for mechanism debugging. Use RuntimeBench for release
claims.
