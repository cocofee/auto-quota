# 阶段B Findings：正确性审查

更新时间：2026-02-22  
适用项目：`auto-quota`

## 0. 结论总览

针对你最早提出的 4 个风险点，本轮结论如下：

1. `_build_alternatives` 异常候选 `KeyError`：已修复。  
2. 经验库参数校验“规则族缺失”兜底：大部分已修复，但仍有 1 个边界场景未修（已被 `xfail` 锁定）。  
3. 灯具规则化搜索词：仍存在“强规则硬映射”带来的偏移风险。  
4. `import_history` 仅按 `file_name` 唯一：已修复为按 `file_path` 唯一，且有迁移逻辑。  

---

## 1. Findings（按严重级别）

### [P1] 经验库精确命中在“规则族可用但提参失败”时仍可能漏放错误经验

- 位置：
  - `src/match_core.py:222`
  - `tests/test_experience_validation_guard.py:147`
- 现象：
  - 当前方法2执行条件为：
    - `main_quota_name and not rule_validated and (not is_exact or not rule_family_available)`
  - 当 `is_exact=True` 且 `rule_family_available=True` 但方法1提参失败（`rule_validated=False`）时，方法2被跳过，可能放过参数不一致经验。
- 证据（可复现）：
  - 命令：`python -m pytest -q tests/test_experience_validation_guard.py::test_extract_fail_exact_still_runs_method2 -rxX`
  - 结果：`XFAIL`（测试内已明确标注为“已知问题”）。
- 影响：
  - 经验库“精确匹配”路径可能误放，导致错误定额被高置信直通。
- 建议修复：
  - 以 `rule_validated` 作为是否跳过方法2的唯一依据；当方法1未确认时，方法2必须兜底。
  - 将上述 `xfail` 转正为 `pass` 并加入 CI required 测试集合。

### [P2] 灯具类规则化仍偏硬编码，长尾灯具存在搜索偏移风险

- 位置：
  - `src/query_builder.py:130-272`
- 现象：
  - 只要命中“灯”且未命中排除正则，就进入大量固定映射分支（吸顶灯/壁灯/应急/标志/荧光等）。
  - 其中有较强映射：`src/query_builder.py:179-181` 将 `直管|灯管|线槽灯` 直接改写为 `LED灯带 灯管式`。
- 证据（样例）：
  - 运行 `_normalize_bill_name("线槽灯")` 输出 `LED灯带 灯管式`（存在跨类偏移可能）。
- 影响：
  - 长尾灯具可能被“过早归类”到不合适家族，影响召回与重排质量。
- 建议修复：
  - 为高风险映射增加条件约束（结合描述字段/安装方式/专业册号）。
  - 给灯具归一化增加“置信标签”，低置信时保留原词并并行检索（原词+映射词）。
  - 追加最小回归样例：`线槽灯`、`地脚灯`、`筒灯`、`庭院灯`、`路灯`。

---

## 2. 已确认修复项（与你最初问题对应）

### [已修复] `_build_alternatives` 对异常候选字段缺失已做防护

- 代码证据：
  - `src/match_pipeline.py:192-196` 使用 `alt.get(...)` 并对缺字段候选 `continue` 跳过，不再直接 `alt["quota_id"]`。
- 回归验证：
  - `python -m pytest -q tests/test_regression_fixes.py::test_build_alternatives_skips_invalid_candidates`
  - 结果：`1 passed`

### [已修复] `import_history` 已从 `file_name` 唯一迁移到 `file_path` 唯一

- 代码证据：
  - `src/quota_db.py:99-106`（`UNIQUE(file_path)`）
  - `src/quota_db.py:171-206`（旧 schema 迁移）
- 回归验证：
  - `python -m pytest -q tests/test_regression_fixes.py::test_import_history_schema_migration_sql_is_applied_when_file_path_missing tests/test_regression_fixes.py::test_record_import_and_get_history_use_file_path`
  - 结果：`2 passed`

### [已部分修复] “规则族缺失 + 精确匹配”场景已有兜底

- 代码证据：
  - `src/match_core.py:220-223` 注释和条件显示：规则族不可用时即使 `is_exact=True` 也会执行方法2兜底。
- 回归验证：
  - `python -m pytest -q tests/test_regression_fixes.py::test_validate_experience_params_exact_still_checks_when_family_missing`
  - 结果：`1 passed`
- 备注：
  - 但“规则族可用但提参失败”仍是未闭环边界（见本页 P1）。

---

## 3. 建议给 Claude 的修复批次

1. 批次B1（P1）：修复 `match_core` 方法2触发条件，消除 `xfail`。  
2. 批次B2（P2）：灯具规则加“约束条件+低置信并行检索”，补长尾回归测试。  
3. 批次B3（稳定性）：将阶段B关键测试并入 `tools/system_health_check.py` 的 required 集合。  

---

## 4. 可直接发送给 Claude 的指令

```text
请按 docs/阶段B_正确性findings.md 执行修复，要求：
1) 先处理 P1：修复 src/match_core.py 中方法2触发条件，并把
   tests/test_experience_validation_guard.py::test_extract_fail_exact_still_runs_method2
   从 xfail 改为 pass；
2) 再处理灯具规则偏移：为高风险映射加约束，并补最小回归样例；
3) 每个批次结束提交 validation（命令、结果、未覆盖风险）。
```

