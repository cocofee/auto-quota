# 统一价格参考库现状快照
日期：2026-03-27  
状态：已完成大规模入库，仍需补齐日期治理与精细特征

## 当前库内存量

基于 `db/common/price_reference.db` 的实测统计：

| 指标 | 数值 |
|------|------|
| `historical_boq_items` 总量 | `1,959,982` |
| 其中带 `composite_unit_price` 的记录 | `1,218,381` |
| 其中未带价格的记录 | `741,601` |
| `normalized_name` 已回补 | `1,959,978` |
| `materials_signature` 非空 | `9,988` |
| `materials_signature_first` 非空 | `1,959,982` |
| `price_type` 非空 | `1,218,381` |
| `price_value` 非空 | `1,218,381` |
| `price_date_iso` 非空 | `0` |
| 原始 `source_date` 非空 | `0` |
| `source_record_id` 非空 | `1,959,982` |
| 已标记 `price_outlier=1` | `284,960` |

## 来源拆分

| 来源 | 总量 | 已带价格 |
|------|------|----------|
| `seed_source=backfill_script` | `1,218,387` | `1,218,381` |
| `seed_source=experience_db` | `741,595` | `0` |

结论：
- “100 多万条综合单价”这批数据已经进统一价格库，而且数量已经达到 `121.8` 万条带价记录。
- 目前剩下的不是“没导入”，而是 `74.1` 万条 `experience_db` 种子记录还没有真实价格，只能作为 BOQ 参考种子，不能当成价格样本。

## 现阶段缺口

### 1. 日期治理未完成

- `price_date_iso = 0`
- `source_date_raw = 0`

这说明当前大批量导入链没有把价格日期写进来，`latest_price` 只能退化为“无日期样本中的某条记录”，时间维度还不可靠。

### 2. 主材签名覆盖率低

- `materials_signature` 只有 `9,988` 条非空

这会影响：
- 结构化分桶精度
- 同类聚类质量
- `category_match` 的稳定性

### 3. 仍存在大量种子层记录

- `741,595` 条 `experience_db` 种子记录无价格

这些记录适合：
- 清单相似召回
- 定额组合参考

但不适合：
- 推荐综合单价
- 价格统计中位数

## 目前可确认的事实

- 统一价格库已经不是空壳，`historical_boq_items` 已达 `195.9` 万条。
- 可用于综合单价统计的实价样本，当前规模为 `121.8` 万条。
- `layered_result` 已经可以基于这批带价记录工作，但时间维度和材料维度还不完整。
- `experience_db` 迁移过来的 `74.1` 万条记录应继续保留，但只能作为 seed，不应混入推荐价统计。

## 下一步执行顺序

### P1

- 先补价格日期来源。
- 优先检查历史 XML / 带价 Excel / 文档元信息里是否已有可回填的日期字段。
- 2026-03-27 已完成代码级 fallback：
  - 新写入时可从 `source_file_name / source_file_path / project_name` 推断 `source_date`
  - 老数据可通过 `backfill_boq_item_enhancements()` 回补
  - 已实测回补：
    - `document_id=12280`：`1692` 行
    - `document_id in (633, 659, 660)`：`658` 行
  - 截至本次试跑后，全库 `price_date_iso` 非空行数已从 `0` 提升到 `2350`

### P2

- 对大批量带价记录补 `materials_signature`。
- 至少把高频设备/材料类目覆盖起来，先提升分桶质量。

### P3

- 明确查询时只用 `price_value IS NOT NULL` 且非异常值记录参与推荐价。
- `experience_db` 种子层继续保留在 `historical_boq_items`，但只参与召回，不参与价格推荐。

### P4

- 等日期和主材维度补齐后，再跑一次全量异常值重扫。
- 之后再做前端 / OpenClaw 对 `layered_result` 的联调验收。
