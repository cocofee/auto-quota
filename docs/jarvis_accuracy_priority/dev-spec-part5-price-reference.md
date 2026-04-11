# 开发说明 Part 5：价格参考层改造

模块位置：`src/price_reference_db.py`  
依赖上游：Part 3 入口层  
下游：`result-backfill`、OpenClaw 价格参考

## 1. 改动概述

将现有简单 `LIKE + 最近优先 + min/max/median` 升级为：

```text
标准化 -> 价格事实化 -> 同类聚类 -> 异常值标记 -> 分层输出
```

## 2. 物理方案

采用 Addendum A3 锁定的方案：

- 不建统一大表
- 保留 `historical_quote_items` 与 `historical_boq_items`
- 双表增强
- 查询层共享公共逻辑

## 3. 双表新增字段

两张表都新增：

```sql
ALTER TABLE {table} ADD COLUMN normalized_name TEXT DEFAULT NULL;
ALTER TABLE {table} ADD COLUMN materials_signature TEXT DEFAULT NULL;
ALTER TABLE {table} ADD COLUMN materials_signature_first TEXT DEFAULT NULL;
ALTER TABLE {table} ADD COLUMN price_type TEXT DEFAULT NULL;
ALTER TABLE {table} ADD COLUMN price_outlier INTEGER DEFAULT 0;
ALTER TABLE {table} ADD COLUMN outlier_method TEXT DEFAULT NULL;
ALTER TABLE {table} ADD COLUMN outlier_score REAL DEFAULT NULL;
ALTER TABLE {table} ADD COLUMN outlier_reason TEXT DEFAULT NULL;
ALTER TABLE {table} ADD COLUMN price_date_iso TEXT DEFAULT NULL;
ALTER TABLE {table} ADD COLUMN date_parse_failed INTEGER DEFAULT 0;
ALTER TABLE {table} ADD COLUMN source_record_id INTEGER DEFAULT NULL;
```

新增索引：

```sql
CREATE INDEX IF NOT EXISTS idx_{table}_normalized ON {table}(normalized_name);
CREATE INDEX IF NOT EXISTS idx_{table}_bucket ON {table}(specialty, unit, materials_signature_first, price_type);
CREATE INDEX IF NOT EXISTS idx_{table}_outlier ON {table}(price_outlier);
CREATE INDEX IF NOT EXISTS idx_{table}_materials_signature ON {table}(materials_signature);
```

## 4. 价格事实化规则

> 采用“写入时拆”。

### 4.1 设备价表

一条原始设备价记录最多拆成 3 条逻辑 price fact：

| 原字段 | 拆出 `price_type` | `price_value` |
|--------|------------------|---------------|
| `unit_price` | `equipment_unit_price` 或 `material_unit_price` | `unit_price` |
| `install_price` | `install_unit_price` | `install_price` |
| `combined_unit_price` | `equipment_combined_price` | `combined_unit_price` |

每条 fact：

- 只有一个 `price_type`
- 只有一个 `price_value`
- 共享 `source_record_id`

### 4.2 综合单价表

默认：

- `composite_unit_price -> composite_price`

可选拆出：

- `labor_cost -> labor_component_price`
- `material_cost -> material_component_price`
- `machine_cost -> machine_component_price`

P0 至少保证 `composite_price`。

## 5. 价格记录标准化

每条逻辑价格事实至少包含：

- `raw_name`
- `normalized_name`
- `specialty`
- `unit`
- `materials_signature`
- `materials_signature_first`
- `brand`
- `model`
- `spec`
- `price_type`
- `price_value`
- `price_date_iso`
- `province`
- `project_id`
- `source_type`
- `price_outlier`
- `outlier_method`
- `outlier_score`
- `outlier_reason`

## 6. bucket 分桶

桶键：

```text
bucket_key = (specialty, unit, materials_signature_first, price_type)
```

桶内细分优先级：

1. 品牌 + 型号
2. 品牌
3. 规格区间
4. 全量

## 7. 缺失字段降级

| 字段缺失 | 处理 |
|----------|------|
| `materials_signature` 为空 | 归入 `_unknown_material`，仅参与 category_match |
| `brand` 为空 | 不参与 exact/brand，只参与 category |
| `model` 为空 | 不参与 exact，可参与 brand |
| `specialty` 为空 | 归入 `_unknown_specialty` |
| `unit` 为空 | 归入 `_unknown_unit` |

`_unknown` 桶：

- 不参与推荐价计算
- 只出现在样本展示

## 8. 日期标准化

写入新字段 `price_date_iso`，保留原 `source_date`。

示例：

- `2024-03-15 -> 2024-03-15T00:00:00+08:00`
- `2024年3月 -> 2024-03-01T00:00:00+08:00`
- 无法解析：`price_date_iso = NULL`，`date_parse_failed = 1`

规则：

- 最新价格排序按 `price_date_iso DESC`
- 统计量不依赖日期时，可保留 `date_parse_failed = 1` 的价格

## 9. 异常值标记

原则：

- 不删除，只标记
- 计算参考价时排除
- 样本展示时保留并标注

### 9.1 IQR

```python
def detect_outlier_iqr(prices: list[float]) -> list[bool]:
    if len(prices) < 4:
        return [False] * len(prices)
    ...
```

### 9.2 数量级

```python
def detect_outlier_magnitude(prices: list[float]) -> list[bool]:
    if len(prices) < 3:
        return [False] * len(prices)
    ...
```

### 9.3 额外规则

- `price <= 0` 直接标记异常
- 所有样本都异常时，不给推荐价，只展示样本

## 10. 分层输出

保持旧字段兼容，同时新增 `layered_result`。

```python
class LayeredPriceResult(BaseModel):
    exact_match: PriceBucket | None = None
    brand_match: PriceBucket | None = None
    category_match: PriceBucket | None = None
    recommended_price: float | None = None
    recommended_source: str | None = None
    total_sample_count: int = 0
    valid_sample_count: int = 0
    outlier_count: int = 0
```

推荐价优先级：

1. `exact_match.sample_count >= 3`
2. 否则 `brand_match.sample_count >= 3`
3. 否则 `category_match.sample_count >= 5`
4. 否则不给推荐价

## 11. API 兼容

直接扩展现有两套 schema：

- `ItemPriceReferenceResponse.layered_result`
- `CompositePriceReferenceResponse.layered_result`

旧字段：

- `summary`
- `samples`

继续保留。

## 12. 存量迁移要求

当前已有：

- `1,218,381` 条综合单价样本

S5 必须对这批存量执行：

1. `normalized_name` 回补
2. `materials_signature` / `materials_signature_first` 回补
3. `price_type` 回补
4. `price_date_iso` 回补
5. 异常值全量扫描

不得只处理新增导入。

## 13. 需要修改的函数

| 函数 | 改动 |
|------|------|
| `replace_quote_items()` | 写入时拆分 price fact |
| `replace_boq_items()` | 回补新增字段并生成 `composite_price` fact |
| `search_item_prices()` | 支持标准化与分层输出 |
| `search_composite_prices()` | 支持标准化与分层输出 |
| `get_item_price_reference()` | 输出 `layered_result` |
| `get_composite_price_reference()` | 输出 `layered_result` |
| `_detect_outliers()` | 新增 |
| `_compute_bucket_stats()` | 新增 |
| `_standardize_price_date()` | 新增 |
| `run_outlier_scan()` | 新增 |

## 14. 验收标准

正例：

- 同品牌同型号 5 条记录时，`exact_match` 可用
- 15 倍异常值应被标记，不参与统计
- 只有品牌没有型号时，落 `brand_match`
- 返回结构同时含旧 `summary/samples` 与新 `layered_result`

反例：

- 样本数 2 条时，不做 IQR
- 价格非正时必须标异常
- `_unknown` 桶不能给推荐价
