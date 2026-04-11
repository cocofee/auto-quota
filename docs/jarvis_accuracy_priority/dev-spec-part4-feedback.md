# 开发说明 Part 4：反馈层

模块位置：反馈链路、结果确认链路、OpenClaw 回流  
依赖：Part 2 检索与重排  
下游：权重校准、门控调参

## 1. 改动概述

反馈采集分两阶段：

- 阶段 A：轻量反馈
- 阶段 B：重排反馈

并将误排主因改为系统自动计算，不依赖用户填写。

## 2. 排序反馈表

```sql
CREATE TABLE IF NOT EXISTS ranking_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT DEFAULT '',
    result_id TEXT DEFAULT '',
    province TEXT DEFAULT '',
    query_text TEXT NOT NULL,
    selected_experience_id INTEGER,
    original_top1_experience_id INTEGER,
    original_rank_of_selected INTEGER,
    gate_bucket TEXT DEFAULT '',
    topk_snapshot TEXT DEFAULT '[]',
    dimension_scores_json TEXT DEFAULT '{}',
    misrank_primary_factor TEXT DEFAULT '',
    feedback_source TEXT DEFAULT '',
    actor TEXT DEFAULT '',
    created_at REAL NOT NULL
);
```

## 3. 两阶段采集

### 3.1 阶段 A

先埋轻量字段：

- 用户最终选了谁
- 原 top1 是谁
- 正确答案原来排第几
- gate 档位

此阶段不依赖完整重排上线。

### 3.2 阶段 B

在重排上线后补充：

- top-K 快照
- 各维度得分
- `misrank_primary_factor`

## 4. 误排主因自动计算

不让用户手填。系统比较原 top1 与正确答案在各维度上的差值，取最大差异项：

```python
def infer_misrank_primary_factor(top1_scores: dict, correct_scores: dict) -> str:
    factors = ["text", "specialty", "unit", "material", "source", "consensus"]
    best_factor = ""
    best_gap = -1.0
    for factor in factors:
        gap = float(correct_scores.get(factor, 0.0)) - float(top1_scores.get(factor, 0.0))
        if gap > best_gap:
            best_gap = gap
            best_factor = factor
    return best_factor
```

## 5. 权重校准

初始重排权重沿用 Part 2。  
每积累 50~100 条高质量反馈后，统计：

- 正确答案原排序分布
- 各维度成为 `misrank_primary_factor` 的频次
- 不同 gate 档位的纠错率

调参原则：

- 频繁因 `specialty` 误排：提高 specialty 权重
- 频繁因 `material` 误排：提高 material 权重
- 如果 authority 命中仍频繁被纠错：降低 source 权重，增加 text/material 约束

## 6. 需要修改的函数

| 函数 | 改动 |
|------|------|
| 结果确认/纠错入口 | 增加轻量反馈写入 |
| 重排返回结构 | 输出维度分数与 gate 信息 |
| `infer_misrank_primary_factor()` | 新增 |
| `run_weight_calibration_report()` | 新增 |

## 7. 验收标准

正例：

- 用户纠错后能记录“正确答案原来排第几”
- 重排上线后可记录 top-K 与维度得分
- `misrank_primary_factor` 能自动给出主因

反例：

- 不要求用户手填“为什么排错”
- 重排未上线前，不强行依赖维度得分字段
