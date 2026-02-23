# J-Batch6 Patch Summary：E2E 冒烟纳入体检

## 改动点

| 文件 | 位置 | 改动 |
|------|------|------|
| `tools/system_health_check.py` | L193-201 | full 模式新增 `e2e_output_smoke` 检查项 |
| `tools/system_health_check.py` | L226-228 | ci 模式也包含 E2E 输出冒烟检查 |

## 修复前后对比

```
# 修复前（full 模式 5 项检查，不含输出链路）：
[PASS] Python syntax compile
[PASS] Import smoke
[PASS] Pytest regression fixes
[PASS] Pytest all
[PASS] Quota DB schema init
[PASS] Experience health

# 修复后（full 模式 7 项检查，含 E2E 输出冒烟）：
[PASS] Python syntax compile
[PASS] Import smoke
[PASS] Pytest regression fixes
[PASS] Pytest all
[PASS] E2E output writer smoke (merged cell)  ← 新增
[PASS] Quota DB schema init
[PASS] Experience health
```

## 验证

```
python tools/system_health_check.py --mode full
→ Required failures: 0 | Optional failures: 0
→ 7/7 PASS
```

## 回滚

```bash
git checkout tools/system_health_check.py
```
