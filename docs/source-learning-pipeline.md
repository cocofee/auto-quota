# Source Learning Pipeline

## Goal

把 `source_pack` 变成可审核的知识候选，而不是只生成资料来源页。

正式链路：

`source_pack -> chunk -> LLM extraction -> promotion_queue -> human review -> promote -> QMD rebuild`

第一版只抽三类候选，因为当前正式提升链已经支持：

- `rule` -> `RuleKnowledge`
- `method` -> `MethodCards`
- `experience` -> `ExperienceDB`

## What This Layer Does

`tools/extract_source_to_staging.py` 负责：

1. 读取 `data/source_packs/packs/*.json`
2. 按标题和长度切块
3. 对每个 chunk 调用 LLM 抽取候选
4. 把候选标准化为 `promotion_queue` 记录
5. 合并同标题重复候选
6. 写入 `knowledge_staging`，等待人工审核

这层不直接写正式知识库。

## Candidate Contract

LLM 只能返回三种 `candidate_type`：

- `rule`
- `method`
- `experience`

每条候选必须至少包含：

- `candidate_type`
- `title`
- `summary` 或 `evidence_text`

建议字段：

- `keywords`
- `conditions`
- `exclusions`
- `common_errors`
- `rule_text`
- `method_text`
- `bill_text`
- `bill_name`
- `bill_desc`
- `final_quota_code`
- `final_quota_name`

所有候选都必须保留证据链：

- `evidence_ref = source_pack:{source_id}#chunk:{chunk_id}`
- `candidate_payload.evidence_refs` 保留 source pack 和原始资料路径

## Review Rule

LLM 输出只算候选，不算正式知识。

人工审核时重点看：

1. 这是不是工程造价判断知识，而不是摘要。
2. 证据是否真能支撑这条结论。
3. 类型是否分对了。
4. 是否需要补充省份、专业、定额编号。
5. 是否能进入正式层。

## Command

单个来源，正式写入 staging：

```bash
python tools/extract_source_to_staging.py --source-id doc-c0d82dda08e6
```

仅做干跑，不写库：

```bash
python tools/extract_source_to_staging.py --source-id doc-c0d82dda08e6 --dry-run
```

使用固定响应做离线调试：

```bash
python tools/extract_source_to_staging.py ^
  --source-id doc-c0d82dda08e6 ^
  --fixture-response tests/fixtures/source_learning_rule.json ^
  --json
```

打印 prompt 检查抽取口径：

```bash
python tools/extract_source_to_staging.py --source-id doc-c0d82dda08e6 --dry-run --print-prompts
```

## Integration Notes

要接入 OpenClaw / JARVIS，建议只暴露两个动作：

1. `extract_source_to_staging`
2. `export_staging_to_wiki` / `import_wiki_promotions`

也就是：

- OpenClaw 负责触发抽取、汇报结果
- 人在 Obsidian 或管理面板里审核
- 审核通过后再 promote
- promote 后再 rebuild QMD

## Acceptance

这一层完成的验收标准：

1. 能从一个 source pack 产出 `promotion_queue` 候选。
2. 候选能区分 `rule/method/experience`。
3. 候选保留证据引用。
4. 不经过人工审核，不能直接进入正式知识库。
5. promote 后现有 QMD 链不需要重写。

## OpenClaw API

现在 OpenClaw 可以直接走后端接口，不必手工跑脚本。

列出可学习资料：

```http
GET /api/openclaw/source-packs?q=山东&limit=20
X-OpenClaw-Key: <your-key>
```

查看单个资料：

```http
GET /api/openclaw/source-packs/{source_id}
X-OpenClaw-Key: <your-key>
```

触发学习抽取：

```http
POST /api/openclaw/source-packs/{source_id}/learn
X-OpenClaw-Key: <your-key>
Content-Type: application/json

{
  "dry_run": false,
  "llm_type": "deepseek",
  "chunk_size": 1800,
  "overlap": 240,
  "max_chunks": 24
}
```

建议 OpenClaw 固定成两步：

1. 先 `GET /source-packs` 找到 source_id
2. 再 `POST /source-packs/{source_id}/learn` 发起抽取