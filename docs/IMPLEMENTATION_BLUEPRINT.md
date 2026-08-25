# AgentKernel → AgentOS 实现蓝图

> 目的：这不是面向最终用户的产品说明，而是面向“后续实现 AgentKernel 的大模型/工程师”的工程大纲。
>
> 每个阶段都回答四个问题：
> 1. 要实现什么；
> 2. 这一层必须保证什么不变量；
> 3. 主要参考谁；
> 4. 什么先不要做。

---

## 0. 总目标

最终目标不是复制 DeepSeek Harness，而是构建：

```text
                    AgentOS
┌─────────────────────────────────────────────┐
│ Applications / Business Agents              │
├─────────────────────────────────────────────┤
│ Profiles / Skills / Workflows / Plugins     │
├─────────────────────────────────────────────┤
│ Agent Syscall ABI                           │
├═════════════════════════════════════════════┤
│ AgentKernel                                 │
│ Process │ Scheduler │ Context VM │ Security │
│ Session │ IPC       │ Resource   │ Audit    │
├─────────────────────────────────────────────┤
│ Drivers                                     │
│ LLM │ FS │ Browser │ DB │ MCP │ SaaS        │
├─────────────────────────────────────────────┤
│ Host OS / Sandbox                           │
└─────────────────────────────────────────────┘
```

### AgentKernel 的长期职责

- Agent/process 生命周期
- Tool/syscall boundary
- Session/event source of truth
- Context address space / working set
- Capability / namespace
- Resource accounting / scheduling
- IPC / child agents
- durable side-effect protocol
- cancellation / signals
- runtime introspection
- driver/plugin stable interfaces

### Kernel 不应该承担

- 某个业务的 Prompt
- 某个业务的 Tool
- 某个模型厂商细节
- 客服/科研/Coding 的业务规则
- UI
- 特定 RAG 实现
- 特定数据库实现

---

# 1. 设计基线：参考谁

## 1.1 DeepSeek Harness：Agent runtime 参考实现

重点参考：

### `packages/core`
学习：
- `session`
- `system-prompt`
- `tools`
- `agent`
- `agent-loop`
- `scope`

核心借鉴：
- Agent contract 与 concrete loop 分离
- Session event log 是 source of truth
- Tool model schema 与 host execution metadata 分离
- Tool guarded pipeline
- service seam
- extension through events/plugins

官方入口：
- `docs/architecture.md`
- `docs/subsystems/core.md`
- `docs/subsystems/session.md`
- `docs/subsystems/tools.md`
- `packages/core/README.md`

### `packages/compaction`
学习：
- Compaction 是 capability seam，不硬编码进 loop
- token pressure
- summary checkpoint
- model-free tool-result pruning

入口：
- `docs/subsystems/compaction.md`
- `packages/compaction/README.md`

### `session-query`, `subagent`, `jobs`, `sandbox`, `skill`
等进入对应阶段后再读，不要 V0 一次照搬。

---

## 1.2 Linux：系统设计参考

### Process / Scheduler
参考：
- Linux scheduler documentation
- process state / parent-child / wait / signal 思想

迁移到 Agent：
- AgentControlBlock
- READY/RUNNING/WAITING/PAUSED/EXITED
- budget scheduling
- priority/deadline
- child process/subagent

### cgroup v2
参考：
- hierarchy
- resource controllers
- delegation

迁移到 Agent：
- hierarchical token budget
- cost budget
- tool-call quota
- child-agent quota
- browser/network quota

### Linux capabilities
参考：
- 把 root 权限拆成独立 capability
- permitted/effective/bounding set
- child inheritance

迁移：
- `fs.read`
- `fs.write:/workspace`
- `net.http:github.com`
- `db.query:prod`
- `email.send`
- capability bounding set

### Linux namespaces
参考：
- 为进程提供隔离的资源视图

迁移：
- Tool namespace
- Resource namespace
- Credential namespace
- Memory namespace
- Network namespace

### Linux VFS
参考：
- stable interface + multiple implementations
- open/read/write/stat
- filesystem driver abstraction

迁移：
- Virtual Resource Layer
- `file://`
- `web://`
- `mail://`
- `memory://`
- `session://`
- `artifact://`

---

# 2. 不变量（后续任何大模型改代码前先检查）

这是 AgentKernel 最重要的一页。

## K1. LLM 永远不是 Kernel

LLM 是非确定、不可信的决策执行引擎。

它可以：
- 请求能力
- 请求 Tool
- 规划
- 生成参数

它不能直接：
- 修改权限表
- 修改审计日志
- 绕过 Tool Registry
- 直接持有 credential
- 修改 Kernel invariant

## K2. 所有外部副作用经过 syscall/tool boundary

禁止业务代码直接从 loop：
- 发邮件
- 写 DB
- 写文件
- 调浏览器

全部通过受控 provider/tool。

## K3. Session Event Log 是事实来源

`messages[]` 只是 projection。

后续：
- compaction
- UI
- replay
- recovery
- evaluation

全部从事件数据面派生。

## K4. Model-facing schema 与 host runtime object 分离

模型只能看到：
- tool name
- description
- argument schema

不能看到：
- Python callable
- credential
- timeout implementation
- scheduler metadata
- presentation callback
- internal policy

## K5. Mechanism 与 Policy 分离

Kernel 提供：
- capability check
- scheduling mechanism
- context eviction mechanism
- approval hook

Policy 决定：
- 批不批准
- 淘汰什么
- 谁优先
- 哪种业务可使用什么

## K6. Child privilege 不得超过 parent bounding set

后续 subagent/fork 必须满足：

```text
child.capability <= parent.bounding_set
child.resource_budget <= delegated_parent_budget
```

## K7. Durable side effect 必须可对账

最终需要：

```text
prepare → execute → commit/reconcile
```

不能只依赖“tool/result 是否写进 session”。

---

# 3. V0.1：Agent Spine（当前 MVP）

目标：跑通一条最小但边界正确的 Agent path。

## 3.1 `protocol.py`

实现：
- Message
- ToolCall
- ToolResult
- ToolSchema
- ModelRequest
- ModelResponse

参考：
- DeepSeek Harness `packages/llm`
- OS syscall ABI 的稳定接口思想

验收：
- Agent Loop 不依赖 OpenAI/DeepSeek SDK 类型。

---

## 3.2 `llm.py`

实现：
- `LLMService`
- `ScriptedLLM`

下一步 Provider：
- OpenAI-compatible adapter
- DeepSeek adapter
- Anthropic adapter
- vLLM adapter

不变量：
- Provider 做 wire translation；
- Kernel 只看内部 protocol。

参考：
- DeepSeek Harness LLM seam
- device-driver abstraction

---

## 3.3 `session.py`

当前：
- append-only events
- seq
- `derive_messages()`

下一步：
- JSONL persistence
- SQLite persistence
- header/checkpoint
- replay

参考：
- DeepSeek `core/session`
- event sourcing
- journal/WAL 思想

验收：
- 删除任何额外 chat-history cache 后，仍能从 event log 重建模型消息。

---

## 3.4 `tools.py`

当前：
- ToolDefinition
- ToolRegistry
- model schema projection
- capability check
- stable errno-like failures

下一步：
- schema validation
- timeout
- cancellation
- pre-execute policy
- guard
- middleware waterfall
- post-execute
- concurrency metadata

参考：
- DeepSeek `core/tools`
- Linux syscall boundary
- errno
- capability checking

---

## 3.5 `agent.py`

当前：
- AgentControlBlock
- AgentState
- AgentBudget
- capability + bounding set

下一步：
- parent/child
- generation
- namespace IDs
- open handles
- pending I/O
- exit code
- statistics

参考：
- PCB/process model

---

## 3.6 `loop.py`

当前：
- Turn
- Step
- LLM
- Tool calls
- Tool results
- step/tool budgets

原则：
- 保持薄。
- 禁止业务 if/else。

下一步：
- Inbox
- steering
- cancellation
- stream assembler
- parallel tool dispatch
- exclusive barriers

参考：
- DeepSeek `agent-loop`

---

## 3.7 `hooks.py`

当前：
- notification hook

下一步：
- serial hooks
- waterfall middleware
- reversible registration
- scoped hook
- runtime generation

参考：
- DeepSeek Cordis
- middleware/event bus
- kernel extension hooks

---

# 4. V0.2：Persistence + Recovery

这是第一个真正应该做的增强阶段。

实现：

```text
SessionPersistence
├── JSONL
└── SQLite
```

新增：
- flush/checkpoint
- restart/replay
- recovery tests
- operation id

重点测试：
1. LLM 调用前 crash
2. tool execute 前 crash
3. tool execute 后、result 写入前 crash
4. result 写入后 crash

参考：
- DeepSeek session persistence
- SQLite WAL
- journaling filesystem

---

# 5. V0.3：Tool Transaction / Agent WAL

解决外部副作用“执行成功但 Kernel crash”的问题。

事件：

```text
tool/prepare
tool/dispatch
tool/commit
tool/reconcile
tool/abort
```

每个 mutation tool 必须支持：
- `operation_id`
- 幂等 key，或
- `reconcile(operation_id)`

典型资源：
- email send
- DB update
- payment/order
- Git push
- approval submission

参考：
- WAL
- transactional outbox
- idempotency key

---

# 6. V0.4：Context VM

不要从“Memory VectorDB”开始。

先实现：

```text
Raw Session Log
      ↓
Surface
      ↓
Context Pages
      ↓
Working Set Policy
      ↓
Model Context
```

## ContextPage

计划字段：

```text
page_id
kind
source_uri
token_cost
priority
temperature: hot/warm/cold
pinned
trust_label
last_access
dependencies
summary_of
```

## Context policy

动作：
- pin
- evict
- summarize
- spill
- page_in

参考：
- DeepSeek Session Surface/Compaction
- Linux virtual memory
- working set / page cache

第一版策略可以简单：
1. Kernel/system policy 永久 pinned
2. current goal pinned
3. recent interaction hot
4. tool large result handle 化
5. old history compact
6. demand retrieval

---

# 7. V0.5：Virtual Resource Layer / VFS

统一资源访问：

```text
file://workspace/...
artifact://...
session://...
memory://...
web://...
github://...
mail://...
```

核心 API：

```text
stat(uri)
open(uri)
read(handle, offset, limit)
write(handle, data)
list(uri)
search(uri, query)
close(handle)
```

关键点：
- 不要求所有后端行为完全一样；
- 统一的是资源生命周期和访问协议。

参考：
- Linux VFS
- DeepSeek filesystem/provider seams

收益：
- 大型 Tool Result 不再直接塞 prompt
- Context 只保留 preview + resource handle
- 权限可以绑定 handle

---

# 8. V0.6：Capability + Namespace

## Capability

实现：
- permitted
- effective
- bounding
- delegated

Capability 使用结构化 scope，而非只用字符串：

```text
Capability(
  action="fs.write",
  resource="/workspace/**",
  constraints={...}
)
```

## Namespace

至少：
- tools
- resources
- credentials
- network
- memory

参考：
- Linux capabilities
- Linux namespaces
- DeepSeek guarded tool execution/sandbox

原则：
- “看不到”优于“Prompt 告诉它不要访问”。

---

# 9. V0.7：Process / Scheduler / cgroup

Agent 从单 loop 升级成 process。

## Process Manager

实现：
- spawn
- wait
- signal
- exit

后续：
- fork
- exec

## Scheduler

第一版：
- round-robin / priority
- cooperative yield
- max concurrency

## Agent cgroup

资源 controller：
- token
- cost
- wall time
- model calls
- tool calls
- subagent count
- browser sessions
- network requests

参考：
- Linux scheduler
- cgroup v2
- DeepSeek jobs/subagent

---

# 10. V0.8：IPC + Subagent

先定义 IPC，再做复杂 Workflow。

基础：
- mailbox
- structured message
- signal
- shared artifact handle

消息不要传完整历史：

```text
{
  type,
  source_agent,
  target_agent,
  payload,
  artifact_refs,
  correlation_id
}
```

再实现：
- spawn child
- delegate task
- wait/join
- child report

参考：
- OS IPC
- DeepSeek subagent

---

# 11. V0.9：Plugin Runtime

这时再把 plugin 做强。

需要：
- service registry
- dependency declaration
- lifecycle
- reversible effects
- scope
- hot reload
- runtime generations

区别于 DeepSeek Harness：
- 不建议所有 Kernel invariant 都可替换。
- security/accounting/audit boundary 属于 tiny trusted kernel。

参考：
- Cordis
- kernel module / driver model
- RCU/epoch（热更新阶段）

---

# 12. V1：AgentOS

AgentKernel 成熟后才做上层 OS。

AgentOS 增加：

```text
Profiles
Bundles
Skills
Business Plugins
App/CLI/Web UI
Supervisor
Settings
Credentials
Workspace
Package Manager
Driver Registry
Observability
```

可形成：

```text
AgentOS
  ├── Safe profile
  ├── Developer profile
  ├── Research profile
  └── Business profile
```

---

# 13. 推荐实现顺序

严格建议：

```text
V0.1 Agent Spine
      ↓
V0.2 Persistence / Recovery
      ↓
V0.3 Tool WAL
      ↓
V0.4 Context VM
      ↓
V0.5 VFS / Handles
      ↓
V0.6 Capability / Namespace
      ↓
V0.7 Process / Scheduler / cgroup
      ↓
V0.8 IPC / Subagent
      ↓
V0.9 Plugin Runtime
      ↓
V1 AgentOS
```

不要第一周就：
- multi-agent
- graph workflow
- vector memory
- dynamic plugin generation
- complex UI

先把 kernel spine 和 durable semantics 做对。

---

# 14. 每次让大模型实现模块时的任务模板

后续实现某模块时，把下面内容一起给模型：

```text
你正在实现 AgentKernel 的 <MODULE>。

先阅读：
1. docs/IMPLEMENTATION_BLUEPRINT.md
2. 与该模块相关的现有 interface/tests
3. 对应参考实现（DeepSeek Harness / Linux subsystem）

必须遵守 Kernel Invariants K1-K7。

任务：
- 说明该模块属于 mechanism 还是 policy
- 列出 public interface
- 列出 durable events（如果有）
- 列出 error taxonomy
- 列出 capability checks
- 列出 cancellation/recovery semantics
- 实现最小功能
- 添加 unit tests
- 添加 crash/replay test（若涉及 side effect/session）
- 不把业务逻辑写入 Agent Loop
- 不直接依赖具体 LLM provider SDK 类型

完成后输出：
1. 改动文件
2. 接口说明
3. 不变量验证
4. 测试
5. 尚未实现的下一阶段内容
```

---

# 15. Definition of Done

AgentKernel 进入 1.0 前至少满足：

- [ ] LLM provider 可替换
- [ ] loop strategy 可替换
- [ ] session crash 后可恢复
- [ ] side-effect tool 可 reconcile
- [ ] model 不能绕过 capability
- [ ] child 权限/预算不能超过 parent
- [ ] context 可以 compact/page-in/page-out
- [ ] 大型资源用 handle，不强塞 prompt
- [ ] multi-agent 使用 IPC，不复制整段历史
- [ ] scheduler 能限制 token/cost/tool/subagent
- [ ] kernel 有统一 audit trace
- [ ] plugin 无法修改核心安全不变量
- [ ] 所有业务能力可在 kernel 外实现

这时它才真正从 Harness Demo 进入 Agent Kernel。
