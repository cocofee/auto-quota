# 2026-04-24 R1-2 candidate pool merge 验收记录

## 修复范围

本轮只修 R1 candidate pool merge，不改 LTR、Picker、CGR、final validator。

落点：
- `HybridSearcher.collect_prior_candidates()` 补齐非标准定额库 book 归一化。
- 同册短名称 / 定额名称 fallback prior。
- 同册相邻编号 neighbor prior。
- search-mode 结果组装前，对已有候选池追加同册相邻编号候选，保证 benchmark 当前实际路径能进入候选池。

## 验证命令

```powershell
python -m pytest tests/test_search_mode_candidate_neighbors.py tests/test_hybrid_searcher_prior_candidates.py tests/test_match_core_context_passthrough.py -q
python -m py_compile config.py src\hybrid_searcher.py src\match_core.py src\match_pipeline\orchestrator.py
python tools/run_benchmark.py --province 浙江省市政 --json-only --profile full --summary-json-out reports\attribution\r1_2_zhejiang_only_summary_after_neighbor.json --latest-result-out reports\attribution\r1_2_zhejiang_only_latest_after_neighbor.json
python tools/export_r1_recall_diagnostics.py --input reports\attribution\r1_2_zhejiang_only_latest_after_neighbor.json --output-csv reports\attribution\r1_2_zhejiang_only_r1_after_neighbor.csv --summary-output reports\attribution\r1_2_zhejiang_only_r1_summary_after_neighbor.json
```

## 聚焦测试

- `tests/test_search_mode_candidate_neighbors.py`
- `tests/test_hybrid_searcher_prior_candidates.py`
- `tests/test_match_core_context_passthrough.py`

结果：`28 passed`

## 浙江市政 full 子集验收

对比基准来自本轮修复前的浙江市政 full 子集：

| 指标 | 修复前 | 修复后 |
|---|---:|---:|
| 题数 | 541 | 541 |
| 命中 | 123 | 129 |
| 命中率 | 22.7% | 23.8% |
| 召回命中率 | 约 54.2% | 69.7% |
| R1 召回未命中 | 248 | 164 |
| R2 LTR 选错 | 117 | 201 |
| R3 CGR 推翻正确 | 1 | 2 |
| R4 Picker 推翻正确 | 52 | 45 |

R1 分桶：

| bucket | 修复前 | 修复后 |
|---|---:|---:|
| `semantic_candidate_pool_miss` | 220 | 154 |
| `thin_candidate_pool` | 14 | 3 |
| `missing_specialty_context` | 109 | 1 |
| `weak_context_manual_review` | 14 | 5 |
| `search_no_result` | 7 | 0 |
| `hard_param_reject` | 2 | 1 |

## 结论

R1-2 candidate pool merge 在浙江市政子集验收通过：R1 从 `248` 降到 `164`，召回命中率提升到 `69.7%`，总命中也从 `123` 提升到 `129`。

R2 明显上升是 R1 被成功转移到 in-pool 排序后的结果，不在本轮修复范围。下一步应进入 R2/LTR 排序治理，不能继续扩大 candidate pool 来掩盖排序问题。

## 未完成项

全量跨省 benchmark 本轮未完整完成：`--province 市政` 在浙江完成后进入福建阶段，因超时中断。进入下一步前如需全量验收，应单独安排长时间运行。
