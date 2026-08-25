# V0.4 context compaction source study

This note records the read-only source study used for AgentKernel V0.4 phase 2. It is a design input, not a claim that AgentKernel duplicates or outperforms either runtime.

## DeepSeek Harness

Studied locations:

- `../deepseek-harness-master/docs/subsystems/compaction.md`
- `../deepseek-harness-master/packages/core/session/src/surface.ts`
- `../deepseek-harness-master/packages/core/session/src/index.ts`
- `../deepseek-harness-master/packages/compaction/compaction-basic/src/config.ts`
- `../deepseek-harness-master/packages/compaction/compaction-basic/src/region.ts`
- `../deepseek-harness-master/packages/compaction/compaction-basic/src/summarizer.ts`
- `../deepseek-harness-master/packages/compaction/compaction-basic/src/index.ts`
- `../deepseek-harness-master/packages/compaction/compaction-tool-result-pruner/src/index.ts`

Observed design:

- Session is an append-only event log. Surface derives the model-visible history by applying append/replace operations; replacement shadows a current surface range without deleting raw events.
- TokenMeter owns pressure accounting. The basic defaults trigger near 80% of context and retain a recent tail near 16%.
- Deterministic Tool Result pruning runs before semantic summary, then pressure is measured again. Pruning keeps head/middle-marker/tail.
- Range selection is older/head anchored, retains a recent tail, and adjusts boundaries so Tool Call/Result pairs remain balanced.
- Compaction persists start, generated Summary/replacement, and completion records. An unmatched start detects a crash. A slow summary is committed only after selected/surface state remains valid.
- The Summary prompt produces a structured handoff checkpoint rather than a literary conversation recap. The implementation can reuse the unchanged conversation prefix for model cache efficiency.
- Context-overflow recovery retries only after the visible replacement generation advances, preventing an unchanged infinite retry loop.

## OpenAI Codex

Studied locations:

- `../codex-main/codex-rs/core/src/context_manager/history.rs`
- `../codex-main/codex-rs/core/src/session/mod.rs`
- `../codex-main/codex-rs/core/src/session/rollout_reconstruction.rs`
- `../codex-main/codex-rs/core/src/compact.rs`
- `../codex-main/codex-rs/core/src/compact_remote.rs`
- `../codex-main/codex-rs/core/src/compact_remote_v2.rs`
- `../codex-main/codex-rs/core/src/session/turn.rs`
- `../codex-main/codex-rs/utils/output-truncation/src/lib.rs`
- `../codex-main/codex-rs/utils/string/src/truncate.rs`
- `../codex-main/codex-rs/core/src/tools/context.rs`

Observed design:

- The rollout is durable append-only state, while `ContextManager.items` is the versioned model history that may be replaced.
- Conversation recording can persist prepared full Tool output in the rollout while storing a normalized/truncated version in live history. Tool output uses middle truncation with an explicit marker and token/character accounting.
- Compaction has local prompt-based and server `/responses/compact` paths chosen by Provider capability.
- `replace_compacted_history` persists a `CompactedItem` with replacement history. Resume reverse-scans for the latest replacement checkpoint, then replays its tail.
- Persisted provenance is chiefly the replacement-history snapshot rather than AgentKernel-style explicit source Page/event ranges; trace data can retain input/replacement details.
- Compaction may trigger before a turn or mid-turn. Ordinary context overflow marks the window full; local compaction itself can discard the oldest item and retry its compaction request.
- Multi-agent context is explicitly isolated by fork modes (`none`, `all`, or a bounded recent prefix), so child execution does not implicitly share an unbounded mutable history.

## Comparison and AgentKernel decision

| Question | DeepSeek Harness | Codex | AgentKernel phase 1 | Phase 2 decision |
|---|---|---|---|---|
| Durable truth | Append-only Session | Append-only rollout | Append-only Session events | Keep Session as the sole raw truth |
| Model-visible state | Surface operations | Versioned ContextManager history | Context Pages + Working Set | Keep Pages/Working Set; add completed-summary shadow folding |
| Pressure owner | TokenMeter/compaction capability | Context manager/turn thresholds | Explicit input budget only | Add explicit `ContextPressure`; keep actions in replaceable policy |
| Cheap reclaim | Tool result pruner | Middle truncation | Eviction | Evict, then deterministic head/marker/tail pruning |
| Semantic compaction | Surface range replacement | Local/server replacement history | None | `ContextCompactor` through `LLMService` |
| Range safety | Retained tail + tool-pair boundaries | History normalization/replacement | Atomic groups/dependencies | Reuse atomic groups, pinned constraints, configurable retained tail |
| Durability | Lifecycle/replacement events | `CompactedItem` checkpoint | Working set reconstructed | Persist Summary lifecycle/provenance, not ordinary working sets |
| Crash behavior | Unmatched start + revalidation | Resume from latest checkpoint | JSONL replay analysis | Only completion activates Summary; active lifecycle is interrupted |
| Rolling behavior | Surface can be replaced again | Latest replacement checkpoint wins | None | S1 + newer old Pages → S2 with parent provenance |
| Provenance | Replacement region/surface records | Replacement snapshot/trace | Page event sequence | Explicit source Page IDs, leaf event sequences, costs, hash, parent |
| Provider coupling | Service/capability seams | Provider-selected local/remote | Provider-neutral `LLMService` | Keep Core provider-neutral; defer overflow taxonomy/retry |
| Multi-agent context | Separate subsystem | Explicit fork modes | Out of scope | Remains out of V0.4 phase 2 |

AgentKernel deliberately does not clone DeepSeek Surface or Codex replacement-history objects. Its existing Context Pages and atomic dependency closure already provide the smallest useful replacement boundary. The independent value being tested is the composition of event-sourced truth, durable side-effect recovery, provider-neutral tool atomicity, and auditable Summary provenance in one compact Python kernel.
