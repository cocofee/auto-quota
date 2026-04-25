# 2026-04-25 R2-2a surface orientation rescue 验收记录

## 本轮目标

继续 R2/LTR 修复，但只做一个窄口径子问题：当 LTR top1 的平面/立面方向与清单文本明显冲突，且 top5 内存在同一对偶子目的正确方向候选时，允许 guard 把 top1 救回。

本轮不做：
- LTR 模型重训
- R1 召回修改
- CGR / Picker / final validator 修改

## 改动文件

- `src/ltr_ranker.py`
- `tests/test_ltr_ranker_v2.py`

新增逻辑：
- `_quota_major_prefix()`：识别同册大前缀，防止跨册救援。
- `_surface_pair_base()`：去掉 `平面/立面` 后比较候选名称骨架，防止同册但不同材料误救。
- `_apply_surface_orientation_rescue()`：在 LTR guard 内执行窄口径救援。

救援必须同时满足：
- 清单文本明确要求墙面/立面，或屋面/楼地面/地面/顶棚等水平面。
- LTR top1 含相反方向。
- top5 内存在目标方向候选。
- 候选与 top1 同册大前缀。
- 去掉 `平面/立面` 后名称骨架一致。
- 候选 `param_score >= top_param_score - 0.05`。
- 候选 `rerank_score >= top_rerank_score - 0.20`。

## 验收命令

```powershell
python -m pytest tests/test_ltr_ranker_v2.py tests/test_export_r2_ltr_diagnostics.py -q
python -m py_compile src\ltr_ranker.py tools\export_r2_ltr_diagnostics.py
python tools\run_benchmark.py --province 浙江省市政 --json-only --profile full --summary-json-out reports\attribution\r2_2a_zhejiang_only_summary.json --latest-result-out reports\attribution\r2_2a_zhejiang_only_latest.json
python tools\export_r2_ltr_diagnostics.py --input reports\attribution\r2_2a_zhejiang_only_latest.json --output-csv reports\attribution\r2_2a_ltr_diagnostics.csv --summary-output reports\attribution\r2_2a_ltr_diagnostics_summary.json
```

## 验收结果

单元测试：
- `28 passed`

编译：
- `src/ltr_ranker.py` 通过
- `tools/export_r2_ltr_diagnostics.py` 通过

浙江市政 full 子集：

| 指标 | R1-2 后 | R2-1 后 | R2-2a 后 | 门槛 | 结果 |
|---|---:|---:|---:|---|---|
| 题数 | 541 | 541 | 541 | - | 通过 |
| 命中 | 129 | 150 | 154 | `>= 150` | 通过 |
| 命中率 | 23.8% | 27.7% | 28.5% | 不下降 | 通过 |
| 召回命中率 | 69.7% | 69.7% | 69.7% | 不下降 | 通过 |
| R1 召回未命中 | 164 | 164 | 164 | `<= 164` | 通过 |
| R2 LTR 选错 | 201 | 192 | 187 | 下降 | 通过 |
| benchmark `pre_ltr_correct_but_ltr_changed` | 45 | 43 | 44 | `<= 45` | 通过 |
| 诊断 `ltr_bad_flip_pre_correct` | 45 | 33 | 34 | `<= 45` | 通过 |
| 诊断 R2 总面 | 246 | 225 | 221 | 下降 | 通过 |
| `structure_signal_sparse` | - | 111 | 106 | 下降 | 通过 |
| `pre_ltr_correct_anchor_overturned` | 3 | 1 | 1 | `<= 3` | 通过 |
| R3 + R4 | 47 | 34 | 35 | `<= 47` | 通过 |

R2-2a 诊断输出：
- `reports/attribution/r2_2a_ltr_diagnostics.csv`
- `reports/attribution/r2_2a_ltr_diagnostics_summary.json`

诊断分桶：

| bucket | R2-1 后 | R2-2a 后 | 变化 |
|---|---:|---:|---:|
| `structure_signal_sparse` | 111 | 106 | -5 |
| `oracle_beyond_snapshot_window` | 39 | 39 | 0 |
| `oracle_missing_from_snapshot` | 32 | 32 | 0 |
| `pre_ltr_correct_overturned` | 31 | 32 | +1 |
| `selected_struct_conflict` | 6 | 6 | 0 |
| `hybrid_over_param` | 2 | 2 | 0 |

## 自我诊断

第一版只要求同册，会把 `改性沥青自粘卷材 立面` 误救到 `高分子卷材 平面`，导致 `pre_ltr_correct_but_ltr_changed` 回升到 48，未通过严格验收。

已收紧为同册且名称骨架一致，并补了回归测试：
- 允许同一材料/同一子目平立面对偶救援。
- 禁止跨册救援。
- 禁止同册不同材料救援。

## 结论

R2-2a 验收通过。该轮在不改变 R1、CGR、Picker 的前提下，把浙江市政 full 子集命中从 `150` 提到 `154`，R2 LTR 选错从 `192` 降到 `187`，R2 诊断总面从 `225` 降到 `221`。

下一步进入 R2-2b：继续处理最大剩余桶 `structure_signal_sparse`。不能直接放宽 rescue，也不能重训 LTR；应先从 `reports/attribution/r2_2a_ltr_diagnostics.csv` 抽样，确认剩余 106 条里是否存在稳定的结构信号缺口，再决定只补一个可观测信号或一个窄 guard。
