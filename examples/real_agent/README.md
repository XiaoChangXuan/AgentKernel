# Real Model Trace Demos

这些 demo 用真实 OpenAI-compatible provider 展示 AgentKernel application flow。

它们不是默认 CI gate，也不是 benchmark。它们的目的只是让用户看到一个真实模型如何：

```text
receive context
  -> propose tool / operation
  -> Kernel checks request
  -> Tool executes or is denied
  -> observation returns
  -> Session records observable runtime facts
```

## 安全配置

必须显式 opt-in：

```bash
set AGENTKERNEL_RUN_REAL_MODEL=1
set AGENTKERNEL_LLM_BASE_URL=https://your-openai-compatible-endpoint/v1
set AGENTKERNEL_LLM_MODEL=your-model
set AGENTKERNEL_LLM_API_KEY=your-key
```

`AGENTKERNEL_LLM_API_KEY` 可选，取决于你的 provider。AgentKernel 不会自动加载 `.env`，不会提交 secret，也不会回退到公共 endpoint。

没有 `AGENTKERNEL_RUN_REAL_MODEL=1` 时，demo 会安全跳过。

## Demos

```bash
python examples/real_agent/basic_tool_trace.py
python examples/real_agent/capability_denial_trace.py
python examples/real_agent/resource_handle_trace.py
```

| Demo | Scenario | Real model role | Kernel mechanism |
| --- | --- | --- | --- |
| `basic_tool_trace.py` | Basic tool-use trajectory | consume `math.add` schema, call tool, answer from observation | Tool boundary, authorization, Session facts, Process state |
| `capability_denial_trace.py` | Capability denial | propose an operation as untrusted content | Kernel denial, denied ToolResult, Session facts |
| `resource_handle_trace.py` | Large output / ResourceHandle | request large deterministic logs and answer from bounded observation | Resource externalization, bounded ToolResult, Session facts |

可选输出机器可读 trace：

```bash
python examples/real_agent/basic_tool_trace.py --trace-jsonl traces/basic.jsonl
```

## What These Demonstrate

- real provider integration；
- model-visible tool schema consumption；
- provider-native tool call in the basic/resource demos；
- untrusted operation proposal in the capability denial demo；
- Kernel authorization decision；
- ToolResult or ResourceHandle observation；
- Session event trajectory。

## What These Do Not Demonstrate

- general model reasoning correctness；
- production reliability；
- production security；
- crash safety unless a demo actually injects crash；
- universal exactly-once；
- benchmark superiority over other systems。

Execution trajectory records observable runtime facts only. It does not require or expose private chain-of-thought.

These demos are not mandatory CI correctness gates. They are opt-in examples
for humans who have explicitly configured a provider and accepted any network
or provider cost.
