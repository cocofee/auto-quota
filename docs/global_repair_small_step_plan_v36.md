# 全专业算法修复小步验收计划 v36

本文档是后续全专业算法修复的固定执行契约。以后需要修复时，不再重新讨论方案是否完善，直接从 Step 0 开始小步执行。

## 后续启动语

以后你想让我执行这个方案，可以直接说：

```text
按 docs/global_repair_small_step_plan_v36.md 执行全专业算法修复小步验收流程。从 Step 0 开始，只做当前步骤，完成后按文档的固定汇报格式汇报，不要扩大范围。
```

如果你想继续上一次已经完成的步骤，可以说：

```text
按 docs/global_repair_small_step_plan_v36.md 继续执行。以上一次产物为输入，只做下一步，完成后按固定汇报格式汇报。
```

如果你已经有 OpenClaw、audit、benchmark 结果，也可以说：

```text
按 docs/global_repair_small_step_plan_v36.md 执行。优先使用我提供的 latest/attribution/audit 输入；如果不符合全专业输入标准，先停止并说明原因。
```

## 总原则

- 主线固定为全专业优先，不用浙江-only、单专题、smoke 替代全专业判断。
- 每一步只交付一个最小结果；验收失败、超时、口径不清，就停止汇报。
- Step 0 到 Step 3 只做诊断工具和产物，不进入算法修复。
- 不回滚、不整理无关脏工作区；只改目标工具、必要测试和本轮产物。
- 所有输出必须记录输入路径、生成时间、schema_version。

## Step 0：确认输入

目标：找可用的全专业 benchmark 输入，不跑长任务，不改代码。

动作：
- 检查 `reports/attribution` 现有 latest/summary/attribution。
- 优先选择最新全专业产物。
- 全专业判定必须满足：
  - 非 `zhejiang_only`、非单专题、非 smoke。
  - 文件名或 metadata 显示 `global` / `full`。
  - 样本覆盖多专业或多省，不能只有单一切片。
- 输出实际采用的 `latest_path`、`attribution_path`、可选 `summary_path`。

验收：
- 找到可用输入路径，或明确“没有可用输入”。
- 时间上限 5 分钟。
- 不改代码。

失败退出：
- 没有可用输入时停止；下一步单独决定是否跑 full。

## Step 1：生成最小 CSV

目标：先看到全专业错误样本，不做复杂决策。

新增或扩展：
- `tools/build_global_repair_decision.py`

输入：
- 必须使用 Step 0 选出的实际路径。
- 不再假设固定文件名一定存在。

输出：
- `reports/attribution/global_repair_decision_table.csv`

CSV 字段固定为 10 个：
- `sample_id`
- `province`
- `error_stage`
- `attribution_category`
- `expected_ids`
- `selected_id`
- `recall_rank`
- `pre_ltr_top1_id`
- `post_ltr_top1_id`
- `post_final_top1_id`

错误样本定义：
- 优先使用 latest/attribution 中已有 `passed=false`、`is_correct=false`、`correct=false`、`status=wrong/failed`。
- 若没有显式字段，则用 `selected_id not in expected_ids` 判断。
- 无法判断的样本不进入错误 CSV，但计入诊断缺字段统计。

验收命令示例：

```powershell
python tools/build_global_repair_decision.py `
  --latest <Step0_latest_path> `
  --attribution <Step0_attribution_path> `
  --decision-table reports/attribution/global_repair_decision_table.csv
```

通过标准：
- CSV 存在。
- 表头完整且顺序固定。
- 至少 1 行错误样本。
- 工具内置校验通过。
- 时间上限 30 分钟。

失败退出：
- latest JSON 结构不兼容：停止，汇报需要适配的字段。
- CSV 为空：停止，确认输入是否真是 benchmark latest。

## Step 2：生成 summary

目标：知道最大错误桶和诊断字段是否足够。

新增输出：
- `reports/attribution/global_repair_decision_summary.json`

内容固定包含：
- `schema_version`
- `generated_at`
- `input_latest_path`
- `input_attribution_path`
- `wrong_total`
- `stage_counts`
- `missing_field_rate`
- `largest_bucket`

`missing_field_rate` 定义：
- 分母：`wrong_total`
- 分子：关键诊断字段不完整的错误样本数。
- 关键字段：
  - `error_stage`
  - `attribution_category`
  - `expected_ids`
  - `selected_id`
  - `recall_rank`
  - `pre_ltr_top1_id`
  - `post_ltr_top1_id`
  - `post_final_top1_id`

R 桶映射：
- R1：recall / candidate / retrieval / missing_candidate / recall_miss
- R2：ltr / rank / rerank / pre_ltr / post_ltr
- R3：cgr / guard / constraint / reasoning_guard
- R4：picker / category_safe / family_picker / final_pick
- R5：validator / final_validator / experience
- R6：data / label / expected / ambiguous / unknown / unclassified

冲突规则：
- `error_stage` 优先于 `attribution_category`。
- 两者都缺失或无法识别：归 R6。
- `missing_field_rate > 10%` 时，后续 action 强制为 `improve_diagnostics`。

验收：
- summary 存在。
- `wrong_total > 0`。
- `largest_bucket` 非空。
- `missing_field_rate` 可见。
- 时间上限 20 分钟。

失败退出：
- `largest_bucket` 无法判断：停止修 summary 口径。
- `missing_field_rate > 10%`：下一步只能补诊断字段，不能修算法。

## Step 3：生成唯一 next_action

目标：工具只给一个下一步，不并行发散。

新增输出：
- `reports/attribution/global_repair_next_action.json`

合法 action 仅允许：
- `improve_diagnostics`
- `fix_r1_recall`
- `fix_r2_ltr`
- `fix_r3_cgr`
- `fix_r4_picker`
- `fix_r5_validator`
- `review_data`

决策规则：
- `missing_field_rate > 10%`：`improve_diagnostics`
- 最大桶 R1：`fix_r1_recall`
- 最大桶 R2：`fix_r2_ltr`
- 最大桶 R3：`fix_r3_cgr`
- 最大桶 R4：`fix_r4_picker`
- 最大桶 R5：`fix_r5_validator`
- 最大桶 R6 或 unknown：`review_data`

next_action 必须包含：
- `schema_version`
- `generated_at`
- `action`
- `reason`
- `largest_bucket`
- `sample_count`
- `representative_sample_ids`
- `suggested_validation_scope`
- `input_latest_path`
- `input_attribution_path`

验收：
- JSON 存在。
- 只有一个 `action`。
- action 合法。
- action 与 summary 最大桶一致，除非 `missing_field_rate > 10%`。
- 时间上限 20 分钟。

失败退出：
- action 不唯一：停止。
- action 与 summary 冲突：停止。

## Step 4：按 action 做一个最小修复

目标：一次只修一个点，当天可验收。

允许范围：
- `improve_diagnostics`：只补诊断字段。
- `fix_r1_recall`：只改 query/router/candidate pool 中一个最小点。
- `fix_r2_ltr`：只改 LTR feature/guard 中一个最小点。
- `fix_r3_cgr`：只改 CGR guard 一个点。
- `fix_r4_picker`：只改 picker/category_safe 一个点。
- `fix_r5_validator`：只改 validator/experience 一个点。
- `review_data`：不改算法，只标记疑似数据问题。

验收顺序：
1. 先跑目标切片或相关单测。
2. 再跑 `next_action.suggested_validation_scope` 指向的最小 benchmark。
3. 输出 `changed`、`improved`、`regressed`、代表样本变化。

失败退出：
- 出现回归：停止，不叠加第二个修复。
- 目标样本没改善：停止，回到诊断。
- 修复跨越多个层级：停止拆小。

## 每轮固定汇报格式

每一步结束只汇报：

- 当前步骤
- 产物路径
- 验收命令
- 验收结果
- 是否通过
- 下一步唯一动作

