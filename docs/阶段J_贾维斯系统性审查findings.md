# 阶段J Findings：贾维斯系统性审查

更新时间：2026-02-22  
适用项目：`auto-quota`

## 0. 审查范围与结论

本轮按“整体系统”而非单一清单审查，覆盖：

1. 端到端流水线稳定性（真实清单运行）  
2. 降级链路与容错（向量/重排/LLM不可用时）  
3. 自动审核纠错质量门禁  
4. 体检脚本覆盖盲区与可运维性

结论：当前系统在“环境正常”时可跑通，但在网络/权限异常场景下存在多处结构性放大问题；且有一处确定性崩溃（MergedCell写入）会直接中断整次任务。

---

## 1. 关键复现记录

1. 系统体检（full）  
命令：`python tools/system_health_check.py --mode full`  
结果：全部 PASS（required/optional 都为 0 fail）。

2. 真实样例端到端复现（失败）  
命令：`python tools/jarvis_pipeline.py "data/reference/北京/北京通州数据中心-1#2#精密空调系统.xlsx" --province "北京市建设工程施工消耗量标准(2024)" --quiet`  
结果：退出码 1，报错 `AttributeError: 'MergedCell' object attribute 'value' is read-only`。

3. 历史大样本运行日志（成功但重度降级）  
日志：`output/logs/jarvis_丰台区城中村改造张仪村路东侧安置房项目总包清单（安装部分)_20260222_220837.log`  
关键计数：`WinError 10013=1078`、`Agent大模型调用失败=1066`、`NoneType.encode=6479`、`WARNING=7941`。

---

## 2. Findings（按严重级别）

### [P1] 保结构回写遇到合并单元格会崩溃，导致整单失败

- 位置：
  - `src/output_writer.py:597`
  - `src/output_writer.py:608`
  - `src/output_writer.py:624`
  - `src/output_writer.py:289`
- 现象：
  - `_write_bill_extra_info()` 直接对 J~O 列写值；
  - 当目标格是 `MergedCell`（非左上主格）时，openpyxl 禁止写入并抛异常。
- 复现证据：
  - 见“关键复现记录 #2”的命令与堆栈，最终落点 `src/output_writer.py:608`。
- 影响：
  - 该 sheet 后续全部中断，整份输出失败。
- 修复建议：
  - 写入前统一做“可写单元格解析”（若是 merged 区域，重定向到左上主格或跳过）；
  - J/K/L/M/N/O 都走同一安全写入入口；
  - 增加回归测试：包含合并列的原始清单模板。

### [P1] 向量不可用后缺少全局短路，导致异常风暴与性能劣化

- 位置：
  - `src/model_cache.py:67`
  - `src/model_cache.py:74`
  - `src/vector_engine.py:163`
  - `src/vector_engine.py:198`
  - `src/experience_db.py:741`
  - `src/universal_kb.py:364`
  - `src/hybrid_searcher.py:156`
  - `src/hybrid_searcher.py:183`
- 现象：
  - `ModelCache` 冷却期返回 `None`，但多条链路直接 `self.model.encode(...)`；
  - 异常由每条清单、每个 query variant 重复触发并记录 warning。
- 证据：
  - 日志 `...220837.log` 中 `NoneType' object has no attribute 'encode'` 达 6479 次。
- 影响：
  - 日志噪声巨大、吞吐下降，问题定位成本高；降级链路虽然能跑，但运行代价显著上升。
- 修复建议：
  - 引入 run-level `vector_unavailable` 熔断标志（首次失败后本轮直接跳过向量分支）；
  - 将 warning 改为“首次 warning + 汇总计数”。

### [P1] LLM链路缺少故障熔断，且低置信重试会放大失败成本

- 位置：
  - `src/agent_matcher.py:387`
  - `src/agent_matcher.py:404`
  - `src/match_engine.py:614`
  - `src/match_engine.py:618`
  - `src/match_engine.py:621`
- 现象：
  - LLM请求失败后没有“连续失败熔断”，每条继续请求；
  - 同时低置信策略会触发“全库重试搜索 + 再次LLM”。
- 证据：
  - 日志 `...224932.log`：`Agent大模型调用失败=36`（36条全失败），仍触发 `全库重试搜索=20` 次。
- 影响：
  - 在网络异常期，系统会主动放大 CPU/IO 消耗，拉长总体时延。
- 修复建议：
  - 增加 LLM failure circuit breaker（如连续 N 次失败后本轮禁用 LLM）；
  - 熔断状态下跳过低置信全库重试，直接走 deterministic fallback。

### [P2] 规则知识库向量检索在权限异常时重复失败，缺少一次性降级

- 位置：
  - `src/rule_knowledge.py:288`
  - `src/rule_knowledge.py:297`
  - `src/rule_knowledge.py:321`
- 现象：
  - `_vector_search()` 使用 `query_texts`，在当前环境触发 onnx 模型缓存权限错误；
  - `search_rules()` 每次查询都再尝试一次向量路，再失败，再记日志。
- 证据：
  - 日志 `...220837.log` 中 `Permission denied` 431 次。
- 影响：
  - 虽有关键词路兜底，但重复失败造成无效开销和噪声。
- 修复建议：
  - 对该类权限错误设置 `rule_vector_disabled=True`（进程级/本轮级）；
  - 启动时显式检查并提示缓存目录可写性。

### [P2] 自动纠错“命中即采用”缺少二次校验，存在语义漂移风险

- 位置：
  - `src/review_correctors.py:23`
  - `src/review_correctors.py:29`
  - `src/review_correctors.py:101`
  - `src/review_correctors.py:117`
  - `tools/jarvis_auto_review.py:146`
  - `tools/jarvis_auto_review.py:162`
  - `src/quota_search.py:53`
  - `src/quota_search.py:75`
- 现象：
  - 多个纠错分支都是 `search_quota_db(...)` 后直接取第 1 条；
  - `_correct_phase()` 只要返回即计入自动纠错，缺少“参数一致性二次验真”。
- 影响：
  - 在关键词泛化/章节重叠场景，可能把错误纠错结果直接写回“已审核结果”。
- 修复建议：
  - 自动纠错前增加强校验门禁（材质/连接/参数至少满足 N 项）；
  - 未达标改为人工项，不自动落盘。

### [P2] 日志观测口径失真：fallback 被标记 OK，分数字段长期为 0

- 位置：
  - `tools/jarvis_pipeline.py:223`
  - `tools/jarvis_pipeline.py:225`
  - `tools/jarvis_pipeline.py:228`
- 现象：
  - 分数取 `main_q.get("score", 0)`，多数结果没有 `score` 字段，日志固定 0.00；
  - `agent_fallback` 只要有 quota 就标记 `OK`，掩盖降级程度。
- 证据：
  - 日志 `...220837.log` 末尾大量 `来源:agent_fallback` 且 `状态:OK`、`分数:0.00`。
- 影响：
  - 运营侧无法从日志快速识别“成功但重度降级”的运行批次。
- 修复建议：
  - 分数字段改为 `confidence`（结果级）或显式 `fallback_score`；
  - 状态增加 `降级`/`fallback` 标签并汇总占比。

### [P2] 现有体检脚本未覆盖端到端输出链路，出现“体检全绿但实跑崩溃”

- 位置：
  - `tools/system_health_check.py:148`
  - `tools/system_health_check.py:183`
  - `tools/system_health_check.py:216`
- 现象：
  - `full` 仅包含语法、import、pytest、DB init、experience health；
  - 不含最小 E2E（`jarvis_pipeline` + `output_writer`）回归。
- 证据：
  - `full` 检查 PASS（见“关键复现记录 #1”），但真实样例直接崩溃（见 #2）。
- 影响：
  - CI/本地体检对真实生产风险感知不足。
- 修复建议：
  - 新增 `health-e2e-smoke`：用小样例跑一条完整流水线并验证输出文件可打开。

---

## 3. 建议修复批次（给 Claude）

1. `J-Batch1 (P1)`：修复 merged-cell 写入崩溃，补 `output_writer` 回归测试。  
2. `J-Batch2 (P1)`：建立向量/重排/LLM 三路熔断与一次性降级日志。  
3. `J-Batch3 (P2)`：规则知识库向量权限异常 fail-fast + 缓存目录可写性检测。  
4. `J-Batch4 (P2)`：自动纠错增加二次验真门禁，未达标转人工。  
5. `J-Batch5 (P2)`：统一运行观测口径（fallback 状态、score 字段、降级占比）。  
6. `J-Batch6 (P2)`：将最小 E2E 冒烟纳入 `system_health_check --mode full`。

---

## 4. 可直接发给 Claude 的指令

```text
请按 docs/阶段J_贾维斯系统性审查findings.md 执行修复，顺序必须是：
1) 先做 J-Batch1/J-Batch2（P1），每批都补可复现测试并回传命令+结果；
2) 再做 J-Batch3~J-Batch6（P2），保证 system_health_check full 能覆盖最小E2E；
3) 输出每批次的风险余项与回滚点，不要跨批次混改。
```
