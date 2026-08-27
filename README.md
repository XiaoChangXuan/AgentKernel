# AgentKernel

AgentKernel 是一个面向 LLM Agent 的小型可信运行时内核。它关注的不是
“提示词怎么写得更聪明”，而是当模型输出不确定、工具会产生副作用、上下文
会丢失、进程会崩溃时，哪些运行时不变量必须由 Kernel 保证。

核心原则：

```text
模型提出行动。
Kernel 维护不变量。
```

AgentKernel 不是通用 Agent 框架。Prompt、产品策略、业务流程、模型选择、
UI、插件和长期记忆产品都应位于 Kernel 之上。AgentKernel 只负责那些不能
交给模型文本自行保证的运行时机制：持久事实、授权、恢复、调度、通信、资源
共享和副作用边界。

## 为什么需要 AgentKernel

工具型 Agent 需要明确的运行时边界，因为：

- LLM 输出是非确定性的。
- 工具可能产生外部副作用。
- 上下文有限且会丢失信息。
- 进程可能在关键事件之间崩溃。
- 工具结果和 artifact 可能远大于模型上下文。
- 权限不能安全地存在于 prompt 里。
- 多 Agent 协作需要身份、隔离、通信和恢复边界。

AgentKernel 将 LLM 视为不可信的 proposer，由可信 Kernel 负责 durable
truth、authorization、recovery、scheduling、IPC、resource sharing 和
side-effect safety。

## 架构概览

```text
LLM / Policy Layer
    |
    v
Agent                能力主体与语义 actor
    |
    v
Process              可调度运行时身份
    |
    v
Scheduler / Accounting
    |
    v
Session / Context VM  持久事实与模型可见投影
    |
    v
Tool / IPC / Resource / Durable WAL
    |
    v
External World
```

关键边界：

- Agent != Process
- Agent Tree != Process Tree
- Session != Context
- Context != durable truth
- Resource != Handle
- ResourceStore != authorization boundary
- IPC payload != authority
- Accounting != durable ledger
- LLM != Authority

## 当前能力

当前 V0.8 alpha baseline 包含：

| 版本 | 机制 |
| --- | --- |
| V0.1 | Execution Kernel 与工具边界 |
| V0.2 | Session 持久化、事件 replay 与 recovery analysis |
| V0.3 | Durable Tool Execution、WAL 与 reconcile |
| V0.4 | Context VM、context pages、working set、pruning 与 compaction |
| V0.5 | Virtual Resource / Artifact Handle，用于大工具结果 |
| V0.6 | Capability core，以及 Tool / Resource / Durable 边界 enforcement |
| V0.7 | Process runtime、cooperative scheduler 与 runtime accounting |
| V0.8 | Agent Registry、Process Tree、Capability Delegation、Kernel IPC、Resource Sharing、runtime isolation、integrated multi-agent recovery 与 Multi-Agent RuntimeBench |

## Kernel 不变量

1. LLM 永远不是 Kernel。
2. 副作用必须穿过可信边界。
3. Session 是 durable semantic truth。
4. Context 是模型可见投影，不是事实源。
5. Mechanism 与 Policy 分离。
6. Resource、Handle、Preview 不是同一件事。
7. Agent 不是 Process。
8. Agent 是 capability principal。
9. Scheduler 负责运行时机制，不负责业务策略。
10. Durable mutation 由 WAL/reconcile 控制。
11. Accounting 是观察，不是持久计费账本。
12. IPC 传递数据，不传递权限。
13. 跨 Agent 资源访问必须同时满足当前 capability 与 active ResourceShare。
14. 恢复后的运行时状态由 durable semantic facts 和当前 Host 配置重建；
    runtime-only 的旧状态不会作为权限恢复。

## RuntimeBench

Canonical benchmark：

```bash
python -m benchmarks.runtimebench
```

V0.8 机器可读结果：

```text
benchmarks/results/runtimebench_v0.8.json
```

V0.8 评测说明：

```text
docs/evaluation/V0.8_MULTI_AGENT_RUNTIMEBENCH.md
```

当前 RuntimeBench 家族：

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

当前摘要：

```text
total = 8
passed = 8
failed = 0
decision = PASS
```

B8 覆盖 M1-M10 多 Agent 运行时不变量。M10 在 100、500、1000 logical
steps 的 deterministic offline profiles 下通过。

## 快速运行

要求 Python 3.11 或更新版本。基础示例没有必需的第三方运行时依赖。

```bash
python examples/basic_agent.py
python examples/persistent_session.py
python examples/resource_handles.py
python examples/process_runtime.py
```

安装测试依赖并运行测试：

```bash
python -m pip install -e ".[test]"
pytest -q
```

运行 RuntimeBench，但不重写冻结结果文件：

```bash
python -m benchmarks.runtimebench --no-write
```

## MiniCode

MiniCode 是构建在 AgentKernel 之上的本地 coding harness。它不是新的
Kernel，也不拥有 Session、Capability、Context VM、WAL 或 Recovery
authority。

安装 console script 后，可以在任意 workspace 里运行：

```bash
python -m pip install -e .
minicode
```

不安装 console script 也可以：

```bash
python -m minicode
python -m minicode chat --workspace /path/to/project
```

交互模式下，MiniCode 会显示类似下面的进度：

```text
Working (3s • Esc to interrupt) - asking model
Working (34s • Esc to interrupt) - running tool: run_command: command=python -m pytest -q
```

`run` 子命令面向脚本，输出单个 JSON 对象；`chat` / `python -m minicode`
面向人工交互，直接打印可读文本。

### MiniCode 配置

`.minicode/` 是本地私有配置和运行痕迹目录，不应提交到 GitHub。本仓库
已将所有 `.minicode/` 目录加入 `.gitignore`。

推荐将模型 endpoint 和 key 放在本地环境文件中，例如：

```text
AGENTKERNEL_LLM_BASE_URL=http://llm.api.corp.example/v1
AGENTKERNEL_LLM_MODEL=azure/example-model
AGENTKERNEL_LLM_API_KEY=<secret>
MINICODE_ALLOW_NETWORK=true
MINICODE_APPROVE=on-mutation
MINICODE_MAX_TURNS=80
```

这些文件可以放在：

```text
<workspace>/.env
<workspace>/.minicode/.env
```

不要把真实 API key、bearer token 或 Authorization header 写进 JSON 配置或提交
到仓库。

真实模型运行示例：

```powershell
cd D:\path\to\project
$env:AGENTKERNEL_LLM_API_KEY = "<secret>"
python -m minicode
```

或者显式传参：

```powershell
python -m minicode run `
  "修复这个项目里的失败测试，并运行测试确认" `
  --workspace D:\path\to\project `
  --model openai-compatible `
  --allow-network `
  --approve always
```

`--allow-network` 只允许 MiniCode 调用模型 provider。它不会自动允许 shell
里的网络命令，也不会把 shell mutation 变成 WAL-safe。

## MiniCode Workload Evaluation

MiniCode Phase 2F workload evaluation 是 deterministic offline 的
CodeAgent 集成评测：

```bash
python -m benchmarks.minicode
```

结果文件：

```text
benchmarks/results/minicode_phase2f_validation.json
```

当前 F1-F8：

| Check | 内容 |
| --- | --- |
| F1 Workspace | workspace discovery、path containment、AGENTS.md projection |
| F2 Tool Visibility | tool schema filtering 与 execution-time capability denial |
| F3 Durable Patch Recovery | durable apply_patch crash/reconcile |
| F4 Resource Authority | large stdout -> ResourceHandle，且 Handle != Permission |
| F5 Nonzero Command | pytest exit 1 是结构化结果，不是 Tool crash |
| F6 Budget Block | scheduler budget safe-point blocking |
| F7 Resume / Handoff | 新 Process 继续同一个 durable Session |
| F8 Trace Redaction | observable trace 不泄露 secret-shaped fields |

覆盖分析见：

```text
docs/minicode/MINICODE_PHASE2F_WORKLOAD_EVALUATION.md
```

## 文档入口

新读者推荐从这里开始：

- [中文入门指南](docs/getting-started/AGENTKERNEL_GUIDE.zh-CN.md)
- [English newcomer guide](docs/getting-started/AGENTKERNEL_GUIDE.en.md)
- [docs/README.md](docs/README.md)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [Runtime design comparison](docs/research/AGENT_RUNTIME_DESIGN_COMPARISON.md)
- [V0.8 Release Review](docs/releases/V0.8_RELEASE_REVIEW.md)
- [V0.8 Release Notes](docs/releases/V0.8_RELEASE_NOTES.md)
- [V0.8 RuntimeBench](docs/evaluation/V0.8_MULTI_AGENT_RUNTIMEBENCH.md)
- [MiniCode Phase 2F Workload Evaluation](docs/minicode/MINICODE_PHASE2F_WORKLOAD_EVALUATION.md)

教程：

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

推荐阅读路径：

```text
README
  -> Getting Started guide
  -> V0.1-V0.8 tutorials
  -> Architecture
  -> Evaluation / RuntimeBench
  -> Release Review
```

## 当前证据支持的结论

V0.8 alpha 只支持有边界的 runtime-mechanism claim：

- 覆盖到的 deterministic crash prefixes 中，Session replay 能保留 durable facts。
- 在 tested reconcilable fake service 中，WAL/reconciliation 能避免重复副作用。
- ResourceHandle 能让大结果不进入模型上下文，同时保留精确 durable access。
- Context VM 能在 deterministic fixtures 中减少模型可见上下文。
- Capability enforcement 能阻止已测试的 unauthorized Tool、Resource 和 Durable 操作。
- Scheduler 与 Accounting 能在 configured budgets 超限时于 cooperative safe points 阻塞 Process。
- V0.1-V0.8 runtime mechanisms 在 deterministic single-agent workloads 中保持已测试不变量。
- V0.8 multi-agent mechanisms 在 deterministic offline fixtures 中保持已测试的 identity、delegation、IPC、resource sharing、budget、fault、cancellation 和 recovery invariants。
- Multi-agent recovery 能从 durable semantic facts 和当前配置重建运行时机制，不恢复 stale runtime-only authority。

## 限制

AgentKernel V0.8 alpha 尚不包含：

- V0.9 Persistent Memory。
- Namespace security。
- 完整 revocation semantics。
- RBAC 或 IAM。
- 生产级 sandbox security。
- 分布式运行时正确性。
- 分布式共识。
- 生产 SLA 或生产 workload traces。
- 抢占式调度。
- 通用 exactly-once side effects。
- 任意外部系统原子性。
- 语义级 long-horizon reasoning。
- “模型智能更强”之类结论。
- “超过 Codex、OpenHands、Gemini CLI、LangChain、Letta 或其他项目”的结论。

RuntimeBench 是 synthetic、deterministic、offline、local 的。它不使用真实
API provider、网络服务、统计重复运行分析或生产 workload trace。

## Roadmap

- V0.8：Multi-Agent Runtime alpha release freeze。
- V0.9：Persistent Memory Runtime architecture。
- V1.0：Stable Agent Runtime Kernel baseline。

V0.9 尚未在本 release 中实现。
