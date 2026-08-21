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
- 任一单省、单来源或单项目数据集都只能报告为切片，不能代表整个系统。
- 只有完整覆盖合同通过多省、多来源族、多项目、多专业、split 隔离和占比门禁后，才允许标记为系统基线。
- Provider 可以替换，数据契约与指标口径不能随算法变化。
- taxonomy 默认作为可校准信号；是否改为软特征属于后续算法实验，不在本阶段实施。
- 所有评测默认只读，不连接经验写入、在线任务队列或生产配置更新。

## 3. 数据集设计

### 3.1 人工主指标候选集

人工主指标候选集来自真实人工确认、人工纠正或经过明确审核的清单-定额对，用于计算最终 Top1、Top3、拒答率和置信度校准。数据可信度不等于系统代表性；在覆盖合同通过前，报告必须标记为 `scope=slice`，指标只描述该切片。

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

数据切分必须保证 `project_id` 和全局 query content fingerprint 跨 split 零重叠。省份和 source-family 是分层维度，可以按经批准的覆盖合同跨 split 出现，但必须分别报告重叠数量。query 泄漏同时报告全局内容指纹和 `province + query` 指纹，避免把跨省同文案与同省重复样本混为一谈。

数据规模与系统代表性必须分开审计。`national_index.sqlite`、清单库和 OSS XML 母库可作为候选母库、抽样框或诊断资产，但在缺少独立人工审核标签时不能升级为主指标集。覆盖盘点只报告各证据集的省份、来源族、项目、专业、split 和泄漏情况，不生成跨证据集总分。系统基线还必须提供完整覆盖合同，并包含 `contract_version`、`approval_reference`、`target_surface` 和 `approved_for_system_baseline=true`；技术代码不能代替业务方制定代表性阈值。

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

- `SearchCoreProvider`：调用当前 `evaluate_province_records` 搜索/决策内核；它代表评测进程中的 search core，不自动等价于完整部署系统。
- `ProductionProvider`：仅保留为历史报告兼容别名，执行模式仍记录为 `search_core`。
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

`EvalCase` 表示一个可评测清单，包含输入字段、正确答案集合、数据来源、切分信息和排除原因。`oracle_semantics=any` 表示多个可替代答案，命中任一答案即可；`oracle_semantics=all` 表示组合定额，必须完整输出全部必需定额。多定额样本未显式声明语义时，Loader 以 `ambiguous_oracle_semantics` 拒绝，禁止默认按 OR 计分。

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
- `ranked_quota_ids`
- `confidence`
- `lifecycle`
- `raw_trace`
- `runtime_metadata`
- `errors`

`final_quota_ids` 只表示 Provider 实际最终输出；`ranked_quota_ids` 表示最终候选排序，专供 Final Top3 等排序指标使用。不得用 validated/retrieved 顺序补写最终排序，也不得把候选列表伪装成最终输出集合。

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
- 最终输出准确率
- 必需输出覆盖准确率
- 拒答率
- 置信度校准
- 系统分母、可用样本数、Provider 故障数与各状态数量

Final Top3 只读取 `ranked_quota_ids`。`oracle_semantics=all` 的组合定额不参与 Top1/Top3，但参与最终输出集合指标：严格输出准确率要求最终集合与 oracle 集合相等，必需输出覆盖准确率允许额外输出但要求包含全部 oracle。

OSS 诊断集只报告召回、条件排序和切片指标。历史压力集只报告修复率与新增回归数。

### 6.4 专项指标

- `taxonomy_false_veto_rate`
- `param_false_hard_fail_rate`
- `route_filter_oracle_loss_rate`
- `postprocess_bad_flip_rate`
- `shadow_unique_recall_gain`
- trace 完整率
- candidate contract 字段覆盖率
- `provider_failure_rate`

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

Search Core Provider 与 Goal Provider 分开执行并分别落盘。一个 Provider 失败不得导致另一个 Provider 的已完成结果丢失。Provider 返回缺失 case、重复 case、畸形 payload 或单 case 归一化异常时，必须转换为显式 `provider_error`，不得覆盖、静默跳过或终止整个数据集。

## 8. 失败与分母处理

- 所有通过 Loader 的 case 都进入 `system_denominator`；缺失结果、重复结果、Provider 崩溃、资产不可用或 oracle 不在本地库都按系统未命中计入系统指标，避免通过排除失败样本抬高准确率。
- 省份数据库缺失：仅对明确的资产不可用异常标记 `province_unavailable`；普通 `RuntimeError` 标记 `provider_error`。
- 正确定额不在目标省本地库：标记 `oracle_not_in_local_db` 并单独报告；ALL 语义要求全部 oracle 都存在。
- trace 阶段缺失：标记 `trace_incomplete`；允许计算最终 Top1，但不参与缺失阶段归因。
- Provider 异常、缺失结果和重复结果进入 `provider_failure_count/provider_failure_rate`，同时在系统分母中计为失败。
- 输入缺少 oracle 或多定额语义不明确：由 Loader 拒绝；任何加载拒绝都会强制 `scope=slice`、禁止 headline metrics，并在报告中保留拒绝原因。
- 多定额答案严格按显式 `oracle_semantics` 评分，不允许隐式 OR。

报告必须同时展示原始总数、accepted cases、系统分母、有效结果数、Provider 故障率和各状态数量，禁止静默跳过。

## 9. 测试策略

### 9.1 单元测试

- 生命周期字段归一化
- Recall@K 与 MRR
- Conditional Top1
- good flip、bad flip 和 net gain
- 分母排除规则
- `oracle_semantics=any/all` 规则
- 最终输出集合与最终候选排序隔离
- 缺失、重复、畸形 Provider 结果的系统分母规则
- taxonomy、route 和 hard fail 损失归因

### 9.2 契约测试

SearchCoreProvider、历史兼容 ProductionProvider 与 GoalShadowProvider 对同一 fixture 输出相同 ProviderResult schema。缺失字段和 disabled stage 的表示必须稳定。

### 9.3 回归测试

固定小样本的 `summary.json` 和关键 CSV 行必须稳定，字段顺序和浮点舍入规则固定。

### 9.4 集成测试

使用现有 synthetic fixture 验证完整评测流程，不要求完整省份数据库。真实数据运行作为独立的本地验证步骤，不进入默认快速测试。

## 10. 第一阶段范围

第一阶段包含：

- 统一评测数据契约
- SearchCoreProvider（以及历史兼容 ProductionProvider 别名）
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
- 可比较 Search Core、Goal Shadow 和候选并集的召回差异。
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
