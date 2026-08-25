# AgentKernel Runtime Benchmark Suite v0.1

This suite measures AgentKernel runtime mechanisms. It is offline by default and
does not call model providers, external APIs, or network services.

It is not a model benchmark. It does not compare GPT, Claude, Gemini, Codex, or
any other agent product. The benchmarks isolate four runtime properties:

- Context Management
- Crash Recovery
- Durable Execution
- Resource Management

## Commands

Run one benchmark:

```bash
python -m benchmarks.resource_handle.runner
python -m benchmarks.durable_tool.runner
python -m benchmarks.recovery.runner
python -m benchmarks.context_vm.runner
```

Run the whole suite:

```bash
python -m benchmarks.run_all
```

Outputs are JSON arrays of records with this shape:

```json
{
  "benchmark": "resource_handle",
  "case": "100MB_tool_result",
  "strategy": "artifact_handle",
  "metrics": {
    "context_bytes": 12345,
    "resource_bytes": 104857600,
    "latency_ms": 2.5,
    "success": true
  }
}
```

Default result files are written under `benchmarks/results/`:

- `resource.json`
- `durable_tool.json`
- `recovery.json`
- `context_vm.json`
- `all.json`

## Benchmark Notes

The Resource Handle benchmark uses real `ResourceService`, `LocalResourceStore`,
`ResourceHandle`, and model-facing `resource_read` tool definitions for the
Artifact Handle strategy. Full-history and pruning strategies simulate
model-visible context growth without writing huge transcripts into Session JSONL.

The Durable Tool benchmark uses a deterministic fake payment service and a real
AgentKernel WAL prefix. Recovery is driven through `operation_id` and reconcile.

The Crash Recovery benchmark writes JSONL Session prefixes and reloads them to
measure replay classification.

The Context VM benchmark uses a 1000-turn deterministic fixture and `ScriptedLLM`
for compaction. It measures projection, pruning, compaction, working-set
selection, and replayable compaction events.
