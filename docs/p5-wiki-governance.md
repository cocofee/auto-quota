# P5 Wiki 治理与晋升

## 目标

P5 解决两件事：

- `knowledge_wiki` 页面质量检查
- 已人工审核通过的 `promotion_queue` 执行进正式知识层

这一步不新增新的知识主链，而是复用现有：

- `knowledge_staging`
- `promotion_queue`
- `KnowledgePromotionService`
- `export_staging_to_wiki.py`

## 1. Wiki Lint

脚本：

```powershell
python tools\lint_wiki.py
```

支持：

- 检查 frontmatter 是否存在
- 检查必填字段是否齐全
- 检查目录和 `type` 是否匹配
- 检查 `confidence` 是否合法
- 检查 `source_refs` 是否为空或指向失效目标
- 检查 `related` / `[[wiki-link]]` 是否存在
- 检查 `.generated_manifest.json` 是否引用缺失文件

输出 JSON 报告：

```powershell
python tools\lint_wiki.py --json --report reports\wiki-lint.json
```

默认规则：

- 有 `error` 返回码为 `1`
- 只有 `warning` 返回码为 `0`
- 如需把 warning 也视为失败，可加 `--fail-on-warn`

## 2. 执行已批准晋升

脚本：

```powershell
python tools\import_wiki_promotions.py
```

默认行为：

- 只读取 `promotion_queue` 中 `status=approved` 的记录
- 只执行 `review_status=approved` 的记录
- 通过已有 `KnowledgePromotionService` 写入正式层

当前支持目标层：

- `RuleKnowledge`
- `MethodCards`
- `ExperienceDB`

预演：

```powershell
python tools\import_wiki_promotions.py --dry-run
```

只执行规则类：

```powershell
python tools\import_wiki_promotions.py --candidate-types rule
```

执行后顺手刷新 wiki：

```powershell
python tools\import_wiki_promotions.py --refresh-wiki
```

执行后刷新 wiki 并重建 QMD：

```powershell
python tools\import_wiki_promotions.py --refresh-wiki --build-qmd
```

## 3. 推荐操作顺序

1. 先导入资料，形成 `source_pack`
2. 编译 `knowledge_wiki`
3. 运行 `lint_wiki.py`
4. 人工在 Obsidian / staging 中审核 promotion 候选
5. promotion 记录改为 `approved`
6. 运行 `import_wiki_promotions.py`
7. 如有需要，再刷新 wiki 和 QMD

## 4. 验收标准

- `lint_wiki.py` 能输出可读报告
- 至少 3 条已批准 promotion 能成功执行进正式层
- promotion 执行结果能在 staging 中追溯到 `promoted_target_id / promoted_target_ref / promotion_trace`
