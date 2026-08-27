# AgentKernel Interactive Labs

These notebooks are the human-comprehension layer for AgentKernel V0.8.
They are separate from `examples/tutorials/`, which remain deterministic,
CI-friendly runnable scripts.

Start with the flagship lab:

```text
examples/labs/v0_3_durable_side_effect_lab.ipynb
```

It makes the duplicate-side-effect danger visible:

```text
PREPARE -> DISPATCH -> external success -> crash before COMMIT
      -> restart -> RECONCILE_REQUIRED -> commit existing result
```

Core lesson:

```text
Recovery != Retry
```

## Labs

| Version | Notebook | Teaching question |
| --- | --- | --- |
| V0.1 | `v0_1_agent_execution_lab.ipynb` | 一次 Agent 执行到底发生了什么？ |
| V0.2 | `v0_2_recovery_lab.ipynb` | Python 进程死掉以后，Agent 还记得什么？ |
| V0.3 | `v0_3_durable_side_effect_lab.ipynb` | 外部操作已经成功，但程序崩溃了，还能直接重试吗？ |
| V0.4 | `v0_4_context_vm_lab.ipynb` | Agent 历史越来越长，必须全部放进 Prompt 吗？ |
| V0.5 | `v0_5_resource_handle_lab.ipynb` | Tool 返回巨大输出，模型必须全部读取吗？ |
| V0.6 | `v0_6_capability_lab.ipynb` | 模型请求执行 Tool，就代表它拥有执行权限吗？ |
| V0.7 | `v0_7_process_runtime_lab.ipynb` | Agent 与 Process 为什么必须分开？ |
| V0.8 | `v0_8_multi_agent_runtime_lab.ipynb` | 把资源地址发送给 Child Agent，就等于给它权限了吗？ |

Optional real-provider lab:

```text
examples/labs/real_model_tool_trace_lab.ipynb
```

It is opt-in only. By default it prints a skip message and makes no network or
provider call.

## Running Locally

Jupyter is optional and is not a runtime dependency of AgentKernel.

```bash
python -m pip install jupyter
jupyter lab examples/labs
```

You can also open the notebooks directly in GitHub or VS Code. The Markdown
cells are written as a learning path, so the notebooks remain useful before
execution. Code cells are intentionally small so readers can run one operation,
inspect state, and then continue.

## Teaching Contract

Each lab follows the same teaching discipline:

- problem first, version/mechanism second;
- inspect state before and after a Kernel operation;
- expose observable runtime facts, not private chain-of-thought;
- end with `WHAT THIS DEMONSTRATES` and `WHAT THIS DOES NOT DEMONSTRATE`;
- keep deterministic V0.1-V0.8 labs offline;
- keep the real-model lab behind `AGENTKERNEL_RUN_REAL_MODEL=1`.

## OBSERVABILITY_FRICTION

The labs were implemented without changing `agentkernel/`. A few teaching cells
still need low-level setup to expose exact boundaries:

- V0.3 durable-side-effect crash points require explicit WAL event construction.
- Richer notebook traces would benefit from a stable public observer surface.
- The labs use lightweight tables rather than a full visualization framework.

Those are documentation/runtime API review notes, not V0.8 implementation
changes.
