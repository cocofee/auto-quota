# 2026-04-26 R2-2d bitumen layer rescue 验收记录

## 本轮目标

只处理浙江市政 R2 `structure_signal_sparse` 中的 `透层、粘层` 小簇。目标是让 LTR guard 在清单文本明确出现乳化沥青透层/粘层语义时，把 LTR top1 从跨册噪声候选拉回 C2 道路沥青层候选。

本轮不处理 `砌筑井`、`标杆`，不修改 R1 召回、CGR、Picker，也不重训 LTR。

## 改动范围

- `src/ltr_ranker.py`
  - 新增 `_bitumen_layer_intent()`，识别乳化沥青透层、透油层、PC-3 乳化沥青粘层/黏层。
  - 新增 `_apply_bitumen_layer_rescue()`，只检查 LTR top8，候选必须是 C2 且具备透层/黏层与乳化沥青语义。
  - 将该救援接入 `_apply_ltr_guard()`，放在既有 surface orientation rescue 之前。
- `tests/test_ltr_ranker_v2.py`
  - 覆盖半刚性基层乳化沥青透层救援。
  - 覆盖 PC-3 乳化沥青粘层救援。
  - 覆盖仅标题含 `透层、粘层`、但无乳化沥青信号时不得触发救援。

## 验收命令

```powershell
python -m pytest tests\test_ltr_ranker_v2.py tests\test_export_r2_ltr_diagnostics.py -q
python -m py_compile src\ltr_ranker.py tools\export_r2_ltr_diagnostics.py
python tools\run_benchmark.py --province 浙江省市政 --json-only --profile full --summary-json-out reports\attribution\r2_2d_zhejiang_only_summary.json --latest-result-out reports\attribution\r2_2d_zhejiang_only_latest.json
python tools\export_r2_ltr_diagnostics.py --input reports\attribution\r2_2d_zhejiang_only_latest.json --output-csv reports\attribution\r2_2d_ltr_diagnostics.csv --summary-output reports\attribution\r2_2d_ltr_diagnostics_summary.json
python tools\diff_benchmark_results.py --base reports\attribution\r2_2b_zhejiang_only_latest.json --candidate reports\attribution\r2_2d_zhejiang_only_latest.json
```

## 验收结果

- 单元测试：`32 passed`
- `py_compile`：通过
- 浙江市政 full 子集：`159/541 = 29.4%`
- R1 召回未命中：`172`
- R2 LTR 选错：`182`，上一轮 R2-2b 为 `186`
- R2 诊断总面：`216`，上一轮 R2-2b 为 `220`
- `structure_signal_sparse`：`101`，上一轮 R2-2b 为 `105`
- `ltr_bad_flip_pre_correct`：`34`，与上一轮持平
- R3 + R4：`35`，与上一轮持平

## 差分复核

`tools/diff_benchmark_results.py` 显示：

- `changed_total = 5`
- `improved = 4`
- `regressed = 0`
- `changed_without_outcome_flip = 1`

改善样本：

| bill_id | bill_name | R2-2b | R2-2d | 说明 |
| --- | --- | --- | --- | --- |
| 235 | 透层、粘层 | `3-541` | `2-163` | 乳化沥青透层，命中半刚性基层透层候选 |
| 399 | 透层、粘层 | `4-449` | `2-165` | PC-3 乳化沥青粘层，命中黏层候选 |
| 469 | 透层、粘层 | `4-449` | `2-165` | PC-3 乳化沥青粘层，命中黏层候选 |
| 100 | 水泥稳定碎（砾）石 调节层 | `2-129` | `2-133` | 非本轮 bitumen rescue 直接触发，属于伴随差异，不作为本轮规则收益证据 |

未修复但保留为后续样本：

- `124`：透层半刚性基层 stored 为 `2-163`，当前仍选 `2-161`。需要更细的 `半刚性基层` vs `粒料基层` 家族内区分。
- `347`：stored 为 `2-161`，当前仍被 `3-541` 干扰。需要单独看 `透油层/喷油量` 与候选窗口。
- `6`：`配管` 从 `8-248` 变为 `8-249`，仍错误，且不是本轮规则触发。

## 结论

本轮验收通过。R2 LTR 选错和 `structure_signal_sparse` 均下降，未出现命中回退，`ltr_bad_flip_pre_correct` 持平。

下一步建议继续 R2-2e：仍从 `structure_signal_sparse` 剩余样本中选一个小簇，但优先避开需要 ID 映射或跨阶段修复的问题。候选方向是继续拆 `透层/粘层` 家族内的 `半刚性基层` vs `粒料基层`，或选择另一个正确候选在 top5 且文本显式指向的稳定小簇。
