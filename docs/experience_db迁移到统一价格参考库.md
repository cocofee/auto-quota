# experience.db 迁移到统一价格参考库

## 目标

把现有 `experience.db` 中已经沉淀下来的清单学习资料，迁移为统一历史价格参考库中的 `historical_boq_items` 种子数据。

这一步的目的不是“立刻补齐综合单价”，而是先把 40 万级别的历史清单资产接入统一价格知识层，供后续：

- 清单标准化
- 清单相似召回
- 清单到定额的经验复用
- 后续二次补抽综合单价/组成分析

## 现状确认

当前 `db/common/experience.db` 中的 `experiences` 表已经保存了大量高价值数据，核心字段包括：

- `bill_text`
- `bill_name`
- `bill_code`
- `bill_unit`
- `quota_ids`
- `quota_names`
- `specialty`
- `province`
- `project_name`
- `materials`
- `normalized_text`

但当前并没有独立保存以下价格字段：

- `composite_unit_price`
- `labor_cost`
- `material_cost`
- `machine_cost`

所以这批旧数据应被视为：

- `综合单价主线的种子层`

而不是：

- `已经完整可查价的综合单价成品层`

## 迁移脚本

新增脚本：

- [tools/migrate_experience_to_price_reference.py](C:/Users/Administrator/Documents/trae_projects/auto-quota/tools/migrate_experience_to_price_reference.py)

默认行为：

- 源库：`db/common/experience.db`
- 目标库：`db/common/price_reference.db`
- 自动建表
- 将旧经验记录迁入 `historical_boq_items`
- 将来源分组写入 `price_documents`

## 迁移映射

### 源字段 -> 目标字段

- `experiences.bill_name` -> `historical_boq_items.boq_name_raw`
- `experiences.normalized_text` -> `historical_boq_items.boq_name_normalized`
- `experiences.bill_code` -> `historical_boq_items.boq_code`
- `experiences.bill_unit` -> `historical_boq_items.unit`
- `experiences.quota_ids` -> `historical_boq_items.quota_code`
- `experiences.quota_names` -> `historical_boq_items.quota_name`
- `experiences.specialty` -> `historical_boq_items.specialty`
- `experiences.province` -> `historical_boq_items.region`
- `experiences.project_name` -> `historical_boq_items.project_name`
- `experiences.materials` -> `historical_boq_items.materials_json`
- `experiences.bill_text` -> `historical_boq_items.bill_text`

### 当前会保留为空的字段

以下字段在旧经验库里没有稳定来源，迁移时保持 `NULL`：

- `composite_unit_price`
- `quantity`
- `labor_cost`
- `material_cost`
- `machine_cost`
- `management_fee`
- `profit`
- `tax`

这些字段需要后续通过原始 Excel/XML/PDF 再解析补齐。

## 运行方式

### 先做干跑

```powershell
python tools/migrate_experience_to_price_reference.py --dry-run
```

### 小批量验证

```powershell
python tools/migrate_experience_to_price_reference.py --limit 1000
```

### 只迁移部分来源

```powershell
python tools/migrate_experience_to_price_reference.py --dry-run --sources user_confirmed,project_import
```

### 全量迁移

```powershell
python tools/migrate_experience_to_price_reference.py
```

## 迁移策略说明

为了保证可重跑和可追溯：

- 不修改旧 `experience.db`
- 新库独立写入 `price_reference.db`
- 每条迁移记录保留 `source_experience_id`
- 每条迁移记录带 `migration_flags=seed_from_experience_db;price_pending_backfill`
- 每个来源分组自动生成一条 `price_documents` 记录

## 下一步建议

迁完种子层后，后续建议按这个顺序补：

1. 从原始历史 Excel 中补抽 `综合单价`
2. 从 XML 中补抽 `项目特征`、`综合单价分析表`
3. 把旧经验库中的“主材 price”同步到设备/材料报价主线
4. 再接统一查询接口，把“综合单价种子层”和“真实价格层”一起返回
