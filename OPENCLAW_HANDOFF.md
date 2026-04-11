# OpenClaw 交接说明

项目：auto-quota
路径：/mnt/c/Users/Administrator/Documents/trae_projects/auto-quota
当前任务：为 Agent fastpath / ambiguity gate 增加“风险优先抽检”与诊断字段，采用低风险最小改动方案。

## 当前待办
- b1 [in_progress] 阅读相关测试与现有 fastpath/歧义门控实现
- b2 [pending] 先写或补 failing tests 覆盖风险优先抽检与诊断字段
- b3 [pending] 实现低风险代码改造
- b4 [pending] 运行相关测试并修正问题

## 已确认的代码位置

### 1) 歧义门控核心
文件：src/ambiguity_gate.py
核心函数：analyze_ambiguity(candidates, exp_backup=None, rule_backup=None, route_profile=None, arbitration=None)
返回类型：AmbiguityDecision dataclass

当前字段：
- can_fastpath
- is_ambiguous
- reason
- top_quota_id
- top_param_score
- top_score_gap
- candidates_count
- conflict_with_backup
- route
- require_final_review
- risk_level
- arbitration_applied

结论：这里最适合新增诊断字段，比如：
- audit_recommended: bool
- audit_reasons: list[str] / tuple[str, ...]

理由：match_pipeline 已经把 analyze_ambiguity(...).as_dict() 透传为 reasoning_decision，改 dataclass 后诊断字段能自然进入结果。

### 2) fastpath 跳过 Agent 的入口
文件：src/match_core.py
相关函数：
- _should_skip_agent_llm(...)
- _should_audit_fastpath()
- _mark_agent_fastpath(result)

现状：
- _should_skip_agent_llm(...) 已改为基于 analyze_ambiguity(...) 决定是否 fastpath
- _should_audit_fastpath() 目前只是按 config.AGENT_FASTPATH_AUDIT_RATE 随机抽检，不知道“边界风险”

结论：低风险改法是让 _should_audit_fastpath() 支持接收 ambiguity decision：
- 如果 decision.audit_recommended == True，则强制抽检
- 否则按原随机比例抽检

### 3) reasoning_decision 的构造点
文件：src/match_pipeline.py
位置：
- 约 2520 行：统一排序启用后，会重新 analyze_ambiguity(...).as_dict()
- 约 3054 行：搜索结果构造时，会 analyze_ambiguity(...).as_dict()

结论：这里只要 ambiguity decision 增加字段，pipeline 基本不用大改。

### 4) 配置项已存在
文件：config.py
相关项：
- AGENT_FASTPATH_ENABLED
- AGENT_FASTPATH_SCORE
- AGENT_FASTPATH_SCORE_GAP
- AGENT_FASTPATH_AUDIT_RATE
- AGENT_FASTPATH_REQUIRE_PARAM_MATCH

文件：src/policy_engine.py
- get_route_policy() 会按 route 返回 agent_fastpath_score / score_gap / min_candidates / require_param_match
- installation_spec / material / ambiguous_short / semantic_description 有各自更严格或更宽松阈值

## 建议的实现方案（推荐）

### 先测后改
新增 tests/test_ambiguity_gate.py，至少覆盖：
1. 高置信 fastpath 正常放行，且 audit_recommended=False
2. top_param_score 刚好过线但接近阈值 -> can_fastpath=True 且 audit_recommended=True
3. top1/top2 gap 刚好过线但接近阈值 -> can_fastpath=True 且 audit_recommended=True
4. arbitration_applied=True 且虽然可放行 -> require_final_review=True，risk_level 至少 medium，建议 audit_recommended=True
5. _should_audit_fastpath(decision) 在 AGENT_FASTPATH_AUDIT_RATE=0 时：
   - 若 audit_recommended=True，仍返回 True
   - 若 audit_recommended=False，返回 False

### 最小代码改造
1. src/ambiguity_gate.py
   - 扩展 AmbiguityDecision 字段：audit_recommended, audit_reasons
   - 在可 fastpath 的返回分支（accept_head_confident / high_confidence）里，基于“临界边界”补充审计建议
   - 候选规则可尽量简单：
     - top_param_score - policy.agent_fastpath_score <= 某个小 margin
     - gap - policy.agent_fastpath_score_gap <= 某个小 margin
     - arbitration_applied 为 True
   - margin 优先复用 config.AGENT_FASTPATH_MARGIN

2. src/match_core.py
   - _should_audit_fastpath(decision=None) 新增可选参数
   - 逻辑：
     - decision.audit_recommended -> True
     - 否则保持原随机逻辑

3. fastpath 命中的地方把 analyze_ambiguity 的 decision 传给抽检判断
   - 注意不要重复计算太多次；如已有 decision 就复用

## 已读到的关键代码片段

### src/match_core.py
- _should_skip_agent_llm(...)：当前通过 analyze_ambiguity(...) 返回 decision.can_fastpath
- _should_audit_fastpath()：当前仅按 AGENT_FASTPATH_AUDIT_RATE 随机

### src/ambiguity_gate.py
- analyze_ambiguity(...) 当前已处理：
  - fastpath disabled
  - no candidates
  - param mismatch
  - reranker failed
  - backup conflict
  - hard conflict
  - accept head confident / reject
  - low param score
  - missing primary param
  - insufficient candidates
  - small score gap
  - arbitrated small gap
  - high_confidence

## 注意事项
- 用户偏好：非技术背景，解释要简单；不要先改代码再解释方案并获确认
- 所以如果 OpenClaw 接着做，先简要说明“先补测试，再做最小改造”再动手
- 尽量别大改 match_engine 主流程，优先在 ambiguity_gate + match_core 局部改

## 下一步建议
1. 搜 tests 目录里是否已有与 fastpath / ambiguity 相关测试，可复用风格
2. 先补 failing tests
3. 再实现 audit_recommended 透传与强制抽检
4. 跑定向测试
