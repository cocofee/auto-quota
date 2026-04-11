---
title: "JARVIS Wiki Index"
type: "index"
status: "reviewed"
province: ""
specialty: ""
source_refs:
  - "staging:audit_errors"
  - "staging:promotion_queue"
source_kind: "system"
created_at: "2026-04-06"
updated_at: "2026-04-06"
confidence: 100
owner: "codex"
tags:
  - "wiki"
  - "index"
  - "staging"
related: []
---

# JARVIS Wiki Index

## 本次导出
- 导出日期: `2026-04-06`
- 审核沉淀: `7`
- 规则候选: `5`
- 方法候选: `5`
- 历史案例候选: `5`

## 目录
- `reviews/` 审核沉淀页面
- `rules/` 规则候选页面
- `methods/` 审核方法页面
- `cases/` 历史案例页面
- `daily/` 导出日报

## 用法
- 先运行 `python tools/export_staging_to_wiki.py`
- 再运行 `powershell -ExecutionPolicy Bypass -File tools/sync_wiki_to_obsidian.ps1`
