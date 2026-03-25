# Knowledge Staging P1 实施清单

## 目标
P1 不再解决“这套链路能不能跑”，而是解决三件事：

1. 正式层不再只支持 `RuleKnowledge`，开始稳定扩到 `MethodCards` 和 `ExperienceDB`
2. 晋升不再主要依赖启发式，而是进入显式规则治理
3. staging 不再只是功能页，而是变成可筛选、可回溯、可评估的运营工作台

一句话定义：
P0 解决可用，P1 解决可治理、可扩层、可评估。

---

## P1-1 正式层扩展

### 目标
把 staging -> 正式层的能力，从 `RuleKnowledge` 扩到：

- `MethodCards`
- `ExperienceDB`

`UniversalKB` 在 P1 只预留接口和规则，不作为优先实现对象。

### 产出物

- `src/knowledge_promotion.py`
  - 扩 `promote_method_candidate()`
  - 扩 `promote_experience_candidate()`
- 正式层适配器
  - `src/method_cards.py`
  - `src/experience_db.py`
  - 如有必要补最小写入方法
- `web/backend/app/api/knowledge_staging.py`
  - 执行晋升接口支持：
    - `MethodCards`
    - `ExperienceDB`
- 文档
  - `docs/knowledge_promotion_rules_v1.md`

### 涉及模块

- `src/knowledge_promotion.py`
- `src/method_cards.py`
- `src/experience_db.py`
- `web/backend/app/api/knowledge_staging.py`
- `web/frontend/src/pages/Admin/KnowledgeStagingPage.tsx`

### 风险

- `MethodCards` 和 `ExperienceDB` 的写入结构未必像 `RuleKnowledge` 一样稳定
- 历史案例容易把噪声直接放大进 `ExperienceDB`
- 方法卡如果缺少统一格式，后面会继续脏

### 验收标准

- staging 候选可按 `candidate_type` 分流到：
  - `RuleKnowledge`
  - `MethodCards`
  - `ExperienceDB`
- 每类至少有 1 条真实样例跑通
- 每条正式层写入都能回写：
  - `promoted_target_id`
  - `promoted_target_ref`
  - `promotion_trace`

---

## P1-2 晋升治理规则化

### 目标
把“能晋升”升级为“按明确规则晋升”。

### 核心规则

- 每类候选必须定义晋升条件
- 每类候选必须定义拒绝条件
- 每类候选必须定义审核字段
- 每类正式知识必须支持回溯到 staging
- 每类正式知识必须可回退或降级

### 产出物

- `docs/knowledge_promotion_rules_v1.md`
  - 定义：
    - `rule`
    - `method`
    - `experience`
    的晋升条件
- staging 字段补充
  - `rejection_reason`
  - `review_comment`
  - `promotion_trace`
  - 如有需要补：
    - `conflict_status`
    - `dedupe_status`
- 冲突检查与重复检查
  - 规则重复
  - 方法重复
  - 经验污染风险
- 回退机制
  - 正式层 -> staging 候选

### 涉及模块

- `src/knowledge_staging.py`
- `src/knowledge_promotion.py`
- `web/backend/app/api/knowledge_staging.py`
- 对应正式层读写模块

### 风险

- 规则显式化如果定义太粗，会导致审核依然靠人工判断
- 回退如果只改正式层、不回写 staging，会破坏 trace
- 重复检测如果只看文本 hash，会误杀跨项目可复用内容

### 验收标准

- 每类候选晋升前都要经过显式校验
- 审核通过、驳回、冲突、重复，都能在 staging 中记录清楚
- 任一正式层知识，都能追溯到 staging 原始记录
- 任一已晋升知识，都至少有一种可执行的降级/回退路径

---

## P1-3 staging 运营工作台

### 目标
把 staging 页面从“最小功能页”升级成真正的审核工作台。

### 产出物

- staging 列表页支持筛选：
  - 状态
  - 来源表
  - 候选类型
  - 目标层
  - 错因类型
  - 匹配来源
- 详情联查增强：
  - `promotion_queue -> audit_errors`
  - `audit_errors -> task results`
  - `task results -> staging return`
- 列表页支持：
  - 审核备注
  - 驳回原因
  - promoted 状态查看
- 返回链路保持上下文不丢失

### 涉及模块

- `web/frontend/src/pages/Admin/KnowledgeStagingPage.tsx`
- `web/frontend/src/pages/Results/index.tsx`
- `web/backend/app/api/knowledge_staging.py`

### 风险

- 工作台如果只加筛选不加默认视图，会让管理员不知道先看什么
- 如果列表和详情状态不同步，审核容易误操作

### 验收标准

- 管理员可以在一页内完成：
  - 找候选
  - 看错因
  - 回原始结果
  - 审核
  - 晋升
- 返回 staging 后上下文不丢
- promoted、rejected、approved 三类状态都能快速筛出

---

## P1-4 效果评估

### 目标
开始回答“这套治理值不值得继续扩”。

### 指标建议

- 晋升后主链命中率变化
- 晋升后同类错因是否下降
- 审核耗时是否下降
- 每类候选的通过率 / 驳回率
- 正式层新增知识的实际命中次数
- 回退率 / 污染率

### 产出物

- `docs/knowledge_metrics_v1.md`
- 最小评估接口或脚本
  - staging 统计
  - promoted 命中统计
  - 错因分布统计
- 管理页最小指标卡

### 涉及模块

- `src/knowledge_staging.py`
- 相关日志或命中记录模块
- `web/backend/app/api/knowledge_staging.py`
- `web/frontend/src/pages/Admin/KnowledgeStagingPage.tsx`

### 风险

- 如果主链没有稳定记录“知识命中来源”，评估会失真
- 如果只看晋升数量，不看实际命中，会把噪声误当成果

### 验收标准

- 能看到各类候选的审核通过率
- 能看到正式层新增知识的基础命中统计
- 能给出至少一份“P1 扩层是否有效”的量化结论

---

## 推荐实施顺序

1. `P1-1` 先接 `MethodCards`
2. `P1-1` 再接 `ExperienceDB`
3. `P1-2` 把晋升规则显式化
4. `P1-3` 把 staging 页升成运营工作台
5. `P1-4` 再补效果评估

原因：

- 先扩层，才知道治理规则覆盖的对象是谁
- 先把规则钉死，工作台才不会只是“更好看的手工页”
- 先有稳定流转，再谈指标，数据才有意义

---

## P1 结束标准

满足以下条件可认为 P1 完成：

1. `RuleKnowledge + MethodCards + ExperienceDB` 三类正式层都已打通晋升
2. 晋升、驳回、冲突、重复、回退都有明确记录
3. staging 页面已具备筛选、联查、审核、晋升、回退的工作台能力
4. 能对主链收益做最小量化评估

---

## 一句话收口

P1 的本质不是继续堆功能，而是把 `knowledge_staging` 从一条可跑的回流链，做成一个可扩正式层、可治理、可评估的知识运营系统。
