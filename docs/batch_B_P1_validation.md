# 批次 B-P1 Validation

## 执行命令与结果

```
python tools/system_health_check.py --mode quick
→ 3/3 PASS (syntax, import smoke, regression)

python -m pytest tests/test_experience_validation_guard.py -v
→ 8 passed（含1个从 xfail 转正的测试）
```

## 测试覆盖

| 测试 | 覆盖场景 | 状态变化 |
|------|---------|---------|
| `test_tier_mismatch_rejects` | 方法1拦截：档位不对 | 不变（PASS） |
| `test_tier_match_accepts` | 方法1确认：档位正确 | 不变（PASS） |
| `test_extract_fail_non_exact_falls_to_method2` | 提参失败+非精确 → 方法2兜底 | 不变（PASS） |
| `test_extract_fail_exact_still_runs_method2` | 提参失败+精确 → 方法2兜底 | **xfail → PASS** |
| `test_no_family_falls_to_method2` | 无规则族 → 方法2 | 不变（PASS） |
| `test_empty_quotas_passthrough` | 空定额放行 | 不变（PASS） |
| `test_review_gate_passes_clean_result` | 审核网关正常 | 不变（PASS） |
| `test_review_gate_handles_empty_quotas` | 审核网关空定额 | 不变（PASS） |

## 关键验证点

修复前 `test_extract_fail_exact_still_runs_method2` 标记为 xfail（预期失败），
因为方法2在 `is_exact=True + rule_family_available=True` 时被跳过。

修复后该测试直接通过：方法2正确执行兜底，拦截了参数不一致的经验库结果。

## 未覆盖风险

- 真实定额库环境下的端到端验证（需要实际清单文件）
- 方法2兜底拦截后的用户体验（拦截后是否有足够的日志提示）
