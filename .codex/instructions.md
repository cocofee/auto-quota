# auto-quota Codex Project Instructions

## Project Context

This repository is a long-lived maintenance project for the `auto-quota` system.
The system reads engineering quantity sheets, matches quota items, writes result files,
and maintains quality through tests, health checks, and regression evaluation.

Codex should treat this repository as a persistent engineering workspace, not as a
one-shot prompt playground.

## Default Working Mode

When the user gives a task in this repository, use these defaults unless the user
explicitly overrides them:

1. Read the local repository state before acting.
2. Prefer small, bounded patches over broad refactors.
3. Do not rely on previous chat history for long tasks.
4. For multi-step work, persist progress to files.
5. Verify the changed area before claiming completion.

## Internal Rules

### 编码前思考
陈述你的假设。不确定时提问。绝不猜测。

### 简洁优先
写出解决这个问题的最少代码。
不要添加没人要求的抽象。

### 做精准的修改
不要修改与请求无关的代码。
每行更改都必须追溯到所要求的内容。

### 以目标为导向的执行
将模糊的指令转化为可验证的成功标准。
在编写任何一行代码之前。

## Long-Task Workflow

For tasks that may take multiple rounds, use file-backed state instead of chat memory.

Default state directory:

- `reports/agent_state/progress.json`
- `reports/agent_state/tasks/*.json`
- `reports/agent_state/reports/*.md`

Workflow:

1. Scan or define the work and split it into small batches.
2. Store batch definitions in `reports/agent_state/tasks/`.
3. Track status in `reports/agent_state/progress.json`.
4. Process one batch at a time unless the user explicitly asks for parallel work.
5. After each batch, update the state files and write a short report.

Batch status values:

- `pending`
- `in_progress`
- `completed`
- `failed`
- `blocked`

If a batch exceeds its scope, needs a cross-module refactor, or has no measurable
progress after repeated attempts, mark it `blocked` and report the blocker.

## What A Good Task Looks Like

Prefer tasks with all of the following:

- clear goal
- limited file scope
- explicit validation command
- stop condition
- required state-file updates

Avoid vague instructions such as:

- "continue the previous work"
- "fix everything"
- "based on the previous analysis"

Prefer instructions such as:

- "Read `reports/agent_state/progress.json` and process the next pending batch."
- "Only fix failures listed in `reports/agent_state/tasks/batch_query_router_01.json`."
- "Run the listed validation commands and update the report file."

## Default Validation Ladder For auto-quota

Use the smallest useful verification first, then expand only when needed.

1. Targeted tests
   Example: `pytest tests/test_query_router.py -q`
2. Related module tests
   Example: `pytest tests/test_query_router.py tests/test_query_builder*.py -q`
3. Regression runner for matching quality changes
   Example: `自动回归测试.bat smoke`
4. Standard regression for ranking, routing, or retrieval changes
   Example: `自动回归测试.bat dev <pipeline_version>`

Guidance:

- Small bug fix: run targeted pytest first.
- Query builder / routing / matcher changes: run targeted tests plus smoke regression.
- Changes likely to affect ranking quality: run the standard regression path.
- Do not default to full-repo or full-regression runs unless the change justifies it.

## Stop And Ask The User When

Stop and ask before proceeding if any of the following is true:

- the fix requires a large cross-module redesign
- the task needs schema, deployment, or irreversible data changes
- the requested behavior conflicts with current project policy
- the work would touch clearly unrelated files

## Review Standard

When asked to review code, focus on:

1. correctness
2. regression risk
3. missing or weak validation
4. safety and compatibility

Severity:

- `P0`: system cannot run, data corruption, or core workflow failure
- `P1`: clear functional bug or major quality regression
- `P2`: edge case, maintainability issue, or lower-risk defect

Review output format:

```text
## Review Result: pass / needs changes

### Findings
1. [P0/P1/P2] path:line - issue - suggested fix

### Risks
- residual risk if any

### Summary
- one-sentence conclusion
```

## Expected End-Of-Task Output

After making code changes, report:

1. what changed
2. what was verified
3. any remaining risk or blocker

Do not say the work is complete before the requested validation has run, unless you
explicitly state that validation could not be executed.
