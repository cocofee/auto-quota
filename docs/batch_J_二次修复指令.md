# J 批次二次修复指令（复核后）

更新时间：2026-02-22

## 0. 目标

修复本轮复核发现的剩余问题（2个P1 + 2个P2），并通过验收命令。

---

## 1. 问题清单（按优先级）

### P1-1 输出仍会因 MergedCell 崩溃（表头写入未走安全入口）

- 位置：
  - `src/output_writer.py:379` (`_set_header_cell`)
  - `src/output_writer.py:731` (`_add_extra_headers`)
- 现象：
  - `_set_header_cell()` 仍直接 `ws.cell(..., value=...)`，在合并单元格场景继续抛错。
- 复现命令：
  - `python tools/jarvis_pipeline.py "data/reference/北京/北京通州数据中心-1#2#精密空调系统.xlsx" --province "北京市建设工程施工消耗量标准(2024)" --quiet`

### P1-2 LLM 熔断状态会“粘住”（长进程下后续任务可能永久降级）

- 位置：
  - `src/agent_matcher.py:40-42`
  - `src/agent_matcher.py:155`
  - `src/agent_matcher.py:159-163`
- 现象：
  - 熔断打开后只在“成功LLM调用”时重置；若长期网络故障，同进程后续任务可能一直不再尝试LLM。
- 要求：
  - 增加“按任务/按时间窗口自动恢复尝试”的机制（如 cooldown 后半开）。

### P2-1 UniversalKB 仍频繁 `NoneType.encode` 告警

- 位置：
  - `src/universal_kb.py:443` (`search_hints`)
  - `src/universal_kb.py:488` (`self.model.encode`)
  - `src/universal_kb.py:562`（warning）
- 现象：
  - `search_hints()` 未加 `model is None` 快速返回守卫，导致重复 warning。

### P2-2 RuleKnowledge “一次性禁用”在并发下仍重复告警

- 位置：
  - `src/rule_knowledge.py:42`
  - `src/rule_knowledge.py:293`
  - `src/rule_knowledge.py:305-307`
- 现象：
  - 多线程竞争下会重复打“已禁用向量路”告警。
- 要求：
  - 给 `_vector_disabled` 切换加锁，保证只记录一次 warning。

---

## 2. 实施要求

1. 不做大重构，只做最小侵入修复。  
2. 每个问题都要有对应测试（新增或增强）。  
3. 不降低现有通过测试覆盖。  
4. 输出“修复点->测试点”对应关系。

---

## 3. 验收命令（必须全部通过）

1. 单测：
   - `python -m pytest -q tests/test_output_writer_merged_cell.py tests/test_circuit_breaker.py`
2. 全量体检：
   - `python tools/system_health_check.py --mode full`
3. 真实样例回归（原崩溃样例）：
   - `python tools/jarvis_pipeline.py "data/reference/北京/北京通州数据中心-1#2#精密空调系统.xlsx" --province "北京市建设工程施工消耗量标准(2024)" --quiet`
   - 期望：退出码 0，不再出现 `MergedCell` 异常。
4. 日志验收（同一份最新日志）：
   - `Agent大模型调用失败` 明显低于“每条都失败”
   - `[LLM熔断]` 不应在同一轮重复刷屏
   - `通用知识库向量搜索失败` 显著降低（应接近一次性提示）

---

## 4. 直接发 Claude 的文本

```text
请按 docs/batch_J_二次修复指令.md 执行二次修复。
要求：
1) 先修 P1-1（output_writer 表头写入MergedCell崩溃），再修 P1-2（LLM熔断自动恢复）；
2) 然后修 P2-1（UniversalKB model=None 快速返回）和 P2-2（RuleKnowledge 并发下一次性禁用告警）；
3) 每个问题都补测试，并给出“修复点->测试点”映射；
4) 最后贴出4条验收命令及结果，特别是通州样例必须 exit code 0。
```

