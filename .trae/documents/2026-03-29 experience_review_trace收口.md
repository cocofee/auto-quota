# 2026-03-29 experience_review_trace 收口

## 本步目标

不改算法，不调排序，只补主架构里的可观测性缺口：

- 经验库命中后如果被 `review_check` 拦截
- 这件事不能只留在日志里
- 最终 search/agent 结果 trace 里也必须能看到

## 本步改动

1. `src/match_pipeline.py`
   - 经验命中被审核拦截时，除原有 `_review_rejected=True` 外，再写入：
     - `_experience_review_rejection.type`
     - `_experience_review_rejection.reason`
     - `_experience_review_rejection.match_source`
     - `_experience_review_rejection.quota_id`
   - 新增 `_append_item_review_rejection_trace(...)`
   - `search` 主链最终结果会把这条 rejection 事件带入 trace

2. `src/match_engine.py`
   - `agent` 主链在写入 `agent_llm` trace 前，也会把这条 rejection 事件带入最终结果

3. 测试
   - `tests/test_decision_engine_contract.py`
     - 新增：`test_review_rejected_experience_is_carried_to_final_trace`
   - `tests/test_match_service_trace_compaction.py`
     - 覆盖：trace 精简后仍保留 `experience_review_rejected`

## 验证

已通过的定向测试：

- `tests/test_decision_engine_contract.py::TestResolveSearchResult::test_review_rejected_experience_is_carried_to_final_trace`
- `tests/test_match_service_trace_compaction.py::test_compact_trace_keeps_reasoning_final_validation_and_review_rejection`
- `tests/test_review_check_primary_subject.py`

结果：`3 passed`

说明：

- 之前那两个旧红例仍然存在，但与本步改动无关，没有顺手去改，避免偏离主架构

补充实跑验证：

- `宁夏房屋建筑装饰工程计价定额(2019)` `with_memory`，`20` 条
- 导出明细里已能看到 `experience_review_rejected`
- 命中 `1` 条：
  - `exp:446760`
  - 类型：`category_mismatch`
  - 被拦的经验来源：`experience_similar`
  - 被拦 quota：`1-15-238`
  - 最终结果：`match_source=search`
  - 最终是否匹对：`is_match=true`

结论：

- 这类事件现在已经能被稳定观测
- 且从宁夏这批样本看，它不是当前 95% -> 100% 的主瓶颈
- 后续不应继续围着 review 大改，而应回到主计划

## 当前状态

这一段主架构已经收口到：

- review 对经验直通的误拦，至少现在能在最终 trace 里被看见
- 后续全省 `with_memory` / `closed_book` 跑出来后，可以直接分辨：
  - 资产没命中
  - 资产命中了但被 review 挡掉
  - 还是纯 search 主链问题

## 下一步

继续按计划，不发散：

1. 跑一轮带 `with_memory` 的聚焦评测
2. 统计 `experience_review_rejected` 在样本中的真实数量和类型
3. 如果数量很小：
   - 不继续折腾 review
   - 转入 `synonym_gap`
4. 如果数量不小：
   - 只针对高频误拦类型做最小修正
