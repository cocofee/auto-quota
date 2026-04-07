# QMD -> OpenClaw / JARVIS 正式接口

## 目标

把 `knowledge_wiki` / QMD 索引从脚本能力升级为正式后端接口，并接入：

- `OpenClaw` 机器调度入口
- `JARVIS` 咨询入口
- `OpenClawReviewService` 自动审核上下文

## 已实现接口

### 1. OpenClaw 机器接口

- `GET /api/openclaw/qmd-search`

参数：

- `q`: 查询词，必填
- `top_k`: 返回条数，默认 `5`
- `category`: 目录过滤，如 `rules` / `cases` / `sources`
- `page_type`: 页面类型过滤
- `province`: 省份过滤
- `specialty`: 专业过滤
- `source_kind`: 来源类型过滤，如 `image` / `video`
- `status`: 状态过滤

鉴权：

- `X-OpenClaw-Key`
- 或管理员登录态

### 2. JARVIS 咨询接口

- `GET /api/consult/qmd-search`

参数与返回结构同上，走普通登录鉴权。

## 返回结构

```json
{
  "query": "BV-2.5 穿管纠正",
  "count": 1,
  "filters": {
    "category": "rules"
  },
  "hits": [
    {
      "chunk_id": "rules-1",
      "score": 0.93,
      "title": "BV-2.5 穿管纠正规则",
      "heading": "穿管",
      "category": "rules",
      "page_type": "rule",
      "path": "rules/bv-2.5.md",
      "province": "",
      "specialty": "安装",
      "status": "active",
      "source_kind": "",
      "source_refs_text": "source-1",
      "preview": "优先穿管敷设。",
      "document": "优先穿管敷设。"
    }
  ]
}
```

## OpenClaw 自动注入

`OpenClawReviewService.build_review_context(...)` 现在会自动追加：

```json
{
  "qmd_recall": {
    "query": "...",
    "count": 3,
    "filters": {},
    "hits": []
  }
}
```

默认 query 由以下字段拼接：

- `bill_name`
- `bill_description`
- `specialty`
- `task.province`

这层是附加证据，不替代现有 `quota-search` 主链。

## 推荐调用方式

### OpenClaw

优先顺序：

1. `quota-search/smart`
2. `qmd-search`
3. 汇总为最终提示词或 review evidence

建议用途：

- 查纠正规则
- 查案例
- 查图片/视频来源页
- 给 review draft 增加可解释证据

### JARVIS

建议在以下场景调用 `consult/qmd-search`：

- 用户问“为什么这样套”
- 用户要看案例/图示/照片
- 清单描述模糊，需要补充规则证据

## 当前边界

- 现在是独立 QMD 检索层，不替换原有定额匹配接口
- OpenClaw review context 已自动注入
- `consult/chat` 已自动按用户问题触发 QMD recall，并把命中摘要拼进 Jarvis system prompt
- OpenClaw auto-review draft 已自动把 QMD 证据并入 `openclaw_review_note` 和 `evidence.qmd_summary`

下一步如果继续推进，建议做：

1. 在咨询页把 `qmd_recall` 做成可见证据抽屉
2. 在 OpenClaw 工作台非展开态展示 QMD 命中摘要
3. 按省份/专业为 chat recall 增加更细粒度过滤
