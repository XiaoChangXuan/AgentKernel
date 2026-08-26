# Kernel Architecture Review

## 1. Purpose

本审查冻结 AgentKernel V0.1～V0.4 的现有内核边界，并判断它是否能够在不推翻核心语义的前提下支持 V0.5～V0.8。审查只处理结构性风险，不实现 Virtual Resource、Namespace、Scheduler、Subagent、Memory 或 Plugin Runtime。

判定标准是：如果某个问题不在 V0.5 前处理，是否必然导致 V0.1～V0.4 的核心接口被推翻。代码和测试是证据；操作系统和数据库类比只用于解释已经存在的机制。

结论：**没有 BLOCKER BEFORE V0.5。当前架构 READY FOR V0.5。** V0.5 可以作为新的 Kernel Service 小步加入，现有 Tool、Session、Durable Tool 和 Context VM 接口无需先重写。V0.6～V0.8 前存在若干应处理的中期债务。

## 2. Current V0.1-V0.4 Architecture

真实调用链是：

```text
Agent / AgentControlBlock
        ↓
DefaultAgentLoop
        ├─ PromptService ── ToolRegistry.model_schemas()
        ├─ Session ── SessionPersistence
        ├─ ContextService
        │    Session → ContextProjector → ContextPage
        │            → ContextPolicy → Working Set
        │            → Pressure / Reclaim / Compaction
        ├─ RequestTokenAccounting
        ├─ LLMService → Provider adapter
        └─ DurableToolExecutor
             → ToolRegistry authorization
             → mutation WAL in Session
             → ToolDefinition handler
```

实际边界：

- `protocol.py` 是 Provider-neutral 的模型与 Tool ABI。
- `AgentControlBlock` 持有 agent/session identity、生命周期、capability 和 per-turn budget。
- `DefaultAgentLoop` 组织一个 Turn，强制 step/tool-call budget 和状态迁移。
- `Session` 是 append-only journal 的语义所有者，存储由 `SessionPersistence` 提供。
- `RecoveryAnalysis` 从 Session Event Log 重放 Turn/Step、Tool WAL 和 Compaction lifecycle。
- `ToolRegistry` 是模型 syscall table 与第一次 capability enforcement boundary。
- `DurableToolExecutor` 给 mutation 建立稳定 operation identity 和 WAL boundary。
- `ContextManager` 从 Session 派生一次 ModelRequest 的物理 Working Set。
- `LLMService`/Provider adapter 隔离 wire protocol、Provider error 和 usage。

V0.1～V0.4 的已实现边界不是纯文档类比：capability denial、budget、append ordering、WAL flush、Context mandatory closure、Tool atomicity 和 overflow retry 都由代码及测试强制。

## 3. OS / Database Mapping

| Version | Real system problem | Closest system mechanism | Code evidence |
|---|---|---|---|
| V0.1 | 不可信模型如何安全驱动 Tool | process execution、user/kernel boundary、syscall table | `AgentControlBlock`, `DefaultAgentLoop`, `ToolRegistry`, provider-neutral protocol |
| V0.2 | crash 后如何知道已发生什么 | journal、replay、crash recovery | append-only `Session`, `SessionPersistence`, `analyze_recovery()` |
| V0.3 | 外部副作用在 crash 边界如何对账 | WAL、I/O completion、idempotency、reconciliation | `tool/prepare → dispatch → commit/abort`, stable `operation_id` |
| V0.4 | 总历史大于模型窗口时如何构造当前请求 | working set、pressure、reclaim、page-in | Context Page、Working Set、pruning、durable compaction、request accounting |

类比的边界：AgentKernel 没有实现内核线程、inode、MMU、TLB、POSIX、通用事务或 cgroup。每个机制只对应一个可观察的 Agent runtime 问题。

## 4. Trusted Kernel Boundary

### 4.1 Trusted Kernel

必须由可信代码拥有且不能交给 Model/Plugin 的部分：

- Agent/process identity 与合法 lifecycle transition；
- effective capability 不超过 bounding set；
- Event identity、append ordering、immutable history 和 replay integrity；
- Tool/syscall authorization boundary；
- Kernel-owned durable operation identity；
- mutation prepare/dispatch/commit/reconcile 不变量；
- hard budget enforcement 和未来 Resource accounting admission；
- mandatory Context、Tool atomicity、最多一次 overflow retry 等安全不变量。

### 4.2 Kernel Services

可以演进但仍运行在可信边界内：

- Session / Recovery service；
- Context VM；
- Tool I/O / Durable Tool service；
- V0.5 Resource Service；
- future Process Manager 和 accounting coordinator；
- persistence coordinator。

### 4.3 Policies

必须保持可替换：

- Context selection 和 reclaim policy；
- future scheduler policy；
- approval/delegation policy；
- model choice、resource retention、Memory retrieval policy。

Policy 可以做选择，不能绕过 capability、budget、event integrity 或 WAL。

### 4.4 Drivers / Providers

- LLM Provider adapter；
- Session persistence driver；
- future Resource Driver；
- future Memory backend、sandbox/browser/network driver。

Driver 实现 I/O，不决定 caller 是否有权限，也不拥有 Agent lifecycle。

### 4.5 User-space Agent capabilities

- Prompt、Skill、Workflow、业务 Tool；
- Memory/RAG product policy；
- application-specific Resource adapters；
- UI、Plugin、Agent profile。

## 5. Mechanism vs Policy

| Area | Mechanism in current code | Policy | Status / risk |
|---|---|---|---|
| Context selection | dependency closure、atomic group、pin enforcement、budget failure | `ContextPolicy` 的 priority/temperature/pin | GREEN |
| Reclaim | eviction、pruning、compaction、forced reclaim | `ContextReclaimPolicy` 和 pressure thresholds | GREEN |
| Provider overflow | normalized error、strictly-smaller check、retry ceiling | safety target 来自 Context config | GREEN |
| Tool execution | resolve、authorize、timeout、invoke、failure normalization | 注册哪些 Tool、超时值、effect declaration | GREEN |
| Durable mutation | operation identity、WAL transitions、legal retry/reconcile | host 决定何时对 interrupted operation 采取动作 | GREEN |
| Agent scheduling | state transition 和 WAITING mechanism | loop 固定单 Agent、顺序 Tool、无 scheduler policy seam | YELLOW；V0.7 前拆分 |
| Budget | step/tool-call hard checks | 限额值来自 `AgentBudget` | YELLOW；计数只存在于单次 `run()` 局部变量 |
| Capability | exact-name check 和 bounding invariant | capability naming/scope 尚未结构化 | YELLOW；V0.6 前扩展 |

`DefaultAgentLoop` 仍然足够薄，但“顺序执行一个 Agent 的一个 Turn”同时承担了 V0.1 reference policy。V0.7 应保留该 reference loop，同时让 Process Manager/Scheduler 在它之外管理 runnable work，不应把 scheduling branches 塞进 Loop。

## 6. Durable Truth vs Model View

| Durable or authoritative value | Model-visible projection | Invariant |
|---|---|---|
| Session Event Log | `Message` history | Message 不是第二份 truth |
| raw `ToolResult` event | pruned Tool Result Page | pruning 不修改 raw result |
| raw Pages + completed compaction lifecycle | Summary Page | Summary 有 provenance，但不是事实源 |
| all projected Pages | Working Set | eviction 不是 deletion |
| host ToolDefinition/capability/handler | ToolSchema | Model 看不到 handler、credential 或 permission metadata |
| future Resource bytes/metadata | preview + Artifact Handle | preview 不是 Resource truth |
| future Memory record | Memory Page | Page 是有界 projection |
| future Subagent process state | structured result + Artifact refs | 不复制 child 全历史 |

这个分离原则可自然延伸到 V0.5。Resource Store 应保存原始 bytes/metadata；Session 只记录稳定 handle、preview 和相关操作事实；Context VM 继续选择模型可见投影。

## 7. ContextPage Future Compatibility

### 7.1 Current

`ContextPage` 不完全等于 `Message`：SYSTEM Page 没有 Message，Summary 是派生 Page。但当前实现仍明显是 **Session Message Page model**：

- USER/ASSISTANT/TOOL_RESULT kind 必须携带 `Message`；
- Summary 被渲染为 `Message.user()`；
- `ContextProjector.project()` 只接受 `Session + system_prompt`；
- raw `page_id` 使用 `session:{id}:event:{seq}`；
- `created_seq`、`turn` 和默认 recency policy 绑定 Session ordering；
- `ContextWorkingSet.to_messages()` 是唯一 rendering seam。

`dependencies` 和 `atomic_group` 是通用字符串 identity closure，未绑定 Tool 类型，可以复用于 Resource preview/handle、Skill bundle 或 Subagent result 的整体选入。

### 7.2 Compatibility assessment

| Future source | Can current V0.4 represent it? | Required change |
|---|---|---|
| Resource | 是；V0.5 handle/preview 可作为 structured Tool Result 进入 Session | SMALL EXTENSION；无需先增加 PageSource |
| Memory | 可以通过显式 Tool Result 注入；独立自动 projection 不自然 | MEDIUM REFACTOR：composite projector/source ordering |
| Skill | system prompt 或 Session user projection 可表达；独立 pin/page lifecycle 不自然 | MEDIUM REFACTOR |
| Project Rule | 当前 system prompt 可表达一个整体；细粒度 identity/page-in 需要新 source | MEDIUM REFACTOR |
| Subagent Result | structured Tool/IPC result 可表达；直接共享 child pages 不应支持 | SMALL/MEDIUM，取决于 IPC boundary |

### 7.3 Risk

现在增加通用 `PageSource`、`SourceDescriptor` 或任意 renderer 会是 premature abstraction：V0.5 的真实需求是外置大型 Tool Result，而不是让所有 Resource 自动成为 Context Page。

在独立 Memory/Skill/Rule projection 前，应引入一个最小 composite projection seam，并解决：

- source-qualified identity；
- 跨 source 的稳定 ordering，而不是假设一个 Session seq；
- source-specific default policy metadata；
- Page 到 provider-neutral Message 的显式 renderer；
- pin/page-in state 的 agent/session ownership。

这属于 **SHOULD FIX SOON，但不阻塞 V0.5**。

## 8. Tool / Resource / Driver Boundary

### 8.1 Current

`ToolDefinition` 同时保存 model schema、handler、required capability、timeout、concurrency、effect kind 和 reconcile callback。职责较多，但都属于一个 host syscall entry 的 runtime descriptor；Model-facing `ToolSchema` 仍被正确分离。

`ToolRegistry` 是 syscall table：负责 name resolution、schema projection、coarse capability check 和 handler dispatch。`ToolExecutionContext` 已携带 caller `agent_id`、`session_id`、`tool_call_id`、Kernel `operation_id` 和 attempt，足以让未来 Resource Service 识别 caller 和 durable operation。

### 8.2 Future seam

```text
LLM
 ↓ ToolCall
ToolRegistry / DurableToolExecutor       coarse syscall authorization + WAL
 ↓ trusted handler
Resource Service                        handle ownership + action/scope/budget check
 ↓
Resource Driver                         bytes / backend I/O only
 ├─ File Driver
 ├─ Artifact Driver
 └─ later Session / Memory / Web Driver
```

Resource Driver 应位于 Tool 之下，而不是让每个 Driver 成为特殊 Kernel Tool。少量稳定 Tool（例如 `resource.stat/read/write/close`）构成 user-space syscall API；Resource Service 统一 handle validation、range limit、quota、owner/capability enforcement；Driver 只实现后端操作。

Resource Layer 不应信任 `ToolRegistry` 的第一次检查。未来需要两次 enforcement：

1. ToolRegistry：caller 能否调用 `resource.read`；
2. Resource Service：该 caller 能否对这个 handle/URI 执行 `read`，range 和 bytes 是否在 budget 内。

`DurableToolExecutor` 无需理解 Resource：read 仍可 `READ_ONLY`；resource write/create/delete 按真实 effect 使用 idempotent/reconcilable/opaque mutation contract。稳定 `operation_id` 可传给 Resource Service/Driver。

不要把 capability object、driver object 或 raw credential 暴露给 Model。`ToolExecutionContext` 将来更适合携带一个受限的 Kernel service façade 或 caller identity token，而不是可修改的 capability set。

### 8.3 Targeted reference check

小范围参考显示 DeepSeek Session Surface 同样保持 Event Log 与 model-visible surface 分离；Codex/Gemini 也通过 Tool invocation/handler 暴露资源读取，而不是让 Provider 或 Context 直接拥有资源后端。这支持上述 seam，但 AgentKernel 的 durable operation 和 capability invariants仍以本仓库代码为准。

## 9. Agent → Process Compatibility

| Question | Current answer | Change level |
|---|---|---|
| Agent identity 与 Session identity 是否混合 | 字段分离，但 `Agent.__post_init__` 强制一对一 matching | MEDIUM before multi-session process |
| 一个 Agent 是否可拥有多个 Session | 当前不可以；live `Agent` 只有一个 `session` | MEDIUM |
| Session 是否默认只有一个 Agent | header 不记录 agent，但 ACB/session binding 与 loop 语义实际上按单 Agent 使用 | MEDIUM |
| 生命周期是否可扩展 | 已有 NEW/READY/RUNNING/WAITING/PAUSED/FAILED/EXITED | SMALL |
| Loop 是否绑定具体 Agent | `run(agent, input)` 参数化良好，但一次只运行一个 Turn | SMALL for reuse；MEDIUM for scheduler integration |
| Context 是否适合作为 per-process resource | 接口按 Session 构建；manager 内有 mutable pin/request sets，应明确 instance ownership | MEDIUM before concurrent agents |
| Capability/Budget 是否可挂 Process | 已经位于 ACB | SMALL structurally |
| Tool 是否知道 caller | `ToolExecutionContext` 有 agent/session identities | SMALL |
| Recovery 恢复什么 | 恢复 Session lifecycle/Tool operation/Compaction，不恢复 ACB process state | MEDIUM before V0.7 |

总体判定：**MEDIUM REFACTOR，不是 MAJOR REWRITE。** ACB 的 identity/state/bounds 已经存在；V0.7 主要需要把 1:1 live binding、process-state persistence、pending I/O、usage snapshot 和 scheduler ownership显式化。

## 10. Capability / Namespace Compatibility

当前 capability 是 exact `frozenset[str]`，每个 Tool 最多声明一个 `required_capability`。Bounding Set 只保证集合包含关系；`Agent.create(parent_agent_id=...)` 不读取 parent，也不验证 child bounding set 是否被 parent 委派。

这足以保护当前 Tool-name 能力，但不能表达：

```text
action=read, resource=file://workspace/**
deny action=read, resource=file://home/user/.ssh/**
```

V0.6 需要 structured capability/value object、scope matcher、deny/constraint semantics 和 delegation check。兼容路径是让旧字符串 capability 成为 exact tool-action capability 的一种表示，而不是删除 ToolRegistry authorization。

Namespace 应作用于 Resource Service 的 name/URI resolution 和 handle table；Tool namespace 仍由 ToolRegistry 提供。Resource Service 必须执行第二次 resource-scoped authorization，Driver 不应自行解释高层 capability policy。

判定：V0.5 可先用明确的 internal owner/action checks 和窄 Tool capability；完整 V0.6 是 **MEDIUM REFACTOR**。

## 11. Resource Accounting

当前 accounting 分散在三个位置：

- `AgentBudget`：每 Turn steps/tool calls hard limits；usage 只在 `DefaultAgentLoop.run()` 局部变量；
- `ContextBudget` / `ContextMetrics`：单次物理 context；
- `RequestTokenAccounting` / `ModelUsage`：单次 model request estimate/actual。

优点是每个现有有限资源都在使用点附近被计量和限制。缺口是没有统一、可查询、可持久恢复的 process `ResourceUsage` snapshot；money、wall time、model calls、network/resource bytes 和 child count 也没有共同 admission interface。

V0.5 可以在 Resource Service 内先计量 `resource_bytes_read/written`、storage bytes 和 open handles，并把 admission 放在 Driver I/O 之前。不要现在造 cgroup。

V0.7 Scheduler 前应建立统一 accounting service：

```text
reserve/admit → perform → commit actual usage / release reservation
                         ↓
                immutable UsageSnapshot
```

Scheduler 应只消费 process state、priority 和 UsageSnapshot，不重复计算 Token/Tool/Resource usage。现有 Context/Request 计量应成为 controller 输入而不是被重写。

判定：V0.5 不阻塞；V0.7 前 **SHOULD FIX SOON / MEDIUM REFACTOR**。

## 12. Event Log Extensibility

`SessionEvent` envelope（seq/type/data/time）足够通用，但 `EventType` 是 closed enum，`analyze_recovery()` 是一个集中式状态机，强假设 Turn/Step/Message/Tool/Compaction。未知 event 会导致 corruption；Tool WAL event 必须位于原 prepared Turn/Step。

因此：

- 给现有 Session 增加少量、明确语义的 Resource reference event 是 SMALL/MEDIUM extension；
- 把 process/IPC/resource lifetime 的全部事件直接塞进当前 validator 会快速扩大单体状态机；
- 将 Session Event Log 升级成整个 Kernel 的 global event log 会破坏其局部 conversation journal 职责。

推荐边界是 **A：Session Event Log 继续作为每 Agent/Session 的局部 journal**。只记录影响该 Session replay/model truth/Tool recovery 的事实。未来 Process Manager、Resource Store 或 IPC 可有各自 journal/metadata store，并用 stable IDs/correlation IDs 关联；是否需要上层 audit stream 留到真实需求出现时决定。

在 V0.7/V0.8 前，应把 recovery validator 拆成共享 envelope/order validation 加可组合的 Session lifecycle、Tool operation、Compaction validators，但不要现在改 EventType 或创建 Global Kernel Event Log。

## 13. Persistence Extensibility

`Session` 真正依赖 `SessionPersistence` protocol，不知道 path、JSONL、file handle 或 fsync 实现。JSONL single-writer 细节没有泄漏到 model/session projection，但当前 protocol 本身就是“一实例、单 Session、单 writer”：

- `append(event)` 没有 session_id，因为 driver instance 已绑定一个 Session；
- seq 由 `Session` 使用内存长度分配；
- driver 验证 next seq，但没有 compare-and-swap、lease 或 transaction ownership；
- Summary lifecycle 和 Tool WAL 共享同一个 `Session.flush()` durability boundary，这是当前设计的优点。

单 writer SQLite driver 可以实现相同 protocol，属于 **SMALL EXTENSION**。如果只是替换 JSONL，它不要求修改 Session semantics。

多进程同时写一个 Session 不是“换 driver”即可解决：需要 writer lease/ownership、atomic sequence allocation、transaction boundaries 和 stale-owner recovery。V0.7 多 Process 不必共享写同一 Session；保持 per-process/session single writer 可推迟这项复杂度。若未来必须共享写，则是 persistence coordinator 的 MEDIUM/MAJOR extension，而不是现在重写 Session。

## 14. OS Principles Scorecard

| Principle | Status | Evidence | Risk | Recommended action |
|---|---|---|---|---|
| Model decides, Kernel enforces | GREEN | Tool authorization、budget、state、WAL、Context mandatory closure 全部在 trusted code | argument semantic correctness 仍依赖 Tool handler | 保持 enforcement 在调用边界 |
| Mechanism != Policy | GREEN | ContextPolicy/ReclaimPolicy、recovery fact/action 分离 | scheduling 尚无 policy seam | V0.7 在 Loop 外加入 scheduler policy |
| Small Trusted Kernel | GREEN | 无 RAG/Memory/workflow/UI；Provider/driver 可替换 | Public package 聚合较宽 | 按 enforcement/service/policy 分层演进 |
| Stable abstraction, replaceable implementation | GREEN | LLMService、SessionPersistence、ContextService、policies | Agent/Session 1:1 是 concrete binding | 仅在真实多 Session/process 用例出现时拆分 |
| Least privilege | YELLOW | schema hiding + execution-time exact capability check + bounding set | 无 action/resource scope，child delegation 未强制 | V0.6 structured capability + Resource Service second check |
| Failure isolation | YELLOW | Tool errors normalized；ambiguous mutation 不盲重试 | 单 Agent loop、hook failure 会结束 Turn；无 sandbox/cancel domain | V0.7 process isolation，Driver error boundaries |
| Durable truth != projection | GREEN | Event Log/Message、raw/pruned、raw/Summary、Pages/Working Set 均分离 | independent sources 尚无 composition seam | V0.5 保持 bytes vs preview/handle 分离 |
| Recovery-oriented design | GREEN | replay validation、WAL、reconcile、compaction lifecycle | 只恢复 Session，不恢复 ACB process | V0.7 增加 process-state recovery |
| Resource accounting before scheduling | RED | context/request/tool-call 已局部计量 | 无统一 UsageSnapshot/admission；scheduler 无法直接消费 | 必须在 V0.7 scheduler 前完成，非 V0.5 blocker |
| Stable interfaces | YELLOW | protocol values 和 service Protocols 清晰 | closed EventType、monolithic recovery、Context source/order 绑定 Session | 在对应需求前做兼容扩展，不提前泛化 |
| Avoid premature abstraction | GREEN | 未提前实现 VFS、Memory、Namespace、Plugin Runtime | blueprint 中 OS 名词可能诱导过度设计 | 每阶段绑定真实 Agent benchmark |
| No Linux cosplay | GREEN | 当前机制均解决已测试的 runtime failure/resource problem | V0.5 易滑向通用 VFS | 限定 handle、range read、externalization、driver seam |

## 15. Compatibility Matrix

| Future subsystem | V0.1 contribution | V0.2 contribution | V0.3 contribution | V0.4 contribution | Change level |
|---|---|---|---|---|---|
| V0.5 Virtual Resource / Artifact Handle | Tool/Protocol 可传 structured handle | Session 可记录 handle/preview facts | Resource mutation 可复用 operation identity/WAL | Context 保留 preview，避免 bytes 常驻 | **SMALL EXTENSION** |
| V0.6 Capability / Namespace | Tool authorization 与 ACB bounds 是基础 | Session 可审计授权结果 | mutation 仍需先授权 | Page trust/pin 可消费 policy metadata | **MEDIUM REFACTOR** |
| V0.7 Agent Process / Scheduler / Budget | ACB states/identity/budget 已存在 | 只有 Session recovery，无 process persistence | pending I/O recovery facts可复用 | Context 可作为 process resource controller | **MEDIUM REFACTOR** |
| V0.8 Subagent / IPC | parent_agent_id、caller identity 是基础 | per-child Session journal 可复用 | delegated side effect 仍走 WAL | IPC 只传 result/Artifact refs，不复制历史 | **MEDIUM REFACTOR** |
| Long-term Memory | Tool ABI 可显式 query/read | Memory facts 可引用 Session provenance | write/delete 需 durable effect contract | 当前 Context source/order 过度 Session-bound | **MEDIUM REFACTOR** |

没有项目被判定为 MAJOR REWRITE。未来最重的变化集中在 capability data model、process-state persistence、unified accounting 和 multi-source Context projection，但现有 provider-neutral ABI、append-only truth、Tool boundary、WAL 与 Context projection原则均可保留。

## 16. Blockers

### A. BLOCKER BEFORE V0.5

**None.**

V0.5 的首个真实路径可以是：大型 Tool output 写入 Resource Store，Tool Result 只返回 metadata、preview 和 unforgeable handle；后续 `resource.read(handle, range)` 仍经 ToolRegistry 和 Resource Service。它不要求 ContextPage 先支持任意 source。

### B. SHOULD FIX SOON

1. **V0.6 前：structured capability 与 resource-scoped second enforcement。** 当前 exact string/one-required-capability 不能表达 action + resource scope。
2. **V0.7 前：统一 UsageSnapshot/admission service。** 当前计数分散且 per-turn usage 不可查询/恢复。
3. **V0.7 前：Agent process state persistence。** ACB state、priority、pending I/O 和 exit status 当前不是 durable truth。
4. **并发多 Agent 前：明确 ContextManager ownership。** mutable pin/page-in sets 应归属于 process/session context instance，不能隐式共享。
5. **V0.7/V0.8 前：modular recovery validators。** 避免把 Process/IPC event 强塞进 Turn/Step monolithic parser。
6. **独立 Memory/Skill/Rule projection 前：multi-source Context seam。** 需要 source-qualified identity、ordering 和 renderer；不应在 V0.5 handle externalization 中提前做。

### C. FUTURE CONCERN

- shared-session multi-writer lease 和 atomic sequence allocation；
- Global audit stream（不等于把 Session 变成 global Kernel log）；
- exact Provider tokenizer 和 production telemetry；
- Plugin Runtime generation/hot reload；
- long-term Memory ranking/RAG；
- cross-process handle transfer/revocation；
- distributed Resource Driver transaction。

## 17. Technical Debt

- `Agent` 强制一个 ACB 对应一个 Session；合理但尚未声明为长期 process contract。
- `AgentBudget` 只包含 step/tool-call limit，usage 是 Loop 局部变量。
- `ToolDefinition.required_capability` 只能表达一个 exact string。
- `ToolExecutionContext` 没有 cancellation、deadline、scoped service façade 或 Usage reservation。
- `ContextProjector`、`created_seq`、`turn`、默认 policy 和 ordering 都围绕 Session。
- Summary provenance 同时使用通用 page IDs 和 Session event seqs；独立 source 需要区分两者。
- `ContextManager` 的 manual pin/page-in state 是实例内 mutable set。
- `EventType`/`analyze_recovery()` 扩展成本会随新 subsystem 上升。
- `SessionPersistence` 适合单 writer；没有 lease/transaction ownership。
- Tool argument JSON Schema 仍未在 Kernel boundary 验证。

这些债务都不要求在 V0.5 前修改现有 Python 核心。

## 18. V0.5 Readiness

**READY FOR V0.5**

理由：

1. Tool Result 已支持 structured JSON，可携带 handle、metadata 和 bounded preview。
2. ToolExecutionContext 已有 caller/session/operation identities。
3. DurableToolExecutor 可无感包裹 Resource mutation。
4. Session 可以把 handle/preview 作为 durable Tool fact 保存。
5. Context VM 已能对 preview 进行 selection/pruning/compaction，而 Resource bytes 不必进入 Context。
6. 新 Resource Service/Driver 可以通过新增模块加入，不需要改变 Provider ABI、Session truth、Tool WAL 或 Context reclaim 不变量。

Architecture freeze rule：V0.5 不得为了未来 Memory/Skill/Namespace 预先增加通用 PageSource、通用 VFS URI 语义、Global Event Log 或 Scheduler interface。

## 19. Proposed V0.5 Boundary

### 19.1 Real problem

V0.5 解决“大型资源为什么必须整体进入模型 Context”：100MB log、PDF、数据库结果、shell output 或 repository scan 应外置为可寻址 Resource。模型只看到 metadata、bounded preview 和 handle，需要时按 range 读取。

### 19.2 Narrow architecture

```text
                         AgentKernel

                       Trusted Kernel
       identity / capability / event integrity / accounting
                 durable operation / process state
                              │
          ┌───────────────────┼───────────────────┐
          ▼                   ▼                   ▼
   Session + Recovery     Tool I/O          Resource Service
          │               + WAL             handle table
          │                   │              bounds/accounting
          └──────────┬────────┴──────────┬────────┘
                     ▼                   ▼
                 Context VM       Resource Drivers
                     │            file / artifact
                     ▼
                ModelRequest
                     │
                     ▼
               LLM Provider

 Outside mechanism: Context/Reclaim/Scheduler policies, Persistence Driver,
 LLM Provider, Resource Drivers, Memory Backend, Skills, Plugins, Workflows.
```

V0.5 只应定义：

- **Virtual Resource**：Kernel 管理的 resource identity、metadata 和 byte access contract；
- **Artifact Handle**：不可由模型伪造为有效权限、绑定 owner/scope/lifetime 的稳定引用；
- **Resource Driver**：stat/read/write 等最小后端 seam，不拥有 authorization policy；
- **Large Tool Result Externalization**：超过明确阈值的 Tool output 存入 Resource Store，Session/Context 只保留 handle + metadata + preview。

V0.5 不应定义完整 Unix filesystem、mount、inode、通用 POSIX fd、Memory/RAG、Namespace、Scheduler、IPC 或 Plugin Runtime。

### 19.3 Proposed benchmark, not executed

真实问题：100MB Tool Result 中包含早期约束、中段证据和尾部 fatal error，Agent 必须定位并引用目标证据。

比较：

| Mode | Path |
|---|---|
| A | 完整 Tool Result 直接进入 Session/Context |
| B | V0.4 head/marker/tail pruning + compaction |
| C | V0.5 externalized Resource + preview + range reads |

必须测量：

- durable Session size；
- Resource Store bytes；
- estimated 与 actual Provider input/output；
- model calls 和 resource reads 数量；
- read ranges/bytes；
- task correctness（明确 cases passed，不声称通用成功率）；
- first-turn 与 steady-state latency；
- recovery 后 handle/read 是否仍有效且权限不扩大。

验收重点不是“像 VFS”，而是 C 在保留任务证据和 durable truth 的同时，避免 100MB payload 常驻 Session model projection，并使访问量可计量、可限制、可恢复。
