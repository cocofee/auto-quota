# 人工审核错因反馈 Schema v1

用于第一阶段的人机协同回流，目标是让 `review-confirm` 后的 `human_feedback_payload` 能稳定进入后续统计、审计和知识分层。

## 推荐结构

```json
{
  "error_tags": ["wrong_family", "wrong_param"],
  "root_cause": "retrieval_bias",
  "decision_basis": "清单是配管，当前候选是配电箱，且单位 m/台 冲突",
  "action": "retry_search_then_select",
  "note": "先回电气配管语义重搜，再人工确认",
  "review_bucket": "red"
}
```

## 字段说明

- `error_tags`: 错因标签数组，建议优先复用 `openclaw_error_type` 与 `openclaw_reason_codes` 的语义
- `root_cause`: 人工判断的根因归类，如 `retrieval_bias` / `ranking_bias` / `book_scope_error` / `missing_candidate`
- `decision_basis`: 人工为什么这么判，一句话说明
- `action`: 人工认可的处理动作，如 `agree` / `override_within_candidates` / `retry_search_then_select` / `candidate_pool_insufficient`
- `note`: 补充说明
- `review_bucket`: 人工确认时看到的灯色，建议复用 `green/yellow/red`

## 第一阶段最小要求

如果前端或接口暂时不能一次性填满所有字段，至少保证：

- `error_tags`
- `root_cause`
- `note`

这三项可写入。

## 推荐 root_cause 枚举

- `retrieval_bias`
- `ranking_bias`
- `book_scope_error`
- `missing_candidate`
- `parameter_conflict`
- `ambiguous_text`
- `experience_pollution`
- `manual_exception`
