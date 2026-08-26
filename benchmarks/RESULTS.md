# AgentKernel Benchmark Results

This file is a compatibility and navigation entrypoint. It is not the canonical
V0.7 release evidence report.

Canonical aggregate result:

```text
benchmarks/results/runtimebench_v0.7.json
```

Human-readable evidence review:

```text
docs/V0.7_RUNTIMEBENCH_REVIEW.md
```

Canonical command:

```bash
python -m benchmarks.runtimebench
```

## Current V0.7 RuntimeBench Summary

| Family | Result |
| --- | --- |
| B1 Fault Tolerance | PASS |
| B2 Side Effect Safety | PASS |
| B3 Context + Truth Preservation | PASS |
| B4 Capability Isolation | PASS |
| B5 Resource Governance | PASS |
| B7 Boundary Isolation | PASS |

```text
total = 6
passed = 6
failed = 0
decision = PASS
```

## Leaf / Historical / Diagnostic Results

The files below are retained as leaf benchmark outputs and historical release
evidence. They remain useful for debugging individual mechanisms, but they are
not the top-level V0.7 release claim surface:

- `benchmarks/results/resource.json`
- `benchmarks/results/durable_tool.json`
- `benchmarks/results/recovery.json`
- `benchmarks/results/context_vm.json`
- `benchmarks/results/capability_runtime.json`
- `benchmarks/results/v0.7_runtime.json`
- `benchmarks/results/all.json`

Earlier real-provider Context VM observations are retained in git history. Real
provider runs require explicit local configuration and are not part of default
pytest or RuntimeBench release evidence.
