# J-Batch3 Patch Summary：规则知识库向量权限异常 fail-fast

## 改动点

| 文件 | 位置 | 改动 |
|------|------|------|
| `src/rule_knowledge.py` | L41-43 | 新增类级 `_vector_disabled` / `_vector_disable_reason` 标志 |
| `src/rule_knowledge.py` | L290-305 | `search_rules()` 向量路加入 fail-fast：权限错误后一次性禁用 |

## 核心逻辑

```python
# 修复前：每次查询都尝试向量路→失败→记日志→再尝试（431次/轮）
# 修复后：首次权限错误 → 设 _vector_disabled=True → 后续直接跳过

if "Permission" in err_str or "Access" in err_str or "denied" in err_str:
    RuleKnowledge._vector_disabled = True
    logger.warning("规则知识库向量检索权限异常，已禁用向量路（仅用关键词兜底）")
```

## 验证

- 语法检查通过
- 全量测试 83/83 通过，零退化
- 关键词兜底路不受影响（`_keyword_search` 路径独立）

## 回滚

```bash
git checkout src/rule_knowledge.py
```
