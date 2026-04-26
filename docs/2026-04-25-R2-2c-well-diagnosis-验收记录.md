# 2026-04-25 R2-2c well diagnosis 验收记录

## 本轮目标

按 4.24 计划继续 R2-2c，只诊断 `structure_signal_sparse` 中的井类问题，判断是否能安全补一个 LTR 窄 guard。

本轮不改代码，原因是砌筑井样本不是单一 LTR 信号缺口，直接写规则会带来错误转移。

## 输入基线

- 最新诊断：`reports/attribution/r2_2b_ltr_diagnostics.csv`
- `structure_signal_sparse = 105`
- 浙江市政 full 子集：`155/541 = 28.7%`
- R2 LTR 选错：`186`

## 诊断结论

`structure_signal_sparse` 中井类样本包括：
- `砌筑井`：6 条
- `混凝土井`：2 条
- `砖砌井筒`：1 条

这些样本至少分成三类：

| 类型 | 例子 | 结论 |
|---|---|---|
| 圆形/矩形冲突 | #17、#119、#318、#522 | 不能只用形状救援；部分样本 stored_ids 与高分同名候选不一致 |
| 同名跨册/跨章节 | #119、#273 | `1-394/1-395` 与 `6-*` 同属井类但册别和定额体系不同，不能在 LTR 层硬压 |
| 后续阶段改错 | #273、#325、#370 | 已经超出 R2/LTR，本轮不能改 CGR/final validator |

## 关键风险

砌筑井不能直接套用“清单有砖砌圆形就选井砌筑砖砌圆形”的规则。

典型反例：
- #522 清单明确 `砖砌圆形雨水检查井`。
- 候选中存在 `6-251 井砌筑 砖砌圆形` 和 `6-249 井砌筑 砖砌圆形`。
- stored_ids 是 `6-245, 6-249`。
- 如果只按名称语义 rescue，会优先选到高分同名候选 `6-251`，仍然不是验收答案。

因此，这不是一个单纯 LTR 语义缺口，而是候选族内档位/ID 映射问题。强行在 LTR guard 里硬修，会把错误从 R2 转移到 R3/R4 或制造新的同名误选。

## 验收方式

本轮验收不是 benchmark 提升，而是拒绝不安全改动：

- 已读取 `reports/attribution/r2_2b_ltr_diagnostics_summary.json`，确认最大桶仍为 `structure_signal_sparse = 105`。
- 已抽取井类样本并核对 `r2_2b_zhejiang_only_latest.json` 的原始候选、stored_ids、LTR guard trace。
- 已确认无单一安全 LTR guard 可以覆盖砌筑井样本。
- 未修改 `src/ltr_ranker.py`、CGR、Picker、final validator。

## 结论

R2-2c 诊断验收通过，但不进入代码修复。

砌筑井应拆到后续单独阶段，先修候选族内档位/ID 映射或增加更细的候选族归一化诊断，再谈算法规则。当前 R2/LTR 小步修复线应继续选择能闭环验收的窄子簇。

下一步建议进入 R2-2d：继续分析 `structure_signal_sparse`，优先选择“正确候选已在 top5，且文本显式参数能唯一指向 stored_ids”的小簇，例如 `透层/粘层` 或 `标杆` 的规格档位，但必须先做同样的 stored_ids 反例检查。
