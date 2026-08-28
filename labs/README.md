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

V0.1-V0.3 notebook 会自动把 AgentKernel 仓库根目录加入 Python import path，
所以从仓库根目录或 `labs/` 目录打开都可以导入 `from labs import create_lab`。

如果只想在命令行看一个 lab：

```powershell
python -c "from labs import run_lab, render_lab; render_lab(run_lab('v03'))"
```

## 模式

每个 Notebook 默认：

```python
MODE = "deterministic"
```

这条路径离线、可重复、适合教学和 CI。它的语义是：

```text
Model decision = SCRIPTED
Kernel execution = REAL
```

也就是说，模型决策由固定脚本提供，但 ToolRegistry、Session、Recovery、
Capability、Resource、Process 等 AgentKernel 执行路径仍然是真实代码。

如果把 `MODE` 改成 `"real_model"`，V0.1-V0.3 flagship notebooks 会真正调用
OpenAI-compatible Lab LLM adapter。真实模型运行用于 demo，不作为 deterministic CI
oracle，也不会展示 hidden chain-of-thought。

真实模型配置优先使用 lab 专用环境变量：

```powershell
$env:AGENTKERNEL_LAB_LLM_BASE_URL="https://..."
$env:AGENTKERNEL_LAB_LLM_MODEL="..."
$env:AGENTKERNEL_LAB_LLM_API_KEY="..."
```

如果没有设置这些环境变量，Lab 会复用 MiniCode 的本地配置：

```json
{
  "model": "openai-compatible",
  "allow_network": true,
  "openai_compatible": {
    "base_url": "https://...",
    "model": "provider/model",
    "api_key": "<local secret>"
  }
}
```

也就是说，仓库根目录下的 `.minicode/config.json` 可以直接驱动
`MODE = "real_model"`。`.minicode/` 是本地 secret 配置目录，已经被
`.gitignore` 排除，不要提交。Notebook 只会展示 `base_url`、`model`、
`config_source`、`api_key_configured` 这类脱敏 metadata，不展示 key。

真实模式只有在 provider 请求成功时，才表示：

```text
Model decision = REAL OpenAI-compatible
Kernel execution = REAL
```

## Flagship interactive labs

V0.1-V0.3 已经重构成逐格运行的 stateful experiment controller：

```python
from labs import create_lab

lab = create_lab("v03", mode=MODE)
lab.setup()
lab.show_initial_state()
lab.model_step()
lab.prepare()
lab.dispatch()
lab.apply_effect()
lab.crash()
lab.restart()
lab.analyze()
lab.reconcile()
lab.summary()
```

这些 notebook 不再是一个 cell 调 `run_lab()` 执行完整实验，而是在关键 Kernel
boundary 停下来让你观察状态。

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
