# 2026-04-27 R2-2e LTR guard confirmation 验收记录

## 本轮目标

继续处理浙江市政 R2 `structure_signal_sparse` 中正确候选已在 LTR 前列、但被通用 guard 回滚的小簇。

本轮重点样本：

- `347`：`透层、粘层`，文本为 `透油层乳化沥青 喷油量:0.9-1.0L/m2`。R2-2d 中 raw LTR top1 已经是正确的 `2-161`，但 bitumen rescue 因为候选已经在 top1 而返回未触发，随后 `weak_route_manual_margin` 把结果回滚到 `3-541`。

同时补一个窄保护：

- `100`：`水泥稳定碎（砾）石 调节层`。当前 generated knowledge 状态下，`水泥稳定碎石 + 专用摊铺机摊铺` 会被通用 guard 压回 `人铺` 候选；增加显式摊铺机语义确认，避免该类伴随回退。

本轮不修改 R1 召回、CGR、Picker，不重训 LTR。

## 改动范围

- `src/ltr_ranker.py`
  - `bitumen_layer_rescue` 在最佳候选已经是 LTR top1 时返回 `bitumen_layer_confirmed`，避免继续落入通用 weak-route guard。
  - 新增 `water_stabilized_paver_rescue`，仅在清单文本同时包含 `水泥稳定` 与 `摊铺机` 时，在 LTR top8 内确认/救援 `摊铺机摊铺` 且非 `每减` 的候选。
- `tests/test_ltr_ranker_v2.py`
  - 增加 `透油层乳化沥青` 已为 LTR top1 时不被 weak-route guard 回滚的回归测试。
  - 增加 `水泥稳定碎石 + 摊铺机摊铺` 候选确认测试。

## 验收命令

```powershell
python -m pytest tests\test_ltr_ranker_v2.py tests\test_export_r2_ltr_diagnostics.py -q
python -m py_compile src\ltr_ranker.py tools\export_r2_ltr_diagnostics.py
python tools\run_benchmark.py --province 浙江省市政 --json-only --profile full --summary-json-out reports\attribution\r2_2e_zhejiang_only_summary.json --latest-result-out reports\attribution\r2_2e_zhejiang_only_latest.json
python tools\export_r2_ltr_diagnostics.py --input reports\attribution\r2_2e_zhejiang_only_latest.json --output-csv reports\attribution\r2_2e_ltr_diagnostics.csv --summary-output reports\attribution\r2_2e_ltr_diagnostics_summary.json
python tools\diff_benchmark_results.py --base reports\attribution\r2_2d_zhejiang_only_latest.json --candidate reports\attribution\r2_2e_zhejiang_only_latest.json
```

## 验收结果

- 单元测试：`34 passed`
- `py_compile`：通过
- 浙江市政 full 子集：`160/541 = 29.6%`
- R1 召回未命中：`164`
- R2 LTR 选错：`181`，上一轮 R2-2d 为 `182`
- R2 诊断总面：`215`，上一轮 R2-2d 为 `216`
- `structure_signal_sparse`：`100`，上一轮 R2-2d 为 `101`
- `ltr_bad_flip_pre_correct`：`34`，与上一轮持平
- R3 + R4：`35`，与上一轮持平

## 差分复核

`tools/diff_benchmark_results.py` 显示：

- `changed_total = 1`
- `improved = 1`
- `regressed = 0`

改善样本：

| bill_id | bill_name | R2-2d | R2-2e | 说明 |
| --- | --- | --- | --- | --- |
| 347 | 透层、粘层 | `3-541` | `2-161` | `透油层乳化沥青` 已为 raw LTR top1，本轮确认后不再被 weak-route guard 回滚 |

伴随保护样本：

- `100`：R2-2d 与 R2-2e 均保持命中 `2-133`。R2-2e trace 中由 `water_stabilized_paver_confirmed` 保持 `摊铺机摊铺` 候选，避免当前 generated knowledge 状态下回退到 `2-129`。

## 结论

本轮验收通过。R2 LTR 选错下降 1，`structure_signal_sparse` 下降 1，无 outcome 回退。

下一步建议进入 R2-2f：继续从 `structure_signal_sparse` 选择一个小簇。`124` 的 `半刚性基层` vs `粒料基层` 仍保留，但证据依赖 stored_ids 与候选名称细粒度一致性，修复前必须先做 ID/名称反例检查。
