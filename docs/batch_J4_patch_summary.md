# J-Batch4 Patch Summary：自动纠错二次验真门禁

## 改动点

| 文件 | 位置 | 改动 |
|------|------|------|
| `src/review_correctors.py` | L336-370 | 新增 `validate_correction()` 函数 |
| `src/review_correctors.py` | L386-389 | `correct_error()` 返回前调用二次验真 |

## 核心逻辑

```python
def validate_correction(error, corrected_id, corrected_name) -> bool:
    """纠正结果二次验真"""
    # 材质纠错：纠正定额名必须包含正确材质关键词
    if error_type == "material_mismatch":
        should_contain = MATERIAL_MAP[material]["should_contain"]
        if not any(kw in corrected_name for kw in should_contain):
            return False  # 搜到的定额不含正确材质 → 拒绝

    # 连接方式纠错：纠正定额名必须包含正确连接方式
    # 电气配对纠错：纠正定额名必须包含正确配对关键词
    # ...同理
    return True

# correct_error() 中使用：
result = corrector(item, error, dn, province, conn)
if result and not validate_correction(error, result[0], result[1]):
    return None  # 验真不通过 → 转人工
```

## 设计决策

- **只验已有规则的错误类型**：material、connection、electric_pair 有明确的 `should_contain` 规则表，可以做硬校验。category、parameter 等类型的纠正逻辑本身已足够特化，不额外验真。
- **验真不通过 → 返回 None → 自动转人工**：调用方（`jarvis_auto_review.py:162-167`）已有 `if correction: ... else: manual_items.append()`，无需改调用方。

## 验证

- 语法检查通过
- 全量测试 83/83 通过，零退化

## 回滚

```bash
git checkout src/review_correctors.py
```
