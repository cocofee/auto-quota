# OpenClaw × Jarvis 二审闭环实施顺序说明

## 结论
这次不要再按“继续微调主排序算法”的方式推进，而是改成：

1. `Jarvis` 继续做主链匹配和候选产出
2. `OpenClaw` 升级为二审复判层
3. 人工保留最终确认权
4. 最终确认结果结构化回灌给 `Jarvis`

推荐实施顺序：

1. 先统一结果数据契约和状态流
2. 再补 OpenClaw 结构化复判能力
3. 再补前端结果对照与人工确认工作台
4. 最后接反馈回流、统计和 staging 晋升

不建议一开始就同时改主匹配链、OpenClaw 审核页、经验回流和知识晋升。这样会把问题混在一起，无法判断是哪一层出了偏差。

## 总体原则

- `Jarvis` 仍然是业务主判引擎，不把最终正式决策权下放给 `OpenClaw`
- `OpenClaw` 拿“结构化复判权”，不拿“直接正式写入权”
- 人工确认仍是正式纠正和正式沉淀的唯一入口
- 每个阶段完成后必须停下来验收，通过后再进入下一阶段
- 任何阶段如果契约未稳定，不继续堆界面和自动化逻辑

## 当前基础
当前仓库已经有以下基础能力，可直接复用：

- `MatchResult` 已有 `quotas`、`alternatives`、`trace`、`openclaw_*` 字段
- `OpenClaw` 已有 `review-draft`、`review-confirm`、`auto-confirm-green`
- 正式确认/纠正时，后端已经会构造经验回流 `feedback_payload`
- `trace` 中已经有 `pre_ltr_top1_id`、`post_arbiter_top1_id`、`post_final_top1_id` 等链路信息
- staging 方案已经明确：`OpenClaw` 只写 staging，不直写正式层

## 目标架构

### 角色分工

- `Jarvis`
  - 负责召回、排序、仲裁、终检
  - 输出候选池、top1、trace、reason tags
- `OpenClaw`
  - 负责二审复判
  - 对 `Jarvis` 结果给出结构化建议
  - 可建议“候选内改判”或“建议重搜”
- 人工
  - 对冲突项、高风险项、OpenClaw 建议项做最终确认
  - 决定最终写入正式结果

### 目标状态流

`Jarvis结果` -> `OpenClaw draft` -> `人工确认/驳回` -> `正式结果` -> `反馈回流` -> `staging候选`

### OpenClaw 页面定位

`OpenClaw` 必须是一个独立工作台，不依赖用户先进入任务结果页逐条点击。

目标交互应该是：

1. `OpenClaw` 页面主动拉取 `Jarvis` 已完成任务
2. 按任务查看待复判结果
3. 支持一键批量复判整批任务
4. 支持在 `OpenClaw` 页面内完成筛选、复判、人工确认分流
5. 结果页只作为明细核对页，不作为 OpenClaw 主工作入口

禁止回退成以下模式：

- 先打开任务列表
- 再打开结果页
- 再一条条点“去核对”

那种流程只适合人工明细复核，不适合作为 OpenClaw 二审主链。

### 统一任务标识

`Jarvis` 和 `OpenClaw` 必须识别并操作同一个任务对象，不能各自复制一套任务。

目标规则：

1. `Jarvis` 产出的 `task_id` 是主任务标识，也是唯一事实来源
2. `OpenClaw` 不重新创建“匹配任务”，只创建“审核作业”
3. `OpenClaw` 审核作业必须绑定到 `source_task_id = Jarvis.task_id`
4. 单条结果继续使用同一个 `result_id`
5. 人工确认后，正式结果直接回写原 `match_results` 行，而不是写一份 OpenClaw 副本

推荐标识模型：

- `task_id`
  - Jarvis 主任务 ID
- `result_id`
  - Jarvis 主结果 ID
- `review_job_id`
  - OpenClaw 审核作业 ID
- `source_task_id`
  - 指向 Jarvis `task_id`
- `source_result_id`
  - 指向 Jarvis `result_id`

这样做的目的：

- 所有系统都知道“跑的是同一个任务”
- OpenClaw 不会和 Jarvis 出现任务副本漂移
- 反馈回流、staging、审计日志都能直接追到原任务
- 后续接第三方系统时，也能统一挂在同一个任务主键上

禁止做法：

- Jarvis 跑一套 `task_id`
- OpenClaw 再复制一套“审核任务”
- 人工确认又落到第三套记录

那样后面统计、回流、追错都会乱。

## P0-1 统一结果契约与状态流
目标：先把后端和前端对“OpenClaw 二审结果”的表达统一，避免后面接口和页面反复返工。

范围：

- 统一 `MatchResult` 中 OpenClaw 相关字段
- 增加结构化二审字段
- 统一前后端状态枚举
- 保持现有结果页和 OpenClaw 页面兼容

建议交付：

- `web/backend/app/models/result.py`
- `web/backend/app/schemas/result.py`
- `web/frontend/src/types/index.ts`
- 一份状态枚举约定

建议新增字段：

- `openclaw_decision_type`
- `openclaw_error_stage`
- `openclaw_error_type`
- `openclaw_retry_query`
- `openclaw_reason_codes`
- `openclaw_review_payload`
- `human_feedback_payload`

建议统一枚举：

- `openclaw_review_status`
  - `pending`
  - `drafted`
  - `reviewed`
  - `applied`
  - `rejected`
- `openclaw_review_confirm_status`
  - `pending`
  - `approved`
  - `rejected`

必须解决的问题：

- 当前模型注释与前端筛选状态存在不一致
- 现有 `review-draft` 只保存“建议定额+备注”，信息量不足，无法支撑后续回流统计

验收标准：

- 后端 schema 和前端 type 对新增字段完全对齐
- `MatchResultResponse` 能返回结构化 OpenClaw 字段
- 不改动现有业务逻辑时，结果页和 OpenClaw 页不报错
- 状态枚举只有一套，没有前后端各自解释

停门条件：

- 如果前后端类型还没完全对齐，不进入 P0-2

## P0-2 OpenClaw draft 数据模型升级
目标：保留现有 `review-draft` 流程，但把 draft 从“备注文本”升级为“结构化复判结果”。

范围：

- 扩 `OpenClawReviewDraftRequest`
- 扩 `MatchResultResponse`
- `review-draft` 持久化结构化 payload
- 保持老接口兼容

建议交付：

- `web/backend/app/api/openclaw.py`
- `web/backend/app/schemas/result.py`
- 数据库存储迁移或兼容处理

结构化 draft 最少应包含：

- `decision_type`
  - `agree`
  - `override_within_candidates`
  - `retry_search_then_select`
  - `candidate_pool_insufficient`
  - `abstain`
- `suggested_quotas`
- `review_confidence`
- `error_stage`
- `error_type`
- `retry_query`
- `reason_codes`
- `note`

验收标准：

- 可通过 API 提交结构化 `review-draft`
- 数据库能稳定保存和返回结构化字段
- 老版只传 `openclaw_suggested_quotas + note` 的请求仍可用

停门条件：

- 如果 draft 存不稳或接口兼容性没验证完，不进入 P1

## P1-1 OpenClaw 结构化复判服务
目标：引入一个明确的后端服务层，负责把 `Jarvis` 结果转成 OpenClaw 可消费的复判任务。

范围：

- 新增 OpenClaw 复判服务
- 读取 `MatchResult`、`alternatives`、`trace`
- 输出结构化 draft
- 默认只允许候选内改判，不直接改正式结果

建议交付：

- `web/backend/app/services/openclaw_review_service.py`
- `web/backend/app/api/openclaw.py`

服务层必须明确区分：

- `Jarvis` 主任务
- `OpenClaw` 审核作业

但审核作业只是一层工作流对象，不是新的匹配任务副本。

服务输入：

- 清单信息
- `Jarvis` 主结果
- `alternatives`
- `trace`
- `reason_tags`
- 终检信息

服务输出：

- `decision_type`
- `suggested_quotas`
- `retry_query`
- `error_stage`
- `error_type`
- `reason_codes`
- `review_confidence`
- `evidence`

建议决策规则：

- 候选池中已存在更合适结果：`override_within_candidates`
- 候选池看起来整体方向错：`candidate_pool_insufficient`
- 候选池勉强可用，但建议扩大搜索：`retry_search_then_select`
- 与 Jarvis 一致：`agree`
- 信息不足：`abstain`

验收标准：

- 能对单条结果生成结构化二审结果
- 二审结果可回写为 `review-draft`
- 不需要改正式结果就能完整展示二审结论

停门条件：

- 如果输出结构化结果还依赖人工拼接备注，不进入 P1-2

## P1-2 OpenClaw 批量复判入口
目标：让 OpenClaw 可以批量处理黄灯、红灯和冲突项，而不是人工逐条触发。

范围：

- 单条自动复判接口
- 批量自动复判接口
- 任务级 review-items 查询补充结构化字段

建议交付：

- `POST /api/openclaw/review-jobs`
  - 创建一个绑定 `source_task_id` 的审核作业
- `GET /api/openclaw/review-jobs/{review_job_id}`
  - 查询审核作业进度
- `POST /api/openclaw/tasks/{task_id}/results/{result_id}/auto-review`
- `POST /api/openclaw/tasks/{task_id}/results/batch-auto-review`
- `GET /api/openclaw/tasks/{task_id}/review-items`
- `GET /api/openclaw/tasks`
  作为 OpenClaw 页面主动拉取 Jarvis 已完成任务的主入口

推荐调用方式：

1. `Jarvis` 跑完，得到 `task_id`
2. 你给 `OpenClaw` 一个指令：审核这个 `task_id`
3. `OpenClaw` 创建 `review_job_id`
4. `review_job_id` 只负责审核流程
5. 所有审核结果仍回写到 `source_task_id/source_result_id` 对应的主结果上

默认批量范围建议：

- 黄灯
- 红灯
- `needs_reasoning=true`
- `Jarvis` 快通道抽检样本

验收标准：

- 能对一个任务批量生成 OpenClaw draft
- 复判失败不会污染原始正式结果
- 复判结果能通过现有查询接口读到
- OpenClaw 页面不需要跳到结果页逐条点选，也能完成一批任务的复判
- 给 OpenClaw 下发的始终是 Jarvis 的 `task_id`，不是另一套任务编号

停门条件：

- 如果批量复判无法稳定落库或影响主任务结果查询，不进入 P2

## P2-1 结果页升级为对照工作台
目标：把结果页从“单结果审核页”升级成 “Jarvis / OpenClaw / 最终结果” 对照工作台。

范围：

- 结果页增加二审对照信息
- 增加冲突筛选
- 增加结构化诊断展示
- 增加人工采纳入口

建议交付：

- `web/frontend/src/pages/Results/index.tsx`

建议新增展示：

- `Jarvis 原始结果`
- `OpenClaw 建议结果`
- `最终结果`
- `decision_type`
- `error_stage`
- `error_type`
- `retry_query`
- `review_confidence`

建议新增筛选：

- 只看 `Jarvis != OpenClaw`
- 只看 `candidate_pool_insufficient`
- 只看 `retry_search_then_select`
- 只看待人工确认

建议新增操作：

- `采纳 Jarvis`
- `采纳 OpenClaw`
- `人工改判`
- `按建议重搜`

验收标准：

- 管理员能在结果页直接看出 Jarvis 和 OpenClaw 是否冲突
- 管理员能对冲突结果进行最终确认
- 普通用户视图不被破坏

停门条件：

- 如果结果页还不能清楚区分“原结果”和“二审建议”，不进入 P2-2

## P2-2 OpenClaw 管理页升级为二审工作台
目标：把现有 OpenClaw 页面从“建议列表”升级为“复判工作台”。

范围：

- 列表页增加结构化字段
- 支持按二审类型筛选
- 支持批量自动复判
- 提供右侧详情或跳转核对入口

建议交付：

- `web/frontend/src/pages/Admin/OpenClawReviewPage.tsx`

建议新增列：

- `decision_type`
- `error_stage`
- `error_type`
- `retry_query`
- `OpenClaw 置信度`

建议新增批量操作：

- `拉取 Jarvis 已完成任务`
- `批量自动复判`
- `批量通过一致项`
- `只看冲突项`

建议页面主流程：

1. 进入 OpenClaw 页面
2. 看到最近完成的 Jarvis 任务
3. 选择一个任务后直接看到待复判队列
4. 点击“批量自动复判”
5. 只对冲突项和高风险项做人审确认

结果页承担的角色：

- 查看某一条的详细 trace
- 查看 Excel 风格结果明细
- 对极少数复杂条目做最终人工核对

不再承担的角色：

- OpenClaw 的主审核入口
- OpenClaw 的批量作业入口

验收标准：

- 页面可以独立完成“任务选择 -> 批量复判 -> 查看冲突 -> 跳转确认”
- 不需要进入数据库或日志才能看懂 OpenClaw 为什么建议改判

停门条件：

- 如果管理页只能看到备注文本、看不到结构化原因，不进入 P3

## P3-1 人工确认回流结构化反馈
目标：让人工确认后的结果不再只是“经验入库”，而是形成可用于 Jarvis 诊断的结构化反馈。

范围：

- 扩 `_build_feedback_payload()`
- 记录 Jarvis、OpenClaw、人工最终三方结果
- 记录问题发生阶段和类型

建议交付：

- `web/backend/app/api/results.py`

建议补充到反馈 payload 的字段：

- `jarvis_top1_id`
- `openclaw_top1_id`
- `human_final_top1_id`
- `accepted_source`
- `decision_type`
- `error_stage`
- `error_type`
- `retry_query`
- `oracle_in_candidates`
- `feedback_tags`

建议 `accepted_source`：

- `jarvis`
- `openclaw`
- `human_new`

验收标准：

- 正式确认和正式纠正后，反馈 payload 可区分：
  - 召回问题
  - 排序问题
  - 仲裁问题
  - 终检问题
- 反馈 payload 里能看到 `Jarvis/OpenClaw/人工` 三方关系

停门条件：

- 如果经验回流里还看不到结构化错因，不进入 P3-2

## P3-2 反馈统计与最小诊断报表
目标：让这套闭环先能被验证有效，而不是只把字段写进库里。

范围：

- 增加最小统计指标
- 输出冲突率和采纳率
- 输出 `error_stage/error_type` 分布

建议交付：

- 一个最小后台统计接口或脚本
- 管理端可读的简单报表

建议指标：

- `Jarvis/OpenClaw 一致率`
- `OpenClaw 建议采纳率`
- `人工改判率`
- `candidate_pool_insufficient 占比`
- `wrong_rank / wrong_family / wrong_param / wrong_book` 分布

验收标准：

- 至少能按任务看到一份结构化反馈统计
- 至少能证明 OpenClaw 在哪些错因上有价值

停门条件：

- 如果看不到任何可解释指标，不进入 P4

## P4 OpenClaw 审核结果进入 staging
目标：在人工确认基础上，把高价值反馈转成 staging 候选，而不是直接写正式层。

范围：

- 扩 OpenClaw 审核回流到 `audit_errors/promotion_queue`
- 复用现有 `knowledge_promotion_rules.py`
- 只在人工确认后入 staging

建议交付：

- `app/services/openclaw_staging.py`
- `src/knowledge_promotion_rules.py`

约束：

- `OpenClaw` 只写 staging
- staging 之后的正式晋升仍走审核和适配器
- 不允许绕过 staging 直接写 `RuleKnowledge`、`MethodCards`、`ExperienceDB`

验收标准：

- 一条人工确认过的 OpenClaw 改判，可以：
  - 生成 audit error
  - 生成 promotion candidate
  - 进入 staging 查询
- 正式知识层没有被绕过直接写入

停门条件：

- 如果发现 OpenClaw 可以绕开 staging 直接影响正式知识层，立即停止推进并回滚

## 文件落点建议

### 后端

- `web/backend/app/models/result.py`
- `web/backend/app/schemas/result.py`
- `web/backend/app/api/openclaw.py`
- `web/backend/app/api/results.py`
- `web/backend/app/services/openclaw_review_service.py`
- `web/backend/app/services/openclaw_staging.py`

### 前端

- `web/frontend/src/types/index.ts`
- `web/frontend/src/pages/Results/index.tsx`
- `web/frontend/src/pages/Admin/OpenClawReviewPage.tsx`

### 规则 / staging

- `src/knowledge_promotion_rules.py`
- `src/knowledge_staging.py`

## 阶段验证规则
每完成一个阶段，只做该阶段的验证，不提前做下一阶段开发。

建议验证节奏：

1. 代码改完
2. 本地接口/页面冒烟
3. 样例任务人工走一遍
4. 记录问题
5. 通过后再开下一阶段

## 第一阶段执行建议
先从 `P0-1 + P0-2` 开始，只做“数据契约 + draft 结构化”。

本阶段不做：

- OpenClaw 自动复判服务
- 页面复杂交互
- 重搜执行
- staging 晋升扩大化

原因：

- 这一步风险最低
- 这一步决定后续所有接口和页面是否稳定
- 如果契约不先收口，后面做多少功能都会返工

## 通过标准
当且仅当满足以下条件，才进入下一阶段：

- 前后端类型完全对齐
- OpenClaw draft 可结构化读写
- 结果页和 OpenClaw 页都能兼容现有任务数据
- 不改变正式结果的前提下，可以完整表达 OpenClaw 二审结论

## 下一步
按这个顺序推进：

1. `P0-1` 统一结果契约与状态流
2. `P0-2` OpenClaw draft 数据模型升级
3. 验证通过后，再进入 `P1-1`
