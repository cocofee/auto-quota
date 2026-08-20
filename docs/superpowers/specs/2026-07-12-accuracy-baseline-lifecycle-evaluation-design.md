# 准确率基线与候选生命周期评测设计

日期：2026-07-12

## 1. 背景与目标

当前准确率问题无法归因到单一模型或规则。现有证据表明，召回深度、路由过滤、taxonomy 硬冲突、参数校验、排序和后处理都可能造成正确候选丢失或 Top1 被改写；同时，生产链与 Goal 实验链使用不同候选源、特征和评测口径。

本设计建立一个只读、可复现的统一评测内核，用相同数据和指标公平比较生产算法、Goal Shadow 候选源以及未来统一 ranker。第一阶段只建立基线和归因能力，不改变在线算法、不写经验库、不部署模型。

## 2. 已确认原则

- 保留现有生产主链作为编排壳，不立即替换。
- GoalSearcher 先作为 Shadow Candidate Provider，不直接决定生产 Top1。
- 使用真实人工确认集与 OSS 诊断集双轨评测。
- 历史失败集只做压力测试，不承担总体准确率结论。
- Provider 可以替换，数据契约与指标口径不能随算法变化。
- taxonomy 默认作为可校准信号；是否改为软特征属于后续算法实验，不在本阶段实施。
- 所有评测默认只读，不连接经验写入、在线任务队列或生产配置更新。

## 3. 数据集设计

### 3.1 主指标集

主指标集来自真实人工确认、人工纠正或经过明确审核的清单-定额对，用于计算最终 Top1、Top3、拒答率和置信度校准。

每条样本必须包含：

- `case_id`
- `province`
- `bill_name`
- `bill_text`
- `unit`
- `specialty`
- `oracle_quota_ids`
- `source_family`
- `project_id` 或可审计的项目来源标识
- 数据版本与审核来源

数据按项目、source-family 和省份切分，避免同项目或同源样本同时进入训练与评测。

### 3.2 OSS 诊断集

OSS 诊断集从 OSS XML 母库及其可审计衍生物构建，用于衡量召回覆盖、条件排序能力和跨省泛化。OSS 指标不与真实主指标合并。

抽样必须保留 province、source、source-family、项目或 XML 来源，并覆盖不同定额体系、专业、family 和参数复杂度。

### 3.3 历史压力集

历史失败、全局修复和困难样本只报告修复率、新增回归数与阶段错因。它们不参与总体准确率结论，也不作为 heldout 代表集。

## 4. 评测架构

采用“统一评测内核 + Provider Adapter”方案，包含五个职责明确的组件。

### 4.1 DatasetLoader

读取主指标集、OSS 诊断集和历史压力集，并统一为 `EvalCase`。Loader 负责 schema 校验、来源元数据保留和无效样本标记，不调用算法。

### 4.2 CandidateProvider

Provider 接收 `EvalCase`，输出标准化候选、最终选择和原始 trace。

- `ProductionProvider`：调用真实生产匹配链。
- `GoalShadowProvider`：调用 GoalSearcher，只用于 Shadow 比较。

Provider 不计算总体指标，也不修改数据库或生产状态。

### 4.3 LifecycleNormalizer

将不同算法链的原始字段归一化为六个稳定阶段：

1. `retrieved`
2. `route_filtered`
3. `reranked`
4. `validated`
5. `selected`
6. `postprocessed`

标准化阶段必须保留原始 stage 名称、候选来源、原始排名、分数、过滤原因和关键冲突标志。若算法链没有某一阶段，记录为 `not_emitted`，不得伪造候选状态。

### 4.4 MetricEngine

基于标准化生命周期计算召回、条件排序、最终结果、阶段翻转和切片指标。MetricEngine 不依赖具体 Provider 实现。

### 4.5 ReportWriter

输出机器可读、可重复比较的报告：

- `summary.json`
- `cases.jsonl`
- `stage_attribution.csv`
- `slice_metrics.csv`
- `provider_comparison.csv`

第一阶段不引入数据库型实验平台，也不写 AccuracyTracker。

## 5. 标准数据契约

### 5.1 EvalCase

`EvalCase` 表示一个可评测清单，包含输入字段、正确答案集合、数据来源、切分信息和排除原因。多个合理定额使用 `oracle_quota_ids` 表示，命中任一值即视为正确。

### 5.2 CandidateSnapshot

候选快照至少包含：

- `quota_id`
- `name`
- `unit`
- `province`
- `provider`
- `source`
- `stage`
- `rank`
- `scores`
- `family`
- `book`
- `param_match`
- `hard_conflicts`
- `drop_reason`
- `raw_stage`

缺失字段保留为空并记录 contract coverage，不用默认值伪装为有效信号。

### 5.3 ProviderResult

Provider 结果包含：

- `case_id`
- `provider_name`
- `status`
- `final_quota_ids`
- `confidence`
- `lifecycle`
- `raw_trace`
- `runtime_metadata`
- `errors`

## 6. 指标与归因口径

### 6.1 召回层

- `Recall@5/10/25/80`
- 正确候选首次出现的阶段与排名
- Provider 独立召回率
- 候选并集的增量召回率
- 路由过滤、候选截断和 hard fail 的正确候选损失数及损失率

### 6.2 排序层

- `Conditional Top1`：仅在正确候选进入当前候选池时计算
- MRR
- 正确候选平均排名
- 每阶段相对上一阶段的 `good_flip`
- 每阶段相对上一阶段的 `bad_flip`
- `net_gain = good_flip - bad_flip`

应分别报告 manual、LTR、structural、lifecycle、CGR、arbiter、explicit picker、final decider 和 final validator；未执行的阶段明确标记为 disabled 或 not emitted。

### 6.3 最终结果层

真实主指标集计算：

- 最终 Top1
- 最终 Top3
- 拒答率
- 置信度校准
- 可用样本数与排除样本数

OSS 诊断集只报告召回、条件排序和切片指标。历史压力集只报告修复率与新增回归数。

### 6.4 专项指标

- `taxonomy_false_veto_rate`
- `param_false_hard_fail_rate`
- `route_filter_oracle_loss_rate`
- `postprocess_bad_flip_rate`
- `shadow_unique_recall_gain`
- trace 完整率
- candidate contract 字段覆盖率

### 6.5 切片规则

按省份、family、source-family、项目来源、专业、候选来源和参数复杂度切片。低于最小样本数的切片只展示样本数量和原始命中数，不输出结论性百分比。

## 7. 运行元数据与可复现性

每次运行记录：

- 数据集名称、版本、内容摘要
- Provider 名称与配置摘要
- 模型与特征文件路径及摘要
- 索引路径及摘要
- Git revision 与工作树状态
- Python 版本和关键运行时信息
- 开始时间、结束时间、耗时

生产 Provider 与 Goal Provider 分开执行并分别落盘。一个 Provider 失败不得导致另一个 Provider 的已完成结果丢失。

## 8. 失败与分母处理

- 省份数据库缺失：标记 `province_unavailable`，不计入准确率分母。
- 正确定额不在目标省本地库：标记 `oracle_not_in_local_db`，单独报告。
- trace 阶段缺失：标记 `trace_incomplete`；允许计算最终 Top1，但不参与缺失阶段归因。
- Provider 异常或超时：记录错误类型，不作为算法匹配错误计入准确率。
- 输入缺少 oracle：标记 `missing_oracle`，不进入准确率分母。
- 多个合理答案：命中任一 `oracle_quota_ids` 即为正确。

报告必须同时展示原始总数、有效分母和各排除原因数量，禁止静默跳过。

## 9. 测试策略

### 9.1 单元测试

- 生命周期字段归一化
- Recall@K 与 MRR
- Conditional Top1
- good flip、bad flip 和 net gain
- 分母排除规则
- 多 oracle 命中规则
- taxonomy、route 和 hard fail 损失归因

### 9.2 契约测试

ProductionProvider 与 GoalShadowProvider 对同一 fixture 输出相同 ProviderResult schema。缺失字段和 disabled stage 的表示必须稳定。

### 9.3 回归测试

固定小样本的 `summary.json` 和关键 CSV 行必须稳定，字段顺序和浮点舍入规则固定。

### 9.4 集成测试

使用现有 synthetic fixture 验证完整评测流程，不要求完整省份数据库。真实数据运行作为独立的本地验证步骤，不进入默认快速测试。

## 10. 第一阶段范围

第一阶段包含：

- 统一评测数据契约
- ProductionProvider
- GoalShadowProvider
- 生命周期归一化
- 基础指标与阶段归因
- JSON/JSONL/CSV 报告
- 对应单元测试、契约测试和小型集成测试

第一阶段不包含：

- 修改在线召回或排序
- 接入 Goal 候选到生产结果
- 训练或启用 LTR
- taxonomy 软化实现
- 数据库持久化实验平台
- Web UI
- 微服务或独立进程部署

## 11. 验收标准

第一阶段验收重点是测量完整性，不要求准确率提升：

- 同一案例可明确回答正确候选是否进入候选池。
- 若正确候选丢失，可定位到具体生命周期阶段和原因。
- 若最终选错，可识别首次选错阶段以及后续 good/bad flip。
- 可比较 Production、Goal Shadow 和候选并集的召回差异。
- 主指标集、OSS 诊断集和历史压力集的指标严格隔离。
- 缺省数据库、Provider 异常和 trace 缺失不会静默污染分母。
- 固定 fixture 的结果可重复，报告包含完整运行元数据。

## 12. 后续演进

基线稳定后，按以下顺序开展算法实验：

1. 比较 Goal Shadow 候选并集带来的 Recall@K 增量。
2. 量化路由过滤、taxonomy hard conflict 和参数 hard fail 的 oracle 损失。
3. 统一生产与训练的候选特征契约。
4. 训练并 Shadow 验证统一 ranker。
5. 依据阶段 gain/loss 精简后处理规则。
6. 通过真实主指标集和 OSS source-family/project split 验证后，再讨论生产接入。
