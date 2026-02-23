# 批次 A-P1 Findings

## 问题清单

### [P1] PipelineRunner 与 init_search_components 返回契约不一致
- **位置**: `src/orchestration/pipeline_runner.py:37-39` (已修复)
- **原因**: `init_search_components()` 返回 `tuple(searcher, validator)`，但 PipelineRunner 按 dict 键取值
- **修复**: 改为 tuple 解构赋值

### [P1] RunReportRepository 传无效参数给 AccuracyTracker
- **位置**: `src/repository/run_report_repo.py:16` (已修复)
- **原因**: `AccuracyTracker` 不接受 `run_id` 参数
- **修复**: 去掉无效参数

### [P1] MethodCardService 调用不存在的方法名
- **位置**: `src/learning/method_card_service.py:31` (已修复)
- **原因**: 调用 `find_cards()` 但底层方法叫 `find_relevant()`
- **修复**: 改为正确方法名

### [P1] MatchResult.to_legacy_dict 字段名与核心流水线不兼容
- **位置**: `src/contracts.py:45` (已修复)
- **原因**: 核心流水线用 `name` 字段，契约层只读 `quota_name`
- **修复**: 兼容读取 `quota_name` 和 `name`，quota_name 优先
