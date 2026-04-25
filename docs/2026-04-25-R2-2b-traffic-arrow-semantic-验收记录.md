# 2026-04-25 R2-2b traffic arrow semantic 验收记录

## 本轮目标

继续处理 R2 最大剩余桶 `structure_signal_sparse`，但只修一个已确认的窄口径显式语义缺口：交通标记箭头的方向和长度。

本轮不做：
- LTR 模型重训
- R1 召回修改
- CGR / Picker / final validator 修改
- 标杆直径档位推断
- 砌筑井跨册/圆矩形规则

## 诊断结论

`reports/attribution/r2_2a_ltr_diagnostics.csv` 中 `structure_signal_sparse = 106`，不是单一问题，包含回填方、砌筑井、防水材料、交通标记等多个小簇。

本轮只采纳一个安全子簇：
- 清单文本明确写 `6m转弯箭头`。
- LTR 候选 `标记 箭头 转弯(6m)热熔型` 已经是正确候选。
- 但 guard 因弱 route/manual margin 把结果拦回 `标记 箭头 直行(9m)热熔型`。

曾评估但未采纳：
- `门式架` 放行：会把 #32 从 R2 转到 R3，最终仍错，已撤回。
- 标杆直径/高度档位：涉及 `159*4750` 到 `φ140×4500以内` 的定额档位语义，不能用简单数值规则硬修。
- 砌筑井：同时存在圆/矩形、同名跨册、给排水册优先等混合问题，需下一轮单独诊断。

## 改动文件

- `src/ltr_ranker.py`
- `tests/test_ltr_ranker_v2.py`

新增逻辑：
- `_traffic_arrow_spec()` 提取箭头方向和长度。
- `_detect_explicit_semantic_advantage()` 增加 `traffic_arrow_spec_alignment`。

放行条件：
- item 文本和 challenger 都包含 `箭头`。
- item 的箭头方向与 challenger 一致，且与 incumbent 不一致。
- item 的箭头长度与 challenger 一致，且与 incumbent 不一致。

## 验收命令

```powershell
python -m pytest tests/test_ltr_ranker_v2.py tests/test_export_r2_ltr_diagnostics.py -q
python -m py_compile src\ltr_ranker.py tools\export_r2_ltr_diagnostics.py
python tools\run_benchmark.py --province 浙江省市政 --json-only --profile full --summary-json-out reports\attribution\r2_2b_zhejiang_only_summary.json --latest-result-out reports\attribution\r2_2b_zhejiang_only_latest.json
python tools\export_r2_ltr_diagnostics.py --input reports\attribution\r2_2b_zhejiang_only_latest.json --output-csv reports\attribution\r2_2b_ltr_diagnostics.csv --summary-output reports\attribution\r2_2b_ltr_diagnostics_summary.json
```

## 验收结果

单元测试：
- `29 passed`

编译：
- `src/ltr_ranker.py` 通过
- `tools/export_r2_ltr_diagnostics.py` 通过

浙江市政 full 子集：

| 指标 | R2-2a 后 | R2-2b 后 | 门槛 | 结果 |
|---|---:|---:|---|---|
| 题数 | 541 | 541 | - | 通过 |
| 命中 | 154 | 155 | `>= 154` | 通过 |
| 命中率 | 28.5% | 28.7% | 不下降 | 通过 |
| 召回命中率 | 69.7% | 69.7% | 不下降 | 通过 |
| R1 召回未命中 | 164 | 164 | `<= 164` | 通过 |
| R2 LTR 选错 | 187 | 186 | 下降 | 通过 |
| benchmark `pre_ltr_correct_but_ltr_changed` | 44 | 44 | `<= 45` | 通过 |
| 诊断 `ltr_bad_flip_pre_correct` | 34 | 34 | `<= 45` | 通过 |
| 诊断 R2 总面 | 221 | 220 | 下降 | 通过 |
| `structure_signal_sparse` | 106 | 105 | 下降 | 通过 |
| R3 + R4 | 35 | 35 | `<= 47` | 通过 |

R2-2b 诊断输出：
- `reports/attribution/r2_2b_ltr_diagnostics.csv`
- `reports/attribution/r2_2b_ltr_diagnostics_summary.json`

最终变化样本：
- #212 `标记`：`2-310` -> `2-314`，由 `traffic_arrow_spec_alignment` 放行，结果从错变对。

## 结论

R2-2b 验收通过。该轮只新增一个可解释的交通箭头规格信号，未引入 R3/R4 转移回退；浙江市政 full 子集命中从 `154` 提升到 `155`，R2 LTR 选错从 `187` 降到 `186`。

下一步进入 R2-2c：继续分析 `structure_signal_sparse = 105`。优先单独诊断 `砌筑井`，但必须先区分圆/矩形冲突、同名跨册错误和给排水册优先问题，不能直接写宽泛跨册规则。
