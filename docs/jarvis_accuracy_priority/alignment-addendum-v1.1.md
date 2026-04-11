# 开发前置对齐文档（Alignment Addendum v1.1）

日期：2026-03-26  
优先级：高于 Part 1~5 的理想化表述

## A1. 旧入口收口与 `ingest()` 契约

### 问题

现有旧入口会绕过统一入口：

- `bill_price_documents.py` 直写 `experience_db`
- `feedback.py` 直写 `experience_db`
- `file_intake.py` 的 `learning_pipeline` 尚未激活

### 规则

所有写入 `experience_db` 与 `price_reference_db` 的链路，必须经过：

```python
ingest(
    *,
    file_id: str | None = None,
    records: list[dict] | None = None,
    ingest_intent: str,
    evidence_level: str,
    business_type: str = "unknown",
    actor: str = "",
    source_context: dict | None = None,
) -> IngestResult
```

调用方：

- `feedback.py`
- `bill_price_documents.py`
- OpenClaw 回流

`add_experience()` 视为内部写入函数，不作为外部业务入口。

## A2. 现表字段对齐

### 现表关键字段

- `bill_text`
- `bill_name`
- `bill_unit`
- `quota_ids`
- `quota_names`
- `materials`
- `specialty`
- `province`
- `source`
- `confidence`
- `normalized_text`

### 修正规则

| schema 字段 | 现表字段 | 说明 |
|-------------|----------|------|
| `raw_name` | `bill_text` | 原始清单全文 |
| 标准化后名称 | `normalized_text` | 必须写这里 |
| 展示名称 | `bill_name` | 只用于展示 |
| `unit` | `bill_unit` | 直接映射 |
| `specialty` | `specialty` | 直接映射 |
| `confidence` | `confidence` | 直接落表 |
| `evidence_level` | `source` | 经映射表转换 |

`evidence_level -> source`：

| evidence_level | source |
|----------------|--------|
| `user_corrected` | `user_correction` |
| `openclaw_approved` | `openclaw_approved` |
| `completed_project` | `completed_project` |
| `reviewed_import` | `reviewed_import` |
| `raw_import` | `project_import` / `historical_parse` / `xml_backfill` |

新增列：

```sql
ALTER TABLE experiences ADD COLUMN feature_text TEXT DEFAULT NULL;
ALTER TABLE experiences ADD COLUMN materials_signature TEXT DEFAULT NULL;
ALTER TABLE experiences ADD COLUMN install_method TEXT DEFAULT NULL;
ALTER TABLE experiences ADD COLUMN quota_fingerprint TEXT DEFAULT NULL;
ALTER TABLE experiences ADD COLUMN quota_codes_sorted TEXT DEFAULT NULL;
ALTER TABLE experiences ADD COLUMN promoted_at TEXT DEFAULT NULL;
ALTER TABLE experiences ADD COLUMN promoted_from TEXT DEFAULT NULL;
```

## A3. 价格层物理方案：双表增强

锁定：

- 继续使用 `historical_quote_items`
- 继续使用 `historical_boq_items`
- 不新建统一大表

两张表共同新增：

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

共同索引：

```sql
CREATE INDEX IF NOT EXISTS idx_{table}_normalized ON {table}(normalized_name);
CREATE INDEX IF NOT EXISTS idx_{table}_bucket ON {table}(specialty, unit, materials_signature_first, price_type);
CREATE INDEX IF NOT EXISTS idx_{table}_outlier ON {table}(price_outlier);
CREATE INDEX IF NOT EXISTS idx_{table}_materials_signature ON {table}(materials_signature);
```

## A4. 日期标准化

新增：

- `price_date_iso`
- `date_parse_failed`

示例：

| 原始 | 结果 |
|------|------|
| `2024-03-15` | `2024-03-15T00:00:00+08:00` |
| `2024年3月15日` | `2024-03-15T00:00:00+08:00` |
| `2024-03` | `2024-03-01T00:00:00+08:00` |
| 无法解析 | `NULL`, `date_parse_failed = 1` |

## A5. 缺失字段降级

| 缺失字段 | 处理 |
|----------|------|
| `materials_signature` | `_unknown_material` |
| `brand` | 不参与 exact / brand |
| `model` | 不参与 exact |
| `specialty` | `_unknown_specialty` |
| `unit` | `_unknown_unit` |

`_unknown` 桶：

- 不参与推荐价计算
- 只参与原始样本展示

## A6. API Response Schema 兼容

不破坏现有字段，只给现有两套 schema 加 `layered_result`：

- `ItemPriceReferenceResponse.layered_result`
- `CompositePriceReferenceResponse.layered_result`

共享子模型：

- `LayeredPriceResult`
- `PriceBucket`

旧字段 `summary + samples` 保留。

## A7. 价格事实化规则

锁定：写入时拆。

### 设备价表

一行最多拆成 3 条逻辑 fact：

- `equipment_unit_price`
- `install_unit_price`
- `equipment_combined_price`

共享 `source_record_id` 追溯原始行。

### 综合单价表

至少拆出：

- `composite_price`

旧数据迁移时必须回补 `price_type`、`source_record_id`、异常值标记相关字段。

## A8. 修正后的实施顺序

```text
S1   学习分层
S1.5 旧入口收口 + ingest() 激活
S2   轻量反馈
S3   索引构建
S4   重排 + 门控 + 完整反馈
S5   价格双表增强 + 事实化 + API 兼容 + 日期标准化
S6   入口层完整路由 + schema 对齐
S7   green/yellow/red 上线
S8   扩大导入
```

## A9. 存量综合单价迁移要求

当前已有综合单价存量：

- `1,218,381` 行

这批存量尚未完整接入：

- `materials_signature_first`
- `price_type`
- `price_date_iso`
- `price_outlier`
- `layered_result` 所需统计链路

S5 实施时必须全量补算，不允许只覆盖新增样本。
