# 分阶段验收标准

## 通用规则

每个阶段必须同时交付：

- 代码或脚本
- 规范文档
- 示例输入
- 示例输出
- 验收说明

只有验收通过后，才能进入下一阶段。

## P0

状态：已完成（2026-04-06）

### 交付

- 目录结构
- `AGENTS.md`
- `index.md`
- `log.md`
- 4 份文档

### 验收

- 文档存在且内容完整
- 目录结构正确
- 规则边界无冲突
- 已在 `JARVIS-Wiki` 中完成目录、首页、规范文档整理

## P1

状态：已完成（2026-04-06）

### 交付

- `export_staging_to_wiki.py`
- `sync_wiki_to_obsidian.ps1`
- 至少 3 个 Wiki 页面样例

### 验收

- staging 可导出到 Markdown
- Obsidian 中可见
- 页面 frontmatter 完整
- 已完成真实 staging 数据导出并同步到 Obsidian
- 当前样例规模：7 个审核页、5 个规则页、5 个方法页、5 个案例页、1 个日报页

## P2

状态：已完成（2026-04-06）

### 交付

- 文档、聊天、图片 ingest 脚本
- source pack 样例
- source page 样例

### 验收

- 每类资料至少 2 个样例
- 能从 Wiki 页回溯到原始资料
- 已完成 2 份文档、2 段聊天、2 张图片样例导入
- 已生成 6 个 source 页和 1 个 sources index 页
- 已同步到 Obsidian `60-资料来源`

## P3

状态：已完成（2026-04-06）

### 交付

- 视频 ingest 脚本
- transcript 样例
- video source page 样例

### 验收

- 至少 2 个视频跑通
- 可回溯 transcript 和时间轴
- 已完成 2 个视频 transcript 样例导入
- 已生成 2 个 `source-video-*.md` 页面
- 已同步到 Obsidian `60-资料来源`

## P4

### 交付

- wiki 搜索
- 证据搜索
- 搜索说明文档

### 验收

- 业务搜索、Wiki 搜索、证据搜索三者边界清晰
- 3 类示例查询可跑通

## P5

### 交付

- lint 脚本
- promotion 脚本
- 治理文档

### 验收

- lint 可出报告
- 至少 3 条页面能完成人工确认后回流

## 当前阶段

- 当前完成：P0、P1、P2、P3
- 当前待启动：P4 搜索层
