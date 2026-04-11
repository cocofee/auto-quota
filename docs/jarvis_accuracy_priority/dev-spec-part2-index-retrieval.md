# 开发说明 Part 2：索引构建与检索层

模块位置：`src/experience_db.py`  
依赖上游：Part 1 完成  
下游：反馈层、门控层

## 1. 改动概述

检索流程升级为：

```text
标准化 -> 索引构建/增量更新 -> 多通道召回 -> 硬过滤 -> 结构化重排 -> 门控
```

## 2. 索引体系

| 类型 | 实现 | 用途 | 更新策略 |
|------|------|------|----------|
| 精确索引 | SQLite 普通索引 | 精确匹配、字段过滤 | 随事务更新 |
| 词法索引 | SQLite FTS5 | BM25 召回 | 增量更新，批量导入后重建 |
| 向量索引 | 单 collection + metadata filter | 语义召回 | 增量写入，晋升时改 metadata |
| 结构索引 | SQLite 组合索引 | 结构召回、冲突检测 | 随事务更新 |

### 2.1 建议索引

```sql
CREATE INDEX IF NOT EXISTS idx_exp_normalized_text ON experiences(normalized_text);
CREATE INDEX IF NOT EXISTS idx_exp_specialty_unit ON experiences(specialty, bill_unit);
CREATE INDEX IF NOT EXISTS idx_exp_layer ON experiences(layer);
CREATE INDEX IF NOT EXISTS idx_exp_province ON experiences(province);
CREATE INDEX IF NOT EXISTS idx_exp_quota_fingerprint ON experiences(quota_fingerprint);
CREATE INDEX IF NOT EXISTS idx_exp_structure ON experiences(specialty, bill_unit, materials_signature);
CREATE INDEX IF NOT EXISTS idx_exp_quota_group ON experiences(normalized_text, specialty, bill_unit, quota_fingerprint);
```

FTS：

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS experiences_fts USING fts5(
    experience_id,
    bill_text,
    normalized_text,
    feature_text,
    quota_names,
    content='experiences'
);
```

## 3. 查询扩展顺序

全局 shard-step planner 按以下顺序扩展，不允许各通道各自乱扩：

1. 本省 `authority`
2. 全国 `authority`
3. 本省 `verified`
4. 全国 `verified`
5. 本省 `candidate`
6. 全国 `candidate`

停机条件：

- 已有 >= 3 条候选的预估总分 >= 0.85
- 或已有 >= 5 条候选的预估总分 >= 0.60
- 或已扩到第 6 步

## 4. 五通道召回

| 通道 | 逻辑 | 最大返回 |
|------|------|----------|
| exact | `normalized_text` 精确命中 | 不限 |
| alias | 同义词映射后精确命中 | 20 |
| bm25 | `bill_text + normalized_text + feature_text + quota_names` | 30 |
| vector | embedding top-K | 30 |
| structural | `specialty + unit + materials_signature` | 30 |

归一化原则：

- `exact = 1.0`
- `alias = 0.9`
- `vector` 余弦相似映射到 0~1
- `bm25` 需做 min-max 或 sigmoid 归一化
- `structural` 独立打分，不混进 `text_score`

## 5. 硬过滤

| 条件 | 动作 |
|------|------|
| `specialty` 不一致 | 直接过滤 |
| `unit` 严重不一致 | 直接过滤 |
| `unit` 属于等价组 | 允许通过 |
| 主材首类冲突 | 总分乘 0.5 |
| 定额版本不一致 | 标记 `version_mismatch`，不能 green |
| 跨省 | 标记 `cross_province` |

单位等价组：

- `台 / 套`
- `m / 延长米`
- `m² / 平方米`
- `m³ / 立方米`
- `项 / 处`

## 6. 结构化重排

初始权重：

| 维度 | 权重 |
|------|------|
| text | 0.35 |
| specialty | 0.20 |
| unit | 0.15 |
| material | 0.15 |
| source | 0.10 |
| consensus | 0.05 |

总分：

```python
total = (
    0.35 * text_score +
    0.20 * specialty_score +
    0.15 * unit_score +
    0.15 * material_score +
    0.10 * source_score +
    0.05 * consensus_score
)
total *= penalty_factor
```

说明：

- `materials_signature` 缺失时，该维跳过并重归一权重
- `_unknown` 桶记录固定降 0.1
- `specialty/unit` 严重冲突不只是打红标，必须提前过滤

## 7. 门控

### 7.1 阈值

| 档位 | 条件 |
|------|------|
| green | `total >= 0.85` 且 `layer = authority` 且专业/单位/版本一致且无 red flag |
| yellow | `total >= 0.60` 但不满足 green |
| red | `total < 0.60` 或有 red flag |

### 7.2 red_flag

- `authority_conflict`
- `specialty_mismatch`
- `unit_mismatch_severe`
- `material_conflict`

### 7.3 行为

- green：可自动推荐
- yellow：仅候选展示
- red：末尾展示，不建议使用

## 8. 需要新增/修改的函数

| 函数 | 改动 |
|------|------|
| `search_experience()` | 六段式流程 |
| `_recall_exact()` | 新增 |
| `_recall_alias()` | 新增 |
| `_recall_bm25()` | 新增/重构 |
| `_recall_vector()` | 新增/重构 |
| `_recall_structural()` | 新增 |
| `_merge_recall_results()` | 新增 |
| `_hard_filter()` | 新增 |
| `_compute_rerank_score()` | 新增 |
| `_apply_gate()` | 新增 |
| `_expand_query_layers()` | 新增 |
| `build_fts_index()` | 新增 |
| `sync_vector_metadata()` | 新增 |

## 9. 验收标准

正例：

- 本省 authority 精确命中时，不应扩到全国
- authority 数量不足时，按既定顺序逐层扩展
- 同专业同单位同主材记录应优先排前

反例：

- 不允许 `specialty` 不一致的结果出现在候选中
- 不允许严重 `unit` 不一致结果仅靠 red flag 留在 top-K
- 不允许各通道单独决定扩展顺序
