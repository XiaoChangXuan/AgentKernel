# MiniCode Task Manager Challenge

This directory is a deterministic, offline coding challenge for exercising MiniCode as an AgentKernel runtime workload.

The project is a small Python CLI task manager with JSON persistence, due-date parsing, task formatting, filtering, sorting, and exit-code behavior. It intentionally starts with several implementation defects distributed across the source tree. The test suite is the specification.

Do not treat this README as the answer key. It describes how to run the experiment, not how to fix the code.

## Baseline

From this directory:

```powershell
python -m pytest -q
```

The initial suite should execute successfully as a suite but report several failing tests. Those failures are the workload MiniCode should investigate.

## Recommended MiniCode Prompt

```text
当前项目存在多个失败测试。

请修复整个项目，使全部测试通过。

要求：
1. 不允许修改 tests。
2. 先运行测试了解整体失败情况。
3. 根据测试结果逐步定位实现问题。
4. 不要一次盲目修改大量文件。
5. 修改源码必须使用 apply_patch。
6. 每解决一类问题后运行相关测试。
7. 如果测试失败，根据真实测试输出继续分析。
8. 最后必须运行完整 python -m pytest -q。
9. 只有完整测试通过后才能结束任务。
10. 最后总结修改的文件和解决的问题。
```

## Reset

From the AgentKernel repository root:

```powershell
powershell -ExecutionPolicy Bypass -File .\experiments\reset_minicode_task_manager.ps1
```

This restores `experiments/minicode_task_manager/` from the pristine copy in `experiments/minicode_task_manager_reset/`.

## MiniCode Run Command

From the AgentKernel repository root, set provider configuration in the current PowerShell session:

```powershell
$env:AGENTKERNEL_LLM_BASE_URL = "http://llm.api.corp.qunar.com/v1"
$env:AGENTKERNEL_LLM_MODEL = "azure/gpt-5.4-2026-03-05"
$env:AGENTKERNEL_LLM_API_KEY = "<your-test-key>"

python -m minicode run `
  "当前项目存在多个失败测试。请修复整个项目，使全部测试通过。要求：1. 不允许修改 tests。2. 先运行测试了解整体失败情况。3. 根据测试结果逐步定位实现问题。4. 不要一次盲目修改大量文件。5. 修改源码必须使用 apply_patch。6. 每解决一类问题后运行相关测试。7. 如果测试失败，根据真实测试输出继续分析。8. 最后必须运行完整 python -m pytest -q。9. 只有完整测试通过后才能结束任务。10. 最后总结修改的文件和解决的问题。" `
  --workspace .\experiments\minicode_task_manager `
  --model openai-compatible `
  --allow-network `
  --approve always `
  --session-path .\experiments\minicode_task_manager\.minicode\session.jsonl `
  --trace-jsonl .\experiments\minicode_task_manager\.minicode\trace.jsonl `
  --max-turns 50 `
  --timeout-ms 30000
```

## Inspect Session And Trace

Use the `session_id` printed by MiniCode in its JSON result.

```powershell
python -m minicode trace `
  <session-id> `
  --workspace .\experiments\minicode_task_manager `
  --session-path .\experiments\minicode_task_manager\.minicode\session.jsonl
```

The raw observable trace is stored at:

```powershell
Get-Content .\experiments\minicode_task_manager\.minicode\trace.jsonl
```

The raw session journal is stored at:

```powershell
Get-Content .\experiments\minicode_task_manager\.minicode\session.jsonl
```

Resume with:

```powershell
python -m minicode resume `
  <session-id> `
  --workspace .\experiments\minicode_task_manager `
  --model openai-compatible `
  --allow-network `
  --approve always `
  --session-path .\experiments\minicode_task_manager\.minicode\session.jsonl `
  --trace-jsonl .\experiments\minicode_task_manager\.minicode\trace.jsonl `
  --max-turns 50 `
  --timeout-ms 30000
```

Final verification:

```powershell
Push-Location .\experiments\minicode_task_manager
python -m pytest -q
Pop-Location
```
