# 2026-03-29 wrong_book 实证 trace 补点

## 这一步做了什么

没有继续改路由行为，只补了检索阶段的实证 trace。

- `src/match_core.py`
  - 在 `cascade_search(...)` 内记录每一次实际检索调用
- `src/match_pipeline.py`
  - 在最终结果 trace 的 `retriever` 节点输出这份记录
- `tools/classify_retriever_miss.py`
  - 优先读取这份实证 trace，再回退到旧的路由推断

## 新增 trace 结构

位置：

- `classification.retrieval_resolution`
- `result.trace.steps[*].retriever.search_resolution`

字段：

- `target`: `main` / `aux`
- `stage`: `primary` / `expanded` / `escape` / `open` / `aux`
- `requested_books`
- `resolved_books`
- `source_province`
- `open_search`
- `uses_standard_books`

## 现在能区分什么

之前只能看到：

- `router.classification.search_books`
  - 路由“想搜哪些册”

现在还能看到：

- `retriever.search_resolution.calls[*].resolved_books`
  - 检索“真正传给搜索器的是哪些册”

这一步补完后，`wrong_book` 诊断不再只靠推断。

## 已完成验证

通过的回归测试：

- `tests/test_retrieval_resolution_trace.py`
- `tests/test_classify_retriever_miss.py`
- `tests/test_candidate_reasoning_trace.py`
- `tests/test_match_core_context_passthrough.py`

并且：

- `python -m py_compile src/match_core.py src/match_pipeline.py tools/classify_retriever_miss.py`

## 下一步

按主架构继续，只做 `wrong_book`：

1. 用新 trace 重跑 `wrong_book` 子集归因
2. 看 main search 的 `resolved_books -> oracle_book` 偏差
3. 再做最小共享册别映射层，收口两处重复逻辑：
   - `src/match_core.py:_translate_books_for_industry`
   - `src/hybrid_searcher.py:_normalize_requested_books_for_nonstandard_db`
