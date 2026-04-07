# P4 QMD 搜索层验收

## 状态

已完成，日期：`2026-04-07`

## 交付物

- `src/qmd_index.py`
- `tools/build_qmd_index.py`
- `tools/search_qmd.py`
- `tests/test_qmd_index.py`
- `docs/qmd-search.md`

## 索引范围

- `knowledge_wiki/sources`
- `knowledge_wiki/rules`
- `knowledge_wiki/cases`
- `knowledge_wiki/methods`
- `knowledge_wiki/reviews`

## 实际构建结果

- pages: `31`
- chunks: `194`
- categories:
  - `sources: 53`
  - `rules: 30`
  - `cases: 25`
  - `methods: 30`
  - `reviews: 56`
- chroma dir: `db/chroma/qwen3/common_qmd`
- collection: `qmd_docs`

## 验证命令

```powershell
python tools\build_qmd_index.py
python tools\search_qmd.py "BV-2.5 穿管纠正" --top-k 3
python tools\search_qmd.py "现场照片 电缆" --top-k 3 --type source --source-kind image
```

## 验证结论

- 已完成独立 wiki/QMD 语义索引，不和现有 quota/experience/universal 索引混用
- 默认未设置 `VECTOR_MODEL_KEY` 时，QMD 自动优先使用仓库内置 `qwen3-embedding-quota-v3`
- 搜索结果可回溯到原始 wiki 页面路径、标题分段、`source_refs`
- 当前版本满足 `P4 搜索层` 的第一版验收要求，可进入下一阶段接口接入
