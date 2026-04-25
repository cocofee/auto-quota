# 2026-04-25 R2/LTR 自动诊断与下一步计划

## 接续口令

清理对话后，直接说：

```text
读取 2026-04-25 R2/LTR 自动诊断文档，继续 R2-1。
```

执行时先读本文档，再读 `docs/2026-04-24-算法修复计划.md` 和 `docs/2026-04-24-R1-2-candidate-pool-merge-验收记录.md`。不要重新全仓库发散分析。

## 本轮范围

本轮只做 R2/LTR 自动诊断工具和验收记录，不修改 LTR、CGR、Picker、final validator 的排序逻辑。

新增诊断入口：

```powershell
python tools/export_r2_ltr_diagnostics.py --input reports/attribution/r1_2_zhejiang_only_latest_after_neighbor.json --output-csv reports/attribution/r2_ltr_diagnostics_after_r1_2.csv --summary-output reports/attribution/r2_ltr_diagnostics_after_r1_2_summary.json
```

配套测试：

```powershell
python -m pytest tests/test_export_r2_ltr_diagnostics.py -q
python -m py_compile tools/export_r2_ltr_diagnostics.py
```

## 诊断口径

R1-2 验收文档中的 `R2 LTR 选错 = 201` 是 `rank_miss` 口径：正确答案在召回池中，但 `post_ltr_top1_id` 不正确，且没有在 LTR 前排到 top1。

本轮 R2/LTR 治理口径额外纳入 `pre_ltr` 已正确但被 LTR 翻错的样本：

| 类型 | 数量 |
|---|---:|
| `in_pool_not_ltr_top1` | 201 |
| `ltr_bad_flip_pre_correct` | 45 |
| 合计 | 246 |

因此后续 R2/LTR 修复应同时看两类：一类是 LTR 没把正确候选推到第一，另一类是 LTR 把原本正确的第一名推翻。

## 浙江市政 R1-2 后诊断结果

输入：

- `reports/attribution/r1_2_zhejiang_only_latest_after_neighbor.json`

输出：

- `reports/attribution/r2_ltr_diagnostics_after_r1_2.csv`
- `reports/attribution/r2_ltr_diagnostics_after_r1_2_summary.json`

分桶：

| bucket | 数量 | 含义 |
|---|---:|---|
| `structure_signal_sparse` | 103 | 正确候选与错误候选都缺少稳定结构化锚点，LTR 主要靠语义/混合分排序 |
| `oracle_missing_from_snapshot` | 42 | 召回命中，但正确候选未进入 `candidate_snapshots`，当前快照不可直接解释 |
| `pre_ltr_correct_overturned` | 42 | LTR 前 top1 正确，但缺少强结构锚点，被 LTR 翻错 |
| `oracle_beyond_snapshot_window` | 37 | 正确候选在召回池较后位置，超出当前 top snapshot 窗口 |
| `hybrid_over_param` | 12 | 正确候选参数更强，但错误候选混合分优势压过参数 |
| `selected_struct_conflict` | 4 | LTR 选中的候选存在结构冲突仍胜出 |
| `pre_ltr_correct_anchor_overturned` | 3 | LTR 前正确且有结构锚点，仍被翻错 |
| 其他小桶 | 3 | 个别低位、语义压结构锚点等 |

关键 tag：

| tag | 数量 |
|---|---:|
| `selected_semantic_advantage` | 65 |
| `selected_hybrid_advantage` | 63 |
| `selected_snapshot_top1` | 60 |
| `correct_param_stronger` | 50 |
| `pre_ltr_was_correct` | 45 |

## 当前判断

R2 的最大问题不是先重训模型，而是诊断显示结构化信号在大量样本上稀疏：`structure_signal_sparse = 103`。如果直接重训，模型仍会依赖语义/混合分，容易继续把同名近义但工程含义不同的候选排到前面。

第二大问题是可观测性不足：`oracle_missing_from_snapshot + oracle_beyond_snapshot_window = 79`。这些样本虽然召回命中，但正确候选不在当前候选快照窗口内，无法对 LTR 特征做充分比较。后续如果要修这类，需要先扩大诊断快照或单独导出正确候选特征，不应把它混进模型调参结论。

第三类是明确 LTR 翻错：`pre_ltr_correct_overturned + pre_ltr_correct_anchor_overturned = 45`。这类适合做 R2-1 的最小修复，因为不需要改召回，只需保护 LTR 不要推翻明显正确的 pre-LTR top1。

## R2-1 修复计划

目标：只修 `ltr_bad_flip_pre_correct`，不处理全部 R2，不重训模型，不改召回。

建议方向：

1. 在 LTR 后增加一个保守保护门：当 `pre_ltr_top1_id` 具备足够参数/结构/特征优势时，禁止 LTR 翻成明显更弱或结构冲突的候选。
2. 保护条件必须窄：只覆盖 `pre_ltr` 已正确的可观测模式，不能扩大到所有同类词、同册号或语义相近候选。
3. 优先覆盖 `pre_ltr_correct_anchor_overturned`、`selected_struct_conflict`、`hybrid_over_param` 中可解释样本；`structure_signal_sparse` 暂不在 R2-1 修。

R2-1 验收命令：

```powershell
python -m pytest tests/test_ltr_ranker_v2.py tests/test_export_r2_ltr_diagnostics.py -q
python -m py_compile src/ltr_ranker.py tools/export_r2_ltr_diagnostics.py
python tools/run_benchmark.py --province 浙江省市政 --json-only --profile full --summary-json-out reports/attribution/r2_1_zhejiang_only_summary.json --latest-result-out reports/attribution/r2_1_zhejiang_only_latest.json
python tools/export_r2_ltr_diagnostics.py --input reports/attribution/r2_1_zhejiang_only_latest.json --output-csv reports/attribution/r2_1_ltr_diagnostics.csv --summary-output reports/attribution/r2_1_ltr_diagnostics_summary.json
```

R2-1 通过标准：

| 指标 | 门槛 |
|---|---:|
| 浙江市政命中数 | `>= 129` |
| `ltr_bad_flip_pre_correct` | `< 45` |
| `pre_ltr_correct_anchor_overturned` | `<= 3`，目标下降 |
| R1 召回未命中 | `<= 164` |
| R3/R4 合计 | 不高于 R1-2 后基线 `47` |
| 单元测试 | 全绿 |

若 `ltr_bad_flip_pre_correct` 下降但总命中不升，必须检查是否只是把错误从 LTR 转移到 CGR/Picker/final validator，不能进入下一步。

## 本轮验收

已执行：

```powershell
python -m pytest tests/test_export_r2_ltr_diagnostics.py -q
python -m py_compile tools/export_r2_ltr_diagnostics.py
python tools/export_r2_ltr_diagnostics.py --input reports/attribution/r1_2_zhejiang_only_latest_after_neighbor.json --output-csv reports/attribution/r2_ltr_diagnostics_after_r1_2.csv --summary-output reports/attribution/r2_ltr_diagnostics_after_r1_2_summary.json
```

结果：

- `tests/test_export_r2_ltr_diagnostics.py`: `3 passed`
- 诊断导出成功
- 本轮未修改排序算法逻辑

