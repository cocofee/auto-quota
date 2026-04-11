# 速度与质量双约束主链方案
更新时间：2026-04-08

## 最新补充结论

经过进一步复查代码，当前系统不是“还没有做速度优化”，而是“速度优化基础已经具备，当前主矛盾转向质量收益率”。

也就是说：

- 现在不是所有样本都走长链
- 现在已经有 fast / standard / deep 分流
- 现在已经有 cache / prewarm / lightweight / exact direct / timeout / cooldown
- 现在真正的问题，是快路径没命中后结束太早，或者进入标准路径后投入不小但质量收益不够高

因此后续口径要修正为：

1. 不重复做已经做过的提速动作
2. 不为了“再快一点”去删质量闸门
3. 重点提升每一段耗时的质量产出

## 当前代码里已经存在的速度优化

### 1. 策略分流已经存在

- `src/adaptive_strategy.py`
  已经有 `fast / standard / deep` 三档策略，不是所有请求都走同一个重链。
- `src/policy_engine.py`
  已经按 route 给 `agent_fastpath_score / score_gap / min_candidates` 分档，不是一个全局死阈值。

### 2. 主链轻量化开关已经存在

- `src/match_pipeline.py`
  已经有：
  - `exact_exp_direct`
  - `lightweight_experience`
  - `lightweight_rule_prematch`
- `src/ambiguity_gate.py`
  已经支持在足够明确时直接 fastpath，不是默认全部进入重审。

### 3. 检索层缓存和限流已经存在

- `src/hybrid_searcher.py`
  已经有 `_session_cache`，同 query + books 组合会复用检索结果。
- `src/hybrid_searcher.py`
  已经做了批量向量编码，不是逐条 encode。
- `src/hybrid_searcher.py`
  `collect_prior_candidates()` 已经把单源 `top_k` 压到小窗口。
- `src/hybrid_searcher.py`
  Universal KB keyword 只在 `standard` 路径触发，且有 timeout + cooldown。

### 4. 运行时预热和重对象复用已经存在

- `src/runtime_cache.py`
  已经缓存：
  - `RuleValidator`
  - `Reranker`
  - `ExperienceDB`
  - `HybridSearcher`
  - `UnifiedDataLayer`
- `src/runtime_cache.py`
  已经支持 prewarm，不是每次任务都冷启动全量对象。

### 5. 性能观测也已经有基础

- `src/match_pipeline.py`
  已经用 `PerformanceMonitor` 给多个阶段做了耗时打点。

所以，当前不应继续把问题表述成“主链还没提速”。这与代码现状不符。

## 现在真正的瓶颈

### 1. `fast` 路径结束太早

- `src/match_pipeline.py`
  当前 `adaptive_fast_return` 在经验库 miss 后直接空返。

这不是慢，而是过早结束。
代价不是时延，而是召回和质量。

### 2. `standard` 路径的投入产出比不够高

- `src/unified_retrieval.py`
  一旦进入标准检索，仍会对 `hybrid / bm25 / vector / experience / rule / universal_kb` 进行统一扇出。

这不代表系统“完全没优化”，但说明：

- 标准路径已经不便宜
- 如果最后仍然落在 `insufficient_candidates / small_score_gap / low_param_score`
- 那问题就不是单纯速度，而是投入后的质量收益率偏低

### 3. validator 不是主耗时，删它不划算

- `src/final_validator.py`
  当前更像轻量兜底和风控层。
- `src/ambiguity_gate.py`
  当前更像最小质量闸门。

这两层删除后换不到多少速度，却会明显伤质量。

## 结论修正

后续工作重点不再是“继续缩短主链”，而是：

1. 保留现有速度优化基础
2. 修复快路径误杀
3. 提高标准路径的质量产出
4. 用现有性能打点确认真实耗时热点

## 对当前代码的直接判断

### 1. 优先级最高的点仍是 `adaptive_fast_return`

`src/match_pipeline.py` 当前逻辑：

- `fast`
- 经验库 miss
- 直接返回空结果

这条策略省时间，但会明显伤召回。

因此这里的建议仍然成立，但意义已经变化：

- 不是为了“继续提速”去改它
- 而是为了避免快路径误杀，提升速度投入的有效性

建议改成：

- `fast miss`
- 升级到 `standard retrieve + rank`
- 只有标准链仍失败时才空返

### 2. 第二优先级是标准检索的分层触发

`src/unified_retrieval.py` 建议改的不是“全量砍掉”，而是：

- 第一层：cheap and decisive
  - rule exact
  - experience exact/high confidence
  - hybrid 基础召回
- 第二层：只在候选不足时补
  - prior candidates
  - vector 扩召回
  - universal kb hints
- 第三层：只在 high-risk 样本触发
  - 跨册借册
  - 更重的补检索/比对

目标不是单纯减少耗时，而是让每次加码都有明确收益条件。

### 3. 第三优先级是用已有打点真正做诊断

当前最该看的不是抽象“快不快”，而是实际阶段分布：

- `查询构建`
- `专业分类`
- `search_rank_pipeline`
- `search_selection_decision`
- `final_validator`

如果没有把这些阶段耗时拉出来，就容易重复优化已经不再是瓶颈的部分。

## 保留不变的原则

这些仍然成立：

- OpenClaw 不进同步首轮搜索主链
- OpenClaw 继续做异步复核和沉淀
- `param_mismatch / backup_conflict / insufficient_candidates / small_score_gap / reranker_failed` 这些最小质量闸门不删

## 最新执行口径

现在的口径应改成：

1. 速度优化基础已具备
2. 当前主问题是快路径误杀和标准路径收益率
3. 不重复做低收益提速
4. 先修 `adaptive_fast_return`
5. 再做标准检索分层触发
6. 最后依据性能打点决定是否还需要继续做更深的性能优化
