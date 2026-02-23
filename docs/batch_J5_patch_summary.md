# J-Batch5 Patch Summary：统一运行观测口径

## 改动点

| 文件 | 位置 | 改动 |
|------|------|------|
| `tools/jarvis_pipeline.py` | L216 | `score` 改为 `confidence`（使用结果级置信度） |
| `tools/jarvis_pipeline.py` | L227-229 | 新增 `agent_fallback` → "降级" 状态标签 |
| `tools/jarvis_pipeline.py` | L232 | 日志格式：`分数:0.00` → `置信:85` |
| `tools/jarvis_pipeline.py` | L200 | 汇总新增 `fallback_count` 统计 |
| `tools/jarvis_pipeline.py` | L237-240 | 汇总行新增降级占比（如 `降级12(22%)`） |

## 修复前后对比

```
# 修复前（score字段长期为0，fallback标记OK）：
[  1] OK   | DN25镀锌钢管              → C10-1-1 管道安装   | 分数:0.00 | 来源:agent_fallback
汇总: 总50 正确42 自动纠正3 人工3 措施2

# 修复后（置信度真实、降级显式可见）：
[  1] 降级 | DN25镀锌钢管              → C10-1-1 管道安装   | 置信: 72 | 来源:agent_fallback
汇总: 总50 正确42 自动纠正3 人工3 措施2 降级12(24%)
```

## 验证

- 语法检查通过
- 全量测试 83/83 通过

## 回滚

```bash
git checkout tools/jarvis_pipeline.py
```
