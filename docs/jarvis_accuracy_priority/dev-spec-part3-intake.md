# 开发说明 Part 3：入口层改造

模块位置：`web/backend/app/api/file_intake.py` 与 `src/file_intake_db.py`  
依赖上游：Part 1 学习分层，JSON schema v0.1  
下游：学习层、价格层

## 1. 改动概述

统一入口增加两个元字段：

- `ingest_intent`
- `evidence_level`

并补一个统一内部入口：

- `ingest()`

要求：

- 有文件上传走同一入口
- 纠错、OpenClaw、旧导入等纯记录回流也走同一入口
- 不允许外部模块直接绕过入口写经验库

## 2. 新增元字段

### 2.1 ingest_intent

| 值 | 含义 | 下游 |
|----|------|------|
| `task_match` | 仅用于本次任务 | 只进 task pipeline |
| `learning` | 进入学习层 | experience_db |
| `price_reference` | 进入价格参考层 | price_reference_db |
| `dual_use` | 学习 + 价格双写 | experience_db + price_reference_db |

### 2.2 evidence_level

| 值 | 含义 | 初始层 |
|----|------|--------|
| `raw_import` | 批量原始导入 | candidate |
| `completed_project` | 已完工项目 | verified 或 candidate |
| `reviewed_import` | 初步复核导入 | verified 或 candidate |
| `user_corrected` | 用户纠错 | authority |
| `openclaw_approved` | OpenClaw 审核通过 | authority |

## 3. `ingest()` 最小契约

```python
def ingest(
    *,
    file_id: str | None = None,
    records: list[dict] | None = None,
    ingest_intent: str,
    evidence_level: str,
    business_type: str = "unknown",
    actor: str = "",
    source_context: dict | None = None,
) -> IngestResult:
    ...
```

支持两种模式：

1. 有文件：`file_id` 存在，`records` 可为空，入口内部负责 parse/classify/route
2. 纯记录回流：`records` 直接传入，不要求 `file_id`

返回：

```python
@dataclass
class IngestResult:
    file_id: str | None
    ingest_intent: str
    evidence_level: str
    written_learning: int
    written_price_reference: int
    skipped: int
    warnings: list[str]
    errors: list[str]
    route_targets: list[str]
```

## 4. 路由规则

```text
task_match
  -> 只进 task pipeline

learning
  -> experience_db

price_reference
  -> price_reference_db

dual_use
  -> experience_db + price_reference_db
```

`evidence_level -> source` 映射：

| evidence_level | source |
|----------------|--------|
| `user_corrected` | `user_correction` |
| `openclaw_approved` | `openclaw_approved` |
| `completed_project` | `completed_project` |
| `reviewed_import` | `reviewed_import` |
| `raw_import` | `project_import` / `historical_parse` / `xml_backfill` 视入口上下文决定 |

## 5. 旧入口收口

以下旧入口必须改成调用 `ingest()`：

| 原入口 | 改造后 |
|--------|--------|
| `bill_price_documents.py` | `ingest(intent="learning", evidence="completed_project")` |
| `feedback.py` | `ingest(intent="learning", evidence="user_corrected")` |
| OpenClaw 审核通过 | `ingest(intent="learning", evidence="openclaw_approved")` |

## 6. schema v0.1 -> experiences 映射

> 以现表为准。

| schema 字段 | experiences 字段 | 规则 |
|-------------|------------------|------|
| `raw_name` | `bill_text` | 直接映射 |
| 标准化后名称 | `normalized_text` | 必须写这里，不写 `bill_name` |
| 展示名 | `bill_name` | 仅展示用途 |
| `unit` | `bill_unit` | 直接映射 |
| `specialty` | `specialty` | 直接映射 |
| `feature_text` | `feature_text` | 扩展列，同时 P0 拼接进 `bill_text` |
| depth 1 `quota_code` | `quota_ids` | 聚合去重排序 |
| depth 1 `quota_name` | `quota_names` | 与 `quota_ids` 顺序对齐 |
| `materials_signature` | `materials_signature` | 直接写 |
| `install_method` | `install_method` | 直接写 |
| `quota_fingerprint` | `quota_fingerprint` | 直接写 |
| `quota_codes_sorted` | `quota_codes_sorted` | JSON 数组 |
| `confidence` | `confidence` | 直接落表，并参与 verified 判定 |
| `province` | `province` | 直接映射 |
| `source` | `source` | 由 `evidence_level` 转换得到 |

### 6.1 层级展平

写 experiences 前先展平：

1. depth 0 作为主清单项
2. depth 1 作为定额子项，聚合 `quota_ids/quota_names`
3. depth 2 作为材料子项，聚合 `materials` 并计算 `materials_signature`

## 7. schema v0.1 -> price_reference 映射

设备价写 `historical_quote_items`，综合单价写 `historical_boq_items`。

关键字段：

- `raw_name -> item_name_raw / boq_name_raw`
- 标准化名 -> `normalized_name`
- `brand/model/spec`
- `specialty/unit`
- `materials_signature`
- `price_type`
- `price_date_iso`
- `source_record_id`

## 8. `file_intake_files` 建议新增字段

```sql
ALTER TABLE file_intake_files ADD COLUMN ingest_intent TEXT DEFAULT '';
ALTER TABLE file_intake_files ADD COLUMN evidence_level TEXT DEFAULT '';
ALTER TABLE file_intake_files ADD COLUMN business_type TEXT DEFAULT '';
ALTER TABLE file_intake_files ADD COLUMN actor TEXT DEFAULT '';
ALTER TABLE file_intake_files ADD COLUMN source_context TEXT DEFAULT '{}';
```

## 9. 验收标准

正例：

- `task_match` 仅进任务链，不写学习层和价格层
- `learning + completed_project` 字段齐全时进 `verified`
- `dual_use` 同时写学习层和价格层
- 纯记录回流也能通过 `ingest()` 入库

反例：

- 不允许外部模块直接持久化学习记录而绕过 `ingest()`
- `completed_project` 缺 `quota_ids` 时，不允许进 `verified`
- `price_reference` 记录无价格字段时，应跳过并记录 warning
