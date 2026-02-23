# 批次 B-P1 Findings

## 问题清单

### [P1] 经验校验方法2在精确匹配+提参失败时被跳过

- **位置**: `src/match_core.py:222`
- **严重程度**: P1（准确率影响）
- **原因**:
  方法2的触发条件为 `not rule_validated and (not is_exact or not rule_family_available)`。
  当 `is_exact=True` 且 `rule_family_available=True` 时，即使 `rule_validated=False`
  （因为提参失败，方法1既不能确认也不能否认），方法2也会被跳过。

  这导致参数不一致的经验库结果被漏放（例如：清单7回路，经验库给了4回路定额）。

- **影响范围**: 所有"规则族存在但无法从清单文本提取参数值"的经验库匹配场景
- **复现条件**:
  1. 经验库命中（精确匹配）
  2. 规则族可用（family_index 有对应定额）
  3. `_extract_param_value` 返回 None（提参失败）
  4. 方法1 的 `rule_validated` 保持 False
  5. 方法2 因条件不满足被跳过
  6. 结果直接放行 → 参数不一致的定额进入输出

- **修复**: 简化方法2触发条件为 `not rule_validated`，确保只要方法1未确认，方法2一定执行兜底
