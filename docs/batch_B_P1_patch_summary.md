# 批次 B-P1 Patch Summary

## 改动点

| 文件 | 行号 | 改动 | 影响面 |
|------|------|------|--------|
| `src/match_core.py` | L222 | 方法2触发条件简化：去掉 `(not is_exact or not rule_family_available)` | 经验库校验路径 |
| `tests/test_experience_validation_guard.py` | L147-187 | xfail 转正式断言，验证方法2兜底拦截 | 无 |

## 具体改动

### src/match_core.py L222

```python
# 修复前：
if main_quota_name and not rule_validated and (not is_exact or not rule_family_available):

# 修复后：
if main_quota_name and not rule_validated:
```

**逻辑**：只要方法1未确认（`rule_validated=False`），方法2一定执行兜底检查，
无论是否精确匹配、规则族是否可用。这样即使提参失败导致方法1无法判断，
方法2的参数解析对比仍然可以拦截参数不一致的经验库结果。

### tests/test_experience_validation_guard.py

- 移除 `import pytest` 和 `@pytest.mark.xfail` 标记
- `test_extract_fail_exact_still_runs_method2` 从预期失败改为直接断言 `result is None`

## 回滚方式

单文件回滚：`git checkout src/match_core.py`

## 未改动项

- `main.py` 主入口不变
- Excel 输出契约和用户可见字段语义不变
- 方法1（规则族校验）逻辑不变
- 方法2（参数解析对比）内部逻辑不变，只是触发条件放宽
