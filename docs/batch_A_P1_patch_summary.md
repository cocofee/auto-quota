# 批次 A-P1 Patch Summary

## 改动点

| 文件 | 行号 | 改动 | 影响面 |
|------|------|------|--------|
| `src/orchestration/pipeline_runner.py` | L37-39 | tuple 解构替代 dict 键访问 | 仅影响新分层入口 |
| `src/repository/run_report_repo.py` | L16 | 去掉 `run_id` 参数 | 仅影响新分层入口 |
| `src/learning/method_card_service.py` | L31 | `find_cards` → `find_relevant` | 仅影响新分层入口 |
| `src/contracts.py` | L46 | 兼容 `name` 和 `quota_name` | 契约层输出 |
| `tests/test_pipeline_compatibility.py` | +50行 | 新增5个测试覆盖上述修复 | 无 |

## 回滚方式

每个文件独立可回滚：`git checkout <file>`

## 未改动项

- `main.py` 主入口不变，继续走旧链路
- Excel 输出契约和用户可见字段语义不变
