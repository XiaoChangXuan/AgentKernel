# AgentKernel

AgentKernel is a minimal trusted runtime spine for tool-using agents. It owns the protocol, event log, process metadata, capability checks, budgets, and tool execution boundary; an LLM only proposes tool calls and answers.

It is not a general-purpose Agent Framework. Framework-level prompts, business tools, workflows, memory products, UIs, and provider integrations belong above or outside the Kernel. AgentKernel concentrates on the small set of mechanisms that must remain enforceable even when model output is incorrect or hostile.

## Kernel and user boundary

```text
Untrusted / policy layer                 Trusted mechanism layer

User input ───────────────┐
LLM decisions             ├──> DefaultAgentLoop
Business Tool handlers ───┘         │
                                    ├── AgentControlBlock + budgets
                                    ├── Session append-only Event Log
                                    └── ToolRegistry capability boundary
```

The model receives provider-neutral messages and `ToolSchema` values. It never receives Tool handlers, credentials, capabilities, timeouts, or mutable Session state.

## V0.1 architecture

```text
User
  ↓
Agent + AgentControlBlock
  ↓
DefaultAgentLoop
  ├── PromptService
  ├── Session ──> append-only Event Log ──> derive_messages()
  ├── LLMService / ScriptedLLM
  └── ToolRegistry
        ├── model schema projection
        ├── capability check
        ├── host execution
        └── structured ToolResult
```

The implemented deterministic flow is:

```text
User → ScriptedLLM → math.add → ToolResult(42) → ScriptedLLM → Final Answer
```

## Run the example

Python 3.11 or newer is required. The runtime has no third-party dependencies.

```bash
python examples/basic_agent.py
```

The command prints `The result is 42.` followed by the complete Session Event Log.

## Run the tests

Install the test extra once, then run pytest:

```bash
python -m pip install -e ".[test]"
python -m pytest
```

## Current stage

V0.1 provides an in-memory, single-agent spine with provider-neutral protocol types, deterministic model testing, append-only Session events, derived model history, capability-bounded tools, structured failures, lifecycle hooks, process states, and hard step/tool-call budgets.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for implemented behavior and [`docs/IMPLEMENTATION_BLUEPRINT.md`](docs/IMPLEMENTATION_BLUEPRINT.md) for the longer roadmap.

## Next stage

V0.2 adds Persistence + Recovery behind a `SessionPersistence` interface, beginning with JSONL and crash/replay tests. SQLite and durable side-effect reconciliation remain separate follow-up work.

