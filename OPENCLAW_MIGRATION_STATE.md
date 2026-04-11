# OpenClaw 迁移状态

项目：auto-quota
时间：2026-04-10
目的：把仓库里 OpenClaw 已落盘的任务状态、接口能力、测试现状迁移到当前 Hermes 会话，便于继续接手。

## 一句话结论
OpenClaw 相关能力不是空白，已经做到“桥接接口 + 审核草稿 + 人工二次确认 + staging 写入”的闭环；但它不是全自动直写正式知识层，仍保留人工确认边界。

## 已确认存在的核心文件

### 接口/鉴权/模型
- web/backend/app/api/openclaw.py
- web/backend/app/auth/openclaw.py
- web/backend/app/models/openclaw_review_job.py
- web/backend/app/schemas/openclaw_review_job.py

### 服务
- web/backend/app/services/openclaw_review_service.py
- web/backend/app/services/openclaw_staging.py

### 测试
- tests/test_web_openclaw.py
- tests/test_openclaw_staging.py
- tests/test_openclaw_review_policy_regressions.py

### 文档/产物
- docs/openclaw接入说明.md
- docs/qmd-openclaw-jarvis-api.md
- tmp_openclaw_review_draft.json
- tmp_openclaw_review_confirm.json
- output/temp/openclaw_reports_batch1.md
- test_artifacts/openclaw_staging_runtime.db

## 当前真实能力

### 1. 接口桥接已存在
OpenClaw bridge API 在 `web/backend/app/api/openclaw.py`。
文档确认支持：
- health
- provinces
- quota-search / smart search
- tasks 创建与查询
- review-draft
- review-confirm
- promotion-cards
- auto-confirm-green
- export

### 2. 鉴权方案已存在
`web/backend/app/auth/openclaw.py` 里使用 `X-OpenClaw-Key`。
还会自动创建/复用一个 OpenClaw service user。
读接口支持两种方式：
- OpenClaw Key
- 管理员登录态

注意：代码里存在 `HARDCODED_OPENCLAW_KEYS`，这是一个需要后续留意的安全点。

### 3. 审核上下文构造已存在
`web/backend/app/services/openclaw_review_service.py`
作用不是调模型，而是把 Jarvis task/result 规范化成稳定 review payload。
已抽取的信息包括：
- task/result 基本信息
- quota/candidate pool
- trace steps
- reasoning summary
- final validation
- query_route
- batch_context
- 各阶段 top1 变化链
- QMD recall

说明：OpenClaw 的审核上下文层已经有比较完整的“诊断素材”。

### 4. 人工确认后写 staging 的链路已存在
`web/backend/app/services/openclaw_staging.py`
核心函数：`record_openclaw_approved_review(...)`
已确认行为：
- 创建 audit_error
- 生成 promotion candidates
- 可按层入队：RuleKnowledge / MethodCards / ExperienceDB
- 异步封装里即使失败也不阻塞主确认流（best-effort）

这说明 OpenClaw 已经不是“只给建议”，而是能在人工确认后把结果同步到 staging。

## 从测试里确认到的状态

### tests/test_openclaw_staging.py
已覆盖：
- 审核确认后写入 audit_error
- 对 search 来源可排队 rule/method/experience 三层候选
- 对 polluted experience 类问题，不再错误地推送 rule/method/experience

### tests/test_openclaw_review_policy_regressions.py
已覆盖：
- 对象不一致时，优先从 candidate pool 中找更合理候选
- candidate pool 不够好时，可以借助 smart search 找更合理候选
- 审核应优先对象一致性，而不是盲信原候选池

### tests/test_web_openclaw.py
文件较大，但已确认覆盖：
- file intake 低置信进入 waiting_human
- 人工确认后可继续 parse
- OpenClaw 相关 API 与 review 流程测试存在

## 从文档/临时产物确认到的业务状态

### docs/openclaw接入说明.md
文档明确：
- 真实链路是 “review-draft -> 管理员 review-confirm -> staging”
- `review-confirm` 真实契约只接受 approve / reject
- 已做过真实联调和正式落库验证
- 当前仍不是 OpenClaw 单独全自动入正式知识层

### tmp_openclaw_review_draft.json
有一个本机 3210 联调 draft 样例：
- decision_type = agree
- confidence = 95
- reason_codes = [local_3210_smoke_test]

### tmp_openclaw_review_confirm.json
有一个 confirm 样例：
- decision = approve

### output/temp/openclaw_reports_batch1.md
这是很重要的历史任务痕迹，显示过去做过大量批次审核分析。
从报告能提炼出这些已知方向：
- 旧规则下自动确认门槛过低
- OpenClaw 的“自动纠正”不可靠，容易越纠越错
- 拆除类、跨库、弱电/智能化、市政库错配是高频问题
- 曾经已有修复方向：
  - 提高自动确认门槛
  - 禁止自动纠正
  - 跨库搜索
  - 清理污染经验库
  - 重启 local_match_server 让新同义词/搜索策略生效

## 当前接管判断

### 已能接手的部分
- OpenClaw 接口能力梳理
- 审核/确认/staging 代码继续维护
- 测试继续补与回归
- 基于现有临时产物恢复业务背景

### 不能直接恢复的部分
- OpenClaw 那边未落盘的上下文推理
- 另一个会话窗口里的即时状态
- 若有外部平台 UI 上未保存的数据，这里看不到

## 建议的后续处理方式
1. 把这份文件作为当前会话的 OpenClaw 接管基线
2. 后续若继续做 OpenClaw 相关任务，优先读：
   - docs/openclaw接入说明.md
   - output/temp/openclaw_reports_batch1.md
   - tests/test_openclaw_staging.py
   - tests/test_openclaw_review_policy_regressions.py
3. 当前主任务仍是 fastpath/ambiguity 风险优先抽检；OpenClaw 任务状态已并入，但不应打断主线，除非用户切换优先级
