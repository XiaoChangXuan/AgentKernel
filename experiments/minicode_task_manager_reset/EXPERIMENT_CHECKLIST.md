# MiniCode Task Manager Experiment Checklist

Record facts from MiniCode JSON output, `.minicode/session.jsonl`, and `.minicode/trace.jsonl`. Do not invent values.

## Run Metadata

| Field | Value |
| --- | --- |
| final task status | |
| session id | |
| process id | |
| total model turns | |
| total tool calls | |
| resume count | |
| final pytest result | |

## Tool Calls

| Metric | Value | Where to check |
| --- | --- | --- |
| search calls | | `.minicode/trace.jsonl`, `tool/call` events |
| read calls | | `.minicode/trace.jsonl`, `tool/call` events |
| apply_patch calls | | `.minicode/trace.jsonl`, `tool/call` events and session `tool/prepare` events |
| run_command calls | | `.minicode/trace.jsonl`, `tool/call` events |
| pytest failures before success | | `run_command` outputs in trace/resource handles |

## Runtime Signals

| Metric | Value | Where to check |
| --- | --- | --- |
| Session event count | | line count in `.minicode/session.jsonl` |
| ResourceHandle count | | trace/session tool results containing resource handles |
| PREPARE count | | session events with `tool/prepare` |
| DISPATCH count | | session events with `tool/dispatch` |
| COMMIT count | | session events with `tool/commit` |
| capability denial count | | trace/session events with denied authorization or `EACCES` |
| process count | | MiniCode JSON output and trace process ids |

## Notes

- If a value is not directly visible through current CLI output, inspect the session and trace artifacts listed above.
- This checklist is for manual observation only; it is not a deterministic benchmark artifact.
