# Wiki 页面规范

## 目录

```text
knowledge_wiki/
  AGENTS.md
  index.md
  log.md
  inbox/
  sources/
  rules/
  cases/
  methods/
  concepts/
  entities/
  reviews/
  daily/
```

## Frontmatter

```yaml
title:
type:
status:
province:
specialty:
source_refs:
source_kind:
created_at:
updated_at:
confidence:
owner:
tags:
related:
```

## 页面类型

- `source`
- `rule`
- `case`
- `method`
- `concept`
- `entity`
- `review`
- `daily_summary`

## 状态

- `draft`
- `reviewed`
- `promoted`
- `archived`

## 命名规则

- `sources/`：`source-<source_id>.md`
- `rules/`：`rule-<slug>.md`
- `cases/`：`case-<slug>.md`
- `methods/`：`method-<slug>.md`
- `reviews/`：`review-<id>.md`

## 强制约束

- 每页必须有 `type`
- 每页必须有 `source_refs`
- 规则页和方法页必须区分“事实 / 推断 / 建议”
- 页面应优先更新旧页，避免重复建页