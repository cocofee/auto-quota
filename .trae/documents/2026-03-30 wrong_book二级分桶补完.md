# 2026-03-30 wrong_book 二级分桶补完

## 本轮动作

- 给 `tools/classify_wrong_book.py` 增加了两个证据视角：
  - `router_search_books`
  - `resolved_main_books`
- 把原来的 `out_of_scope_leakage` 拆成：
  - `true_out_of_scope_leakage`
  - `borrow_scope_pollution`

## 判定口径

- `true_out_of_scope_leakage`
  - `selected_book` 不在 `router_search_books` 内
  - 说明错误书已经超出 router 明确声明的搜索范围
- `borrow_scope_pollution`
  - `selected_book` 在 `router_search_books` 内
  - 但不在 `resolved_main_books` 内
  - 说明 planner/router 允许借书，但 main resolved scope 没有真正把这本书收进主范围

## 验证

- `pytest tests/test_classify_wrong_book.py tests/test_classify_wrong_book_output.py -q`
  - `10 passed`

## 新分布

### route_scope_guard_smoke_20260330

- `wrong_book_total = 7`
- `primary_bucket_counts`
  - `in_scope_recall_miss = 5`
  - `open_search_drift = 1`
  - `rank_wrong_book = 1`
- `secondary_bucket_counts`
  - `true_out_of_scope_leakage = 2`
  - `same_scope_wrong_family = 2`
  - `borrow_scope_pollution = 1`
- `tertiary_bucket_counts`
  - `resolved_scope_drift = 1`
  - `candidate_merge_leakage = 1`

### cross5_smoke_20260329

- `wrong_book_total = 8`
- `primary_bucket_counts`
  - `in_scope_recall_miss = 6`
  - `routing_scope_miss = 1`
  - `rank_wrong_book = 1`
- `secondary_bucket_counts`
  - `true_out_of_scope_leakage = 3`
  - `same_scope_wrong_family = 2`
  - `borrow_scope_pollution = 1`
- `tertiary_bucket_counts`
  - `candidate_merge_leakage = 2`
  - `resolved_scope_drift = 1`

## 结论

- 之前混在一起的 `out_of_scope_leakage` 里，确实有一部分是 borrow scope 污染，但不是主量。
- 当前 `wrong_book` 剩余问题仍以两类为主：
  - 真正的 scope 外泄漏
  - 同 scope 内的 wrong family / query drift
- `borrow_scope_pollution` 已经被单独识别，可以避免误把所有 leakage 都归因到 strict guard。
- `true_out_of_scope_leakage` 继续拆开后，可以直接对应两个代码口子：
  - `resolved_scope_drift`
    - 典型样本：`exp:371216`
    - 现象：`resolved_main_books` 本身已经扩到 router scope 外
    - 首查口：`src/match_core.py:_resolve_search_books_for_target(...)`
  - `candidate_merge_leakage`
    - 典型样本：`exp:371759`、`exp:447768`
    - 现象：`selected_book` 不在 router scope，也不在 resolved_main_books，但进了最终 candidate pool
    - 首查口：`src/match_core.py:_merge_with_aux(...)`、prior merge、非标准册 aux 汇总链

## 下一步

- 不先扩 moderate 路由护栏。
- `wrong_book` 这条线还剩两刀：
  - 第 1 刀：收 `resolved_scope_drift`
  - 第 2 刀：收 `candidate_merge_leakage`
- 这两刀做完，`wrong_book` 主链诊断基本可以收尾，下一主线再切回 `same_scope_wrong_family`。

## 2026-03-30 收尾验证

### 本轮代码修复

- `resolved_scope_drift`
  - [match_core.py](C:/Users/Administrator/Documents/trae_projects/auto-quota/src/match_core.py)
  - 主库非标准册解析在已有明确请求册别时，不再用 `classify_to_books` 扩到 router scope 外
- `candidate_merge_leakage`
  - [match_core.py](C:/Users/Administrator/Documents/trae_projects/auto-quota/src/match_core.py)
  - 候选汇总后新增 effective guard：
    - 允许范围 = `router search_books + resolved_main_books`
    - main `open/escape` 路径不启用这道 guard
  - [match_pipeline.py](C:/Users/Administrator/Documents/trae_projects/auto-quota/src/match_pipeline.py)
    - retriever trace 新增 `candidate_scope_guard`

### 验证

- 单测：
  - `pytest tests/test_match_core_broad_route_scope.py tests/test_retrieval_resolution_trace.py tests/test_candidate_reasoning_trace.py tests/test_hybrid_searcher_nonstandard_books.py -q`
  - `33 passed`

- 三省 smoke：
  - 数据集：`output/real_eval/real_eval_smoke_install_only.jsonl`
  - 口径：上海 / 广东 / 浙江，各 `10` 条
  - 输出：
    - `output/real_eval/wrong_book_tailfix_smoke10_20260330.summary.json`
    - `output/real_eval/wrong_book_tailfix_smoke10_20260330.details.jsonl`
    - `output/real_eval/wrong_book_tailfix_smoke10_20260330.wrong_book.summary.json`

### 结果

- 命中率：
  - `43.3%` (`13/30`)
- 诊断分布：
  - `wrong_book = 1`
  - `synonym_gap = 8`
  - `wrong_tier = 7`
  - `no_result = 1`
- `wrong_book` 细分：
  - 仅剩 `open_search_drift = 1`
  - 不再有：
    - `true_out_of_scope_leakage`
    - `borrow_scope_pollution`
    - `rank_wrong_book`

## 当前结论

- `wrong_book` 主链问题已基本收尾。
- 当前主病灶已切换为：
  - `synonym_gap`
  - `wrong_tier`
- 下一主线应回到 `same_scope_wrong_family / query 主体抽取`，不再继续围着 `wrong_book` 扩修。
