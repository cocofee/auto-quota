# 搜索能力优先路线图
更新时间：2026-04-08

## 决策结论

当前阶段不让 OpenClaw 直接替代 Jarvis 做首轮定额匹配。

优先级调整为：

1. 先提升首轮搜索能力。
2. 再提升排序与参数对齐能力。
3. 最后再用 OpenClaw 降低人工复核量。

原因：

- OpenClaw 当前定位更接近复核器，不是独立首轮匹配引擎。
- 真正决定整体命中率上限的是首轮召回质量、候选池覆盖率、top1/top2 可分性。
- 现有链路里，很多人工复核并不是因为完全找错，而是因为 `insufficient_candidates`、`small_score_gap`、`low_param_score` 这类首轮问题没有解决。

## 路线原则

- 只改主链路，不把主问题转嫁给二审。
- 先补召回，再拉开排序差距，最后压缩人工量。
- 每个阶段都必须有可观测指标，不接受“感觉更准了”。
- 优先修改已有骨架，不重写整条流水线。

## 阶段 0：补齐诊断面板

目标：先把“错在哪”看清，再做有方向的优化。

输出：

- 按 `route`、`province`、`specialty`、`book` 统计首轮结果。
- 统计以下失败原因占比：
  - `routing_miss`
  - `keyword_miss`
  - `insufficient_candidates`
  - `small_score_gap`
  - `low_param_score`
  - `experience_review_rejected`
- 新增 top1/top2 gap 监控和候选池覆盖率监控。

重点文件：

- `src/match_pipeline.py`
- `src/ambiguity_gate.py`
- `src/final_validator.py`
- `src/accuracy_tracker.py`

验收：

- 每日评测报表能直接回答“当前主要损失发生在 router / retriever / ranker 哪一层”。
- 能区分“完全没召回到”与“召回到了但排不出来”。

## 阶段 1：查询改写与路由增强

目标：优先解决候选池不够和书册路由偏差。

输出：

- 把单一检索词扩成多路查询：
  - 原始词
  - 规范词
  - 规格词
  - 系统词
  - 别名词
  - 去噪词
- 把 `query_route` 从纯启发式升级为“规则 + 可回放统计”的路由。
- 强化材料/安装分流、短标题场景、跨册借册场景的 route 决策。

重点文件：

- `src/query_router.py`
- `src/match_pipeline.py`
- `src/match_core.py`

验收：

- `routing_miss` 显著下降。
- `insufficient_candidates` 显著下降。
- 候选池 `candidate_count >= 3` 的比例上升。

## 阶段 2：候选池扩容与先验注入治理

目标：让正确答案更稳定地进入候选池，而不是靠后置纠错。

输出：

- 强化 unified retrieval 的多源合并策略。
- 扩大 prior candidates 的有效覆盖，但收紧低质量注入。
- 把经验库、别名、通用知识、rule 命中从“有就塞”改成“带理由和来源权重注入”。
- 对候选池补充以下元数据：
  - 来源数
  - 命中原因
  - 是否跨册借册
  - 是否规格直命中
  - 是否经验精确锚点

重点文件：

- `src/unified_retrieval.py`
- `src/hybrid_searcher.py`
- `src/unified_knowledge.py`
- `src/rule_matcher.py`

验收：

- `empty_result` 和 `insufficient_candidates` 继续下降。
- 正确答案进入 top10 候选池的比例提升。
- prior candidates 不再明显放大错误召回。

## 阶段 3：排序与参数对齐

目标：把“前两名都像”的情况拉开，不再大量掉进 `small_score_gap`。

输出：

- 重新校正 unified scoring 权重，不再只靠通用模板。
- 单独强化以下特征：
  - 主参数精确匹配
  - 单位一致性
  - 材质一致性
  - family / system 对齐
  - route 下的特征权重差异
- 对 `dirty_short_text`、规格重、安装重等场景做专项权重校准。
- 引入按 route 分桶的 confidence 校准，而不是全局一套阈值。

重点文件：

- `src/unified_scoring_engine.py`
- `src/unified_ranking_pipeline.py`
- `src/candidate_canonicalizer.py`
- `src/param_validator.py`

验收：

- `small_score_gap` 显著下降。
- `low_param_score` 显著下降。
- top1/top2 margin 中位数提升。

## 阶段 4：参数抽取结构化

目标：把 DN、线径、材质、安装方式等从“文本命中”升级为结构化约束。

输出：

- 为核心专业建立统一参数槽位。
- 缺失参数和冲突参数进入显式字段，而不是混在自然语言说明里。
- 参数抽取失败和参数冲突要能单独统计。

重点文件：

- `src/param_validator.py`
- `src/installation_validator.py`
- `src/unified_scoring_engine.py`
- `src/match_pipeline.py`

验收：

- 参数相关误判样本下降。
- 规格型 query 的 top1 稳定性提升。

## 阶段 5：收紧 fast path，建立学习闭环

目标：减少“没搜深就提前结束”的情况，并把人工纠正转成首轮搜索资产。

输出：

- 收紧 `adaptive fast` 适用范围。
- fast miss 后优先升级到 `standard/deep`，而不是直接返回空结果。
- 把人工修正结果优先沉淀成：
  - query rewrite 规则
  - alias 映射
  - 参数模板
  - 借册策略

重点文件：

- `src/match_pipeline.py`
- `src/adaptive_strategy.py`
- `src/accuracy_tracker.py`
- `knowledge` / `knowledge_notes` 相关沉淀流程

验收：

- `adaptive_fast` 提前返回造成的 miss 下降。
- 人工修正后，同类样本的首轮命中率可复现提升。

## 阶段 6：最后才做 OpenClaw 自动化收口

目标：在首轮能力提升后，用 OpenClaw 只处理困难样本，不承担主搜职责。

输出：

- OpenClaw 继续聚焦黄灯/红灯和高风险歧义项。
- 仅在首轮候选池已经足够好的前提下，尝试减少人工确认量。
- 不把 OpenClaw 改造成主检索器。

重点文件：

- `web/backend/app/api/openclaw.py`
- `web/backend/app/services/openclaw_review_service.py`

验收：

- 人工复核量下降。
- 不以牺牲首轮可解释性和可控性为代价。

## 两周执行顺序

第一周：

1. 做阶段 0 的诊断统计。
2. 做阶段 1 的 query rewrite 和 route 修正。
3. 复跑评测，确认主要损失是否从 `routing_miss / insufficient_candidates` 下移。

第二周：

1. 做阶段 2 的候选池扩容。
2. 做阶段 3 的排序和 confidence 校准。
3. 复跑评测，重点看 `small_score_gap / low_param_score`。

## 本轮明确不做

- 不让 OpenClaw 替代 Jarvis 做首轮匹配。
- 不把大量问题继续后移给人工二审。
- 不同时重写 router、retriever、ranker 三层。
- 不先改大模型调用方式来掩盖召回和排序问题。

## 当前执行口径

后续关于“准确率提升”的改动，默认按以下顺序推进：

1. router / rewrite
2. retriever / candidate pool
3. ranker / confidence
4. param extraction
5. fast-path governance
6. OpenClaw review automation

## 2026-04-08 补充判断：速度侧基础已具备

这一轮补充结论：当前代码已经具备一批明确的速度优化基础，不应再把问题简单表述成“主链还没提速”。

已存在的速度优化包括：

- `adaptive_strategy` 的 `fast / standard / deep` 分流
- `match_pipeline` 的 `exact_exp_direct / lightweight_experience / lightweight_rule_prematch`
- `hybrid_searcher` 的 session cache、批量向量编码、prior candidate 小窗口
- Universal KB keyword timeout + cooldown
- `runtime_cache` 的 cache + prewarm
- `policy_engine` 的 route 级 fastpath 阈值

因此当前更准确的判断是：

1. 速度基础已经有了
2. 当前主要问题是 `adaptive_fast_return` 导致快路径误杀
3. 当前第二问题是标准检索链投入后的质量收益率不够高
4. `ambiguity_gate` 和 `final_validator` 属于最小质量闸门，不值得作为下一轮提速的主要裁剪对象

这会影响执行顺序：

- 第一优先级：修 `fast miss -> 空返`
- 第二优先级：把 `standard retrieval` 改成按条件分层触发
- 第三优先级：用现有 `PerformanceMonitor` 拉出真实阶段耗时，再判断是否还有新的性能热点
