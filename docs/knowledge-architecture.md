# 知识架构总览

## 目标

建立一条可长期运行的知识流水线，把分散的文档、视频、聊天记录、图片和系统审核结果，转成可追溯、可搜索、可维护的 Markdown Wiki，并在 Obsidian 中进行浏览和人工修订。

## 角色边界

- OpenClaw：统一入口、路由、任务编排、定时触发
- auto-quota / JARVIS：业务执行、审核、正式知识层
- knowledge_staging：候选知识缓冲层，不直接作为正式知识源
- knowledge_wiki：Markdown Wiki 编译层
- Obsidian：Wiki 展示层和人工修订层
- Codex：实现脚本、接口、同步器、lint 和自动化

## 总体数据流

```text
原始资料
  -> file-intake
  -> classify / parse / route
  -> source pack
  -> staging / wiki compiler
  -> knowledge_wiki
  -> Obsidian 浏览与修订
  -> 人工确认
  -> 正式知识层
```

## 三层搜索

- 业务搜索：定额、候选、价格参考，服务业务执行
- Wiki 搜索：规则、案例、方法、概念，服务知识导航
- 证据搜索：source pack、OCR、transcript、review 记录，服务追溯

## 统一入口

- OpenClaw 文件入口：`/api/openclaw/file-intake/upload`
- 统一文件入口：`/api/file-intake/upload`
- 审核学习入口：`review-confirm -> knowledge_staging -> knowledge_wiki`

## 原则

- Obsidian 不是数据库
- LLM 不直接学习整个硬盘
- 所有知识页必须带 `source_refs`
- 不确定内容只能进入 `draft/review`
- 正式规则页必须人工确认后晋升