# 全专业算法修复小步验收计划 v36

本文档是后续全专业算法修复的固定执行契约。以后需要修复时，不再重新讨论方案是否完善，直接从 Step 0 开始小步执行。

## 后续启动语

以后你想让我执行这个方案，可以直接说：

```text
按 docs/global_repair_small_step_plan_v36.md 执行全专业算法修复小步验收流程。从 Step 0 开始，只做当前步骤，完成后按文档的固定汇报格式汇报，不要扩大范围。
```

如果你想继续上一次已经完成的步骤，可以说：

```text
按 docs/global_repair_small_step_plan_v36.md 继续执行。以上一次产物为输入，只做下一步，完成后按固定汇报格式汇报。
```

如果你已经有 OpenClaw、audit、benchmark 结果，也可以说：

```text
按 docs/global_repair_small_step_plan_v36.md 执行。优先使用我提供的 latest/attribution/audit 输入；如果不符合全专业输入标准，先停止并说明原因。
```

## 总原则

- 主线固定为全专业优先，不用浙江-only、单专题、smoke 替代全专业判断。
- 每一步只交付一个最小结果；验收失败、超时、口径不清，就停止汇报。
- Step 0 到 Step 3 只做诊断工具和产物，不进入算法修复。
- 不回滚、不整理无关脏工作区；只改目标工具、必要测试和本轮产物。
- 所有输出必须记录输入路径、生成时间、schema_version。
- 全量 benchmark 是 1-2 小时级长任务，不作为普通修复回合的阻塞步骤；只有用户明确要求、已有外部长任务产物、或累计到批量验收点时才执行。
- 短切片 benchmark 只能作为 Step 4 的局部验收，不能替代 full/global 输入，也不能刷新默认运行时知识库产物。
- 除非本轮目标就是重建知识库，否则不得提交 `data/province_plugins/generated/knowledge*.json` 或 `knowledge_digest.md` 的短切片覆盖结果。
- 每个 Step 4 小修复通过局部验收后，状态记为 `pending_full_validation`；允许继续下一轮小修复，但最终合并/发布前必须通过 full/global 验收。
- 同一轮只修一个点；“继续下一轮”必须重新从 Step 0 使用同一份或更新后的 full/global 输入重新生成 next_action，不沿用上轮 action 直接叠加。
- 禁止按单个定额、单个样本打补丁。Step 2/Step 3 必须先找 `common_issue_clusters`，Step 4 只能修一个共性根因；如果只剩单例簇，只能补诊断、跑冒烟验证或等待新的 full/global 聚类输入，不得把单例当成算法修复验收依据。
- 禁止把“继续添加同义词/别名”作为默认修复策略。只有当共性簇证明根因是稳定的词汇归一化缺口时，才允许新增受控词汇规则；新增规则必须有适用范围、反例边界和同簇多样本回归验证。优先修复分词、归一化、特征、候选生成或排序机制中的共性缺陷。
- 代码形态优先级固定为：复用现有机制 > 小型通用函数 > 表驱动规则 > 受控配置 > 局部分支。能用少量通用代码表达一类问题时，不写长 if 链、不堆注释说明、不复制相似规则。

## V36 完整闭环定义

V36 不再只是算法修复步骤，而是一套工程控制面。完整闭环固定为 13 个模块：

1. 统一入口工具：`tools/v36_gate.py`。
2. 基线冻结：记录 commit、命令、数据集、配置、准确率、耗时和失败分布。
3. P0 自动闸门：检查仓库产物、大文件膨胀、generated knowledge 污染、乱码、secret、SSL bypass 和静默异常。
4. 纯搜索诊断：产出召回、排序、validator、路由过滤、final pick 和阶段耗时指标。
5. 硬阈值：准确率、召回率、速度、复杂度必须和冻结基线可比较。
6. 数据质量隔离：答案错、题库歧义、省份/专业错进入 `review_data`，不强修算法。
7. 共性问题选择：只从 `common_issue_clusters` 选择一个最大共性根因。
8. 最小修复协议：一轮只修一个机制，不做单题补丁。
9. `pending_full_validation` 台账：局部通过但未 full/global 验证的修复必须登记。
10. full/global 发布门禁：未验收通过不得发布或刷新正式知识。
11. 巨型文件 owner 边界：新逻辑进入小模块，不继续塞入巨型文件。
12. 灰度/回滚机制：每个算法修复必须能单独关闭、撤销或定位。
13. 发布后监控：跟踪 top1、人工改派率、validator 否决率、P95/P99、fallback 和异常率。

缺少任一模块时，V36 只能进入诊断或治理补齐，不进入大范围算法修复。

## P0 治理闸门

V36 从本版开始先止血，再修算法。任何 Step 4 修复前必须通过 P0 轻量检查；未通过时，本轮唯一合法动作是补闸门、补诊断或停止汇报，不进入算法补丁。

### P0-1 仓库产物闸门

目标：防止仓库越来越脏。

- `reports/attribution/**`、`reports/agent_state/**`、`output/**`、`models/**`、临时训练 CSV、`.pid`、`.stdout.log`、`.stderr.log`、`diff_code.txt` 默认视为本地产物。
- 需要长期保留的 benchmark 基线必须进入固定目录，例如 `eval/baselines/`；不能散落在 `reports/`。
- Step 0 必须汇报 `git_status_summary`：只说明是否存在大量未跟踪产物、是否影响本轮，不清理无关文件。
- 本轮不得提交无关生成物；短切片 benchmark 产物不得进入正式知识文件。

通过标准：`git status` 中本轮新增/修改项能解释为代码、测试、文档或明确的验收产物。

### P0-2 大文件和膨胀闸门

目标：防止代码越写越大。

- `src/ltr_ranker.py` 禁止继续新增新的 `_apply_xxx_rescue` 手写链。
- `src/query_builder.py`、`src/param_validator.py`、`src/match_engine.py`、`web/backend/app/api/openclaw.py`、`web/backend/app/api/material_price.py` 禁止继续塞大段业务分支。
- 单文件超过 1500 行时，新增功能默认应放入小模块、规则注册表、shared primitive 或 service/domain 层。
- 新修复必须说明为什么不是继续堆 if、堆同义词、堆 rescue。

通过标准：本轮 patch 不新增巨型分支，不扩大 API 层或 ranking 层的职责。

### P0-3 测试分层闸门

目标：防止没完没了地跑 full/global。

- `smoke`：5 分钟内，提交前优先。
- `targeted`：只覆盖本轮改动路径，目标 15 分钟内。
- `slice benchmark`：只验证 `next_action.suggested_validation_scope`，必须加 `--no-materialize-learning`。
- `full/global benchmark`：1-2 小时长任务，只在 Step 5、用户明确要求、发布前或无合格 full/global 输入时运行。

通过标准：每轮汇报必须写明 `test_tier` 和“为什么这些测试足够、为什么没有跑 full/global”。

### P0-4 架构边界闸门

目标：防止 legacy、facade、unified skeleton、shadow 路径继续混成一团。

- `match_engine` 只能编排，不继续承载匹配细节。
- `match_pipeline` facade 不得成为新私有函数的跨模块调用入口。
- `unified_*` 要么 shadow-only，要么成为正式 owner；不得把 skeleton 逻辑当作生产决策依据。
- Web API 层不得继续承载核心算法、学习、解析、导出的大段业务分支；新增业务应进入 service/domain/repository。

通过标准：本轮改动必须说明影响的层级，且只动一个层级。

### P0-5 运行时状态闸门

目标：防止多用户/并发任务互相污染。

- 禁止任务运行中直接修改全局 `config.AGENT_LLM`、`VERIFY_LLM`、API key、模型名等。
- 新代码必须优先使用显式 `RuntimeContext` / `MatchSettings` / 函数参数传递任务级配置。
- cache key 必须包含 province、settings、model profile 等会影响结果的维度。

通过标准：本轮不得新增全局可变配置写入；如发现旧路径污染本轮，先修隔离或停止。

### P0-6 知识和 benchmark 产物闸门

目标：防止评测副产物污染运行时默认知识。

- `output/benchmark_assets` 是临时产物。
- `data/province_plugins/generated/knowledge*.json` 和 `knowledge_digest.md` 是发布产物。
- benchmark 默认不刷新正式知识；只有 Step 5 且结果可接受时，才允许提交 generated knowledge。
- 干净部署不能依赖本地 `output/benchmark_assets`。

通过标准：短切片不改 generated knowledge；full/global 刷新知识必须记录 source、digest、record_count。

### P0-7 编码和安全闸门

目标：防止乱码和密钥风险继续扩散。

- 新增代码、日志、用户可见文本不得出现明显 mojibake，例如 `锛`、`鈥`、`鍖`、`绠`。
- 禁止硬编码密码、API key、token、生产 URL 登录凭据。
- 禁止在生产路径关闭 SSL 校验。
- `shell=True`、`pickle.load`、外部文件执行类逻辑只能留在明确的 non-production tools 中，不能进入核心服务路径。

通过标准：本轮改动不新增 secret、mojibake、生产路径 `shell=True` 或 SSL bypass。

### P0-8 数据迁移和可观测性闸门

目标：防止失败被吞掉、schema 演进散落。

- 禁止继续把生产 schema 迁移追加到运行时 init 的长 SQL 列表里；新迁移应进入统一 migration 路径。
- 核心链路不得新增静默 `except Exception: pass`。
- match/ranking/knowledge/write-back 失败必须有结构化 error code、trace 或 warning。

通过标准：本轮不新增静默失败；若修复依赖数据库字段，必须说明迁移路径。

### P0-9 准确率、速度、复杂度闸门

目标：防止只提高一个样本，却牺牲全局准确率、运行速度和代码可维护性。

- 每个算法修复必须同时回答三件事：准确率预期提升在哪里，速度影响在哪里，复杂度是否下降或至少不增加。
- 优先修召回、路由、归一化、特征、约束、排序协议这类共性机制；不优先修单个定额文本。
- 不允许为了一个小簇引入全局高成本扫描、重复向量检索、重复 LLM 调用或无缓存数据库循环。
- 候选池、rerank、validator、knowledge prior 的执行顺序必须保持“先便宜后昂贵”：轻量规则和结构化特征在前，模型和外部服务在后。
- 新增逻辑必须有明确适用范围和退出条件；不能让所有清单都多走一条昂贵路径。
- 如果修复让核心路径变慢，必须说明补偿手段：缓存、短路、限流、top-k 限制、懒加载或只在目标簇触发。

通过标准：本轮汇报包含 `accuracy_impact`、`speed_impact`、`complexity_impact`；其中任一项说不清楚，不进入 Step 4。

### P0-10 纯搜索模式诊断闸门

目标：把纯搜索当成独立产品路径治理，而不是“去掉 LLM 的 agent fallback”。纯搜索准确率低于 40% 或速度不可接受时，先量化链路，再修瓶颈。

- 纯搜索修复前必须拆开四类问题：正确答案没有进候选池、进了候选池但排序低、排序正确但被 validator/guard 否决、路由/book/scope 过滤把正确候选过滤掉。
- 必须输出 `pure_search_metrics`，至少包含：
  - `recall_at_k`：正确定额是否进入 raw candidate topK。
  - `rank_at_k`：正确定额在 rerank 前后的位置。
  - `validator_veto_rate`：正确候选被参数校验、安装方式、单位、工法 guard 否决的比例。
  - `route_filter_loss`：正确候选因专业、book、scope、aux province 过滤丢失的比例。
  - `prior_candidates_delta`：开启/关闭 candidate prior、knowledge prior、同文件先验后的召回和耗时变化。
  - `latency_breakdown_ms`：search、vector encode/search、BM25、KB hint、prior lookup、rerank、validator、final pick 的耗时。
- 速度预算必须和准确率一起看：不得为了提升局部 recall 全局提高 top_k、重复向量检索、扩大 aux 搜索、全量 KB lookup 或增加无缓存循环。
- 优先修便宜且共性的结构问题：query 归一化、route/book 判定、候选池合并顺序、特征权重、validator 误杀、缓存 key、短路条件。
- 不把新增同义词、别名、route hint 当默认动作；只有 `pure_search_metrics` 证明是稳定词汇归一化缺口时才允许，并且必须有反例边界。
- 如果瓶颈是速度，优先处理重复初始化、重复 embedding、重复 DB/KB 查找、无效 rerank 输入、fast/standard/deep 分流错误，而不是直接减少验证步骤导致准确率失控。
- 纯搜索当前准确率低于 40% 时，Step 4 默认动作应是 `improve_diagnostics` 或修一个经指标证明的最大瓶颈；不得继续按单个错题打补丁。

通过标准：纯搜索相关回合必须汇报 `pure_search_metrics`、`latency_budget`、`bottleneck_classification`、`accuracy_impact`、`speed_impact`、`complexity_impact`。缺任一项，不进入算法修复。

### P0-11 基线、阈值和发布门禁闸门

目标：保证每次“提升”可比较、可回滚、可发布。

- 修复前必须有冻结基线，记录：
  - `commit`
  - benchmark 命令和参数
  - dataset/profile/province/scope
  - `scoring_mode`
  - 是否启用经验库
  - 是否 `--no-materialize-learning`
  - 关键配置和环境变量快照
  - top1、recall@K、失败分布、P50/P95/P99 耗时
- 没有冻结基线时，不得声称准确率或速度提升；下一步只能 `freeze-baseline` 或补诊断。
- 阈值必须相对冻结基线判断：top1 不得下降，recall@20 不得下降，P95 不得无解释超过基线 110%，复杂度不得进入巨型文件或新增 rescue 链。
- 如果诊断显示主要问题是 expected 答案错、题库歧义、省份/专业错、样本重复或数据缺字段，必须走 `review_data`，不得用算法补丁掩盖数据问题。
- `pending_full_validation` 未清空或未通过 release check 时，不得发布，不得提交 refreshed generated knowledge。
- full/global 失败时，停止继续修复，先定位回归来源；无法定位时回滚本批最小可疑修复或关闭对应开关。

通过标准：本轮汇报包含 `baseline_snapshot`、`threshold_check`、`release_gate_status`；发布相关回合必须说明 `pending_full_validation` 是否清空。

## 统一入口工具契约

V36 的长期入口固定为 `tools/v36_gate.py`。后续可以分阶段实现，但文档、报告和自动化必须围绕同一个入口收敛。

推荐子命令：

```powershell
python tools/v36_gate.py preflight
python tools/v36_gate.py freeze-baseline
python tools/v36_gate.py diagnose-pure-search
python tools/v36_gate.py choose-next-action
python tools/v36_gate.py register-validation
python tools/v36_gate.py release-check
```

职责边界：
- `preflight`：执行 P0 自动闸门，10 秒内完成，不跑 benchmark。
- `freeze-baseline`：保存可比较基线和配置快照，不修改算法。
- `diagnose-pure-search`：生成 `reports/attribution/pure_search_diagnosis.json`，只做诊断。
- `choose-next-action`：根据 full/global 或纯搜索诊断选择唯一下一步。
- `register-validation`：把局部通过的修复写入 `reports/agent_state/v36_pending_full_validation.json`。
- `release-check`：检查 full/global、pending 台账、generated knowledge 来源和发布门禁。

入口工具不得承载算法业务逻辑；它只调度检查、诊断、登记和门禁。

## Owner 边界和模块归属

新增逻辑默认按以下 owner 放置，不继续扩大巨型文件：

- 搜索诊断：`src/search_diagnostics/`
- 路由、book、scope、aux province：`src/search_routing/`
- 查询归一化和搜索特征：`src/search_features/`
- 校验规则和 validator 软硬约束：`src/validation_rules/`
- ranking guard 和表驱动排序规则：`src/ranking_rules/`
- benchmark 闸门和阈值判断：`src/benchmark_gates/`
- Web API：只做请求编排、鉴权、序列化和 service 调用，不承载算法决策。

如果本轮必须触碰 `ltr_ranker.py`、`query_builder.py`、`param_validator.py`、`match_engine.py`、`openclaw.py` 或 `material_price.py`，必须说明为什么不能放到对应 owner 模块，并控制为最小桥接改动。

## P0 执行顺序

当仓库很脏或架构风险已经影响修复效率时，执行顺序固定为：

1. 先补或运行 `tools/v36_gate.py preflight`，保证本轮不会继续污染工作区。
2. 冻结可比较基线，明确 benchmark 命令、配置快照、准确率和速度。
3. 加测试分层口径，明确 smoke、targeted、slice benchmark、full/global 的边界。
4. 加大文件/新增 rescue/新增乱码/新增 secret/generated knowledge 污染的轻量检查。
5. 加最小 import-boundary 或 architecture check，先守住最危险的依赖方向。
6. 对纯搜索问题先生成 `pure_search_diagnosis.json`，再选择唯一瓶颈。
7. 加准确率/速度/复杂度阈值判断，避免为了命中局部样本拖慢全局主链路。
8. 再回到 Step 0，用合格 full/global 输入或合格纯搜索诊断生成下一轮动作。

P0 只负责“停止变脏、变大、变慢、变不准”。不要在 P0 阶段顺手重构主算法。

## 双线执行模型

V36 固定拆成两条线：

- 快速修复线：Step 0 到 Step 4。使用最近一次合格 full/global 输入做诊断，做一个最小修复，跑单测和目标切片 benchmark。单轮目标是几分钟到几十分钟内闭环。
- 全量验收线：Step 5。full/global benchmark 作为独立长任务，定期或用户明确要求时运行。它刷新下一轮 Step 0 输入，并把多个 `pending_full_validation` 修复统一判定为通过或回退诊断。

执行规则：
- 如果没有新的 full/global 结果，但存在旧的合格 full/global 输入，可以继续快速修复线；汇报中必须写明 `full_validation_status=pending`。
- 如果没有任何合格 full/global 输入，Step 0 停止，不能用短切片、浙江-only、单专题、smoke 推导全局 next_action。
- 如果 full/global 验收出现回归，停止继续小修复，先回到 Step 2/Step 3 重新定位最大错误桶或回滚本批中导致回归的最小点。

## Step 0：确认输入

目标：先确认 P0 闸门状态，再找可用的全专业 benchmark 输入；不跑长任务，不改代码。

动作：
- 先做 P0 轻量 preflight，不修复、不清理无关工作区，只汇报状态：
  - `git_status_summary`：是否存在大量未跟踪产物、是否影响本轮。
  - `dirty_artifact_risk`：是否出现 reports/output/models/temp/训练数据等本地产物准备被误提交。
  - `giant_file_touch_risk`：本轮是否可能触碰巨型核心文件。
  - `secret_or_mojibake_risk`：是否发现本轮相关路径存在硬编码密钥、SSL bypass 或新增乱码风险。
  - `test_tier_plan`：本轮如果进入 Step 4，预计使用 smoke、targeted、slice benchmark 还是 Step 5 full/global。
  - `pure_search_risk`：如果本轮涉及纯搜索，说明是否已有 `pure_search_metrics`；若纯搜索准确率低于 40% 且没有链路拆分指标，默认 `p0_gate_status=block`，下一步只能补诊断。
  - `baseline_snapshot`：是否已有可比较基线；没有则不得进入准确率/速度修复。
  - `pending_full_validation_summary`：是否存在未 full/global 验证的局部修复；如存在，说明是否影响本轮。
- 检查 `reports/attribution` 现有 latest/summary/attribution。
- 优先选择最新全专业产物。
- 全专业判定必须满足：
  - 非 `zhejiang_only`、非单专题、非 smoke。
  - 文件名或 metadata 显示 `global` / `full`。
  - 样本覆盖多专业或多省，不能只有单一切片。
- 输出实际采用的 `latest_path`、`attribution_path`、可选 `summary_path`。
- 输出 `p0_gate_status`：`pass`、`warn` 或 `block`。
- 如果没有新的 full/global 产物，但已有旧的合格 full/global 输入，则继续使用旧输入，并输出 `input_freshness=stale`、`full_validation_status=pending`。
- 如果没有任何合格 full/global 输入，则停止在 Step 0，输出“没有可用输入”；不要用短切片继续生成全局 next_action。

验收：
- `p0_gate_status` 已输出；如为 `block`，本轮不进入算法修复。
- `baseline_snapshot` 已输出；如缺失，下一步优先冻结基线或补诊断。
- 找到可用输入路径，或明确“没有可用输入”。
- 时间上限 5 分钟。
- 不改代码。

失败退出：
- P0 preflight 为 `block` 时停止；下一步只能是修 P0 闸门或整理输入。
- 没有可用输入时停止；下一步只能是准备或启动 Step 5 full/global benchmark。

## Step 1：生成最小 CSV

目标：先看到全专业错误样本，不做复杂决策。

新增或扩展：
- `tools/build_global_repair_decision.py`

输入：
- 必须使用 Step 0 选出的实际路径。
- 不再假设固定文件名一定存在。

输出：
- `reports/attribution/global_repair_decision_table.csv`

CSV 基础字段固定为 10 个，顺序不得改变：
- `sample_id`
- `province`
- `error_stage`
- `attribution_category`
- `expected_ids`
- `selected_id`
- `recall_rank`
- `pre_ltr_top1_id`
- `post_ltr_top1_id`
- `post_final_top1_id`

CSV 还必须追加共性聚类字段，用于把失败从“单样本”提升到“共性问题簇”：
- `bill_name`
- `bill_text`
- `expected_names`
- `selected_name`
- `specialty`
- `match_source`
- `expected_prefixes`
- `selected_prefix`
- `common_issue_key`

错误样本定义：
- 优先使用 latest/attribution 中已有 `passed=false`、`is_correct=false`、`correct=false`、`status=wrong/failed`。
- 若没有显式字段，则用 `selected_id not in expected_ids` 判断。
- 无法判断的样本不进入错误 CSV，但计入诊断缺字段统计。

验收命令示例：

```powershell
python tools/build_global_repair_decision.py `
  --latest <Step0_latest_path> `
  --attribution <Step0_attribution_path> `
  --decision-table reports/attribution/global_repair_decision_table.csv
```

通过标准：
- CSV 存在。
- 表头完整且顺序固定。
- 至少 1 行错误样本。
- 工具内置校验通过。
- 时间上限 30 分钟。

失败退出：
- latest JSON 结构不兼容：停止，汇报需要适配的字段。
- CSV 为空：停止，确认输入是否真是 benchmark latest。

## Step 2：生成 summary

目标：知道最大错误桶和诊断字段是否足够。

新增输出：
- `reports/attribution/global_repair_decision_summary.json`

内容固定包含：
- `schema_version`
- `generated_at`
- `input_latest_path`
- `input_attribution_path`
- `wrong_total`
- `stage_counts`
- `missing_field_rate`
- `largest_bucket`
- `common_issue_clusters`
- `target_common_issue`
- `cluster_selection_reason`

`common_issue_clusters` 定义：
- 同一个簇必须代表同一类可解释失败，不是简单把同一 R 桶内样本排在一起。
- 聚类 key 至少使用 R 桶、归因类别、专业/册、候选来源、`selected_prefix -> expected_prefixes` 转换。
- 每个簇输出 `cluster_id`、`bucket`、`issue_key`、`sample_count`、`sample_ratio`、`commonality`、`representative_sample_ids`、`expected_id_examples`、`selected_id_examples`、`shared_signals`、`recommended_action`。
- `commonality=shared` 表示同簇至少 2 个失败样本；`commonality=singleton_only` 表示当前输入只支持单例诊断，不支持直接算法修复。
- `target_common_issue` 优先选择最大 shared 簇；若没有 shared 簇，才从 `largest_bucket` 中选择单例簇，并在下一步标记为单例诊断。

`missing_field_rate` 定义：
- 分母：`wrong_total`
- 分子：关键诊断字段不完整的错误样本数。
- 关键字段：
  - `error_stage`
  - `attribution_category`
  - `expected_ids`
  - `selected_id`
  - `recall_rank`
  - `pre_ltr_top1_id`
  - `post_ltr_top1_id`
  - `post_final_top1_id`

R 桶映射：
- R1：recall / candidate / retrieval / missing_candidate / recall_miss
- R2：ltr / rank / rerank / pre_ltr / post_ltr
- R3：cgr / guard / constraint / reasoning_guard
- R4：picker / category_safe / family_picker / final_pick
- R5：validator / final_validator / experience
- R6：data / label / expected / ambiguous / unknown / unclassified

冲突规则：
- `error_stage` 优先于 `attribution_category`。
- 两者都缺失或无法识别：归 R6。
- `missing_field_rate > 10%` 时，后续 action 强制为 `improve_diagnostics`。

验收：
- summary 存在。
- `wrong_total > 0`。
- `largest_bucket` 非空。
- `missing_field_rate` 可见。
- `common_issue_clusters` 非空。
- `target_common_issue` 可见，且能解释为什么不是按单个定额选择。
- 时间上限 20 分钟。

失败退出：
- `largest_bucket` 无法判断：停止修 summary 口径。
- `missing_field_rate > 10%`：下一步只能补诊断字段，不能修算法。

## Step 3：生成唯一 next_action

目标：工具只给一个下一步，不并行发散。

新增输出：
- `reports/attribution/global_repair_next_action.json`

合法 action 仅允许：
- `improve_diagnostics`
- `fix_r1_recall`
- `fix_r2_ltr`
- `fix_r3_cgr`
- `fix_r4_picker`
- `fix_r5_validator`
- `review_data`

决策规则：
- `missing_field_rate > 10%`：`improve_diagnostics`
- 否则先看 `target_common_issue.bucket`，不直接按桶内第一个样本选动作。
- `target_common_issue.bucket=R1`：`fix_r1_recall`
- `target_common_issue.bucket=R2`：`fix_r2_ltr`
- `target_common_issue.bucket=R3`：`fix_r3_cgr`
- `target_common_issue.bucket=R4`：`fix_r4_picker`
- `target_common_issue.bucket=R5`：`fix_r5_validator`
- `target_common_issue.bucket=R6` 或 unknown：`review_data`
- 若 `target_common_issue.commonality=singleton_only`，`next_action` 可以给出归属动作，但 Step 4 不得只凭该单例进入算法修复；必须先补诊断或等待 full/global 重新聚类。

next_action 必须包含：
- `schema_version`
- `generated_at`
- `action`
- `reason`
- `largest_bucket`
- `sample_count`
- `target_common_issue`
- `cluster_sample_ids`
- `representative_sample_ids`
- `suggested_validation_scope`
- `input_latest_path`
- `input_attribution_path`
- `full_validation_status`

验收：
- JSON 存在。
- 只有一个 `action`。
- action 合法。
- action 与 `target_common_issue.bucket` 一致，除非 `missing_field_rate > 10%`。
- `suggested_validation_scope` 包含 `filter_cluster_id` 或 `filter_common_issue_key`。
- 时间上限 20 分钟。

失败退出：
- action 不唯一：停止。
- action 与 `target_common_issue` 冲突：停止。

## Step 4：按 action 做一个最小修复

目标：一次只修一个点，当天可验收。

前置硬约束：
- `p0_gate_status=block` 时不得进入 Step 4；必须先处理 P0 闸门。
- 必须写明 `target_common_issue.cluster_id`、`issue_key`、`commonality`、共性根因假设和影响代码路径。
- `commonality=shared` 时，修复必须同时验证同簇 2-5 个样本；只改善代表样本、不改善同簇样本的改动视为过拟合，不能通过。
- `commonality=singleton_only` 时，不能按该单个定额打算法补丁；允许动作只有补诊断字段、补聚类特征、跑最小冒烟或等待 Step 5/full-global 生成更多同类样本。
- 单样本 benchmark 只能作为 smoke，不能作为 Step 4 接受标准。
- 若候选修复是新增同义词、别名、关键词或 route hint，必须先回答三个问题：这是跨样本稳定概念缺口，还是单个清单/定额的文本巧合；是否会扩大到错误专业、错误工法或错误计量单位；是否存在更上游的通用修复点。回答不清楚时，不得提交词表堆叠。
- 代码必须简洁有效：优先用一个谓词、一个归一化函数、一个 scoring feature 或一张小表覆盖共性簇；如果需要为每个样本新增分支、同义词、特殊 case 或大段解释，默认判定为设计失败，回到根因分析。
- 不得新增 `_apply_xxx_rescue` 手写链；如必须处理 ranking 保护逻辑，优先走规则注册表、表驱动 guard 或通用 feature。
- 不得把业务逻辑继续塞进巨型 API 文件；Web 请求处理、domain 决策、repository 写入必须分层。
- 不得新增全局 `config` 写入、静默 `except Exception: pass`、硬编码 secret、用户可见乱码或生产路径 SSL bypass。
- 如果本轮目标是纯搜索准确率或速度，必须先给出 `pure_search_metrics` 和 `bottleneck_classification`；没有证明瓶颈前，不得直接调大 top_k、扩大搜索范围、添加同义词或删除 validator。
- 必须有冻结基线和阈值判断；没有 `baseline_snapshot` 时不得声明提升。
- 修复必须能单独定位和回滚：优先小函数、小表、规则开关或独立模块，不把多个根因揉进一个大 patch。
- 必须预估 `accuracy_impact`、`speed_impact`、`complexity_impact`。如果为了局部准确率引入全局慢路径，默认不通过。

允许范围：
- `improve_diagnostics`：只补诊断字段。
- `fix_r1_recall`：只改 query/router/candidate pool 中一个最小点。
- `fix_r2_ltr`：只改 LTR feature/guard 中一个最小点。
- `fix_r3_cgr`：只改 CGR guard 一个点。
- `fix_r4_picker`：只改 picker/category_safe 一个点。
- `fix_r5_validator`：只改 validator/experience 一个点。
- `review_data`：不改算法，只标记疑似数据问题。

验收顺序：
1. 先跑目标切片或相关单测。
2. 再跑 `next_action.suggested_validation_scope` 指向的最小 benchmark；短切片必须加 `--no-materialize-learning`，避免把局部错误资产写回默认 `data/province_plugins/generated`。
3. 输出 `test_tier`、`changed`、`improved`、`regressed`、代表样本变化，以及未跑 full/global 的理由。
4. 纯搜索相关修复额外输出 `pure_search_metrics`、`latency_budget`、`bottleneck_classification`，并说明修复前后指标变化。
5. 输出 `accuracy_impact`、`speed_impact`、`complexity_impact`。
6. 输出 `threshold_check`：说明 top1、recall@K、P95、复杂度相对冻结基线是否通过。
7. 输出 `rollback_plan`：说明本修复如何单独关闭、撤销或定位。
8. 输出 `p0_gate_after_patch`：确认未新增本地产物污染、巨型分支、secret、mojibake、全局状态写入或静默失败。
9. 修复通过后，把本修复写入 `reports/agent_state/v36_pending_full_validation.json`，状态为 `pending_full_validation`；下一步唯一动作是“回到 Step 0 开始下一轮小修复”或“启动 Step 5 full/global 验收”，二选一，不在当前回合继续叠加第二个算法修复。

失败退出：
- 出现回归：停止，不叠加第二个修复。
- 目标样本没改善：停止，回到诊断。
- 修复跨越多个层级：停止拆小。
- P0 闸门被破坏：停止，先修闸门，不继续算法。
- 准确率、速度或复杂度影响说不清：停止，回到方案压缩。
- threshold check 未通过且没有合理解释：停止，回到诊断或回滚本修复。
- 诊断显示主要是数据质量问题：停止算法修复，转 `review_data`。

## Step 5：异步 full/global 验收

目标：把一批 `pending_full_validation` 小修复放到 full/global benchmark 下统一验收，刷新下一轮 V36 输入。

触发条件：
- 用户明确要求跑 full/global benchmark。
- 累计多个 `pending_full_validation` 修复，需要发布或合并前验收。
- Step 0 没有任何合格 full/global 输入。
- 快速修复线出现目标切片改善但全局风险较高，需要确认是否有回归。
- 发布、提交 generated knowledge 或清空 `pending_full_validation` 台账前。

推荐命令：

```powershell
python tools/run_benchmark.py `
  --mode search `
  --profile full `
  --scoring-mode two_stage `
  --latest-result-out reports/attribution/global_repair_v36_full_latest.json `
  --attribution-json-out reports/attribution/global_repair_v36_full_attribution.json `
  --summary-json-out reports/attribution/global_repair_v36_full_summary.json `
  --asset-out-dir output/benchmark_assets/global_repair_v36_full
```

纯搜索切片诊断命令示例：

```powershell
python tools/run_benchmark.py `
  --mode search `
  --profile dev `
  --scoring-mode two_stage `
  --json-only `
  --no-materialize-learning `
  --latest-result-out reports/attribution/pure_search_v36_dev_latest.json `
  --attribution-json-out reports/attribution/pure_search_v36_dev_attribution.json `
  --summary-json-out reports/attribution/pure_search_v36_dev_summary.json
```

硬约束：
- `run_benchmark.py` 默认会导出 benchmark assets 并物化 learning outputs；短切片必须加 `--no-materialize-learning`。
- full/global 验收可以物化 learning outputs，但只能在确认输入覆盖完整且结果可接受后提交 `data/province_plugins/generated` 变化。
- 如果只是生成 full/global 验收报告、不准备刷新知识库，也应加 `--no-materialize-learning`。
- Step 5 可以长时间运行；如果当前交互不适合等待，允许只给出命令和输出路径，由用户在独立终端运行。

验收：
- `global_repair_v36_full_latest.json` 存在且是 full/global。
- `global_repair_v36_full_attribution.json` 存在。
- `global_repair_v36_full_summary.json` 存在。
- full/global 没有不可接受回归，或回归已明确归因。
- `reports/agent_state/v36_pending_full_validation.json` 中本批修复已逐条判定为 `full_validated`、`rejected` 或 `rollback_required`。
- `release_gate_status=pass` 时，才允许发布或提交 refreshed generated knowledge。

完成后：
- 下一轮 Step 0 优先使用 Step 5 产物。
- 通过后，把本批 `pending_full_validation` 标记为 `full_validated`。
- 未通过时，停止继续修复，回到 Step 2/Step 3 重新生成 next_action。
- 如 full/global 失败且无法明确归因，默认不继续叠加修复，先回滚或关闭本批最小可疑修复。

## 当前执行记录

### 2026-04-29 弱电箱 Step 4 局部修复

- 输入：沿用现有 V36 full/global 诊断输入；full/global 刷新仍为长任务，状态 `pending`。
- 目标样本：北京市建设 2024 `弱电箱`，期望 `C5-2-10 弱电箱体挂墙安装 半周长1m以内`。
- 失败原因：候选已召回，但 `InstallationValidator` 将清单 `暗装` 与定额 `挂墙/挂壁` 判为安装方式硬冲突，导致正确候选 `param_match=False`。
- 修复：仅对 `弱电箱/弱电箱体` 的 `暗装` 清单 vs `挂墙/挂壁/壁挂/悬挂` 定额做软兼容；保留普通安装方式硬冲突。
- 局部验收：`tests/test_installation_validator.py`、`tests/test_param_validator_feature_alignment.py`、`tests/test_hybrid_searcher_prior_candidates.py`、`tests/test_hybrid_searcher_primary_aliases.py`、`tests/test_query_builder_stage3_recall_cleanup.py` 均通过。
- benchmark 验收：`global_repair_v36_weakbox_probe` 命中 1/1，`弱电箱 -> C5-2-10`。
- 产物：`reports/attribution/global_repair_v36_weakbox_probe_latest.json`、`reports/attribution/global_repair_v36_weakbox_probe_attribution.json`、`output/benchmark_assets/v36_weakbox_probe_20260429`。
- 知识库：短切片使用 `--no-materialize-learning`；未修改 `data/province_plugins/generated/knowledge*.json` 或 `knowledge_digest.md`。
- 状态：`pending_full_validation`。
- 备注：这是 V36 共性聚类规则固化前的单点局部修复记录；后续同类修复必须以 `target_common_issue` shared 簇和多样本验收为准。
- 下一步唯一动作：回到 Step 0，使用同一份或更新后的 full/global 输入生成下一轮小修复；或启动 Step 5 full/global 长验收。

## 每轮固定汇报格式

每一步结束只汇报：

- 当前步骤
- 产物路径
- 验收命令
- 验收结果
- 是否通过
- 下一步唯一动作
- full_validation_status
- p0_gate_status
- baseline_snapshot
- test_tier
- accuracy_impact
- speed_impact
- complexity_impact
- threshold_check
- rollback_plan
- release_gate_status
- pending_full_validation_summary
- pure_search_metrics（仅纯搜索相关回合必填）
