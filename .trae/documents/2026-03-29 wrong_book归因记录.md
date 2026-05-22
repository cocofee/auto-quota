# 2026-03-29 wrong_book归因记录

## 当前背景

- 主架构收口已基本完成，主链已收成：
  - `parser/query -> retriever -> ranker -> veto -> output`
- 全省 `closed-book smoke` 结果：
  - `805` 条
  - `273` 条正确
  - `33.9%`

当前低分已经不是“后处理层乱改 top1”为主，而是主链真实问题暴露出来。

## 总体误差分布

- `retriever`: `279`
- `ranker`: `235`
- `ltr_ranker`: `18`

主因：

- `wrong_book`: `192`
- `synonym_gap`: `187`
- `wrong_tier`: `130`

## 这一步新增诊断

本轮补了两件事：

1. `router trace` 新增 `classification`
   - `primary`
   - `candidate_books`
   - `search_books`
   - `hard_book_constraints`
   - `route_mode`
2. `tools/classify_retriever_miss.py` 支持从旧明细里回退读取：
   - `router.classification.search_books`
   - `router.unified_plan.preferred_books`
   - `router.unified_plan.primary_book`

这样下一轮评测可以直接看到“真实搜了哪些册”，不再只看 `primary_book`。

## 当前 retriever miss 归因

基于 `output/real_eval/real_eval_smoke_all_province.closed.details.jsonl`：

- `index_miss`: `24`
- `routing_miss`: `120`
- `knowledge_not_used`: `135`

注意：

- 这份评测是 `closed-book`。
- 所以 `knowledge_not_used = 135` 不能直接当成线上 bug，它更像是在说明：
  - 如果把经验/知识资产接回主链，这一块有潜在回收空间。
- 当前 closed-book 基线真正可直接优化的还是：
  - `routing_miss`
  - `index_miss`

## 当前确认的真问题

### 1. 路由仍然是大头之一

`120` 条 retriever miss 属于 `routing_miss`。

典型表现：

- oracle 在库里
- 但 `search_books` 没覆盖 oracle 所在册
- 或 query 被路由到错误家族后，只在错册里搜

### 2. 有一批是数据/索引问题，不是算法问题

`24` 条属于 `index_miss`。

典型表现：

- oracle `quota_id` 在当前省份 `quota.db` 里不存在
- 再怎么调排序和 query 都救不回来

这一批要单独登记，不能混进算法锅里。

### 3. closed-book 低分天然压住了资产能力

`knowledge_not_used = 135` 说明很多样本和经验库/通用知识库有强重合，
但这轮 `closed-book` 本来就没把这条能力链 fully 打开。

因此：

- 这条不是当前 closed-book 的第一实现项
- 但它决定了后面 `with-experience` 必须单独压测

## 下一步只做什么

只做 `wrong_book`，不并行开 `synonym_gap/wrong_tier`。

下一步执行顺序：

1. 先拉 `routing_miss` 样本表
   - 按省份
   - 按 `search_books -> oracle_book` 偏差
   - 按 query 主体是否跑偏
2. 再拉 `index_miss` 清单
   - 单独当数据问题处理
3. 暂不在这一步处理 `knowledge_not_used`
   - 等主链路由看清后，再单独做 `with-experience` 子集压测

## 一句话结论

现在低，不是主架构没收住，而是收住之后暴露了两类真问题：

- 一类是 `routing/index`
- 一类是 `rank/synonym/tier`

当前先打第一类，而且先打 `routing_miss`。
