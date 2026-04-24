# 2026-04-24 R1 召回未命中自动诊断

## 读取入口

清理对话后，直接说：

```text
读取 R1 召回自动诊断 4.24，继续下一步。
```

执行时先读本文档，不要重新全仓库发散分析。

## 诊断范围

只诊断第二阶段：`R1_召回未命中`。

禁止本阶段同时修改：

- LTR 训练与模型选择
- Picker
- CGR
- final validator
- OpenClaw 在线替代 Jarvis 首轮匹配

## 基线产物

已通过 LTR Mixed Safety 验收：

- `docs/2026-04-24-LTR-mixed-safety-验收记录.md`
- `reports/attribution/ltr_v2_mixed_safety_candidate.json`
- `output/benchmark_compare/ltr_v2_mixed_safety_candidate_latest_result.json`

当前 full benchmark 关键指标：

| 指标 | 当前值 |
|------|------|
| 总命中率 | `35.7%` |
| 召回命中率 | `72.3%` |
| `R1_召回未命中` | `1269` |
| `R2_LTR选错` | `1364` |
| `R4_Picker推翻正确` | `204` |
| `R3_CGR推翻正确` | `65` |

下一阶段验收底线：

- 召回命中率必须高于 `72.3%`。
- 总命中率不能低于 `35.7%`。
- `R2/R3/R4` 不能明显上升。

## R1 Top 省份

从 `reports/attribution/ltr_v2_mixed_safety_candidate.json` 读取：

| 省份 | R1 数量 | 占 R1 比例 |
|------|------:|------:|
| 浙江省市政工程预算定额(2018) | `248` | `19.5%` |
| 浙江省通用安装工程预算定额(2018) | `139` | `10.9%` |
| 福建省市政工程预算定额(2017) | `118` | `9.3%` |

从 full latest result 扫描完整 R1 后的 Top 省份：

| 省份 | R1 数量 |
|------|------:|
| 浙江省市政工程预算定额(2018) | `248` |
| 浙江省通用安装工程预算定额(2018) | `139` |
| 福建省市政工程预算定额(2017) | `118` |
| 广东省通用安装工程综合定额(2018) | `113` |
| 重庆市通用安装工程计价定额(2018) | `113` |
| 福建省房屋建筑与装饰工程预算定额(2017) | `88` |
| 江西省通用安装工程消耗量定额及统一基价表(2017) | `79` |
| 广东省房屋建筑与装饰工程综合定额(2018) | `76` |

## R1 问题分桶

对 `output/benchmark_compare/ltr_v2_mixed_safety_candidate_latest_result.json` 做完整扫描，R1 总数 `1269`，与归因汇总一致。

| 分桶 | 数量 | 判断 |
|------|------:|------|
| `semantic_candidate_pool_miss` | `557` | 有候选，但正确项没有进入候选池，优先看候选池扩展/查询改写 |
| `missing_specialty_context` | `494` | 专业字段为空，多出现在非安装/市政/园林/土建卷，属于上下文或路由输入问题 |
| `thin_candidate_pool` | `143` | 候选池太薄，正确项没召回，适合补充 candidate pool 合并 |
| `hard_param_reject` | `63` | 搜索到候选后被硬参数校验全拒，需单独做参数校验误杀诊断 |
| `real_specialty_route_mismatch` | `12` | 真正专业路由错，数量小，不作为第一轮目标 |

前缀归一化说明：

- `C4` 与 `4`、`C9` 与 `9` 视为同一专业前缀。
- 归一化后，真实专业路由错只有 `12` 条，不是第一优先级。

## 第一轮推荐目标

第一轮只修一个召回模块：

```text
候选池扩展 / candidate pool merge
```

理由：

- 最大分桶是 `semantic_candidate_pool_miss = 557`。
- 第二相关分桶 `thin_candidate_pool = 143` 也属于候选池覆盖不足。
- 合并后直接影响 `700` 条左右 R1 样本，比先修 `hard_param_reject=63` 或真实路由错 `12` 更有收益。
- 不需要碰 LTR/Picker/CGR。

第一轮不要先修 `missing_specialty_context`：

- 数量大，但集中在非安装/市政/园林/土建卷。
- 需要先拆输入上下文和省份/专业路由，容易扩大范围。
- 可作为 R1 第二轮。

## 第一轮样本焦点

优先从浙江市政开始，因为它是最大 R1 省份：

- 省份：`浙江省市政工程预算定额(2018)`
- R1：`248`
- 高频词：
  - `混凝土井`
  - `沥青混凝土`
  - `标杆`
  - `垫层`
  - `面涂膜防水`
  - `后浇构件钢筋`
  - `现浇构件钢筋`
  - `塑料管`

代表样本：

| bill_id | bill_name | specialty | correct | algo | candidate_count |
|------|------|------|------|------|------:|
| `18` | `混凝土井` | `C6` | `6-311` | `10-1` | `11` |
| `63` | `沥青混凝土` | `C2` | `2-210` | `3012` | `2` |
| `80` | `混凝土井` | `C6` | `6-311` | `6-276` | `20` |
| `92` | `检查井四周回填` | `C6` | `6-308` | `4-202` | `2` |
| `39` | `钢筋连接` | `C5` | `5-74` | `5-78` | `14` |

## 下一步执行计划

### R1-1 导出 compact R1 诊断样本

目标：

从 1.5GB latest result 中导出小 CSV，避免后续每次都读大文件。

建议输出：

- `reports/attribution/r1_recall_miss_diagnostics.csv`
- `reports/attribution/r1_recall_miss_summary.json`

字段：

- `province`
- `bill_id`
- `bill_name`
- `specialty`
- `correct_quota_id`
- `algo_id`
- `candidate_count`
- `recall_topk_count`
- `bucket`
- `no_match_reason`
- `top_candidate_ids`
- `top_candidate_names`

验收：

```powershell
python tools/export_r1_recall_diagnostics.py `
  --input output/benchmark_compare/ltr_v2_mixed_safety_candidate_latest_result.json `
  --output-csv reports/attribution/r1_recall_miss_diagnostics.csv `
  --summary-output reports/attribution/r1_recall_miss_summary.json
```

通过标准：

- CSV 行数等于 `1269`。
- summary 中 bucket 数量与本文档一致或只有可解释差异。
- 能筛选浙江市政 `semantic_candidate_pool_miss` 和 `thin_candidate_pool` 样本。

### R1-2 只修 candidate pool merge

进入条件：

- R1-1 诊断 CSV 已导出并通过验收。

目标：

只针对候选池覆盖不足，不改排序。

允许方向：

- 对同省同专业的精确短词增加 fallback 检索。
- 对候选池太薄的 query 追加 standard retrieve 或 exact-name retrieve。
- 对 `semantic_candidate_pool_miss` 的浙江市政高频词做候选池合并。

禁止方向：

- 不改 LTR 权重。
- 不改 Picker/CGR。
- 不加全局硬排序规则。
- 不把 OpenClaw 放在线上首轮匹配。

验收：

```powershell
python tools/run_benchmark.py --profile full --json-only
```

通过标准：

- 召回命中率 `> 72.3%`。
- 总命中率 `>= 35.7%`。
- `R2/R3/R4` 不明显上升。

## R1-1 验收记录

已新增工具：

- `tools/export_r1_recall_diagnostics.py`
- `tests/test_export_r1_recall_diagnostics.py`

验收命令：

```powershell
python -m pytest tests/test_export_r1_recall_diagnostics.py -q

python tools/export_r1_recall_diagnostics.py `
  --input output/benchmark_compare/ltr_v2_mixed_safety_candidate_latest_result.json `
  --output-csv reports/attribution/r1_recall_miss_diagnostics.csv `
  --summary-output reports/attribution/r1_recall_miss_summary.json
```

验收结果：

- 单测：`3 passed`
- CSV 行数：`1269`
- 浙江市政可筛选样本：
  - `semantic_candidate_pool_miss = 220`
  - `thin_candidate_pool = 14`

导出后的 bucket 结果：

| bucket | 数量 |
|------|------:|
| `semantic_candidate_pool_miss` | `557` |
| `missing_specialty_context` | `368` |
| `search_no_result` | `158` |
| `thin_candidate_pool` | `72` |
| `hard_param_reject` | `63` |
| `weak_context_manual_review` | `39` |
| `real_specialty_route_mismatch` | `12` |

与前置自动诊断的差异说明：

- 前置诊断把 `search_no_result` 和 `weak_context_manual_review` 粗略并入上下文类问题。
- R1-1 导出工具将它们拆成独立 bucket，方便后续按模块验收。
- R1 总数仍为 `1269`，关键第一目标 `semantic_candidate_pool_miss = 557` 不变。

是否进入下一步：是。

下一步：

```text
R1-2：只修 candidate pool merge，优先浙江市政 semantic_candidate_pool_miss / thin_candidate_pool。
```
