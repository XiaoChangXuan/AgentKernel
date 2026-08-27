# AgentKernel Teaching And Trace Guide

This guide explains the public teaching artifacts added for the V0.8 alpha
onboarding pass. It separates deterministic tutorials, RuntimeBench evidence,
and opt-in real-model trace demos so readers do not confuse local runtime
fixtures with real LLM evaluation.

## 1. Interactive Labs

The notebooks under `examples/labs/` are the recommended first learning path.
They are problem-oriented experiments rather than terminal output fixtures.

Start with:

```text
examples/labs/v0_3_durable_side_effect_lab.ipynb
```

It shows:

```text
PREPARE -> DISPATCH -> external success -> crash before COMMIT
      -> restart -> RECONCILE_REQUIRED -> commit existing result
```

Core lesson:

```text
Recovery != Retry
```

Then continue through V0.1-V0.8:

| Version | Lab |
| --- | --- |
| V0.1 | `examples/labs/v0_1_agent_execution_lab.ipynb` |
| V0.2 | `examples/labs/v0_2_recovery_lab.ipynb` |
| V0.3 | `examples/labs/v0_3_durable_side_effect_lab.ipynb` |
| V0.4 | `examples/labs/v0_4_context_vm_lab.ipynb` |
| V0.5 | `examples/labs/v0_5_resource_handle_lab.ipynb` |
| V0.6 | `examples/labs/v0_6_capability_lab.ipynb` |
| V0.7 | `examples/labs/v0_7_process_runtime_lab.ipynb` |
| V0.8 | `examples/labs/v0_8_multi_agent_runtime_lab.ipynb` |

Each notebook exposes state before an operation, the operation itself,
Kernel-visible facts, boundary/failure behavior, and the invariant learned.
They use simple local display helpers and no heavy notebook-only dependency.

Optional Jupyter usage:

```bash
python -m pip install jupyter
jupyter lab examples/labs
```

Jupyter is not an AgentKernel runtime dependency.

## 2. Deterministic Tutorials

The tutorials under `examples/tutorials/` are the first runnable path for new
users who want terminal scripts. They are intentionally offline and
deterministic.

They use:

- `ScriptedLLM` or direct deterministic fixtures;
- fake external systems;
- temporary local stores;
- public AgentKernel APIs where practical;
- no provider API keys;
- no network calls.

Each tutorial now ends with:

```text
本实验验证什么 / WHAT THIS DEMONSTRATES
...
本实验不证明什么 / WHAT THIS DOES NOT DEMONSTRATE
...
```

This footer is part of the tutorial contract and is checked by
`tests/test_tutorials.py`.

| Version | Runnable demonstration |
| --- | --- |
| V0.1 | `python examples/tutorials/v0_1_agent_spine.py` |
| V0.2 | `python examples/tutorials/v0_2_recovery.py` |
| V0.3 | `python examples/tutorials/v0_3_durable_side_effect.py` |
| V0.4 | `python examples/tutorials/v0_4_context_vm.py` |
| V0.5 | `python examples/tutorials/v0_5_resource_handle.py` |
| V0.6 | `python examples/tutorials/v0_6_capability_core.py` |
| V0.7 | `python examples/tutorials/v0_7_process_runtime.py` |
| V0.8 | `python examples/tutorials/v0_8_multi_agent_runtime.py` |

These scripts demonstrate runtime semantics. They do not demonstrate real model
reasoning, production reliability, production security, or superiority over any
other agent system.

## 3. RuntimeBench

RuntimeBench is release evidence, not a beginner tutorial and not a real-model
eval.

Run it with:

```bash
python -m benchmarks.runtimebench --no-write
```

RuntimeBench V0.8 is:

- offline;
- deterministic;
- synthetic;
- local;
- focused on release runtime invariants B1-B8.

It supports scoped mechanism claims such as "the tested WAL/reconcile fixture
does not duplicate the fake external effect" and "the tested multi-agent
fixtures preserve identity and authority boundaries." It does not measure model
intelligence or production workload performance.

## 4. Real-Model Trace Demos

The real-model demos under `examples/real_agent/` are opt-in integration traces.
They are not required by CI and do not run unless explicitly enabled.

Configure them with:

```bash
set AGENTKERNEL_RUN_REAL_MODEL=1
set AGENTKERNEL_LLM_BASE_URL=https://your-openai-compatible-endpoint/v1
set AGENTKERNEL_LLM_MODEL=your-model
set AGENTKERNEL_LLM_API_KEY=your-key
```

`AGENTKERNEL_LLM_API_KEY` is optional for providers that do not require a bearer
token. AgentKernel does not load `.env`, does not hard-code secrets, and does
not fall back to a public endpoint.

Available demos:

| Demo | Scenario | Kernel mechanism exercised |
| --- | --- | --- |
| `basic_tool_trace.py` | A real model receives a tool schema, calls `math.add`, observes the result, and answers. | Tool boundary, authorization, Session facts, Process state |
| `capability_denial_trace.py` | A real model proposes a fake `payment.charge` operation without authority. | Untrusted proposal, Kernel denial, denied ToolResult, Session facts |
| `resource_handle_trace.py` | A real model calls a large-output log tool and observes a bounded ResourceHandle representation. | Tool boundary, Resource externalization, bounded observation, Session facts |

Example:

```bash
python examples/real_agent/basic_tool_trace.py
```

Without `AGENTKERNEL_RUN_REAL_MODEL=1`, the command exits safely with a skip
message.

There is also an optional notebook:

```text
examples/labs/real_model_tool_trace_lab.ipynb
```

It uses the same explicit provider configuration gate and makes no network call
by default.

## 5. Trace Format

The default trace is human-readable. It emphasizes observable runtime facts:

- task;
- agent identity;
- process identity when present;
- session identity;
- model/provider identifier;
- model request summary;
- model-visible tools;
- model response;
- tool call and arguments;
- Kernel authorization decision;
- tool result or ResourceHandle;
- Session events appended;
- process state where applicable;
- final answer.

It does not request or expose private chain-of-thought.

Each real demo also supports an optional machine-readable JSONL artifact:

```bash
python examples/real_agent/basic_tool_trace.py --trace-jsonl traces/basic.jsonl
```

The JSONL trace records observable event summaries only. It does not record API
keys or authorization headers.

## 6. Testing Strategy

| Artifact | Purpose | Mandatory CI? | Network/API key? |
| --- | --- | --- | --- |
| Interactive labs | Human-comprehension notebooks | JSON and deterministic cell validation | V0.1-V0.8 no; real-model lab opt-in |
| Deterministic tutorials | Teaching fixture + Kernel semantics | Yes | No |
| RuntimeBench | Release runtime invariants | Yes | No |
| Real-model traces | Provider integration + visible runtime trajectory | No | Yes, opt-in only |

The real-model demos are intentionally outside the mandatory correctness gate.
Provider availability, latency, model behavior, and cost should not make
`pytest -q` flaky.

## 7. Onboarding Friction Recorded

This documentation pass did not modify `agentkernel/` core code. The trace
demos were built at the application/example layer using existing hooks, model
wrapping, Session events, ToolRegistry authorization checks, ResourceService,
and scheduler objects.

Observed friction to revisit during MiniCode Runtime API Review:

- low-level durable-side-effect tutorials still need explicit WAL event setup
  to demonstrate exact crash points;
- richer trace capture may benefit from a stable public observer surface;
- the real-model trace demos are examples, not a full observability framework.
- interactive labs can expose state with small tables, but a stable public
  observer API would make notebook teaching cells shorter.
