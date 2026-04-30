# 全专业算法修复小步验收计划 v36.3

## 使用说明

日常不要把整篇文档复制进新对话。先用确定性工具生成状态和下一步动作，再让 Codex 只处理被框定的 Step 4。

1. 先跑 P0 preflight：

```powershell
python tools\v36_gate.py preflight --out reports\attribution\v36_preflight.json
```

重点看 `p0_gate_status`、`block_reasons`、`selected_input`、`baseline_snapshot`、`version_tuple_status`、`pending_full_validation_summary`。如果 `p0_gate_status=block`，不要修算法，先做 P0 治理或准备合格输入。

2. 再生成唯一下一步：

```powershell
python tools\v36_gate.py choose-next-action `
  --decision-table reports\attribution\global_repair_decision_table.csv `
  --summary reports\attribution\global_repair_decision_summary.json `
  --next-action reports\attribution\global_repair_next_action.json
```

重点看 `reports/attribution/global_repair_next_action.json` 里的 `action`、`target_common_issue`、`suggested_validation_scope`、`reason`。这个动作由程序确定，LLM agent 不得自行改判。

3. 新对话让 Codex 执行 Step 4 时，直接说：

```text
读取 reports/attribution/global_repair_next_action.json，按 docs/global_repair_small_step_plan_v36.md 只执行 Step 4。只能在 suggested_validation_scope 和 owner_module 限定范围内做一个最小修复。
```

4. 如果只是继续上一轮，说：

```text
按 docs/global_repair_small_step_plan_v36.md 继续执行。以上一轮产物为输入，只做下一步，完成后按固定汇报格式汇报。
```

规则：`preflight` 和 `choose-next-action` 是确定性判定，零 LLM 调用；Codex 只负责 Step 4 中被框定的代码修改和解释原因。`accuracy_impact`、`speed_impact`、`complexity_impact`、`threshold_check`、`partial_validation_status` 等可计算字段必须来自程序产物，不由 Codex 自判。

本文档是后续全专业算法修复的唯一固定执行契约。V36.3 是合并版协议：阈值、题库版本、回归 golden、data review、回滚、执行者行为、trade-off、flaky 和发布门禁必须在同一条链路内判断，不再靠零散补丁解释。以后需要修复时，不再重新讨论方案是否完善，不再另开同类修复计划，直接从 Step 0 开始小步执行。

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
- baseline、threshold、golden、rollback、data review、metric confidence 任一必填字段缺失时，不允许用“影响很小”“显然不会回归”“只是文档变化”绕过流程。

## 唯一主文档和历史内容归并

从 V36.1 起，算法修复执行入口只保留本文档。以下旧文档不再作为独立执行计划使用，只作为历史来源：

- `docs/2026-04-24-算法修复计划.md`
- `docs/2026-04-25-R2-LTR-auto-diagnosis.md`
- `docs/套定额系统诊断与修复计划.md`
- 各 `docs/2026-04-*-R*-*-验收记录.md`

旧文档中的有效内容已经收敛为本文档的固定约束：

1. 4.24 计划中的“每轮只处理一个问题域、必须有验收命令、未过验收不进入下一步、不同时改 LTR/召回/Picker/CGR、每轮结束输出改动/验收/下一步”，归并到本文档的总原则、Step 4 和固定汇报格式。
2. 4.24 的旧基线 `ltr_v2_full_20260422` 只保留为历史基线参考，不再作为 V36 默认验收门槛。V36 默认只接受 Step 0 冻结的当前 full/global 输入和 `tools/v36_gate.py` 产物。
3. 4.25 R2/LTR 自动诊断中的 `in_pool_not_ltr_top1`、`ltr_bad_flip_pre_correct`、`pre_ltr_correct_overturned`、`oracle_missing_from_snapshot`、`oracle_beyond_snapshot_window` 等口径，归并到 V36.1 的候选生命周期追踪和 R1/R2 细分桶。
4. `套定额系统诊断与修复计划` 中的业务链路定义，归并为 V36 的阶段追踪：输入门控、经验库、raw recall、候选合并、参数验证、LTR、family gate、picker、final validator、置信度和输出。
5. 历史验收记录只用于追溯“某一轮改了什么、验收过什么、遗留什么”，不得绕过本文档直接作为下一轮修复入口。

后续如果需要新增规则、诊断口径、执行记录或补充协议，必须直接更新本文档对应章节；不得再创建新的同类“算法修复计划”“下一步计划”“最终执行方案”。主材查价、OpenClaw 接入、知识库治理等非套定额算法主题，可以保留自己的专题文档，但不能替代本文档的套定额算法修复入口。

## V36.3 完整闭环定义

V36.3 不再只是算法修复步骤，而是一套工程控制面。完整闭环固定为 13 个模块：

1. 统一入口工具：`tools/v36_gate.py`。
2. 基线冻结：记录算法版本、题库/知识库版本、命令、数据集、配置、准确率、耗时和失败分布。
3. P0 自动闸门：检查仓库产物、大文件膨胀、generated knowledge 污染、乱码、secret、SSL bypass 和静默异常。
4. 纯搜索诊断：产出召回、排序、validator、路由过滤、final pick 和阶段耗时指标。
5. 硬阈值：准确率、召回率、速度、复杂度必须和冻结基线可比较，且阈值口径必须可机器判定。
6. 数据质量隔离和回流：答案错、题库歧义、省份/专业错进入 `review_data` 队列，修订后从普通错误簇中剔除或单独成桶。
7. 共性问题选择：只从 `common_issue_clusters` 选择一个最大强共性根因；弱共性只补诊断，不进入算法修复。
8. 最小修复协议：一轮只修一个机制，不做单题补丁。
9. 回归 golden set 和 `pending_full_validation` 台账：局部通过但未 full/global 验证的修复必须登记，并同步固化历史回归样本。
10. full/global 发布门禁：未验收通过、题库版本不一致、data review 超阈值或 flaky 未治理时不得发布或刷新正式知识。
11. 巨型文件 owner 边界：新逻辑进入小模块，不继续塞入巨型文件。
12. 灰度/回滚机制：每个算法修复必须能单独关闭、撤销或定位。
13. 发布后监控：跟踪 top1、人工改派率、validator 否决率、P95/P99、fallback 和异常率。

缺少任一模块时，V36 只能进入诊断或治理补齐，不进入大范围算法修复。

模块和 P0 闸门映射固定为：

| 模块 | 对应闸门/章节 | 缺失时默认动作 |
| --- | --- | --- |
| 1 统一入口工具 | 统一入口工具契约 | 只补工具契约或 schema，不修算法 |
| 2 基线冻结 | P0-11、版本快照契约、flaky 契约 | `freeze-baseline` 或补诊断 |
| 3 P0 自动闸门 | P0-1 到 P0-8 | P0 治理回合 |
| 4 纯搜索诊断 | P0-10、候选生命周期追踪 | `improve_diagnostics` |
| 5 硬阈值 | P0-9、P0-11、trade-off 矩阵 | 阈值失败则停止或进入声明的 tradeoff 模式 |
| 6 数据质量隔离 | data review 回流契约、Step 2 R6 规则 | 写入 data review 队列 |
| 7 共性问题选择 | Step 2/Step 3 | 不进入 Step 4 |
| 8 最小修复协议 | Step 4、执行者行为闸门 | 拆轮或停止 |
| 9 回归 golden 和 pending 台账 | 回归 golden set 契约、Step 4 | golden 失败则回退 |
| 10 full/global 发布门禁 | Step 5、release-check | 不发布 |
| 11 owner 边界 | P0-2、P0-4、Owner 边界 | 先做 owner_boundary 治理 |
| 12 灰度/回滚 | 回滚计划契约 | rollback 不合格则停止 |
| 13 发布后监控 | release-check、flaky tracking | 补监控或治理 |

## 判定权边界

V36.3 的核心原则是：能由规则或指标判断的内容必须由确定性程序判断，不交给 LLM agent 自判。LLM 只能在被 `choose-next-action` 框定的修复空间内写代码和解释原因。

确定性程序负责，且不得调用 LLM：
- Step 0：P0 preflight、输入选择、baseline/version tuple 比对。
- Step 1：CSV 生成和字段校验。
- Step 2：cluster 聚类、`largest_bucket`、`missing_field_rate`、`sample_ratio`、data review 剔除。
- Step 3：`next_action` 推导、R 桶映射、owner 选择。
- Step 4 验收：policy check、golden set、targeted/slice 结果解析、threshold check、复杂度量化、`partial_validation_status` 判定。
- 所有 manifest、pending 台账、data review 队列、flaky tracking 和 release gate 登记。

LLM agent 只负责：
- 在 `next_action` 指定的 action、owner 模块、repair_unit 和 rollback 形式内写代码。
- 失败时给出自然语言根因假设、`mechanism`、`failed_slice_next_action.reason` 和人工可读说明。
- 不得填写或覆盖程序可计算字段。

人负责：
- Step 5 full/global 结果异常时的最终归因和回滚策略选择。
- `review_data` 队列中数据问题的最终裁定。
- P0 治理回合的策略选择。
- V36 协议本身是否需要修改。

以下字段只能由程序生成或验证，agent 没有话语权：`accuracy_impact`、`speed_impact`、`complexity_impact`、`complexity_delta`、`metric_confidence`、`threshold_check`、`partial_validation_status`、`regression_golden_status`、`release_gate_status`、`version_tuple_pass`、`flaky_status`。

若 agent 自然语言与程序产物或 git diff 不一致，以程序产物和 diff 为准；该回合必须标记 `agent_claim_mismatch=true`，不得登记 `pending_full_validation`。

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
- 新修复必须天然满足一种合法回滚形式：配置开关、小模块单调用点或独立 commit revert；否则不得进入算法修复。

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
- 新算法逻辑优先进入 owner 小模块；巨型文件只能新增最小桥接调用点，使 `rollback_plan` 可以通过删除或关闭单一调用点完成。

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
- 任何 refreshed generated knowledge 都必须生成新的 `knowledge_digest_hash`，并记录 `quota_db_revision`、`bill_corpus_revision` 和来源 full/global 产物路径。
- 刷新 `data/province_plugins/generated/knowledge*.json` 或 `knowledge_digest.md` 后，所有未关闭的 `pending_full_validation` 必须标记为 `stale_due_to_knowledge_refresh`，不得和新知识一起合并发布；下一步强制 Step 5 重新验收。

通过标准：短切片不改 generated knowledge；full/global 刷新知识必须记录 source、digest、record_count、`knowledge_digest_hash`、`quota_db_revision`、`bill_corpus_revision`，并处理 pending 台账失效。

### P0-7 编码和安全闸门

目标：防止乱码和密钥风险继续扩散。

- 新增代码、日志、用户可见文本不得出现明显 mojibake；示例用 Unicode code point 记录为 `U+951B`、`U+9225`、`U+9346`、`U+7EE0`，不要在正文中直接写入乱码字面量。
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

复杂度量化固定为 `complexity_delta`，至少包含：
- `file_loc_delta`：本轮触碰的每个代码文件新增行数、删除行数和净增行数。
- `branch_delta`：新增 `if`、`elif`、`match`、`case`、循环分支和早退分支数量；测试文件可单独列出但不得混入生产复杂度。
- `public_symbol_delta`：新增公开函数、公开类、公开方法、CLI 子命令、API 路由和配置项数量。
- `rule_entry_delta`：新增规则表项、同义词、别名、route hint、hardcoded pattern 数量。

`complexity_impact` 判定固定为：
- `decrease`：净删除生产分支或公开符号，且没有新增全局规则表膨胀。
- `neutral`：生产净增行数 `<= 80`，新增生产分支 `<= 2`，新增公开符号 `<= 1`，新增规则表项 `<= 5`，且未触碰巨型文件新增业务分支。
- `increase`：任一生产文件净增行数 `> 80`、新增生产分支 `> 2`、新增公开符号 `> 1`、新增规则表项 `> 5`、新增 rescue 链、或向超过 1500 行的巨型文件加入业务分支。

通过标准：本轮汇报包含由程序计算的 `accuracy_impact`、`speed_impact`、`complexity_impact` 和 `complexity_delta`；其中任一项缺失或置信度不足，不进入 Step 4。若 `complexity_impact=increase`，默认不得进入算法修复，除非 `threshold_check` 明确给出可回滚开关、收益和替代方案比较。

trade-off 优先级固定为：
- `tradeoff_mode=none`：默认模式。top1 不得下降，recall@20 不得下降，总耗时 P95 不得超过基线 110%，`complexity_impact` 不得为 `increase`；任一失败即不通过。
- `tradeoff_mode=accuracy_for_latency`：必须显式声明。top1 绝对提升 `>= 1.0%`，总耗时 P95 不得超过基线 130%，且必须提供缓存、短路、限流、top-k 限制或灰度中的至少一项补偿计划；未通过 Step 5 前不得发布。
- `tradeoff_mode=accuracy_for_complexity`：必须显式声明。top1 绝对提升 `>= 2.0%`，复杂度增量必须集中在新 owner 模块，不能污染巨型文件、API 层或 rescue 链；未通过 Step 5 前不得发布。
- trade-off 模式必须写入 `threshold_check.tradeoff_mode` 和 `release_gate_status`；不得用自然语言解释替代。

### P0-10 纯搜索模式诊断闸门

目标：把纯搜索当成独立产品路径治理，而不是“去掉 LLM 的 agent fallback”。纯搜索准确率低于 40% 或速度不可接受时，先量化链路，再修瓶颈。

- 纯搜索修复前必须拆开四类问题：正确答案没有进候选池、进了候选池但排序低、排序正确但被 validator/guard 否决、路由/book/scope 过滤把正确候选过滤掉。
- 必须输出 `pure_search_metrics`，至少包含：
  - `recall_at_k`：正确定额是否进入 raw candidate topK；必须同时输出 `recall@5`、`recall@20`、`recall@100`，Step 4 threshold check 固定使用 `recall@20`。
  - `rank_at_k`：正确定额在 rerank 前后的位置。
  - `validator_veto_rate`：正确候选进入 validator/guard 输入集合后，被参数校验、安装方式、单位、工法 guard 硬否决或移除的比例；分母固定为 `entered_validator_correct_count`，分子固定为 `vetoed_correct_count`。
  - `route_filter_loss`：正确候选在 route/book/scope/aux province 过滤阶段可见后被过滤丢失的比例；分母固定为 `pre_route_filter_correct_count`，分子固定为 `route_filtered_correct_count`。
  - `prior_candidates_delta`：开启/关闭 candidate prior、knowledge prior、同文件先验后的召回和耗时变化。
  - `latency_breakdown_ms`：search、vector encode/search、BM25、KB hint、prior lookup、rerank、validator、final pick 的耗时；每个阶段必须拆 `p50`、`p95`、`p99`，并保留 `sample_count`。
- `validator_veto_rate` 如果 `entered_validator_correct_count=0`，不得输出 0%；必须输出 `null` 并在 `metric_confidence` 中标记 `missing` 或说明正确候选尚未进入 validator。
- `latency_breakdown_ms` 缺少阶段分位时，不得声称速度改善；只能声明总耗时变化或要求补诊断。
- 速度预算必须和准确率一起看：不得为了提升局部 recall 全局提高 top_k、重复向量检索、扩大 aux 搜索、全量 KB lookup 或增加无缓存循环。
- 优先修便宜且共性的结构问题：query 归一化、route/book 判定、候选池合并顺序、特征权重、validator 误杀、缓存 key、短路条件。
- 不把新增同义词、别名、route hint 当默认动作；只有 `pure_search_metrics` 证明是稳定词汇归一化缺口时才允许，并且必须有反例边界。
- 如果瓶颈是速度，优先处理重复初始化、重复 embedding、重复 DB/KB 查找、无效 rerank 输入、fast/standard/deep 分流错误，而不是直接减少验证步骤导致准确率失控。
- 纯搜索当前准确率低于 40% 时，Step 4 默认动作应是 `improve_diagnostics` 或修一个经指标证明的最大瓶颈；不得继续按单个错题打补丁。

通过标准：纯搜索相关回合必须汇报 `pure_search_metrics`、`latency_budget`、`bottleneck_classification`、`accuracy_impact`、`speed_impact`、`complexity_impact`、`complexity_delta`。缺任一项，不进入算法修复。

### P0-11 基线、阈值和发布门禁闸门

目标：保证每次“提升”可比较、可回滚、可发布。

- 修复前必须有冻结基线，记录：
  - `algorithm_commit`
  - `knowledge_digest_hash`
  - `quota_db_revision`
  - `bill_corpus_revision`
  - `vector_index_revision`
  - `embedding_model_version`
  - `model_profile_hash`
  - `seed`
  - benchmark 命令和参数
  - dataset/profile/province/scope
  - `scoring_mode`
  - 是否启用经验库
  - 是否 `--no-materialize-learning`
  - 关键配置和环境变量快照
  - top1、recall@5、recall@20、recall@100、失败分布、总耗时 P50/P95/P99、阶段耗时 P50/P95/P99
- 没有冻结基线时，不得声称准确率或速度提升；下一步只能 `freeze-baseline` 或补诊断。
- 阈值必须相对同一冻结基线判断：`algorithm_commit`、`knowledge_digest_hash`、`quota_db_revision`、`bill_corpus_revision`、`vector_index_revision`、`embedding_model_version`、`model_profile_hash`、`seed` 任一不一致时，不得声明准确率或速度提升，只能重新冻结基线。
- 默认阈值：top1 不得下降，recall@20 不得下降，总耗时 P95 不得无解释超过基线 110%，任一核心阶段 P95 不得无解释超过基线 120%，复杂度不得进入巨型文件、新增 rescue 链或 `complexity_impact=increase`。
- `recall@5` 和 `recall@100` 必须记录用于诊断召回窗口变化，但 Step 4 硬门禁固定使用 `recall@20`。
- `threshold_check` 必须输出机器可判定字段：`top1_pass`、`recall_at_20_pass`、`total_p95_pass`、`stage_p95_pass`、`complexity_pass`、`version_tuple_pass`、`flaky_pass`、`overall_pass`、`baseline_id`、`comparison_command`、`tradeoff_mode`；任一 pass 字段不得用自然语言代替。
- 如果诊断显示主要问题是 expected 答案错、题库歧义、省份/专业错、样本重复或数据缺字段，必须走 `review_data`，不得用算法补丁掩盖数据问题。
- `pending_full_validation` 未清空或未通过 release check 时，不得发布，不得提交 refreshed generated knowledge。
- full/global 失败时，停止继续修复，先定位回归来源；无法定位时回滚本批最小可疑修复或关闭对应开关。

通过标准：本轮汇报包含 `baseline_snapshot`、`threshold_check`、`release_gate_status`；发布相关回合必须说明 `pending_full_validation` 是否清空、题库版本是否一致、data review open 占比是否超阈值。

## 统一入口工具契约

V36 的长期入口固定为 `tools/v36_gate.py`。后续可以分阶段实现，但文档、报告和自动化必须围绕同一个入口收敛。

推荐子命令：

```powershell
python tools/v36_gate.py preflight
python tools/v36_gate.py freeze-baseline
python tools/v36_gate.py diagnose-pure-search
python tools/v36_gate.py choose-next-action
python tools/v36_gate.py validate-step4-manifest --manifest reports/attribution/v36_round_manifest_xxx.json
python tools/v36_gate.py register-validation --manifest reports/attribution/v36_round_manifest_xxx.json
python tools/v36_gate.py release-check
```

职责边界：
- `preflight`：执行 P0 自动闸门，10 秒内完成，不跑 benchmark。
- `freeze-baseline`：保存可比较基线和配置快照，不修改算法。
- `diagnose-pure-search`：生成 `reports/attribution/pure_search_diagnosis.json`，只做诊断。
- `choose-next-action`：根据 full/global 或纯搜索诊断选择唯一下一步。
- `validate-step4-manifest`：读取 round manifest、before/after benchmark 产物、`policy_check_report` 和 `regression_golden_report`，程序复算 `partial_validation_status`、`accuracy_impact`、`speed_impact`、`threshold_check`、`rollback_integrity` 和 `agent_claim_mismatch`；若报告路径存在，以报告文件状态为准，不信任 manifest 手填状态；若声明或派生为 `benchmark_pass`，`rollback_plan` 缺失或不合法时不得登记 pending。
- `register-validation`：只接受带 `--manifest` 的 `benchmark_pass` 修复，把它写入 `reports/agent_state/v36_pending_full_validation.json`，并同步维护 `eval/regression_golden/`；`local_behavior_pass`、`candidate_lifecycle_pass`、`blocked_by_next_stage` 不得登记为 pending。
- `release-check`：检查 full/global、pending 台账、generated knowledge 来源和发布门禁。

入口工具不得承载算法业务逻辑；它只调度检查、诊断、登记和门禁。

`tools/v36_gate.py` 的 6 个子命令必须完全确定性、可复现、零 LLM 调用。输入只能是仓库状态、JSON/CSV/benchmark 产物、git diff 和显式参数；输出只能是结构化 JSON。任何子命令需要“判断语义但没有字段”时，输出 `missing` 或 `requires_human_review`，不得让 LLM 补判断。

子命令最小输入输出 schema 固定如下，后续实现不得漂移：

```json
{
  "preflight": {
    "input": {"scope": "repo|round", "changed_paths": []},
    "output": {"p0_gate_status": "pass|warn|block", "blocking_reasons": [], "git_status_summary": {}}
  },
  "freeze-baseline": {
    "input": {"latest_path": "", "attribution_path": "", "benchmark_command": ""},
    "output": {"baseline_snapshot": {"baseline_id": "", "algorithm_commit": "", "knowledge_digest_hash": "", "quota_db_revision": "", "bill_corpus_revision": "", "vector_index_revision": "", "embedding_model_version": "", "model_profile_hash": "", "seed": ""}}
  },
  "diagnose-pure-search": {
    "input": {"latest_path": "", "attribution_path": "", "filter_cluster_id": ""},
    "output": {"pure_search_metrics": {}, "metric_confidence": {}, "candidate_lifecycle_trace": {}}
  },
  "choose-next-action": {
    "input": {"summary_path": "", "data_review_queue_path": "reports/agent_state/v36_data_review_queue.json", "round_manifest_glob": "reports/attribution/v36_round_manifest_*.json", "pending_path": "reports/agent_state/v36_pending_full_validation.json"},
    "output": {"action": "", "target_common_issue": {}, "reason": "", "full_validation_status": "", "selector_state_inputs": {}, "skipped_repair_units": [], "blocked_next_stage_repair_units": []}
  },
  "validate-step4-manifest": {
    "input": {"manifest_path": "", "before_artifact": "", "after_artifact": "", "policy_check_report": "", "regression_golden_report": "", "candidate_lifecycle_trace": {}},
    "output": {"partial_validation_status": "", "derived_partial_validation_status": "", "agent_claim_mismatch": false, "accuracy_impact": "", "speed_impact": "", "complexity_impact": "", "threshold_check": {}, "rollback_integrity": {}, "register_validation_allowed": false}
  },
  "register-validation": {
    "input": {"manifest_path": "", "required_manifest_fields": {"partial_validation_status": "benchmark_pass", "policy_check_status": "pass", "regression_golden_status": "pass"}, "golden_case": {}, "rollback_plan": {}},
    "output": {"pending_full_validation_entry": {}, "source_manifest": "", "regression_golden_status": "pass|fail", "version_tuple": {}}
  },
  "release-check": {
    "input": {"full_latest_path": "", "pending_path": "reports/agent_state/v36_pending_full_validation.json", "data_review_queue_path": "reports/agent_state/v36_data_review_queue.json"},
    "output": {"release_gate_status": "pass|warn|block", "threshold_check": {}, "data_review_open_rate": 0.0, "flaky_status": "pass|warn|block"}
  }
}
```

配套确定性工具：
- `tools/policy_check.py`：Step 4 patch 静态检查，不调用 LLM。
- `tools/v36_orchestrator.py`：可选半自动编排器，只调度 v36_gate、policy_check、测试、benchmark 和 agent，不拥有验收判定权。

`policy_check` 至少扫描：
- 是否触碰 `ltr_ranker.py`、`query_builder.py`、`param_validator.py`、`match_engine.py`、`openclaw.py`、`material_price.py`，且没有 owner 迁移说明。
- 是否新增 `_apply_xxx_rescue` 命名函数。
- 是否新增同义词、别名、keyword、route hint 列表或硬编码 pattern。
- 是否新增 `except Exception: pass`。
- 是否修改 `data/province_plugins/generated/**` 或 `knowledge_digest.md`。
- 是否新增 secret/token/API key 模式。
- 是否在生产路径关闭 SSL 校验。
- 使用 `--next-action reports/attribution/global_repair_next_action.json` 时，是否修改了 `suggested_validation_scope.owner_module` 之外的 `src/` 或 `web/` 生产代码；越界即 `owner_scope_violation`。
- 复杂度增量：生产 LOC delta、新增 `if/elif/match/case`、新增公开符号、新增规则表项。

`policy_check` 不通过时，当前 patch 直接拒绝，不进入 golden set、targeted benchmark 或 pending 登记。

## 版本快照契约

所有可比较结论都绑定同一个版本元组：

```json
{
  "version_tuple": {
    "algorithm_commit": "",
    "knowledge_digest_hash": "",
    "quota_db_revision": "",
    "bill_corpus_revision": "",
    "vector_index_revision": "",
    "embedding_model_version": "",
    "model_profile_hash": "",
    "seed": "",
    "scoring_mode": "",
    "experience_enabled": false
  }
}
```

规则：
- `baseline_snapshot`、`threshold_check`、`pending_full_validation`、`regression_golden` 和 Step 5 release 产物必须记录同一套 `version_tuple`。
- 任一版本字段变化，历史准确率、速度、recall 和 P95 不再可直接比较；必须重新 `freeze-baseline`。
- generated knowledge 刷新后，旧 pending 只能作为历史记录，不得继续用旧基线发布。
- `knowledge_digest_hash` 缺失时，不能声称知识库未变；`quota_db_revision` 或 `bill_corpus_revision` 缺失时，不能声称题库或清单语料未变。

## 回归 golden set 契约

V36 必须有一层介于单轮切片 benchmark 和 Step 5 full/global 之间的快速历史回归门禁。full/global 运行成本高，当前切片只覆盖本轮 `target_common_issue`；因此所有已经登记过的局部修复都必须进入固定回归集，防止多轮小修复悄悄破坏历史 case。

固定目录：
- `eval/regression_golden/`：保存 V36 修复沉淀的历史回归样本、反例和 manifest。
- `eval/golden_set.jsonl` 是从经验库导出的通用真实样本集，不等同于 V36 修复回归集；两者可以同时运行，但不得互相替代。

每条进入 `pending_full_validation` 的修复必须同步固化一个 `golden_case`，至少包含：
- `case_id`：稳定唯一 id，建议格式 `v36_<date>_<cluster_or_topic>_<index>`。
- `source_repair_id`：对应 `reports/agent_state/v36_pending_full_validation.json` 中的修复 id。
- `target_common_issue`：包含 `cluster_id`、`issue_key`、`bucket`、`commonality`。
- `mechanism` 和 `failing_stage`。
- `positive_samples`：至少 1 个代表样本；若同簇有足够样本，必须再加入 1-2 个同簇正样本。
- `negative_samples`：至少 1 个反例，用来证明修复没有扩大到错误专业、错误工法、错误单位或错误安装方式。
- `expected_behavior`：top1、候选生命周期、validator 状态、主辅项语义或其它本修复真正承诺保持的行为。
- `validation_command`：可在 5 分钟内运行的 targeted/golden 命令；必须带 `--no-materialize-learning` 或等价保护。
- `owner_files`、`created_at`、`status`。

回归 golden 的硬规则：
- Step 4 中 `partial_validation_status=benchmark_pass` 且准备登记 `pending_full_validation` 时，必须先新增或更新对应 `golden_case`。
- 新增同义词、别名、route hint、validator 软化或 ranking guard 的修复，必须有 `negative_samples`；没有反例时不得登记为 `pending_full_validation`。
- golden set 任一历史 case 退化时，本轮状态固定为 `regress`，不得登记 `pending_full_validation`，也不得使用 `local_behavior_pass`、`candidate_lifecycle_pass` 或 `blocked_by_next_stage` 继续保留为“可叠加 patch”。
- `eval/regression_golden/` 的样本不得从短切片 benchmark 自动批量灌入；只能登记本轮代表样本、同簇少量正样本和明确反例，避免把局部资产变成新的隐性题库。

## 回滚计划契约

`rollback_plan` 不是自然语言承诺，必须落到可执行的回滚形式。合法形式只有三类，按优先级选择：

1. `config_flag`：首选。新逻辑挂在 `MatchSettings`、`RuntimeContext` 或等价任务级配置的命名 flag 后，回滚方式是关闭一个明确配置项。禁止通过写全局 `config` 可变状态实现开关。
2. `isolated_module_call`：新逻辑放在 owner 模块的独立函数、类或规则注册表内，生产路径只新增一个清晰调用点。回滚方式是删除或关闭这一处调用点，不需要反向补丁。
3. `git_revert`：兜底。必须给出确切 commit hash、影响文件清单、是否包含生成物、以及 revert 后需要重跑的最小验证命令。

`rollback_plan` 必须包含：
- `rollback_type`：只能是 `config_flag`、`isolated_module_call` 或 `git_revert`。
- `rollback_target`：flag 名、调用点路径或 commit hash。
- `affected_files`：本修复影响文件清单。
- `rollback_command_or_change`：关闭配置、移除调用点或 `git revert <commit>`。
- `post_rollback_validation`：回滚后必须跑的 targeted/golden 验证命令。

`validate-step4-manifest` 必须校验 `rollback_plan`。当本轮声明或由程序派生为 `benchmark_pass` 时，缺少 `rollback_plan`、`rollback_type` 非法、`affected_files` 为空、`rollback_target` 为空、`rollback_command_or_change` 为空或 `post_rollback_validation` 为空，均使 `rollback_integrity.status=fail`，不得进入 `register-validation`。

不合格回滚固定判失败：
- 需要再写一段“反向修复逻辑”才能撤销。
- 需要在多个巨型文件里手工找分支删除。
- 无法指出唯一 flag、唯一调用点或确切 commit。
- 回滚依赖未记录的本地状态、临时产物或人工数据库改写。
- `rollback_type=git_revert` 但本修复混入无关改动、generated knowledge 或多个根因。

这条回滚契约反向约束 P0-2 和 P0-4：如果新逻辑无法挂开关、无法放进 owner 小模块、也无法作为单独 commit 干净 revert，则不得进入 Step 4 算法修复。

## Data Review 回流契约

`review_data` 不是终点，必须进入固定队列并在后续聚类中被正确处理。

固定台账：
- `reports/agent_state/v36_data_review_queue.json`

队列项字段：
- `sample_id`
- `suspected_reason`：`expected_wrong|quota_ambiguous|province_wrong|specialty_wrong|duplicate_sample|missing_field|primary_auxiliary_ambiguous|other`
- `evidence_paths`
- `status`：`open|fixed_in_corpus|wontfix|ambiguous_kept`
- `fix_revision`
- `created_at`
- `updated_at`
- `owner`

规则：
- Step 3 输出 `action=review_data` 时，必须新增或更新队列项；只写“疑似数据问题”不合格。
- Step 2 聚类时，`status != open` 的样本不得参与普通 `wrong_total`、最大共性簇和 `target_common_issue` 选择；必须剔除或单独进入 `R6_known_data_issue`。
- `status=open` 的样本可以进入 R6，但不得用算法补丁解决。
- release gate 必须输出 `data_review_open_rate = open_count / wrong_total_before_data_review_exclusion`。当 `data_review_open_rate > 5%` 时，发布必须附 `data_revision_plan`；当 `data_review_open_rate > 10%` 且没有修订计划时，`release_gate_status=block`。

## 执行者行为闸门

本流程默认执行者可能是 LLM agent，因此必须防止捷径和包装性汇报。

禁止行为：
- 不允许以“影响很小”“显然不会回归”“只是局部变化”为由跳过 `baseline_snapshot`、`threshold_check`、`regression_golden_status`、`rollback_plan` 或 `metric_confidence`。
- 不允许把 `partial_validation_status=local_behavior_pass`、`candidate_lifecycle_pass` 或 `blocked_by_next_stage` 模糊汇报成 `benchmark_pass`。
- 不允许同一回合既改算法又顺手清理无关产物、修无关 typo、调整无关测试；超出 `repair_unit` 的改动必须显式登记并拆到下一轮。
- 不允许把 `metric_confidence=requires_ab_run` 写成 `medium` 或 `high`。
- 不允许用旧 baseline、旧 knowledge digest 或旧 full/global 输入解释新 patch 的收益。
- 不允许 agent 自己填写程序可计算字段；这些字段只能从 `v36_gate.py`、`policy_check.py`、测试、benchmark 或 git diff 产物读取。
- 不允许 agent 在没有程序产物支撑时声明“通过”“无回归”“复杂度不增”“速度无影响”。

违反任一条时，当前回合固定为 `p0_gate_status=block` 或 `partial_validation_status=failed`，不得登记 `pending_full_validation`。

## Flaky 和可重现性契约

benchmark 涉及 LLM、向量检索、数据库和经验库时，必须区分 regression 和 flaky。

规则：
- `baseline_snapshot` 必须锁定 `seed`、`model_profile_hash`、`vector_index_revision`、`embedding_model_version` 和 `experience_enabled`。
- `threshold_check` 如遇边界波动，必须用同一 `version_tuple` 最多重跑 1 次，并输出 `repeat_run_delta`。
- 同配置两次运行 top1 绝对差值 `> 0.3%` 或总耗时 P95 差值 `> 10%` 时，判定为 `flaky`，不直接算 regression，也不得算通过。
- flaky 必须登记到 `reports/agent_state/flaky_tracking.json`，字段包含 `signature`、`version_tuple`、`metric_delta`、`evidence_paths`、`count`、`status`。
- 同一 `signature` 累计 3 次 flaky，下一步强制 P0 治理，目标为 `diagnostic_completeness` 或可重现性修复。

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

1. 先运行 `tools/v36_gate.py preflight`，只判断能不能继续，不在这一步清理或修算法。
2. 如果 P0 为 `block`，先进入 P0 治理回合；治理回合只处理一个阻断原因，不叠加算法修复。
3. 冻结或确认可比较基线，明确 benchmark 命令、配置快照、准确率和速度。
4. 加测试分层口径，明确 smoke、targeted、slice benchmark、full/global 的边界。
5. 加大文件/新增 rescue/新增乱码/新增 secret/generated knowledge 污染的轻量检查。
6. 加最小 import-boundary 或 architecture check，先守住最危险的依赖方向。
7. 对纯搜索问题先生成 `pure_search_diagnosis.json`，再选择唯一瓶颈。
8. 加准确率/速度/复杂度阈值判断，避免为了命中局部样本拖慢全局主链路。
9. 再回到 Step 0，用合格 full/global 输入或合格纯搜索诊断生成下一轮动作。

P0 只负责“停止变脏、变大、变慢、变不准”。不要在 P0 阶段顺手重构主算法。

## P0 治理回合

当 Step 0 发现仓库已经很脏、核心文件继续膨胀、或存在未完成 full/global 验收时，不直接进入 Step 1-4。先执行一个 P0 治理回合。P0 治理回合不是算法修复，它的产出是“让下一轮算法修复可判断、可回滚、可验收”。

P0 治理回合固定只选一个治理目标：

- `artifact_hygiene`：清点、归类、隔离本地产物，避免 reports/output/models/训练 CSV/log/pid/diff_code 误提交。
- `owner_boundary`：为巨型文件建立 owner 边界和迁移入口，避免继续向 `query_builder.py`、`param_validator.py`、`match_engine.py`、`ltr_ranker.py` 等文件塞新业务分支。
- `code_health_triage`：清点大文件、逻辑风险文件和冗余文件，输出 `large_file_decomposition`、`logic_error_triage`、`redundant_file_hygiene` 子目标；只做识别、归属和处理计划，不在同一回合顺手重构或删除。
- `pending_validation_closure`：处理 `pending_full_validation` 台账，决定启动 Step 5、标记 rejected/rollback_required，或明确继续快速修复线的风险条件。
- `baseline_freeze`：补齐可比较基线，包括 commit、命令、输入、配置、准确率、召回率、耗时和失败分布。
- `diagnostic_completeness`：补齐 pure search、candidate lifecycle、threshold check 等诊断字段，避免靠最终 pass/fail 猜原因。

P0 治理回合的通过标准：

- 本轮只修改治理工具、治理文档、轻量测试或治理产物，不修改匹配算法策略。
- 输出 `p0_remediation_target`、`before_risk`、`after_risk`、`remaining_risk` 和 `next_allowed_action`。
- 如果治理目标是 `artifact_hygiene`，必须只做清点和隔离方案；删除、移动、忽略规则变更必须单独汇报，不得顺手清空用户工作区。
- 如果治理目标是 `owner_boundary`，必须先定义 owner 目录、桥接边界和禁止新增分支的规则；只有下一轮才能迁移具体算法逻辑。
- 如果治理目标是 `code_health_triage`，必须把大文件、逻辑风险文件、冗余文件分别列出：大文件只能先拆 owner 和迁移边界；逻辑风险文件只能指向未通过 manifest、失败阶段和下一诊断；冗余文件只能标记候选和来源，删除/移动必须另起 P0 回合。
- 如果治理目标是 `pending_validation_closure`，不得把 pending 项直接当作已通过；必须进入 Step 5，或明确登记为 `rejected`、`rollback_required`、`deferred_with_reason`。

P0 状态解释固定为：

- `pass`：可以进入 Step 1-4。
- `warn`：可以继续诊断，但不得发布、不得刷新 generated knowledge；若 warn 来自大量脏产物、巨型文件触碰或 pending 验收，默认下一步优先 P0 治理。
- `block`：不得进入算法修复；下一步只能是一个 P0 治理回合、Step 5 full/global 验收、或准备合格 full/global 输入。

P0 阻断建议不是简单“越严越好”。以下情况必须 `block`：

- 没有任何合格 full/global 输入。
- changed text 中存在 secret、生产 SSL bypass、生产路径 `shell=True` 或新增 mojibake。
- 缺少可比较 baseline，却准备声明准确率、召回率或速度提升。
- `pending_full_validation` 台账损坏，或发布/刷新知识前仍有 pending 项。
- 本轮准备把短切片 benchmark 产物写入 generated knowledge。
- 大文件、逻辑风险文件或冗余文件被检测到但没有 `code_health_risk` 产物时，不得进入相关 P0 处理或算法修复。

以下情况默认 `warn`，但如果本轮要进入 Step 4 算法修复，应先做 P0 治理：

- 大量 reports/output/models/训练 CSV/log/pid/diff_code 处于未跟踪或待提交状态。
- 本轮会触碰巨型 owner 文件，且没有 owner 迁移说明。
- `code_health_risk.status=warn`，说明存在大文件拆分、逻辑风险或冗余文件候选，下一步优先一个 P0 子目标。
- 当前 full/global 输入是 stale，但仍有旧的合格输入可用。
- 已存在 pending_full_validation，但本轮不是发布，也不是刷新正式知识。

P0 治理回合完成后，不直接进入算法补丁；必须回到 Step 0 重新运行 preflight，用新的状态决定下一步。

## 三线执行模型

V36 固定拆成三条线：

- P0 治理线：只修治理工具、边界、台账、诊断完整性和产物卫生，不修匹配算法。
- 快速修复线：Step 0 到 Step 4。使用最近一次合格 full/global 输入做诊断，做一个最小修复，跑单测和目标切片 benchmark。单轮目标是几分钟到几十分钟内闭环。
- 全量验收线：Step 5。full/global benchmark 作为独立长任务，定期或用户明确要求时运行。它刷新下一轮 Step 0 输入，并把多个 `pending_full_validation` 修复统一判定为通过或回退诊断。

执行规则：
- P0 治理线优先级高于快速修复线；当 P0 风险已经影响判断时，先治理再修算法。
- 如果没有新的 full/global 结果，但存在旧的合格 full/global 输入，可以继续快速修复线；汇报中必须写明 `full_validation_status=pending`。
- 如果没有任何合格 full/global 输入，Step 0 停止，不能用短切片、浙江-only、单专题、smoke 推导全局 next_action。
- 如果 full/global 验收出现回归，停止继续小修复，先回到 Step 2/Step 3 重新定位最大错误桶或回滚本批中导致回归的最小点。

## 自动化编排模型

推荐先半自动、后自动。外层 orchestrator 只负责调度，不负责替代 gate 判定：

1. `v36_gate.py preflight` 输出 P0 状态；`block` 时停止并交给人选择 P0 治理策略。
2. `v36_gate.py freeze-baseline` 或读取已冻结 baseline，确认 `version_tuple`。
3. Step 1/Step 2 产物由确定性工具生成。
4. `v36_gate.py choose-next-action` 生成唯一 action、owner 和 validation scope。
5. LLM agent 只在 action/owner/repair_unit 内改代码。
6. `policy_check.py` 拒绝越界 patch。
7. 自动运行 golden、targeted、slice benchmark 和生命周期诊断。
8. `v36_gate.py validate-step4-manifest --manifest <round_manifest>` 复算 Step 4 结论；如 `agent_claim_mismatch=true`，本轮不得登记 pending。
9. `v36_gate.py register-validation --manifest <round_manifest>` 只把 validate 派生的 `benchmark_pass` 写入 pending；partial 状态只留在 round manifest 供下一轮选择器跳过，失败时 orchestrator 回滚到 patch 前快照。

熔断规则：
- 同一 cluster 连续 3 轮无推进，拉黑该 cluster，等待 Step 5 重新聚类。
- `pending_full_validation` 累计达到 10 条，强制触发 Step 5。
- 单轮生产 patch 超过 200 行，policy check 默认失败，除非人显式批准拆分计划。
- generated knowledge 或 `version_tuple` 变化，全部旧 pending 作废并重跑 Step 5。
- 每日自动修复轮数必须有上限；超过上限只允许诊断和汇报。

## Step 0：确认输入

目标：先确认 P0 闸门状态，再找可用的全专业 benchmark 输入；不跑长任务，不改代码。

动作：
- 先做 P0 轻量 preflight，不修复、不清理无关工作区，只汇报状态：
  - `git_status_summary`：是否存在大量未跟踪产物、是否影响本轮。
  - `dirty_artifact_risk`：是否出现 reports/output/models/temp/训练数据等本地产物准备被误提交。
  - `giant_file_touch_risk`：本轮是否可能触碰巨型核心文件。
  - `code_health_risk`：大文件、逻辑风险文件和冗余文件候选；必须输出 `recommended_p0_subtargets`，可选值为 `large_file_decomposition|logic_error_triage|redundant_file_hygiene`。
  - `secret_or_mojibake_risk`：是否发现本轮相关路径存在硬编码密钥、SSL bypass 或新增乱码风险。
  - `test_tier_plan`：本轮如果进入 Step 4，预计使用 smoke、targeted、slice benchmark 还是 Step 5 full/global。
  - `pure_search_risk`：如果本轮涉及纯搜索，说明是否已有 `pure_search_metrics`；若纯搜索准确率低于 40% 且没有链路拆分指标，默认 `p0_gate_status=block`，下一步只能补诊断。
  - `baseline_snapshot`：是否已有可比较基线；没有则不得进入准确率/速度修复。
  - `version_tuple`：算法、知识库、题库、语料、向量索引、embedding、模型 profile 和 seed 是否和 baseline 一致。
  - `pending_full_validation_summary`：是否存在未 full/global 验证的局部修复；如存在，说明是否影响本轮。
  - `data_review_queue_summary`：open/fixed/wontfix/ambiguous 数量和 open 占比。
  - `flaky_tracking_summary`：是否存在累计 3 次以上的未治理 flaky signature。
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
- `version_tuple` 已输出；如和 baseline 不一致，下一步优先重新冻结基线。
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
- `commonality=shared` 表示强共性簇，必须同时满足 `sample_count >= 3` 且 `sample_ratio >= 1%`。
- `commonality=weak_shared` 表示弱共性簇，满足 `sample_count >= 2` 但未同时满足强共性阈值；弱共性只支持补诊断、补聚类特征或等待 full/global 重新聚类，不得直接进入 Step 4 算法修复。
- `commonality=singleton_only` 表示当前输入只支持单例诊断，不支持直接算法修复。
- `sample_ratio` 分母固定为 `wrong_total`，按 `sample_count / wrong_total` 计算；输出建议保留至少 4 位小数，阈值判断使用未四舍五入的原始值。
- `target_common_issue` 优先选择最大 `shared` 簇；若没有 `shared` 簇，才从 `weak_shared` 或 `largest_bucket` 中选择诊断目标，并在下一步标记为弱共性诊断或单例诊断。

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
- R6_known_data_issue：已经进入 `v36_data_review_queue.json` 且 `status != open` 的已知数据问题，不参与普通修复排序。

冲突规则：
- `error_stage` 表示运行时实际失败阶段，是 R 桶判断的 source of truth。
- `attribution_category` 表示诊断或审计给出的解释类别，只能在 `error_stage` 缺失、unknown 或明显不兼容时作为 fallback。
- 两者冲突时必须输出 `diagnostic_conflicts`；不得静默覆盖。
- 两者都缺失或无法识别：归 R6。
- Step 2 读取 `reports/agent_state/v36_data_review_queue.json`；`status != open` 的样本从普通 `wrong_total`、`common_issue_clusters` 和 `target_common_issue` 中剔除，或进入 `R6_known_data_issue`。
- `missing_field_rate > 10%` 时，后续 action 强制为 `improve_diagnostics`。
- 最大簇若为 `weak_shared` 或 `singleton_only`，后续 action 只能是 `improve_diagnostics`、`review_data`、补聚类字段或等待新的 full/global 输入；不得进入算法修复。

验收：
- summary 存在。
- `wrong_total > 0`。
- `largest_bucket` 非空。
- `missing_field_rate` 可见。
- `common_issue_clusters` 非空。
- 每个簇的 `sample_count`、`sample_ratio` 和 `commonality` 可见，且 `commonality` 只能是 `shared`、`weak_shared` 或 `singleton_only`。
- `target_common_issue` 可见，且能解释为什么不是按单个定额选择。
- `data_review_exclusion_summary` 可见，说明剔除或单独成桶的已知数据问题数量。
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
- 选择器必须先读取 `pending_full_validation` 台账和 `reports/attribution/v36_round_manifest_*.json`，形成 `selector_state_inputs`，再选择 `target_common_issue`。Step 4 已处理过的 repair unit 不能只因为 full/global 输入未刷新就再次成为算法修复目标。
- 若某个 repair unit 在 manifest 中出现 `partial_validation_status=blocked_by_next_stage|candidate_lifecycle_pass|local_behavior_pass`，或出现 duplicate guard，或 `failed_slice_next_action.same_repair_unit=false`，该 repair unit 必须加入 `skipped_repair_units`；其中 `blocked_by_next_stage` 还必须加入 `blocked_next_stage_repair_units`，即使它没有登记为 `pending_full_validation`。
- 若最大簇被 `skipped_repair_units` 跳过，选择器只能在剩余未处理共性簇中重新选择；如果剩余簇不足以授权算法修复，则输出 `improve_diagnostics`、`review_data` 或 `start_step5_full_validation` 对应的治理/诊断动作，不得重复输出同一个 R1/R2/R3/R4/R5 修复 action。
- 若 `blocked_next_stage_repair_units` 指向下一阶段，例如 R1 已推进但被 R2/LTR 阻断，下一轮只能先输出下一阶段诊断授权或等待新的确定性 next_action；不得在当前 Step 4 继续叠加 R1 或 R2/LTR patch。
- `missing_field_rate > 10%`：`improve_diagnostics`
- `target_common_issue.commonality=weak_shared`：`improve_diagnostics`；只能在 `reason` 中记录归属桶和疑似修复方向，不得输出算法修复 action。
- `target_common_issue.commonality=singleton_only`：若 bucket 为 R6 则 `review_data`，否则 `improve_diagnostics`；只能在 `reason` 中记录归属桶和疑似修复方向，不得输出算法修复 action。
- 否则先看 `target_common_issue.bucket`，不直接按桶内第一个样本选动作。
- `target_common_issue.bucket=R1`：`fix_r1_recall`
- `target_common_issue.bucket=R2`：`fix_r2_ltr`
- `target_common_issue.bucket=R3`：`fix_r3_cgr`
- `target_common_issue.bucket=R4`：`fix_r4_picker`
- `target_common_issue.bucket=R5`：`fix_r5_validator`
- `target_common_issue.bucket=R6` 或 unknown：`review_data`
- `target_common_issue.bucket=R6_known_data_issue`：不得生成算法 action；只能更新 data review 队列或等待数据修订。

next_action 必须包含：
- `schema_version`
- `generated_at`
- `action`
- `reason`
- `largest_bucket`
- `sample_count`
- `target_common_issue`
- `repair_unit`
- `repair_unit_id`
- `cluster_sample_ids`
- `representative_sample_ids`
- `suggested_validation_scope`
- `input_latest_path`
- `input_attribution_path`
- `full_validation_status`
- `selector_state_inputs`
- `skipped_repair_units`
- `blocked_next_stage_repair_units`
- `data_review_queue_update`（仅 `action=review_data` 时必填）

验收：
- JSON 存在。
- 只有一个 `action`。
- action 合法。
- action 与 `target_common_issue.bucket` 一致，除非 `missing_field_rate > 10%` 或 `target_common_issue.commonality != shared`。
- 当 `target_common_issue.commonality != shared` 时，算法修复 action 必须降级为诊断动作或在 `reason` 中明确写出不得进入 Step 4 算法补丁。
- 当 `action=review_data` 时，必须写入或更新 `reports/agent_state/v36_data_review_queue.json`；否则不通过。
- `suggested_validation_scope` 包含 `filter_cluster_id` 或 `filter_common_issue_key`。
- 若同一 full/global 输入连续选择同一个已处理 `repair_unit_id`，必须判定为选择器治理失败；本轮只能修 `choose-next-action` 状态读取或补诊断，不得进入算法补丁。旧 manifest 没有 `repair_unit_id` 时才按 `issue_key` 兼容跳过。
- `skipped_repair_units` 必须写明 `issue_key`、`repair_unit_id`、`cluster_id`、`mechanism`、`owner_module`、`reason`、`source_manifest` 和 `next_stage`（如可判定）。
- 时间上限 20 分钟。

失败退出：
- action 不唯一：停止。
- action 与 `target_common_issue` 冲突：停止。

## Step 4：按 action 做一个最小修复

目标：一次只修一个点，当天可验收。

前置硬约束：
- `p0_gate_status=block` 时不得进入 Step 4；必须先处理 P0 闸门。
- 必须写明 `target_common_issue.cluster_id`、`issue_key`、`commonality`、共性根因假设和影响代码路径。
- `commonality=shared` 时，修复必须同时验证同簇至少 3 个样本；若同簇样本多于 5 个，默认验证 5 个代表样本并覆盖不同 `shared_signals` 或 expected/selected 示例。只改善代表样本、不改善同簇样本的改动视为过拟合，不能通过。
- `commonality=weak_shared` 时，不能进入算法修复；允许动作只有补诊断字段、补聚类特征、扩大合格 full/global 输入、跑最小冒烟或等待 Step 5/full-global 生成更多同类样本。
- `commonality=singleton_only` 时，不能按该单个定额打算法补丁；允许动作只有补诊断字段、补聚类特征、跑最小冒烟或等待 Step 5/full-global 生成更多同类样本。
- 单样本 benchmark 只能作为 smoke，不能作为 Step 4 接受标准。
- 若候选修复是新增同义词、别名、关键词或 route hint，必须先回答三个问题：这是跨样本稳定概念缺口，还是单个清单/定额的文本巧合；是否会扩大到错误专业、错误工法或错误计量单位；是否存在更上游的通用修复点。回答不清楚时，不得提交词表堆叠。
- 代码必须简洁有效：优先用一个谓词、一个归一化函数、一个 scoring feature 或一张小表覆盖共性簇；如果需要为每个样本新增分支、同义词、特殊 case 或大段解释，默认判定为设计失败，回到根因分析。
- 不得新增 `_apply_xxx_rescue` 手写链；如必须处理 ranking 保护逻辑，优先走规则注册表、表驱动 guard 或通用 feature。
- 不得把业务逻辑继续塞进巨型 API 文件；Web 请求处理、domain 决策、repository 写入必须分层。
- 不得新增全局 `config` 写入、静默 `except Exception: pass`、硬编码 secret、用户可见乱码或生产路径 SSL bypass。
- 如果本轮目标是纯搜索准确率或速度，必须先给出 `pure_search_metrics` 和 `bottleneck_classification`；没有证明瓶颈前，不得直接调大 top_k、扩大搜索范围、添加同义词或删除 validator。
- 必须有冻结基线和阈值判断；没有 `baseline_snapshot` 时不得声明提升。
- 必须有 `version_tuple`，且与 baseline 完全一致；不一致时不得声明提升，必须重新冻结基线。
- 修复必须能单独定位和回滚：优先小函数、小表、规则开关或独立模块，不把多个根因揉进一个大 patch。
- 必须由程序输出 `accuracy_impact`、`speed_impact`、`complexity_impact`。如果为了局部准确率引入全局慢路径，默认不通过。
- 必须声明 `tradeoff_mode`，默认 `none`；任何 trade-off 都必须符合 P0-9 矩阵。
- 必须遵守执行者行为闸门；不得把部分推进包装为 benchmark 通过。

允许范围：
- `improve_diagnostics`：只补诊断字段。
- `fix_r1_recall`：只改 query/router/candidate pool 中一个最小点。
- `fix_r2_ltr`：只改 LTR feature/guard 中一个最小点。
- `fix_r3_cgr`：只改 CGR guard 一个点。
- `fix_r4_picker`：只改 picker/category_safe 一个点。
- `fix_r5_validator`：只改 validator/experience 一个点。
- `review_data`：不改算法，只写入或更新 `reports/agent_state/v36_data_review_queue.json`。

验收顺序：
1. 先跑相关单测或目标函数级测试。
2. 再跑 `tools/policy_check.py --next-action reports/attribution/global_repair_next_action.json` 或等价确定性 policy check；不通过即拒绝 patch。
3. 再跑 `eval/regression_golden/` 历史回归集；任一历史 case 退化即 `regress`，停止本轮，不得继续目标切片。
4. 再跑 `next_action.suggested_validation_scope` 指向的最小 benchmark；短切片必须加 `--no-materialize-learning`，避免把局部错误资产写回默认 `data/province_plugins/generated`。
5. 运行 `tools/v36_gate.py validate-step4-manifest --manifest <round_manifest>`，由程序解析 targeted/golden/slice/lifecycle 结果并计算 `partial_validation_status`、`accuracy_impact`、`speed_impact`、`complexity_impact`、`threshold_check`；agent 不得手填。
6. 若本轮达到 `benchmark_pass` 并准备登记 `pending_full_validation`，先把代表样本、同簇 1-2 个正样本和至少 1 个反例写入或更新 `eval/regression_golden/`。
7. 输出 `test_tier`、`changed`、`improved`、`regressed`、`policy_check_status`、`regression_golden_status`、代表样本变化，以及未跑 full/global 的理由。
8. 纯搜索相关修复由程序输出 `pure_search_metrics`、`latency_budget`、`bottleneck_classification` 和修复前后指标变化。
9. 程序输出 `accuracy_impact`、`speed_impact`、`complexity_impact`、`complexity_delta`。
10. 程序输出 `threshold_check`：说明版本元组、top1、recall@20、总耗时 P95、阶段耗时 P95、复杂度、flaky 和 tradeoff 相对冻结基线是否通过；`recall@5` 和 `recall@100` 作为诊断字段同时保留但不作为 Step 4 硬门禁。
11. 输出 `rollback_plan`：必须符合“回滚计划契约”，并明确 `rollback_type`、`rollback_target`、`affected_files`、`rollback_command_or_change` 和 `post_rollback_validation`。
12. 程序输出 `p0_gate_after_patch`：确认未新增本地产物污染、巨型分支、secret、mojibake、全局状态写入或静默失败。
13. 修复通过后，由 `v36_gate.py register-validation --manifest <round_manifest>` 把 validate 派生为 `benchmark_pass` 的修复写入 `reports/agent_state/v36_pending_full_validation.json`，状态为 `pending_full_validation`；下一步唯一动作是“回到 Step 0 开始下一轮小修复”或“启动 Step 5 full/global 验收”，二选一，不在当前回合继续叠加第二个算法修复。

失败退出：
- 出现回归：停止，不叠加第二个修复。
- `regression_golden_status=fail` 或缺失：停止，不叠加第二个修复。
- 目标样本没改善：停止，回到诊断。
- 修复跨越多个层级：停止拆小。
- P0 闸门被破坏：停止，先修闸门，不继续算法。
- `policy_check_status=fail`：停止并回滚 patch，不进入 golden 或 benchmark。
- 准确率、速度或复杂度影响说不清：停止，回到方案压缩。
- `rollback_plan` 不属于 `config_flag`、`isolated_module_call` 或 `git_revert`，或需要反向打补丁：停止，回到方案压缩或模块隔离。
- `version_tuple` 与 baseline 不一致：停止，重新冻结基线。
- `tradeoff_mode` 不合规：停止，回到方案压缩或改为 Step 5 验证。
- 被判定为 flaky 且未登记 `flaky_tracking.json`：停止，补可重现性诊断。
- 违反执行者行为闸门：停止，不登记 pending。
- threshold check 未通过且没有合理解释：停止，回到诊断或回滚本修复。
- 诊断显示主要是数据质量问题：停止算法修复，转 `review_data` 并写入 data review 队列。

## Step 5：异步 full/global 验收

目标：把一批 `pending_full_validation` 小修复放到 full/global benchmark 下统一验收，刷新下一轮 V36 输入。

触发条件：
- 用户明确要求跑 full/global benchmark。
- 累计多个 `pending_full_validation` 修复，需要发布或合并前验收。
- `eval/regression_golden/` 出现回归、缺少登记样本或 manifest 与 pending 台账不一致。
- Step 0 没有任何合格 full/global 输入。
- 快速修复线出现目标切片改善但全局风险较高，需要确认是否有回归。
- 发布、提交 generated knowledge 或清空 `pending_full_validation` 台账前。
- `version_tuple` 变化、generated knowledge 刷新、data review open 占比超阈值或 flaky 累计触发治理后。

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
- Step 5 必须输出新的 `version_tuple`；如果刷新 generated knowledge，必须先把旧 pending 标记为 `stale_due_to_knowledge_refresh`，再用新版本重新验收。

验收：
- `eval/regression_golden/` 历史回归集通过；若失败，先定位最近修复或关闭对应 patch，不得继续发布验收。
- `version_tuple` 完整且和 release candidate 一致。
- `global_repair_v36_full_latest.json` 存在且是 full/global。
- `global_repair_v36_full_attribution.json` 存在。
- `global_repair_v36_full_summary.json` 存在。
- full/global 没有不可接受回归，或回归已明确归因。
- `reports/agent_state/v36_pending_full_validation.json` 中本批修复已逐条判定为 `full_validated`、`rejected` 或 `rollback_required`。
- `reports/agent_state/v36_data_review_queue.json` 已检查；`data_review_open_rate > 5%` 时必须附 `data_revision_plan`，`> 10%` 且无计划时 release block。
- `reports/agent_state/flaky_tracking.json` 已检查；累计 3 次未治理 flaky 时 release block。
- `release_gate_status=pass` 时，才允许发布或提交 refreshed generated knowledge。

完成后：
- 下一轮 Step 0 优先使用 Step 5 产物。
- 通过后，把本批 `pending_full_validation` 标记为 `full_validated`。
- 通过后，确认 `eval/regression_golden/manifest.json` 中本批 case 状态为 `active`，并记录对应 full/global 验收产物路径。
- 通过后，记录最终发布的 `version_tuple` 和 generated knowledge digest。
- 未通过时，停止继续修复，回到 Step 2/Step 3 重新生成 next_action。
- 如 full/global 失败且无法明确归因，默认不继续叠加修复，先回滚或关闭本批最小可疑修复。

## V36.1 补充协议：链路追踪、部分验收和失败续航

本节是两轮执行后的修订。V36 治理门禁继续保留，但 Step 4 不再只用“最终 benchmark 是否命中”判断本轮是否有价值。真实算法链路是串联的：query 构造、raw recall、候选合并、family gate、validator、LTR、picker、final validation 任一阶段都可能暴露下一层瓶颈。因此 V36.1 增加“部分推进可记录、不可盲目叠修”的协议。

### 1. 修复单元定义

一轮 Step 4 的修复单元固定为：

```text
repair_unit_id = target_common_issue.cluster_id + target_common_issue.issue_key + mechanism + owner_module
```

其中：
- `target_common_issue.cluster_id`：来自 Step 2/Step 3 的共性簇。
- `target_common_issue.issue_key`：共性问题键；仅靠它不足以区分同簇下的不同机制。
- `mechanism`：本轮处理的共性机制，例如 `surface_process_route_hijack`、`book_scope_loss`、`hard_validator_drop`、`wrong_family_gate`。
- `owner_module`：来自 `suggested_validation_scope.owner_module`，用于限制 Step 4 生产代码改动边界。

禁止把同一簇下多个独立机制揉成一个大 patch。允许在同一簇下继续做“下一阶段诊断”，但若要做下一阶段算法补丁，必须重新生成或更新 `failed_slice_next_action`，并说明它不是第二个独立修复点。

### 2. 候选生命周期追踪

纯搜索和候选相关回合必须尽量输出 `candidate_lifecycle_trace`。如果现有产物缺字段，也必须显式写 `missing`，不能伪造指标。

推荐结构：

```json
{
  "candidate_lifecycle_trace": {
    "query_text": "",
    "raw_recall_ids": [],
    "after_prior_merge_ids": [],
    "after_neighbor_merge_ids": [],
    "after_family_gate_ids": [],
    "after_validator_ids": [],
    "pre_ltr_ids": [],
    "post_ltr_ids": [],
    "post_picker_ids": [],
    "final_ids": [],
    "drop_reasons": [
      {
        "quota_id": "",
        "from_stage": "",
        "reason_code": "",
        "detail": ""
      }
    ]
  }
}
```

R 桶细分补充：
- `R1a_raw_recall_miss`：正确候选从未进入 raw recall。
- `R1b_merge_loss`：raw recall 命中，但合并、去重、neighbor 或 prior 阶段丢失。
- `R1c_materialization_loss`：有候选 id，但物化定额行失败或字段缺失。
- `R1d_hard_validator_drop`：正确候选进入候选池后被硬参数、family gate、book/scope 或 validator 删除。
- `R2_rank_wrong_after_valid_pool`：正确候选仍在有效候选池内，但排序或 final pick 未选中。

当 summary、latest、日志之间出现矛盾时，必须输出 `diagnostic_conflicts`，例如“`recall_topk_ids` 命中但 `all_candidate_ids` 为空”。这类样本不得简单归为纯 R1。

### 3. 部分验收状态

Step 4 新增 `partial_validation_status`，允许记录本轮推进到哪一层：

- `diagnostic_pass`：诊断字段补齐，能解释最大共性簇和下一瓶颈。
- `local_behavior_pass`：本轮目标函数或局部链路行为已按预期改变，但 benchmark 未必命中。
- `candidate_lifecycle_pass`：正确候选已从缺失推进到后续阶段，且生命周期证据清楚。
- `blocked_by_next_stage`：本轮机制已推进，但被下一阶段瓶颈阻断。
- `benchmark_pass`：目标切片或局部 benchmark 命中。
- `failed`：目标行为无改善，或引入明显回归。

只有 `benchmark_pass` 才能直接登记为 `pending_full_validation`。`local_behavior_pass`、`candidate_lifecycle_pass`、`blocked_by_next_stage` 可以保留 patch，但必须登记在本轮报告和 `failed_slice_next_action` 中，不得冒充验收通过。

`local_behavior_pass`、`candidate_lifecycle_pass`、`blocked_by_next_stage` 虽然不能进入 `pending_full_validation`，但必须作为选择器保留状态写入本轮 manifest。下一轮 Step 0-3 必须读取这些 manifest，并把原 repair unit 当作已处理或被下一阶段阻断；不得因为 full/global 输入尚未刷新就重复选择同一个 issue_key 做同阶段算法 patch。

无论是否达到 `benchmark_pass`，只要本轮 patch 准备保留，`regression_golden_status` 必须为 `pass`；否则 `partial_validation_status` 固定为 `failed`，并进入回退或关闭流程。

### 4. 失败切片后的下一动作

切片 benchmark 未通过时，不再只写“停止”。必须生成或汇报 `failed_slice_next_action`：

```json
{
  "failed_slice_next_action": {
    "action": "continue_same_issue_next_stage",
    "same_repair_unit": false,
    "next_failing_stage": "validator",
    "reason": "raw recall now exposes expected quota but hard validation removes it",
    "allowed_next_work": "diagnose_only | targeted_patch_after_new_next_action",
    "rollback_required": false
  }
}
```

合法 action：
- `continue_same_issue_next_stage`：同一共性簇已推进到下一阶段，允许下一轮继续诊断。
- `rollback_current_patch`：当前 patch 没有推进或引入回归。
- `convert_to_data_review`：暴露的是 expected、题库、主辅项或省份定额语义问题。
- `need_more_diagnostics`：生命周期字段不足，先补 trace。
- `start_step5_full_validation`：局部通过但全局风险较高，需要 full/global。

当 `failed_slice_next_action.same_repair_unit=false` 时，下一轮不得沿用当前 Step 4 的 action 直接追加 patch；必须回到 Step 0-3，由 `choose-next-action` 根据 selector state 重新授权。若工具仍输出同一个已阻断 repair unit，本轮唯一合法动作是修选择器状态或补诊断。

### 5. expected 语义和主辅项

多 expected 样本必须标记 `expected_semantics`：

- `any_of`：任一 expected 命中即可。
- `all_of`：必须全部输出。
- `primary_plus_auxiliary`：存在主项和关联辅助项，需区分主项命中与辅助项补充。
- `unknown`：当前题库语义不清，不能用算法补丁强行迎合。

若样本同时包含主项和辅助项，例如电缆敷设主项 + 电缆头辅助项，报告必须写：

```json
{
  "expected_semantics": "primary_plus_auxiliary",
  "primary_expected_ids": [],
  "auxiliary_expected_ids": [],
  "matching_contract": "single_primary | primary_with_related | unknown"
}
```

当 `matching_contract=single_primary` 时，辅助项未命中不得直接判定主项算法失败；当业务要求 `primary_with_related` 时，必须走关联定额输出链路，不得只修主项 picker。

### 6. 指标可信度

`pure_search_metrics` 新增 `metric_confidence`：

```json
{
  "metric_confidence": {
    "recall_at_k": "high",
    "rank_at_k": "medium",
    "validator_veto_rate": "medium",
    "route_filter_loss": "medium",
    "prior_candidates_delta": "requires_ab_run",
    "latency_breakdown_ms": "missing"
  }
}
```

允许值：
- `high`：来自运行时真实链路字段。
- `medium`：来自 latest/static artifact 推导。
- `low`：字段不完整，只能辅助判断。
- `missing`：没有数据。
- `requires_ab_run`：需要成对 benchmark 或开关对照。

字段存在但可信度为 `missing` 或 `requires_ab_run` 时，不得声称速度或 prior 效果已改善。

### 7. patch 保留和回退标准

切片 benchmark 未命中时，当前 patch 可以保留的条件：
- 单测覆盖本轮机制。
- `eval/regression_golden/` 历史回归集通过。
- before/after trace 显示目标链路向正确方向推进。
- 没有相关回归或 P0 新 block。
- 失败原因已迁移到下一阶段，且 `failed_slice_next_action` 清楚。

必须回退或关闭的条件：
- `eval/regression_golden/` 任一历史 case 退化。
- 目标 query、候选生命周期或排序没有改善。
- 正确候选更远或新引入跨类误召回。
- patch 只服务单样本，没有共性机制。
- patch 破坏 P0、引入 secret/mojibake、刷新 generated knowledge 或新增全局慢路径。

### 8. 每轮产物 manifest

每轮必须输出 `round_artifact_manifest`，至少包含本轮新增或修改的：
- 代码文件。
- 测试文件。
- 诊断 JSON/CSV。
- benchmark latest/summary/attribution。
- output/benchmark_assets 目录。
- 是否修改 generated knowledge。

推荐路径：

```text
reports/attribution/v36_round_manifest_<topic>.json
```

### 9. 时间和重试预算

默认预算：
- `targeted unit tests`：5 分钟内。
- `diagnostic command`：5 分钟内。
- `slice benchmark`：最多 20-50 条样本，15 分钟内。
- `benchmark retries`：同一 `version_tuple` 下同一轮最多 1 次；重试只能用于判定 flaky，不得用于挑选好结果。
- `full/global`：只在 Step 5、发布前、无合格输入或用户明确要求时运行。

超过预算时，停止并汇报 `need_more_diagnostics`、`flaky` 或给出 Step 5 独立运行命令，不在当前回合继续消耗。

## V36.2 补充协议：full/global 结果冻结和失败续航

本节来自 2026-04-30 full/global benchmark 结果。full/global 可以刷新下一轮诊断输入，但只有在产物完整、可追溯且 release gate 可判断时才允许作为 Step 0 冻结输入。

### 1. full/global 产物最小可接受集

一次 full/global 运行若要作为下一轮 Step 0 输入，至少需要：
- `reports/attribution/*full*_attribution.json`：必须包含 total、correct、wrong、overall_hit_rate、recall_hit_rate 和 R1-R6 counts。
- `output/benchmark_assets/<full_run>/manifest.json`：必须记录 `all_errors.jsonl`、`rerank_pairs.jsonl`、`synonym_gaps.jsonl`、`route_errors.jsonl`、`tier_errors.jsonl` 的路径和计数。
- `output/benchmark_assets/<full_run>/all_errors.jsonl`：必须存在，且错误总数与 attribution 的 `wrong_total` 一致。
- `version_tuple`：必须包含算法、知识库、题库、清单语料、向量索引、embedding、model profile 和 seed。

如果 `global_repair_v36_full_latest.json` 缺失，但上述三项存在且一致，可以把 `output/benchmark_assets/<full_run>/all_errors.jsonl` 作为 full/global error input；Step 0 必须在 `selected_input.reason` 中写明 `latest_missing_using_asset_all_errors`，不得静默回退到更旧输入。

如果 attribution、manifest、all_errors、version_tuple 四者不一致，或任一缺失，则该 full/global 运行只能作为人工参考，不能刷新 Step 0 输入。

### 2. full/global 失败时的处理

full/global 未通过时，不得把本批 `pending_full_validation` 直接标记为通过，也不得发布或刷新正式知识库。合法动作只有：
- 回到 Step 1/Step 2，用本次 full/global error input 重新生成全局决策表和共性簇。
- 若结果显示明显回归，先定位是否由最近 pending patch 引入；无法归因时，暂停继续叠加修复。
- 若结果主要暴露新的最大桶，例如 R2/LTR 或 R1/召回，则下一轮必须从新的 `common_issue_clusters` 选择唯一动作。

### 3. 2026-04-30 full/global 基线记录

- 输出：`reports/attribution/global_repair_v36_full_attribution.json`、`reports/attribution/global_repair_v36_full_summary.json`、`output/benchmark_assets/global_repair_v36_full/`。
- 规模：4577 题，命中 1737，错误 2840，整体命中率 38.0%。
- 召回：recall_hit_rate 77.7%，R1_召回未命中 1022，占错误 36.0%。
- 排序：R2_LTR选错 1425，占错误 50.2%，为当前最大桶。
- 后处理：R4_Picker推翻正确 262，R3_CGR推翻正确 93，R6_其它 38，R5_经验库直通错 0。
- benchmark assets：`output/benchmark_assets/global_repair_v36_full/manifest.json` 记录 all_errors=2840、rerank_pairs=1134、synonym_gaps=1437、route_errors=356、tier_errors=814。
- 验收结论：full/global 未通过；release gate 继续 blocked；下一轮应优先基于本次 full/global error input 重新生成 Step 1/Step 2，而不是继续沿用 `ltr_v2_full_20260422`。
- 产物状态：已确认 `reports/attribution/global_repair_v36_full_latest.json` 存在；若后续某次 full/global 缺失 latest，Step 0 工具应按 V36.2 规则接受 asset all_errors 作为冻结输入。

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

### 2026-04-29 电力电缆 R1-01 诊断推进记录

- 输入：沿用 `output/benchmark_assets/ltr_v2_full_20260422/all_errors.jsonl` 和 `reports/attribution/ltr_v2_full_20260422.json`；输入为 stale full/global，状态 `pending`。
- target_common_issue：`R1-01`，`R1::recall_miss::c4::search::4-11->4-9`，共 79 条，`commonality=shared`。
- 诊断补齐：`tools/v36_gate.py diagnose-pure-search` 已从静态 full/global latest 产物生成 `pure_search_metrics`，确认该簇 raw candidate top20 命中 0/79，错误集中为 `4-11->4-9`。
- 局部修复：清单主体为 `电力电缆` 且名称本身不是 `刷油/防腐/标识/色环` 时，禁止 `_build_surface_process_query` 被工作内容里的 `标识/色环` 劫持为 `管道标识 色环`。
- before_after_delta：代表样本 query 从 `管道标识 色环` 变为 `室内敷设电力电缆 ... 桥架 穿管 电缆截面 ...`。
- 局部测试：`tests/test_query_builder_stage3_recall_cleanup.py` 新增电缆工作内容标识劫持回归；相关 query builder 和 V36 gate 单测通过。
- 切片 benchmark：`江西省通用安装` + `电力电缆` 20 条，`--no-materialize-learning`，结果 0/20，未通过。
- 失败迁移：切片报告和日志显示正确 `4-9` 系列已进入 `recall_topk_ids`，但最终 `all_candidate_ids=[]` 且候选被硬参数校验拒绝；问题已从 query/召回前置劫持推进到 `R1d_hard_validator_drop` 或候选生命周期缺字段。
- partial_validation_status：`blocked_by_next_stage`；不得登记为 `pending_full_validation`。
- failed_slice_next_action：`continue_same_issue_next_stage`，下一阶段只允许先诊断正确 `4-9` 候选在 materialization/family gate/validator 中被删除的具体 reason_code。
- 备注：该轮促成 V36.1 补充协议，后续同类失败不得只用最终 benchmark pass/fail 判断，应输出 candidate lifecycle、partial status、failed slice next action 和 expected semantics。

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
- p0_remediation_target（仅 P0 治理回合必填）
- before_risk（仅 P0 治理回合必填）
- after_risk（仅 P0 治理回合必填）
- remaining_risk（仅 P0 治理回合必填）
- baseline_snapshot
- version_tuple
- test_tier
- accuracy_impact
- speed_impact
- complexity_impact
- complexity_delta
- threshold_check
- tradeoff_mode
- rollback_plan
- release_gate_status
- pending_full_validation_summary
- selector_state_inputs（Step 3 必填）
- skipped_repair_units（Step 3 必填；无则写空数组）
- blocked_next_stage_repair_units（Step 3 必填；无则写空数组）
- policy_check_status
- regression_golden_status
- data_review_queue_summary
- data_review_open_rate
- flaky_status
- pure_search_metrics（仅纯搜索相关回合必填）
- candidate_lifecycle_trace（候选相关回合必填；缺字段需写 missing）
- before_after_delta（算法或 query 行为变更回合必填）
- partial_validation_status
- failed_slice_next_action（切片未通过时必填）
- expected_semantics（多 expected 或主辅项样本必填）
- metric_confidence（诊断指标来自静态产物或字段不完整时必填）
- round_artifact_manifest
