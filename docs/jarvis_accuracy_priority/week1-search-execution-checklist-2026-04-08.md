# 第一周执行清单：搜索能力优先
更新时间：2026-04-08

## 本周目标

只做两件事：

1. 建立首轮搜索诊断面板。
2. 修正 query rewrite 和 route。

本周不做：

- 不改 OpenClaw 主职责。
- 不大改 reranker。
- 不同时改 router、retriever、ranker 三层。
- 不先碰大模型策略。

## 交付物

### A. 诊断报表

输出一份可重复生成的诊断结果，至少包含：

- `route` 分桶统计
- `province` 分桶统计
- `specialty` 分桶统计
- `book` 分桶统计
- `routing_miss`
- `keyword_miss`
- `insufficient_candidates`
- `small_score_gap`
- `low_param_score`
- `experience_review_rejected`
- top1/top2 gap 分布
- 候选池 `candidate_count` 分布

建议落盘：

- `output/real_eval/`
- `reports/`

### B. query rewrite v1

至少补齐以下几类检索表达：

- 原始 query
- normalized query
- route query
- canonical name
- system hint
- spec-focused query
- alias-expanded query

要求：

- 每条样本都能回放“最终用了哪些 query 参与检索”。
- 改写结果进入 trace，而不是只在内存里临时生效。

### C. route 修正 v1

本周只收口三个高价值场景：

1. 材料 vs 安装分流
2. 短标题/脏标题场景
3. 跨册借册场景

要求：

- route 决策要能带 reason。
- route 命中率要能统计，不接受纯规则黑盒。

## 任务拆分

### Task 1. 补诊断字段和导出脚本

目标：

- 把首轮失败原因稳定导出成报表。

建议改动点：

- `src/match_pipeline.py`
- `src/ambiguity_gate.py`
- `src/final_validator.py`
- `src/accuracy_tracker.py`
- 需要的话新增 `tools/` 下诊断导出脚本

完成标准：

- 对同一批评测数据重复跑两次，统计口径一致。
- 能直接看出主要损失在 router 还是 candidate pool 还是 ranker。

### Task 2. 给 query rewrite 留痕

目标：

- 让 query 改写可观测、可回放、可对比。

建议改动点：

- `src/match_pipeline.py`
- `src/query_router.py`
- `src/match_core.py`

完成标准：

- trace 中能看到：
  - raw query
  - normalized query
  - route query
  - expanded queries
  - 最终用于 search 的 query 集合

### Task 3. route 修正

目标：

- 先把最常见的 route 偏差场景收口。

建议改动点：

- `src/query_router.py`
- `src/match_pipeline.py`
- `src/match_core.py`

完成标准：

- 新增或修正 route reason。
- 能单独统计：
  - `material`
  - `installation_spec`
  - `spec_heavy`
  - `semantic_description`
  - `ambiguous_short`
  - `balanced`

### Task 4. 小样本回归

目标：

- 每完成一小步就确认没有明显副作用。

最低要求：

- 跑现有相关测试。
- 跑一批真实评测或 smoke 数据。
- 对比以下指标：
  - `routing_miss`
  - `insufficient_candidates`
  - 命中率
  - top1/top2 gap

## 每日节奏

### Day 1

- 补诊断统计口径。
- 定义报表字段。
- 固定本周对比样本集。

### Day 2

- 接入 query rewrite 留痕。
- 报表里加入 query 使用信息。

### Day 3

- 修材料/安装分流。
- 复跑评测。

### Day 4

- 修短标题/脏标题 route。
- 复跑评测。

### Day 5

- 修跨册借册 route。
- 复跑评测并汇总结论。

## 本周验收口径

必须至少满足其中两条：

1. `routing_miss` 明显下降。
2. `insufficient_candidates` 明显下降。
3. 候选池 `candidate_count >= 3` 的比例提升。
4. 首轮命中率提升且没有明显引入新的高风险误判。

## 周末输出

周末必须产出三份东西：

1. 一份诊断报表
2. 一份 route/query 改动总结
3. 一份下周是否进入“候选池扩容 + 排序校准”的判断结论

## 下周进入条件

只有当下面条件满足，才进入第二周：

- route 和 query 改动已经可稳定复跑
- 主要损失开始从 `routing_miss / insufficient_candidates` 往后移
- 当前问题已经证明“继续改 route 的边际收益下降”
