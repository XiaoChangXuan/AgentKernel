# AgentKernel

AgentKernel is a small trusted runtime kernel for non-deterministic,
failure-prone, tool-using LLM agents.

Core principle:

```text
Model makes decisions.
Kernel owns invariants.
```

AgentKernel is not a general-purpose agent framework. Prompts, product policy,
business workflows, model choice, UI, plugins, and memory products belong above
the Kernel. AgentKernel focuses on the runtime mechanisms that must remain
enforceable when model output is wrong, incomplete, or hostile.

## Why AgentKernel

Tool-using agents need a runtime boundary because:

- LLM output is non-deterministic.
- Tools can create external side effects.
- Context is finite and lossy.
- Processes can crash between meaningful events.
- Tool results and artifacts can be much larger than model context.
- Authority cannot safely live in prompts.
- Multi-agent cooperation needs identity, isolation, and recovery boundaries.

AgentKernel treats the LLM as an untrusted proposer and keeps durable truth,
authorization, recovery, scheduling, communication, resource sharing, and
side-effect boundaries inside trusted runtime mechanisms.

## Architecture

```text
LLM / policy layer
    |
    v
Agent                capability principal and semantic actor
    |
    v
Process              schedulable runtime identity
    |
    v
Scheduler / Accounting
    |
    v
Session / Context VM  durable truth and model-visible projection
    |
    v
Tool / IPC / Resource / Durable WAL
    |
    v
External World
```

The key object boundaries are:

- Agent != Process.
- Agent Tree != Process Tree.
- Session != Context.
- Context != durable truth.
- Resource != Handle.
- ResourceStore != authorization boundary.
- IPC payload != authority.
- Accounting != durable ledger.
- LLM != Authority.

## Current Capabilities

The current V0.8 alpha baseline contains:

| Version | Mechanism |
| --- | --- |
| V0.1 | Execution Kernel and tool boundary. |
| V0.2 | Session persistence, event replay, and recovery analysis. |
| V0.3 | Durable Tool Execution with WAL and reconciliation. |
| V0.4 | Context VM, context pages, working set, pruning, and compaction. |
| V0.5 | Virtual Resource / Artifact Handle for large tool results. |
| V0.6 | Capability core and enforcement at Tool, Resource, and Durable boundaries. |
| V0.7 | Process runtime, cooperative scheduler, and runtime accounting. |
| V0.8 | Agent Registry, Process Tree, Capability Delegation, Kernel IPC, Resource Sharing, runtime isolation, integrated multi-agent recovery, and Multi-Agent RuntimeBench. |

## Kernel Invariants

1. LLM is never Kernel.
2. Side effects cross a trusted boundary.
3. Session is durable semantic truth.
4. Context is a model-visible projection.
5. Mechanism and policy remain separated.
6. Resource is not the same thing as Handle or Preview.
7. Agent is not Process.
8. Agent is the capability principal.
9. Scheduler owns runtime mechanism, not business policy.
10. Durable mutations remain WAL/reconcile controlled.
11. Accounting is observation, not a durable billing ledger.
12. IPC transfers data, not authority.
13. Cross-Agent Resource access requires both current capability authorization
    and an active ResourceShare.
14. Recovered runtime state is reconstructed from durable semantic facts and
    current Host configuration; stale runtime-only state is not restored as
    authority.

## RuntimeBench

The canonical benchmark entrypoint is:

```bash
python -m benchmarks.runtimebench
```

The canonical V0.8 machine-readable result is:

```text
benchmarks/results/runtimebench_v0.8.json
```

The canonical V0.8 human-readable evidence review is:

```text
docs/evaluation/V0.8_MULTI_AGENT_RUNTIMEBENCH.md
```

Current implemented RuntimeBench families:

| Family | Result |
| --- | --- |
| B1 Fault Tolerance | PASS |
| B2 Side Effect Safety | PASS |
| B3 Context Efficiency / Truth Preservation | PASS |
| B4 Capability Isolation | PASS |
| B5 Resource Governance | PASS |
| B6 Long-Horizon Runtime Stability | PASS |
| B7 Boundary Isolation | PASS |
| B8 Multi-Agent Runtime | PASS |

Current summary:

```text
total = 8
passed = 8
failed = 0
decision = PASS
```

B8 covers M1-M10 multi-agent runtime invariants. M10 passes deterministic
offline profiles at 100, 500, and 1000 logical steps.

## Run

Python 3.11 or newer is required. The runtime has no required third-party
dependencies for basic examples.

```bash
python examples/basic_agent.py
python examples/persistent_session.py
python examples/resource_handles.py
python examples/process_runtime.py
```

Run tests:

```bash
python -m pip install -e ".[test]"
pytest -q
```

Run RuntimeBench without rewriting the frozen artifact:

```bash
python -m benchmarks.runtimebench --no-write
```

Optional real-provider examples require explicit OpenAI-compatible endpoint
configuration. AgentKernel does not load `.env` automatically and does not fall
back to public provider endpoints.

## Documentation

Start with:

- [docs/README.md](docs/README.md)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [docs/releases/V0.8_RELEASE_REVIEW.md](docs/releases/V0.8_RELEASE_REVIEW.md)
- [docs/releases/V0.8_RELEASE_NOTES.md](docs/releases/V0.8_RELEASE_NOTES.md)
- [docs/evaluation/V0.8_MULTI_AGENT_RUNTIMEBENCH.md](docs/evaluation/V0.8_MULTI_AGENT_RUNTIMEBENCH.md)

## Supported Claims

The V0.8 alpha evidence supports only scoped runtime-mechanism claims:

- Session replay preserves durable facts for covered deterministic crash
  prefixes.
- WAL/reconciliation prevents duplicate execution in tested reconcilable fake
  service scenarios.
- ResourceHandle keeps large bytes outside model context while preserving exact
  durable access.
- Context VM reduces model-visible context in tested deterministic fixtures.
- Capability enforcement blocks tested unauthorized Tool, Resource, and Durable
  operations.
- Scheduler and Accounting can block process execution at cooperative safe
  points when configured budgets are exceeded.
- V0.1-V0.8 runtime mechanisms preserve tested invariants in deterministic
  single-agent workloads up to 1000 logical steps.
- V0.8 multi-agent mechanisms preserve tested identity, delegation, IPC,
  resource sharing, budget, fault, cancellation, and recovery invariants in
  deterministic offline fixtures.
- Tested multi-agent recovery reconstructs durable semantic facts and fresh
  runtime mechanisms without restoring stale runtime-only authority.

## Limitations

AgentKernel V0.8 alpha does not include:

- V0.9 Persistent Memory.
- Namespace security.
- Complete revocation semantics.
- RBAC or IAM.
- Production sandbox security.
- Distributed runtime correctness.
- Distributed consensus.
- Production SLA or production workload traces.
- Preemptive scheduling.
- Universal exactly-once side effects.
- Arbitrary external system atomicity.
- Semantic long-horizon reasoning.
- Claims of superior model intelligence.
- Claims that AgentKernel beats Codex, OpenHands, Gemini CLI, LangChain, Letta,
  or any other project.

RuntimeBench is synthetic, deterministic, offline, and local. It does not use
real API providers, network services, statistical repeated-run analysis, or
production workload traces.

## Roadmap

- V0.8: Multi-Agent Runtime alpha release freeze.
- V0.9: Persistent Memory Runtime architecture.
- V1.0: Stable Agent Runtime Kernel baseline.

V0.9 is not implemented in this release.
