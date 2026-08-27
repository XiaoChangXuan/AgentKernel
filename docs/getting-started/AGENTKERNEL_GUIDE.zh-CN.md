# AgentKernel V0.8 新人指南

## 前 30 秒

AgentKernel 是一个面向 tool-using LLM agent 的 runtime kernel。

核心原则：

```text
Model proposes actions.
Kernel owns invariants.
```

模型可以提出工具调用、消息、子 Agent、资源访问和 IPC 数据，但这些提议本身不等于权限、不等于 durable truth，也不等于外部副作用已经安全完成。AgentKernel 把必须可信的 runtime 机制放进 Kernel 边界：Session、Tool boundary、WAL、Context VM、Resource Handle、Capability、Process、IPC、Recovery。

```text
User
  |
  v
Agent / Model
  |
  | proposes actions
  v
AgentKernel
  |-- Session durable truth
  |-- Tool boundary
  |-- Durable Tool WAL
  |-- Context projection
  |-- Resource / Artifact Handle
  |-- Capability authority
  |-- Process / Scheduler / Accounting
  |-- Agent Tree / IPC / Resource Share
  `-- Recovery
  |
  v
External World
```

AgentKernel 不是通用 Agent framework。Prompt、业务策略、模型选择、UI、插件系统、产品记忆策略都应该在 Kernel 之上。Kernel 只负责在模型输出错误、不完整或恶意时仍必须成立的机制。

## 从 Naive Agent Loop 开始

最小 Agent loop 通常像这样：

```python
messages = []
while True:
    response = model(messages)
    if response.tool_call:
        result = execute_tool(response.tool_call)
        messages.append(result)
    else:
        break
```

这个 loop 很容易理解，也很容易在真实 runtime 中失效：

| 问题 | naive 行为 | AgentKernel 机制 |
| --- | --- | --- |
| Python 进程崩溃 | 内存里的 messages 和状态消失 | Session Event Log 记录 durable semantic facts |
| Tool 已产生外部副作用后崩溃 | blind retry 可能重复执行 | Durable Tool WAL 绑定 operation_id 并要求 reconcile |
| messages 无限增长 | full history 线性膨胀 | Context VM 从 durable truth 投影 bounded working set |
| Tool 返回几百 MB | 直接塞进 context | Resource / Artifact Handle 把大字节放在资源层 |
| Model 请求工具 | 请求被当作授权 | CapabilityEvaluator 在 Kernel 边界判断权限 |
| 多 Agent 协作 | 子 Agent 隐式继承所有能力 | Agent Tree、Capability Delegation、ResourceShare 显式建模 |
| IPC 传 URI | URI 被误当权限 | IPC 只传数据，authority 仍来自 Capability / Share |
| cancellation | 被误当 rollback 或 revocation | Process cancellation 是 runtime 控制，不删除 durable facts |

## V0.1 到 V0.8 的演化

### V0.1 Agent Spine

Problem: ad-hoc model/tool loop 缺少结构化 runtime 边界。

Naive failure mode: model output、tool execution、session history 混在同一段业务代码里，后续很难插入 recovery、authorization、context 管理。

Kernel mechanism: `Agent` 持有 `AgentControlBlock`，`Session` 记录事件，`DefaultAgentLoop` 在 turn、step、tool boundary 周围写入结构化事件。

Key invariant: model 提议 tool call，Kernel 负责解析、授权和记录 tool boundary。

Runnable evidence:

```bash
python examples/tutorials/v0_1_agent_spine.py
```

这个教程使用 `ScriptedLLM` 和 deterministic `math.add`，输出包括 `turn/start`、`tool/call`、`tool/result`、`turn/end`。

Trade-off: 需要显式创建 `Agent`、`Session`、`ToolRegistry`，比一个裸 while loop 更重。

### V0.2 Persistence / Recovery

Problem: runtime memory 不等于 durable semantic truth。

Naive failure mode: 进程崩溃后，内存中的 messages、step、tool result 全部消失，只能从不完整 transcript 猜测。

Kernel mechanism: `Session` 追加 JSON-safe event；`JsonlSessionPersistence` 持久化；`Session.load` 重新加载并运行 recovery analysis。

Key invariant: durable facts 来自 Session Event Log，不来自 live Python object。

Runnable evidence:

```bash
python examples/tutorials/v0_2_recovery.py
```

教程先完成一次 deterministic run，然后丢弃旧 runtime object，再从 JSONL Session 重新加载。输出会显示 `after_restart_status=completed` 和 `lost_durable_facts=False`。

RuntimeBench mapping: B1 Fault Tolerance 覆盖 crash prefix replay、lost durable fact count、duplicate effect count 和 recovery status oracle。

Trade-off: event schema 和 persistence driver 必须保持严格，历史记录不能被随意修补。

### V0.3 Durable Tool WAL

Problem: 外部副作用可能在 Kernel 本地记录完成前已经发生。

Naive failure mode: payment API 已成功，但进程在 ToolResult 之前崩溃；重启后 blind retry 可能再次 charge。

Kernel mechanism: Durable Tool WAL 使用 PREPARE、DISPATCH、COMMIT 和 stable `operation_id`。如果崩溃发生在 dispatch 后 commit 前，recovery classification 要求 reconcile，而不是直接 retry。

Key invariant: recovery != retry；recovery classification != reconciliation 本身。

Runnable evidence:

```bash
python examples/tutorials/v0_3_durable_side_effect.py
```

教程模拟 fake payment：

```text
PREPARE -> DISPATCH -> external success -> crash before local completion
restart -> RECONCILE_REQUIRED -> reconcile -> committed
```

输出中 `external_effect_count=1`，表示在这个 deterministic fake fixture 中没有重复执行同一个外部副作用。

What this demonstrates: stable operation identity、dispatch 后 crash 的 classification、reconciliation obligation、tested fake service 中不重复外部 effect。

What this does not demonstrate: universal exactly-once、分布式事务原子性、任意外部系统安全性。

RuntimeBench mapping: B2 Side Effect Safety 覆盖 fake service 场景中的 duplicate execution oracle 和 recovery correctness。

Trade-off: Host 需要为 reconcilable mutation 提供 reconcile handler，不能把所有外部系统都自动变成 exactly-once。

### V0.4 Context VM

Problem: model context 有限，但 Session durable truth 会持续增长。

Naive failure mode: full history 线性增长；summary 可能丢失关键事实；replacement history 可能让 context 和 truth 混淆。

Kernel mechanism: Context VM 将 Event 投影为 Context Page，再形成 Working Set。Context 是 model-visible projection，不是 durable truth。

Key invariant: Session durable truth != Model context。

Runnable demonstration:

```bash
python examples/tutorials/v0_4_context_vm.py
```

该教程先写入多轮 durable turn，再投影成 Context Pages，并选择一个更小的 Working Set。输出中的 `context_equals_truth=False` 表示预期边界：context 只是 model-visible projection，不是 truth。

Evidence: RuntimeBench B3 Context Efficiency / Truth Preservation 验证 bounded context、truth preservation 和 deterministic correctness oracle。

Trade-off: 多了一层 projection 和 compaction 成本，Host 仍需选择合适的 context policy。

### V0.5 Resource / Artifact Handle

Problem: Tool result 可能远大于 model context。

Naive failure mode: 把 100 MB 或 1 GB 结果塞进 messages，context 和内存都线性膨胀。

Kernel mechanism: Resource Layer 保存大字节，Context 中只保留 handle、preview 或 bounded marker。

Key invariant: Resource != Context；ResourceHandle != Permission。

Runnable demonstration:

```bash
python examples/tutorials/v0_5_resource_handle.py
```

该教程把 32,000 bytes 存成 artifact，context 中只携带 `artifact://...` marker，并验证重启后的 ResourceService 仍能读取同一份 bytes。

Evidence: RuntimeBench resource cases 证明 Artifact Handle 让 context size 保持稳定，同时资源字节留在 ResourceStore。

Trade-off: handle 生命周期、read latency、resource cleanup 和 Host storage policy 都需要显式管理。

### V0.6 Capability

Problem: 模型生成 tool call 不等于它有权限执行。

Naive failure mode: 只要 model 写出 `payment.charge`，tool runner 就执行。

Kernel mechanism: `CapabilityGrant`、`AuthorizationRequest`、`AuthorizationDecision`、`CapabilityEvaluator`；Tool、Resource、Durable operation 边界都重新检查 authorization。

Key invariant: Model proposal != Kernel authority。

Runnable demonstration:

```bash
python examples/tutorials/v0_6_capability_core.py
```

该教程给 `agent-allowed` 授予 `tool://math.add` 的 structured authority，让 `agent-denied` 保持无 grant，并展示 denied agent 看不到 model-visible tool，强行执行也会得到 `EACCES`。

Evidence: RuntimeBench B4 Capability Isolation 覆盖 unauthorized tool、resource read、payment dispatch denial 和 legacy tool compatibility。

Trade-off: 权限模型更显式，Host 必须提供 grant 和 policy 输入；当前 V0.8 仍不是 RBAC、IAM 或完整 namespace security。

### V0.7 Process Runtime

Problem: Agent semantic identity 和 runtime scheduling identity 混在一起会妨碍 pause、cancel、budget 和 recovery。

Naive failure mode: cancellation 被误当 rollback；budget exceeded 被误当任务失败；runtime state 被误当 durable truth。

Kernel mechanism: `ProcessControlBlock`、cooperative scheduler、safe point、`UsageCollector`、runtime budget blocking。

Key invariant: Agent != Process；Accounting != Authority；Budget exceeded != Semantic failure。

Runnable demonstration:

```bash
python examples/tutorials/v0_7_process_runtime.py
```

该教程创建一个 Agent-owned Process，记录 deterministic LLM token usage，在 scheduler safe point 触发 budget block，然后重置 runtime-only usage 并把 Process unblock 回 `READY`。

Evidence: RuntimeBench B5 Resource Governance 和 B7 Boundary Isolation 覆盖 budget safe point blocking、unblock recovery，以及 Agent / Process / Session / Context / ResourceStore 边界。

Trade-off: Host 集成时需要显式处理 process lifecycle。V0.8 alpha 不包含 preemptive scheduling。

### V0.8 Multi-Agent Runtime

Problem: 多 Agent 不是简单地 spawn 更多 loops。身份、delegation、IPC、resource sharing、process lineage 和 recovery 都可能被混淆。

Naive failure mode: child agent 隐式继承 parent 全部权限；process lineage 被当作 authority；IPC payload 中的 resource URI 被当成 access grant；crash 后恢复 stale runtime-only authority。

Kernel mechanism: Agent Registry、Agent Tree、Process Tree、Capability Delegation、Kernel IPC、ResourceShare、runtime isolation、integrated multi-agent recovery。

Key invariants:

- Agent Tree != Process Tree
- ResourceShare != Capability
- IPC data != Authority
- Process lineage != Authority inheritance
- Historical delegation != Current authority
- Persistent semantic facts != Live runtime state

Evidence: RuntimeBench B8 Multi-Agent Runtime 覆盖 M1-M10，M10 在 100 / 500 / 1000 logical steps 下 deterministic PASS。

Runnable demonstration:

```bash
python examples/tutorials/v0_8_multi_agent_runtime.py
```

该教程创建 parent/child Agent 与 Process，先证明 child 不会隐式继承 parent 的 tool authority，再通过 IPC 传递 Resource reference，展示 IPC 和 ResourceShare 本身都不等于 access grant，最后 delegate 收窄的 Resource 与 Tool grant 并成功访问。

Trade-off: 多 Agent 协作更显式，调用方要处理更多 runtime objects 和 recovery obligations。

## RuntimeBench Evidence

Frozen artifact:

```text
benchmarks/results/runtimebench_v0.8.json
```

Release evidence:

```text
runtimebench_version = 0.8
runtime_version = AgentKernel V0.8
source commit = 813ca776428987e80bfb9396d4a3beb257ab7ccb
release tag = v0.8.0-alpha
B1-B8 = 8/8 PASS
B8 M1-M10 = 10/10 PASS
M10 horizons = 100 / 500 / 1000 PASS
```

Benchmark oracle summary:

| Benchmark | What PASS means in the tested fixture |
| --- | --- |
| B1 Fault Tolerance | crash prefixes replay without lost durable facts or duplicate tested effects |
| B2 Side Effect Safety | WAL + reconcile avoids duplicate fake external mutation in tested scenario |
| B3 Context Efficiency / Truth Preservation | model-visible context is bounded while durable facts remain available |
| B4 Capability Isolation | unauthorized Tool, Resource, and Durable operations are denied |
| B5 Resource Governance | scheduler safe points block execution when configured budgets are exceeded |
| B6 Long-Horizon Runtime Stability | V0.1-V0.7 invariants compose over deterministic long-horizon fixtures |
| B7 Boundary Isolation | Agent, Process, Session, Context, Accounting, ResourceStore boundaries stay distinct |
| B8 Multi-Agent Runtime | identity, delegation, IPC, resource sharing, cancellation, budget, fault, and recovery invariants pass in deterministic multi-agent fixtures |

RuntimeBench is deterministic, offline, local, and synthetic. It measures runtime invariants, not LLM intelligence.

## 与其他 Runtime / Harness 的设计空间

本指南配套文档：

```text
docs/research/AGENT_RUNTIME_DESIGN_COMPARISON.md
```

该比较基于本地 reference repositories 中可确认的 README、docs、tests 或 source。它不是 superiority benchmark。AgentKernel 不能据此声称超过 Codex、OpenHands、Gemini CLI、DeepSeek Harness、LangChain、Letta 或任何其他项目。

高层差异：

| 问题 | 常见系统关注点 | AgentKernel 关注点 |
| --- | --- | --- |
| Workspace safety | sandbox、approval、filesystem scoping | semantic capability principal 和 Kernel authorization boundary |
| Conversation resume | transcript、session storage、UI continuity | event-sourced durable semantic truth 和 recovery classification |
| Tool lifecycle | call / observation abstraction、approval、retry | WAL、operation_id、dispatch/commit/reconcile |
| Context management | compaction、summary、tool output trimming | Context VM projection，不把 context 当 truth |
| Multi-agent orchestration | subagent UI、routing、server/process ownership | Agent Tree != Process Tree，delegation 和 IPC 不隐式传 authority |

## 设计收益

- 把模型输出降级为 untrusted proposal。
- 把 durable truth、model context 和 live runtime state 分开。
- 对外部副作用提供 crash-aware WAL/reconcile 机制。
- 对大 tool result 提供 Resource / Artifact Handle，而不是污染 context。
- 对权限、delegation、IPC、resource share 使用显式 Kernel object。
- 对 process lifecycle、budget、pause/cancel 使用 runtime mechanism，而不是 prompt 约定。

## 设计成本和限制

- 比普通 Agent loop 有更多 explicit runtime objects。
- Host integration 需要提供 policy、capability grants、resource storage、reconcile handlers。
- Event sourcing、WAL、recovery classification 和 multi-agent recovery 增加实现复杂度。
- 当前 V0.8 alpha 不证明 production sandbox security、distributed correctness、universal exactly-once、semantic long-horizon reasoning 或 superior model intelligence。
- V0.8 不是 V0.9 memory，不包含完整 namespace security、RBAC、IAM 或生产 SLA。

## 建议阅读路径

1. 先读本指南。
2. 跑八个教程：

```bash
python examples/tutorials/v0_1_agent_spine.py
python examples/tutorials/v0_2_recovery.py
python examples/tutorials/v0_3_durable_side_effect.py
python examples/tutorials/v0_4_context_vm.py
python examples/tutorials/v0_5_resource_handle.py
python examples/tutorials/v0_6_capability_core.py
python examples/tutorials/v0_7_process_runtime.py
python examples/tutorials/v0_8_multi_agent_runtime.py
```

3. 阅读 `docs/ARCHITECTURE.md`。
4. 阅读 `docs/evaluation/V0.8_MULTI_AGENT_RUNTIMEBENCH.md`。
5. 阅读 `docs/releases/V0.8_RELEASE_REVIEW.md` 和 `docs/releases/V0.8_RELEASE_NOTES.md`。
6. 需要理解同类系统设计空间时，再读 `docs/research/AGENT_RUNTIME_DESIGN_COMPARISON.md`。

## Onboarding Findings

V0.1 和 V0.2 教程可以直接使用 public AgentKernel API 表达。V0.3 教程为了精确展示 crash after dispatch before commit，手动追加了 WAL prefix event。这适合作为教学用低层 runtime boundary demo，但也说明未来 MiniCode / Runtime API Review 可以考虑提供更容易教学的 durable-side-effect fixture helper。本阶段不修改 Kernel。
