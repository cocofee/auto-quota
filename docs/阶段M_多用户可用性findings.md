# 阶段M Findings：多用户可用性审查

更新时间：2026-02-23  
适用项目：`auto-quota`

## 0. 审查范围与结论

本轮聚焦“是否可稳定支撑多用户并发”：

1. 并发运行时的输出文件/日志隔离
2. 省份上下文隔离（请求A不污染请求B）
3. 运行级熔断状态隔离（LLM、规则向量检索）
4. SQLite写入并发稳定性（经验库、运行记录）

结论：当前系统在“单用户 CLI”可用，但进入“多用户/服务化”后存在 3 个 P1 隔离问题（输出覆盖、省份串扰、熔断串扰），需要先修再扩展为多人使用。

---

## 1. 关键复现记录

1. 基线体检（quick）通过  
命令：`python tools/system_health_check.py --mode quick`  
结果：required failure = 0。

2. 并发跑 3 个流水线（同输入，无 `--store`）  
输入：`output/concurrency_input.xlsx`  
结果：
- 3 个进程均 `rc=0`
- 仅生成 1 个输出文件：`匹配结果_concurrency_input_20260223_073414.xlsx`
- 仅生成 1 个日志文件：`jarvis_concurrency_input_20260223_073414.log`
- 三个进程 stdout 都指向同一路径（见 `output/concurrency_run_ascii_1.out.txt`、`output/concurrency_run_ascii_2.out.txt`、`output/concurrency_run_ascii_3.out.txt`）

3. 并发跑 3 个流水线（同输入，开启 `--store`）  
输入：`output/concurrency_input_corr.xlsx`  
结果：
- 3 个进程均 `rc=0`
- 仅生成 1 套输出：`匹配结果_concurrency_input_corr_20260223_073609.xlsx` + `_已审核.xlsx`
- 仅生成 1 个日志：`jarvis_concurrency_input_corr_20260223_073609.log`
- 三个进程 stdout 均写同一路径（见 `output/concurrency_store_run_1.out.txt`、`output/concurrency_store_run_2.out.txt`、`output/concurrency_store_run_3.out.txt`）

4. 省份上下文串扰复现（线程并发）  
实验脚本结果：`rows= [('task_B', 'PROV_B'), ('task_A', 'PROV_B')]`  
说明：`task_A` 预期 `PROV_A`，实际被写成 `PROV_B`，发生跨请求污染。

5. LLM 熔断跨请求串扰复现  
实验脚本结果：
- `m2_called_llm= False`
- `m2_match_source= agent_fallback`
- `circuit_open= True`

说明：请求1触发熔断后，请求2即使本身可用也被全局熔断拦截。

---

## 2. Findings（按严重级别）

### [P1] 输出/日志/中间文件命名只到秒级，导致并发覆盖

- 位置：
  - `tools/jarvis_pipeline.py:56`
  - `tools/jarvis_pipeline.py:62`
  - `tools/jarvis_pipeline.py:98`
  - `tools/jarvis_pipeline.py:128`
  - `tools/jarvis_pipeline.py:186`
- 问题：
  - 文件名由 `stem + %Y%m%d_%H%M%S` 组成，同秒同文件名会冲突。
  - 并发运行时多个进程写到同一路径，最后保留“最后写入者”的结果。
- 影响：
  - 输出覆盖、日志混写、审计不可追踪。

### [P1] 全局 `_runtime_province` + 隐式默认省份导致请求串扰

- 位置：
  - `config.py:45`
  - `config.py:48`
  - `config.py:54`
  - `main.py:94`
  - `src/learning_notebook.py:199`
- 问题：
  - `main.run()` 每次调用都会写全局 `set_current_province()`。
  - 若下游调用未显式传 `province`，会读到“最近一次请求”的省份。
  - `LearningNotebook.record_note()` 默认 `config.get_current_province()`，已复现错写省份。
- 影响：
  - 经验/笔记数据被错误归属到其他省份，后续检索和评估被污染。

### [P1] LLM 熔断器是类级共享状态，跨请求互相影响

- 位置：
  - `src/agent_matcher.py:40`
  - `src/agent_matcher.py:41`
  - `src/agent_matcher.py:47`
  - `src/agent_matcher.py:179`
  - `src/match_engine.py:476`
- 问题：
  - 熔断计数和开关是 `AgentMatcher` 类变量，不区分请求。
  - 一个请求连续失败后，其他请求会被迫走 fallback。
  - 新请求调用 `reset_circuit_breaker()` 还会重置其他请求状态。
- 影响：
  - 多用户下稳定性不可预测，成功率和时延被互相拖累。

### [P2] 规则向量路禁用标志为类级全局，故障会“全局扩散”

- 位置：
  - `src/rule_knowledge.py:43`
  - `src/rule_knowledge.py:45`
  - `src/rule_knowledge.py:295`
  - `src/rule_knowledge.py:309`
- 问题：
  - `_vector_disabled` 是类级变量，一次权限异常会影响该进程所有后续请求。
- 影响：
  - 单次环境抖动可导致全体用户长期降级。

---

## 3. 已验证的正向结论

1. 经验库并发写入去重逻辑有效：  
在 3 并发 `--store` 压测中，三次都写到同一 `record_id=12843`，未出现重复插入。

2. 运行记录 SQLite 在高并发写下未出现丢记录：  
20 线程 * 40 次写入压力测试，`expected=800 actual=800`。

说明：当前主要瓶颈不是“库写挂掉”，而是“请求隔离和文件命名冲突”。

---

## 4. 建议修复批次（给 Claude）

1. `M-Batch1 (P1)`：引入 `run_id`（毫秒时间戳 + 短 UUID），替换流水线所有输出/日志/temp/corrections 命名。
2. `M-Batch2 (P1)`：移除全局 `set_current_province` 依赖，改为 request-scoped province 显式传参；清理 `config.get_current_province()` 隐式兜底热点。
3. `M-Batch3 (P1)`：将 `AgentMatcher` 熔断状态改为实例级或 request-context 级；删除跨请求共享计数。
4. `M-Batch4 (P2)`：将 `RuleKnowledge._vector_disabled` 改为实例级（至少 province 级）并增加超时恢复策略。
5. `M-Batch5 (P2)`：新增多用户冒烟测试（并发 3~5 进程）纳入 `system_health_check --mode full` 可选项。

---

## 5. 可直接发给 Claude 的指令

```text
请按 docs/阶段M_多用户可用性findings.md 修复多用户问题，顺序必须是 M-Batch1 -> M-Batch2 -> M-Batch3 -> M-Batch4 -> M-Batch5。
每批次要求：
1) 先给最小改动计划；
2) 再打补丁；
3) 跑对应回归（至少包含并发复现用例）；
4) 输出变更文件、风险余项和回滚点。
不要跨批次混改。
```

