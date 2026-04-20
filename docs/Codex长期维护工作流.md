# Codex 长期维护工作流

这份文档是 `auto-quota` 项目的固定工作协议。
目的只有一个：让 Codex 在长任务里靠文件恢复状态，而不是靠对话记忆。

## 1. 你以后怎么下任务

以后不需要每次重讲一大段。

你只要说本轮目标，例如：

```text
按仓库固定规则，修复 tests/test_query_router.py 当前失败项。
只改直接相关文件，先跑最小 pytest，再决定是否跑回归。
```

或者：

```text
读取 reports/agent_state/progress.json，处理下一个 pending batch。
完成后更新状态并运行该 batch 的验证命令。
```

## 2. 固定状态文件放哪里

统一使用：

```text
reports/agent_state/
  progress.json
  progress.template.json
  tasks/
    batch.template.json
    batch_xxx.json
  reports/
    report.template.md
    batch_xxx.md
```

建议约定：

- `progress.json`：唯一权威状态
- `tasks/*.json`：每个批次的自包含任务文件
- `reports/*.md`：每个批次执行后的结果记录

## 3. 主 agent 到底做什么

主 agent 的职责非常窄：

1. 读取 `progress.json`
2. 选择下一个 `pending` 或允许重试的 `failed` 批次
3. 读取对应的 `tasks/*.json`
4. 执行一个小批次
5. 跑验证
6. 更新 `progress.json`
7. 写 `reports/*.md`

也就是说，在没有显式多 agent 的情况下：

- 当前这一轮 Codex + 状态文件 = 主 agent

你不需要先追求复杂的 coordinator 模式。
先把“按文件调度”用熟，已经足够稳定。

## 4. auto-quota 的推荐回测层级

### 4.1 小修复

只跑目标测试：

```powershell
pytest tests/test_query_router.py -q
```

### 4.2 模块修复

跑直接相关的测试集合：

```powershell
pytest tests/test_query_router.py tests/test_query_builder*.py -q
```

### 4.3 影响检索/路由/排序质量

跑 smoke 回归：

```powershell
自动回归测试.bat smoke
```

### 4.4 影响主链质量

跑标准回归：

```powershell
自动回归测试.bat dev <pipeline_version>
```

配套主线脚本在：

- `自动回归测试.bat`
- `eval/run_regression.py`

## 5. 一轮任务的标准动作

每轮固定做这四步：

1. 读状态文件
2. 处理一个小批次
3. 跑最小必要验证
4. 回写状态和报告

不要默认说“继续上一次的工作”。
要默认说“读取状态文件后处理下一批”。

## 6. 什么时候该切批

出现下面任一情况，就应该切批，而不是继续拖长：

- 一个任务要改很多文件
- 已经开始重复检查
- 已经开始反复试错
- 需要依赖很多前文细节
- 同一个 session 做了很久还没稳定收敛

## 7. 推荐的短 prompt

### 7.1 处理一个测试问题

```text
按仓库固定规则，修复 tests/test_query_router.py 当前失败项。
只改直接相关文件。
先跑最小 pytest。
如果需要更大范围改动，先停止并汇报。
```

### 7.2 处理一个批次

```text
读取 reports/agent_state/progress.json 和 reports/agent_state/tasks/batch_query_router_01.json。
只处理该批次。
完成后更新 progress.json，并写 reports/agent_state/reports/batch_query_router_01.md。
```

### 7.3 做扫描，不做修复

```text
扫描当前失败测试，按模块切成小批次。
生成 progress.json 和 tasks/*.json。
先不要改业务代码。
```

## 8. 失败时怎么处理

建议固定规则：

- `retry_count = 0`：可正常再试
- `retry_count = 1`：允许最后一次定向重试
- `retry_count >= 2`：标记 `blocked`，不要无限烧 token

失败报告至少要写清楚：

- 哪个测试还失败
- 已尝试过什么
- 为什么超出当前批次范围
- 建议下一步怎么拆

## 9. 你最该形成的习惯

不是“怎么把 prompt 写得越来越聪明”，而是：

- 让任务边界越来越清楚
- 让验证越来越标准化
- 让状态越来越依赖文件

等你把这套用熟以后，你每次给 Codex 的输入通常只需要一句话。
