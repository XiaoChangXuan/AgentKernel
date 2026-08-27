# AgentKernel

**English:** [README.en.md](README.en.md)

AgentKernel 是一个面向 tool-using LLM agent 的 **runtime kernel**。它不尝试替代 prompt、产品逻辑、模型选择或 UI，而是把那些不能交给模型“自觉遵守”的运行时机制放到 Kernel 边界内。

核心原则：

```text
Model proposes actions.
Kernel owns invariants.
```

模型可以提出工具调用、资源访问、子 Agent 协作和外部副作用请求；但 durable truth、权限判断、WAL、恢复、调度、资源隔离这些事实由 Kernel 负责。

## 为什么需要 AgentKernel

普通 Agent loop 往往长这样：

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

它适合演示，但一旦进入真实 runtime，会遇到几个硬问题：

- 进程可能在任意步骤崩溃。
- Tool 可能已经产生外部副作用。
- Context 有长度上限，而且 summary 可能丢事实。
- Tool result 可能比模型上下文大很多。
- 模型写出一个 tool call，不等于它被授权执行。
- 多 Agent 协作时，身份、权限、IPC、资源共享和恢复不能混在一起。

一个最具体的 failure scenario：

```text
模型请求 payment.charge
Kernel dispatch 到外部支付系统
支付系统成功扣款
Python 进程在写入 ToolResult / commit 前崩溃
重启后 naive agent 只看到“没有结果”
blind retry 可能再次扣款
```

AgentKernel 的 V0.3 Durable Tool WAL 会把这类操作拆成：

```text
PREPARE -> DISPATCH -> COMMIT / RECONCILE
```

如果 crash 发生在 dispatch 后 commit 前，恢复流程要求 reconcile，而不是直接 retry。

## 我应该先打开哪个 demo？

如果你第一次看这个仓库，建议先打开 Interactive Lab：

```text
examples/labs/v0_3_durable_side_effect_lab.ipynb
```

它是 flagship lab，会一步一步展示 fake payment 在
`dispatch -> crash -> restart -> reconcile` 下为什么不能 blind retry。

如果你更想从最小执行骨架开始，可以打开：

```text
examples/labs/v0_1_agent_execution_lab.ipynb
```

这些 notebooks 可以直接在 GitHub / VS Code 中阅读，也可以用 Jupyter
逐 cell 执行。Jupyter 是可选教学工具，不是 AgentKernel runtime 依赖。

对应的 deterministic tutorial scripts 仍然保留，适合命令行 smoke test 和 CI：

```bash
python examples/tutorials/v0_3_durable_side_effect.py
```

也可以从最小 Agent spine 开始：

```bash
python examples/tutorials/v0_1_agent_spine.py
```

这些 tutorial 默认使用 `ScriptedLLM`、deterministic fixtures 和 fake external systems。它们不是“真实大模型自主完成任务”的 evidence；这是有意设计的，因为 tutorial 要可复现、可离线运行，并隔离 Kernel behavior。

如果你已经显式配置了真实模型 provider，可以再运行 real-model trace demo：

```bash
set AGENTKERNEL_RUN_REAL_MODEL=1
set AGENTKERNEL_LLM_BASE_URL=https://your-openai-compatible-endpoint/v1
set AGENTKERNEL_LLM_MODEL=your-model
set AGENTKERNEL_LLM_API_KEY=your-key

python examples/real_agent/basic_tool_trace.py
python examples/real_agent/capability_denial_trace.py
python examples/real_agent/resource_handle_trace.py
```

真实模型 demo 是 opt-in：没有 `AGENTKERNEL_RUN_REAL_MODEL=1` 时会安全跳过，不会默认发起网络请求或产生付费 API 调用。

## 当前阶段

当前公开 baseline 是 **V0.8 Runtime Kernel alpha**：

| Version | Runtime mechanism |
| --- | --- |
| V0.1 | Execution Kernel and Tool boundary |
| V0.2 | Session persistence, event replay, recovery analysis |
| V0.3 | Durable Tool WAL and reconciliation |
| V0.4 | Context VM, pages, working set, pruning, compaction |
| V0.5 | Virtual Resource / Artifact Handle |
| V0.6 | Capability core and Kernel enforcement |
| V0.7 | Process runtime, cooperative scheduler, accounting |
| V0.8 | Agent Registry, Process Tree, Delegation, IPC, Resource Sharing, runtime isolation, integrated multi-agent recovery |

V0.8 不是生产安全沙箱，不是 IAM/RBAC，不是分布式一致性系统，也不是 V0.9 memory。

## 推荐学习路径

1. 先看上面的 payment crash failure scenario。
2. 打开 flagship Interactive Lab：

```text
examples/labs/v0_3_durable_side_effect_lab.ipynb
```

3. 按 V0.1-V0.8 逐个阅读 / 执行 Interactive Labs：

```text
examples/labs/v0_1_agent_execution_lab.ipynb
examples/labs/v0_2_recovery_lab.ipynb
examples/labs/v0_3_durable_side_effect_lab.ipynb
examples/labs/v0_4_context_vm_lab.ipynb
examples/labs/v0_5_resource_handle_lab.ipynb
examples/labs/v0_6_capability_lab.ipynb
examples/labs/v0_7_process_runtime_lab.ipynb
examples/labs/v0_8_multi_agent_runtime_lab.ipynb
```

4. 用 deterministic tutorial scripts 作为 CI-friendly counterpart：

```bash
python examples/tutorials/v0_3_durable_side_effect.py
```

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

5. 如果配置了真实模型，打开 optional real-model lab 或运行 real-model trace，看真实 provider 如何穿过 AgentKernel boundary：

```text
examples/labs/real_model_tool_trace_lab.ipynb
```

```bash
python examples/real_agent/basic_tool_trace.py
```

6. 再阅读架构文档：

- [中文新人指南](docs/getting-started/AGENTKERNEL_GUIDE.zh-CN.md)
- [English newcomer guide](docs/getting-started/AGENTKERNEL_GUIDE.en.md)
- [Teaching / Trace 说明](docs/getting-started/TEACHING_AND_TRACE.md)
- [Architecture](docs/ARCHITECTURE.md)
- [RuntimeBench evidence](docs/evaluation/V0.8_MULTI_AGENT_RUNTIMEBENCH.md)
- [V0.8 release review](docs/releases/V0.8_RELEASE_REVIEW.md)

7. 后续再看 MiniCode reference CodeAgent 和 IntegrationBench。

## Interactive Labs vs Deterministic Tutorials vs RuntimeBench vs Real Model Trace

| 类型 | 目的 | 是否调用真实 API | 是否进入强制 CI |
| --- | --- | --- | --- |
| Interactive Lab | 人读得懂的逐步实验 | V0.1-V0.8 否；real-model lab opt-in | Notebook JSON / deterministic execution 轻量验证 |
| Deterministic Tutorial | 教学 fixture + Kernel semantics | 否 | 是，作为可执行教程验证 |
| RuntimeBench | release runtime invariants | 否 | 是 |
| Real Model Trace Demo | real provider integration + AgentKernel application flow | 是，但必须 opt-in | 否 |

Interactive Labs、Tutorial 和 RuntimeBench 使用 deterministic offline fixtures
时能验证精确 runtime semantics，但不能证明模型智能、生产可靠性、生产安全性或
benchmark superiority。

Real Model Trace 只能说明：

- real model can consume tool schemas；
- model can produce a tool call or operation proposal；
- call/proposal crosses an AgentKernel boundary；
- Kernel authorization executes；
- tool observation returns into subsequent model request；
- Session records observed execution trajectory。

它不能声称：

- model reasoning is generally correct；
- production reliability；
- production security；
- crash safety，除非该 demo 实际测试 crash；
- exactly-once；
- 优于 Claude、Codex、Gemini、OpenHands、LangChain、Letta 或其他项目。

## Architecture Snapshot

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

关键边界：

- LLM != Authority
- Agent != Process
- Agent Tree != Process Tree
- Session != Context
- Context != durable truth
- ResourceStore != authorization boundary
- IPC payload != authority
- Accounting != durable ledger

## RuntimeBench

统一入口：

```bash
python -m benchmarks.runtimebench
```

冻结结果：

```text
benchmarks/results/runtimebench_v0.8.json
```

当前 B1-B8 deterministic offline benchmark 全部 PASS：

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

RuntimeBench 是 synthetic、offline、deterministic、本地运行的 release invariant evidence。它不是真实大模型评测，也不测量模型智能。

## 安装与验证

Python 3.11+。

```bash
python -m pip install -e ".[test]"
pytest -q
python -m benchmarks.runtimebench --no-write
```

基础示例：

```bash
python examples/basic_agent.py
python examples/persistent_session.py
python examples/resource_handles.py
python examples/process_runtime.py
```

## Roadmap

```text
V0.8 Runtime Kernel alpha
  -> Teaching / real-model trace integration
  -> MiniCode reference CodeAgent
  -> MiniCode IntegrationBench
  -> Runtime API feedback
  -> V0.9 Persistent Memory
```

不要把尚未实现的内容当作已完成能力。V0.9 Persistent Memory 当前尚未实现。

## Limitations

AgentKernel V0.8 alpha 不包含：

- V0.9 Persistent Memory
- 完整 namespace security
- 完整 revocation semantics
- RBAC / IAM
- production sandbox security
- distributed runtime correctness
- distributed consensus
- production SLA
- preemptive scheduling
- universal exactly-once side effects
- arbitrary external system atomicity
- semantic long-horizon reasoning
- superior model intelligence claims
