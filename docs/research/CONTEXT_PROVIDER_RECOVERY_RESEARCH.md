# V0.4 phase 3 provider accounting and recovery source study

This note records the read-only source study performed before phase 3 implementation. It distinguishes observed source behavior from AgentKernel design decisions.

## Gemini CLI

Studied local paths under `../gemini-cli-main`:

- `packages/core/src/utils/tokenCalculation.ts`: `estimateTokenCountSync()` and `calculateRequestTokenCount()`.
- `packages/core/src/core/contentGenerator.ts`: `ContentGenerator.countTokens()`.
- `packages/core/src/core/tokenLimits.ts`: `tokenLimit()` model limits.
- `packages/core/src/context/chatCompressionService.ts`: `ChatCompressionService.compress()`, `findCompressSplitPoint()`, `truncateHistoryToBudget()`.
- `packages/core/src/context/toolOutputMaskingService.ts`: `ToolOutputMaskingService.mask()` and tool-output preview/file storage.
- `packages/core/src/context/processors/toolMaskingProcessor.ts`: graph-context Tool output masking.
- `packages/core/src/context/contextManager.ts`: trigger evaluation and `performHotStartCalibration()`.
- `packages/core/src/core/client.ts`: preflight context management, request estimation, `ContextWindowWillOverflow`, and `tryCompressChat()`.
- `packages/core/src/utils/retry.ts`: `retryWithBackoff()`, `isRetryableError()`, and bounded transient retry.
- `packages/core/src/availability/errorClassification.ts`: quota/model-availability classification.

Observed behavior:

- Text and function payloads normally use a deterministic local estimate. ASCII is conservatively weighted, non-ASCII is more expensive, function data is recursively/JSON counted, and media has fixed/local estimates. `calculateRequestTokenCount()` uses Provider `countTokens` for media and falls back locally when that call fails.
- The ContentGenerator interface exposes Provider/model-specific `countTokens`. Context Manager hot-start calibration can compare local estimates with LLM token ground truth. Model windows come from `tokenLimit(model)` rather than a Context Manager hard-coded constant.
- Request calculation includes contents and tools. System/history contents and Tool definitions therefore contribute separately, although the implementation is not presented as an exact billing replica for every endpoint.
- Chat compression normally triggers at a configurable fraction of model capacity (default observed as 0.5). It keeps roughly 30% of the latest history, splits at a Tool-safe boundary, summarizes older history, probes the summary, and records before/after token telemetry.
- `truncateHistoryToBudget()` scans recent function responses first under a 50K budget. Older oversized Tool responses are reduced to their final lines and the full output is saved to a temporary file.
- `ToolOutputMaskingService` protects the latest turn and roughly 50K newest Tool tokens. When enough older output is prunable, it stores the full value in a session Tool-output file and provides a head/tail/error-aware preview and path to the model.
- Client preflight estimates the pending request against remaining model context. When it predicts overflow, it emits `ContextWindowWillOverflow` instead of sending the request. Failed/inflating compression is remembered to avoid repeated ineffective summarization.
- `retryWithBackoff()` is bounded (default maximum attempts observed as 10) for quota, capacity, server, and network failures. HTTP 400 is explicitly non-retryable. No Provider-400 context-overflow-to-compress-and-retry path was found in the studied client; its primary protection is proactive accounting/compression.

AgentKernel adopts the model-limit/accounting seam, complete-request thinking, recent-result preference, token telemetry, and bounded guards. Temporary Tool-output files/path handles belong to V0.5 virtual resources and are not copied into phase 3.

## OpenHands

The supplied local `../OpenHands-main` is Agent Canvas, not the core runtime. Local evidence includes:

- `src/types/agent-server/core/events/condensation-event.ts`: durable wire shape for `Condensation`, `CondensationRequest`, and summary events.
- `src/hooks/use-compact-context-action.ts`: explicit `/condense` request and metrics refresh.
- `src/hooks/use-await-context-compaction.ts`: waits for a new Condensation event plus reduced `per_turn_token`, with a 90-second terminal timeout.
- `src/components/features/conversation/usage-panel/context-meter.tsx`: warning/danger UI at 70%/90% context fill.
- `README.md` and `docs/architecture.md`: identify `OpenHands/software-agent-sdk` agent-server as the runtime.

The runtime study therefore followed that repository's official source paths (also cloned read-only under `../.tmp/software-agent-sdk`):

- `openhands-sdk/openhands/sdk/context/view/view.py`: `View.from_events()`, `append_event()`, and property enforcement.
- `openhands-sdk/openhands/sdk/event/condenser.py`: `Condensation.apply()` and deterministic summary-event identity.
- `openhands-sdk/openhands/sdk/context/condenser/base.py`: `RollingCondenser`, hard/soft requirements, and hard reset fallback.
- `openhands-sdk/openhands/sdk/context/condenser/llm_summarizing_condenser.py`: `LLMSummarizingCondenser`.
- `openhands-sdk/openhands/sdk/context/condenser/utils.py`: `get_total_token_count()` and token-reduction binary search.
- `openhands-sdk/openhands/sdk/agent/utils.py`: `prepare_llm_messages()` / `aprepare_llm_messages()`.
- `openhands-sdk/openhands/sdk/agent/agent.py`: `Agent.step()` / `astep()` overflow handling.
- `openhands-sdk/openhands/sdk/llm/exceptions/classifier.py`: `is_context_window_exceeded()`.
- `openhands-sdk/openhands/sdk/llm/exceptions/mapping.py`: `map_provider_exception()`.
- `openhands-sdk/openhands/sdk/llm/exceptions/types.py`: `LLMContextWindowExceedError` and distinct failure types.
- `openhands-sdk/openhands/sdk/conversation/event_store.py` and `conversation/state.py`: file-backed Event Log, cold-load `View.from_events()`, and view rebuild.
- `openhands-sdk/openhands/sdk/conversation/stuck_detector.py`: context-window stuck detection is currently a TODO.

Observed behavior:

- Full conversation truth is an append-only Event Log. `View.from_events()` applies Condensation tombstones in order, inserts generated summary events, and enforces Tool/history properties. `prepare_llm_messages()` gives the cached View to the condenser, then converts only the resulting LLM-convertible events.
- `LLMSummarizingCondenser` triggers on an unhandled request, token limit, or event count. It uses the stricter of configured `max_tokens` and the agent LLM's effective input limit. `get_total_token_count()` converts all events to messages and passes first-system-event Tool definitions plus security-risk schema additions to the LLM tokenizer.
- Token/request reasons are hard; event-count maintenance is soft. The normal target keeps early `keep_first` events and roughly half-sized recent history while honoring View manipulation boundaries. The production helper currently uses max size 80 and keep-first 4.
- Provider exceptions are mapped centrally. Typed LiteLLM context errors and a bounded provider phrase list become `LLMContextWindowExceedError`; rate limit, timeout, authentication, service unavailable, content policy, malformed history, and generic bad request remain distinct.
- On overflow, `Agent.step()` appends a `CondensationRequest` and returns. A later agent iteration consumes the request, appends a durable `Condensation`, and a later iteration samples from the condensed View. This is a multi-step recovery protocol, not an in-call retry loop.
- A hard reset summarizes the whole View. Summary failure is retried at most five times while each event's rendered maximum is multiplied by 0.8. This guards summary generation, but the studied stuck detector still does not implement a global repeated-overflow loop guard.
- Condensers must treat View as read-only. Condensation is appended to history and shadows forgotten IDs only in reconstructed View; original events remain persisted. Cold load/restart reconstructs the same View by replaying Event Log plus Condensation events.
- No separate phase-3-style head/tail Tool Result pruner was found in the condenser. View manipulation properties preserve Tool Call/Result structure. Coding scenarios in examples include file editing, terminal use, GitHub review/debugging, failing tests, large diffs, and multi-step repair; SWE-style bug repair is therefore a useful benchmark shape.

AgentKernel adopts central typed classification, complete messages+tools accounting, append-only condensation recovery, and bounded hard recovery. It uses an in-step exactly-once retry guard because V0.3 Tool WAL makes the distinction between model-call retry and whole-step replay important.

## Horizontal comparison

| Question | Gemini CLI | OpenHands | DeepSeek Harness | Codex | AgentKernel before phase 3 |
|---|---|---|---|---|---|
| Token accounting | Hybrid local estimate; Provider count for media/calibration | LLM/LiteLLM tokenizer over converted messages | TokenMeter over Surface/model requests | Context-manager/model token accounting | Page text approximation |
| Output reserve | Remaining tokens against model limit | Effective max input/output model metadata | Trigger/retained ratios | Model/Provider turn thresholds | Explicit `reserved_output_tokens` |
| Tool schema accounting | Request tools included | `get_total_token_count(..., tools=...)` | Model request/surface accounting | Tool definitions included in request accounting | Missing from Page total |
| Large Tool output | Recent-first budget; old output truncation and temp file | No independent condenser pruner found | Deterministic Tool-result pruner before summary | Middle truncation with marker | Head/marker/tail Page pruning |
| Compression trigger | Proactive fraction/remaining request | Request, token limit, event count | TokenMeter near configured ratio | Before/mid-turn thresholds | Pressure state; compact at overflow |
| Provider overflow | Mainly preflight event; 400 non-retryable | Typed classification → CondensationRequest | Recovery after Surface generation advances | Window-full/compaction paths | No normalized category |
| Reclaim then retry | No studied 400 recovery loop | Across later agent iterations | Yes after changed replacement | Compaction-specific retries/next turn | No |
| Retry upper bound | Transient retry max attempts; failed-compression state | Hard-summary max 5; global stuck TODO | Replacement-generation guard | Bounded implementation paths | N/A |
| Raw history retained | Chat history replaced; Tool files externalized | Append-only Event Log retained | Append-only Session retained | Durable rollout retained | Append-only Session retained |
| Recovery | Compression state/telemetry | Replay Condensation tombstones into View | Lifecycle/replacement replay | Latest compacted checkpoint + tail | Completed Summary lifecycle replay |
| Benchmark method | CLI integration/evals and telemetry | Coding/file/terminal/GitHub scenarios | Harness tests and context fixtures | Coding-agent eval infrastructure | Offline 200-turn resource fixture |

## Phase 3 decision

AgentKernel adds:

1. Complete-request, provider-neutral accounting with a stable offline fallback and optional adapter/model limits.
2. Typed LLM failure taxonomy with provider text/code parsing confined to the adapter boundary.
3. Context-owned forced reclaim to the existing policy's safety target.
4. A strict proof that the rebuilt request is smaller, followed by at most one Provider retry.
5. Optional Provider usage diagnostics and opt-in real quality/resource benchmark.

It deliberately does not add external Tool-output files, artifact handles, RAG, memory retrieval, a model registry, multi-Provider routing, or a new mutable history store.
