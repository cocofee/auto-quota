# V36 GOAL 小步优化协议

本文档是 V36/GOAL 模式的执行入口。目标不是描述所有历史细节，而是规定一个可长期自动推进、可停止、可回滚的状态机。

## 总目标

GOAL 模式固定闭环：

```text
诊断 -> 选择唯一动作 -> 最小修复 -> 局部验收 -> pending -> full/global 验收 -> 更新目标进度
```

长期目标：

```text
full/global top1 hit_rate >= 75.0%
recall@20 不下降
P95 不超过冻结基线 110%，除非 trade-off gate 明确放行
complexity_impact 只能是 decrease|neutral，increase 必须人工确认
release-check pass
```

核心原则：

- 所有状态迁移只能由 `tools/v36_gate.py goal-next` 输出。
- Codex 只执行 `agent_instruction`，不自行决定下一步。
- 每轮只允许一个 `execution_class`、一个 `target_common_issue`、一个 `repair_unit`。
- 默认不改算法；算法 patch 只有在 gate 明确授权且不需要人工确认时才允许。
- 不自动发布、不自动刷新正式知识库、不自动接受复杂度增加。

## GOAL 状态机

`goal-next` 必须把当前轮次归入一个状态：

| state | 进入条件 | 唯一允许动作 |
| --- | --- | --- |
| `preflight_blocked` | P0 hard block | 做 P0 治理 |
| `needs_baseline` | 缺合格 full/global 输入或输入半写/无效 | freeze-baseline 或准备 Step 5 |
| `needs_diagnostics` | 缺必要诊断字段、聚类字段或 selector state | 补诊断 |
| `ready_for_next_action` | 输入合格且需要生成 next_action | 运行 choose-next-action |
| `ready_for_step4` | 有合法 shared 簇且 pending 未满 | 执行一个最小 Step 4 |
| `pending_full_validation` | 局部 benchmark_pass 已登记 | 回到 Step 0 或等 Step 5 |
| `needs_step5` | pending 达到 5 条、发布前、版本/知识变化或用户要求 | full/global 验收 |
| `release_blocked` | full/global 或 release gate 未过 | 定位、回滚、治理 |
| `goal_complete` | 75% 目标和 release gate 都通过 | 停止，等待人工发布 |

停止条件：

- `pending_full_validation` 达到 5 条。
- 连续 3 次 full/global 净收益低于 1%。
- 连续 full/global 回归。
- flaky 累计 3 次未治理。
- data/runtime/code version tuple 变化导致 pending 失效。
- 需要 trade-off、release、generated knowledge、数据语义裁定或删除/移动产物。

## 权限边界

GOAL 默认可自动执行：

- `diagnostics`
- `governance_patch`
- 报告、manifest、queue、gate/test 的小范围修正

GOAL 默认需要人工确认：

- `step4_small_patch` 算法修复
- `long_validation` full/global benchmark
- generated/formal knowledge 刷新
- release
- trade-off
- data review 语义裁定
- 删除、移动、批量清理产物
- complexity increase

禁止：

- 按样本 ID 或公开 benchmark 答案硬编码。
- 用短切片、单省、smoke、dev profile 宣布长期目标进度。
- 在无合格 full/global 输入时生成全局 next_action。
- 在 `src/**` 之外绕过 owner 边界乱改算法。
- 在 `src/ltr_ranker.py`、`src/query_builder.py`、`src/param_validator.py`、`src/match_engine.py` 继续堆大段业务分支。

## 输入和版本元组

Step 0 只接受合格 full/global 输入。合格输入必须满足：

- latest/attribution/summary 可解析。
- attribution 有 `total`、`wrong_total`、`overall_hit_rate` 等 full/global 指标。
- all_errors 或 latest 与 attribution 的错误总量一致。
- benchmark 命令、profile、scoring_mode、seed 可追溯。

半写 JSON、空 JSON、缺指标 JSON 不能作为 `fresh` 输入。

版本元组拆分：

```json
{
  "data_version_tuple": {
    "knowledge_digest_hash": "",
    "quota_db_revision": "",
    "bill_corpus_revision": "",
    "vector_index_revision": ""
  },
  "runtime_version_tuple": {
    "embedding_model_version": "",
    "model_profile_hash": "",
    "seed": ""
  },
  "code_version_range": {
    "base_commit": "",
    "current_commit": "",
    "allowed_commits": []
  }
}
```

旧 flat `version_tuple` 字段可以保留兼容，但 pending/full-global 判断应优先使用拆分字段。小代码提交不应自动让 data/runtime 证据失效；data/runtime 或 generated knowledge 变化必须让旧 pending 进入 stale/Step 5。

## P0 门禁分级

P0 不再只有粗粒度 block。应拆为：

| severity | 含义 | 例子 |
| --- | --- | --- |
| `hard_block` | 不得继续算法或 release | secret、SSL bypass、无合格输入、generated knowledge 污染、pending 达上限 |
| `soft_block` | 可做治理/诊断，不做算法 | 大量脏产物、owner 边界不完整、code health 风险、data review 超阈值 |
| `warn_only` | 可继续，但必须记录风险 | artifact 多、历史 mojibake、非阻断大文件库存 |

`goal-next` 必须把 P0 结果转成唯一动作：

- hard block -> `p0_remediation` 或 `needs_baseline`
- soft block -> `governance_patch` 或 `diagnostics`
- warn only -> 可继续生成 next_action，但不得发布或刷新知识库

## 共性簇分层

不要用单一 `shared/weak_shared` 截断所有问题。共性分层：

| commonality | 建议阈值 | 允许动作 |
| --- | --- | --- |
| `strong_shared` | `sample_count >= 5` 且 `sample_ratio >= 1%` | 可进入 Step 4 |
| `medium_shared` | `sample_count >= 3` 或稳定跨输入复现 | 可进入 Step 4，但需更强反例 |
| `weak_shared` | `sample_count >= 2` | 诊断、聚类、data review |
| `singleton` | 单例 | 不做算法 patch |

选择器优先级：

```text
R1 recall/query/route/candidate_pool
> R2 rank/LTR
> R3 CGR/confidence
> R4 picker/final
> R5 experience
> R6 data_review/other
```

优先级不能覆盖 pending、data_review、missing_field_rate、owner 边界和 commonality 限制。

## 工具契约

所有工具必须是确定性的，不调用 LLM。

### preflight

只做轻量检查，不跑 benchmark，不修复。

输出要点：

- `p0_gate_status`
- `p0_severity`
- `selected_input`
- `version_tuple`
- `pending_full_validation_summary`
- `data_review_queue_summary`
- `recommended_p0_remediation_target`

### choose-next-action

读取 full/global summary、pending、data review、旧 manifest。

必须避免：

- 重复选择已处理 repair unit。
- 对 `blocked_by_next_stage` 继续叠同阶段 patch。
- 对 open data review issue 做算法硬修。

输出要点：

- `action`
- `execution_class`
- `target_common_issue`
- `repair_unit`
- `skipped_repair_units`
- `blocked_next_stage_repair_units`
- `selector_boundary_next_action`
- `suggested_validation_scope`

### goal-next

GOAL 唯一入口。

输出必须包含：

- `state`
- `decision`
- `execution_class`
- `autonomous_allowed`
- `requires_user_confirmation`
- `agent_instruction`
- `stop_reason`
- `autonomy_budget`
- `stop_conditions`

LLM/Codex 不得覆盖这些字段。

### validate-step4-manifest

程序复算：

- `partial_validation_status`
- `local_accuracy_impact`
- `local_speed_impact`
- `complexity_impact`
- `rollback_integrity`
- `policy_check_status`
- `regression_golden_status`
- `agent_claim_mismatch`

Step 4 只要求局部影响字段。`full_global_impact` 固定为 `pending_step5`，不得要求局部回合声明全局提升。

### register-validation

只允许 `benchmark_pass` 写入 pending。

不得写入 pending：

- `local_behavior_pass`
- `candidate_lifecycle_pass`
- `blocked_by_next_stage`
- `diagnostic_pass`

这些状态只能写 manifest，供下一轮 selector 跳过或转向下一阶段。

### update-goal-progress

只从 full/global summary 计算长期进度。LLM 不手填。

### release-check

release 必须同时检查：

- full/global
- pending 清空
- golden/holdout
- data review
- flaky
- version tuple
- generated knowledge 来源
- speed/complexity gate

## Step 0-5

### Step 0: 输入确认

运行：

```powershell
python tools\v36_gate.py preflight --out reports\attribution\v36_preflight.json
python tools\v36_gate.py goal-next --out reports\agent_state\v36_goal_next.json
```

如果输入无效，停止在 `needs_baseline`。不得使用短切片推导全局 next_action。

### Step 1: 生成决策表

由 `choose-next-action` 或 orchestrator 从合格 latest 生成。只输出产物，不修代码。

### Step 2: 生成 summary

必须包含错误桶、共性簇、数据质量队列、缺字段率、收益上限估算。

### Step 3: 生成唯一 next_action

只能输出一个动作。若所有候选都被 selector state 跳过，输出 `selector_boundary_next_action`，例如：

- `candidate_pool_subcluster_selection`
- `cgr_confidence_subcluster_selection`
- `data_review`
- `selector_manifest_review`

此时默认 `autonomous_allowed=false`，不得改算法。

### Step 4: 最小修复

算法修复必须满足：

- `target_common_issue.commonality` 是 `strong_shared` 或合格 `medium_shared`。
- owner scope 明确。
- 有 `goal_contribution`。
- 有 rollback plan。
- 有局部验证和反例。
- policy/golden/complexity 通过。

治理或诊断修复不得登记 pending。

### Step 5: full/global 验收

触发条件：

- pending 达到 5 条。
- 发布前。
- generated knowledge 刷新前后。
- data/runtime/code version tuple 变化。
- full/global 回归或连续低收益。
- 用户明确要求。

Step 5 逐条判定 pending：

- `full_validated`
- `rejected`
- `rollback_required`
- `stale_due_to_version_change`

## Pending Schema

每条 pending 至少包含：

```json
{
  "repair_id": "",
  "repair_unit_id": "",
  "source_manifest": "",
  "code_version_range": {},
  "data_version_tuple": {},
  "runtime_version_tuple": {},
  "local_validation_artifacts": [],
  "rollback_plan": {},
  "status": "pending_full_validation"
}
```

## 反过拟合治理

长期目标不能只看公开 full/global。必须逐步引入：

- holdout 或人工 blind set。
- 样本来源标记：`benchmark_derived`、`human_reviewed`、`holdout_derived`。
- 每轮理论收益上限估算。
- 连续低收益后的重新分桶。
- data review open rate 的发布门禁。

禁止把 benchmark 错题直接变成隐性训练集。

## 复杂度控制

默认复杂度预算：

```text
complexity_impact = decrease|neutral
```

`increase` 必须进入人工 trade-off。

优先级：

```text
已有机制 > owner 小模块 > 表驱动规则 > 配置开关 > 局部分支
```

巨型文件只能做桥接，不承载新业务分支。每个算法修复必须可回滚：

- `config_flag`
- `isolated_module_call`
- `git_revert`

## GOAL 默认策略

```text
如果 p0_gate_status=hard_block：只做 P0 治理。
如果缺 baseline：只做 freeze-baseline 或 Step 5 输入准备。
如果缺诊断字段：只做 improve_diagnostics。
如果有合法 shared 簇且 pending 未满：允许一个 Step 4 最小修复。
如果 pending 达到 5 条：强制 Step 5 full/global。
如果 full/global 回归：停止修复，定位或回滚。
如果连续低收益：重分桶、补诊断、检查数据质量。
```

## 推荐执行顺序

1. 修协议矛盾：version tuple 拆分、P0 分级、局部/全局 impact 分离。
2. 补 `goal-next` 最小状态机：只输出决策，不执行 patch。
3. 补 preflight/choose-next-action 状态读取，解决重复 repair unit。
4. 补 validate-step4-manifest 和 pending schema。
5. 补 update-goal-progress 和 release-check 闭环。
6. GOAL 才开始长期小步算法优化。

## 固定汇报格式

每轮结束只汇报：

```text
state:
decision:
execution_class:
autonomous_allowed:
requires_user_confirmation:
selected_input:
target_common_issue:
repair_unit:
changed_files:
validation:
pending_status:
release_gate_status:
accuracy_goal_progress:
next_minimal_action:
```

## 启动语

```text
按 docs/global_repair_small_step_plan_v36.md 执行 V36 GOAL 小步流程。
从 goal-next 开始，只执行 gate 给出的唯一 agent_instruction。
不要扩大范围，不自动刷新知识库，不自动发布。
```
