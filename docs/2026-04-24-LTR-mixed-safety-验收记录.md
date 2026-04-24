# 2026-04-24 LTR Mixed Safety 验收记录

## 已完成步骤

- 4.1：`tools/train_ltr_v2.py` 输出 do-not-break 违例明细。
- 4.2：复算 mixed safety 诊断模型，定位保护组失败。
- 4.3：修复保护组训练稳定性。
  - `equipotential_guard` 默认权重调为 `4.0`。
  - `safety_correct + train_and_eval` query 强制留在训练集，不再随机进入 holdout。
- 4.4：训练 `output/ltr/model_v2_mixed_safety_candidate`。
- 4.5：完成 full benchmark 验收。

## 关键提交

- `4898508 Tune equipotential LTR safety guard`
- `fc2c001 Keep protected LTR safety queries in training`

## 验收命令

```powershell
python -m pytest tests/test_train_ltr_v2.py tests/test_build_mixed_safety_ltr_dataset.py -q

python tools/train_ltr_v2.py `
  --input data/ltr_mixed_safety_training_data.csv `
  --output-dir output/ltr/model_v2_mixed_safety_candidate `
  --do-not-break-eval reports/attribution/do_not_break_eval.json

$env:LTR_V2_ENABLED='1'
$env:LTR_V2_MODEL_PATH='C:\Users\Administrator\Documents\trae_projects\auto-quota\output\ltr\model_v2_mixed_safety_candidate\ltr_v2_model.txt'
$env:LTR_V2_FEATURES_PATH='C:\Users\Administrator\Documents\trae_projects\auto-quota\output\ltr\model_v2_mixed_safety_candidate\ltr_v2_features.json'

python tools/run_benchmark.py `
  --profile full `
  --json-only `
  --summary-json-out output/benchmark_compare/ltr_v2_mixed_safety_candidate_summary.json `
  --attribution-json-out reports/attribution/ltr_v2_mixed_safety_candidate.json `
  --latest-result-out output/benchmark_compare/ltr_v2_mixed_safety_candidate_latest_result.json
```

## 验收结果

### 单测

- `tests/test_train_ltr_v2.py tests/test_build_mixed_safety_ltr_dataset.py`
- 结果：`16 passed`

### LTR candidate 训练

- `do_not_break.regression_guard_failed = false`
- `forced_train_queries = 346`
- `holdout_hit_at_1 = 0.5054`
- `holdout_hit_at_1_delta = +0.4839`
- train_and_eval 保护组无阻塞。

### Full Benchmark

产物：

- `reports/attribution/ltr_v2_mixed_safety_candidate.json`
- `output/benchmark_compare/ltr_v2_mixed_safety_candidate_summary.json`
- `output/benchmark_compare/ltr_v2_mixed_safety_candidate_latest_result.json`

指标：

| 指标 | 结果 | 门槛 | 结论 |
|------|------|------|------|
| 总命中率 | `35.7%` | `>= 33.8%` | 通过 |
| 召回命中率 | `72.3%` | 不明显低于 `72.5%` | 通过 |
| `R2_LTR选错` | `1364` | `< 1375` | 通过 |
| `R4_Picker推翻正确` | `204` | `<= 294` | 通过 |
| `R3_CGR推翻正确` | `65` | `<= 84` | 通过 |
| `do_not_break.regression_guard_failed` | `false` | `false` | 通过 |

## 下一步

LTR Mixed Safety 第一阶段已通过。下一轮进入第二阶段：`R1_召回未命中` 修复。

执行原则：

- 只处理召回问题，不同时改 Picker/CGR/LTR。
- 每轮只选一个召回模块。
- 先用 `reports/attribution/ltr_v2_mixed_safety_candidate.json` 定位 R1 Top 省份和样本。
- 验收命令仍以 full benchmark 为准，召回命中率需要高于当前 `72.3%`，总命中率不能下降。
