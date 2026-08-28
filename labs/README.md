# AgentKernel Interactive Labs

这一层不是替代 `pytest`，也不是替代 RuntimeBench。

AgentKernel 现在有三层验证：

1. Unit / CI Tests：`pytest`，给开发者，回答实现有没有坏。
2. Interactive Labs：`labs/*.ipynb`，给新用户和 demo，回答每个 Runtime 机制为什么存在。
3. Real Agent Benchmark：MiniCode + Real LLM workload，回答长任务里这些机制能不能托住真实 agent。

## 八个问题

| Version | Lab | 问题 |
| --- | --- | --- |
| V0.1 | `v01_execution_spine.ipynb` | 一个 LLM ToolCall 究竟经过 Kernel 哪些层？ |
| V0.2 | `v02_crash_recovery.ipynb` | Agent 跑一半程序崩了，为什么还能继续？ |
| V0.3 | `v03_durable_side_effect.ipynb` | 文件已经修改但程序突然崩了，为什么不会重复修改？ |
| V0.4 | `v04_context_vm.ipynb` | 跑 50 轮后 Session 很大，为什么不全部塞给模型？ |
| V0.5 | `v05_large_output.ipynb` | pytest 输出几 MB，为什么不会把 Context 撑爆？ |
| V0.6 | `v06_capability_denial.ipynb` | LLM 明明主动要求修改文件，Kernel 为什么可以拒绝？ |
| V0.7 | `v07_process_runtime.ipynb` | 一个 Process 预算耗尽，为什么 Agent authority 仍然独立？ |
| V0.8 | `v08_multi_agent.ipynb` | 两个 Agent 如何通信、授权和共享资源而不越权？ |

## 运行方式

从仓库根目录启动 Jupyter：

```powershell
jupyter lab labs
```

如果只想在命令行看一个 lab：

```powershell
python -c "from labs import run_lab, render_lab; render_lab(run_lab('v03'))"
```

## 模式

每个 Notebook 默认：

```python
MODE = "deterministic"
```

这条路径离线、可重复、适合教学和 CI。它使用 `ScriptedLLM` 或固定 fixture，但真实调用 AgentKernel/MiniCode 的 Kernel API。

如果把 `MODE` 改成 `"real_model"`，Notebook 会给出对应的真实模型 prompt 和 MiniCode 命令。真实模型运行用于 demo，不作为 deterministic CI oracle，也不会展示 hidden chain-of-thought。

## Lab 输出原则

每个 lab 都展示：

- User prompt
- Model visible context
- Assistant ToolCall 或 runtime action
- Kernel decision
- Tool / resource / process effect
- Session or runtime event
- A/B contrast
- 这个实验能证明什么
- 这个实验不能证明什么
