# AgentKernel RuntimeBench

RuntimeBench is the canonical benchmark entrypoint for AgentKernel release
evidence. It is offline and deterministic by default. It does not call real
model providers, external APIs, or network services.

Canonical command:

```bash
python -m benchmarks.runtimebench
```

Canonical machine-readable result:

```text
benchmarks/results/runtimebench_v0.7.json
```

Canonical human-readable interpretation:

```text
docs/V0.7_RUNTIMEBENCH_REVIEW.md
```

## Current V0.7 Evidence

| Family | Result |
| --- | --- |
| B1 Fault Tolerance | PASS |
| B2 Side Effect Safety | PASS |
| B3 Context + Truth Preservation | PASS |
| B4 Capability Isolation | PASS |
| B5 Resource Governance | PASS |
| B6 Long-Horizon Runtime Stability | PASS |
| B7 Boundary Isolation | PASS |

Summary:

```text
total = 7
passed = 7
failed = 0
decision = PASS
```

## Not Implemented In Current Release Evidence

- B8 Scheduler Scalability.

B6 Long-Horizon Runtime Stability is implemented for deterministic
single-agent profiles at 100, 500, and 1000 logical steps.

B8 is a future micro/stress benchmark only. It is not a headline research claim.

## Benchmark Flow

```text
leaf benchmark
    |
    v
RuntimeBench adapter
    |
    v
canonical RuntimeBench result
```

Leaf benchmarks remain available for mechanism development, debugging, and raw
evidence generation. Release claims should use RuntimeBench.

## Leaf Benchmark Mapping

| Leaf benchmark | RuntimeBench family |
| --- | --- |
| `benchmarks/recovery/runner.py` | B1 Fault Tolerance |
| `benchmarks/durable_tool/runner.py` | B2 Side Effect Safety |
| `benchmarks/resource_handle/runner.py` | B3 Context + Truth Preservation |
| `benchmarks/context_vm/runner.py` | B3 Context + Truth Preservation |
| `benchmarks/capability_runtime_benchmark.py` | B4 Capability Isolation |
| `benchmarks/v0_7_runtime_benchmark.py` | B1, B5, B7 source material |
| `benchmarks/runtimebench/adapters.py` | B5 Resource Governance and B7 Boundary Isolation RuntimeBench cases |
| `benchmarks/runtimebench/long_horizon.py` | B6 Long-Horizon Runtime Stability |

## Leaf Commands

These commands remain useful for local debugging:

```bash
python -m benchmarks.recovery.runner
python -m benchmarks.durable_tool.runner
python -m benchmarks.resource_handle.runner
python -m benchmarks.context_vm.runner
python -m benchmarks.capability_runtime_benchmark
python -m benchmarks.v0_7_runtime_benchmark
python -m benchmarks.run_all
```

Their result files under `benchmarks/results/` are leaf, historical, or
diagnostic results unless explicitly wrapped by RuntimeBench.
