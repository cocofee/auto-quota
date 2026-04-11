# 外部 RAW 资料库用法

## 目标

把原始资料留在仓库外，避免污染 `auto-quota` 工作区。

推荐根目录：

```text
E:\Jarvis-Raw
  00_inbox
  10_docs
  20_images
  30_videos
  40_chats
  90_done
```

边界保持为：

- `E:\Jarvis-Raw`：原始资料库
- `data/source_packs`：结构化抽取结果
- `knowledge_wiki`：正式 Wiki
- `db/chroma/.../common_qmd`：QMD 索引

## 日常流程

1. 新资料先放进 `E:\Jarvis-Raw\00_inbox`
2. 运行批量导入脚本
3. 成功后原文件自动移动到对应目录
4. 批量报告写入 `E:\Jarvis-Raw\90_done`
5. 如需让 OpenClaw/JARVIS 立刻可检索，再编译 Wiki 和重建 QMD

## 批量导入

只导入并整理文件：

```powershell
cd C:\Users\Administrator\Documents\trae_projects\auto-quota
python tools\ingest_raw_batch.py --raw-root "E:\Jarvis-Raw"
```

导入后顺手编译 Wiki：

```powershell
python tools\ingest_raw_batch.py --raw-root "E:\Jarvis-Raw" --compile-wiki
```

导入后同时重建 QMD：

```powershell
python tools\ingest_raw_batch.py --raw-root "E:\Jarvis-Raw" --compile-wiki --build-qmd
```

先预览本批会处理什么，不实际写入：

```powershell
python tools\ingest_raw_batch.py --raw-root "E:\Jarvis-Raw" --dry-run
```

只处理前 20 个：

```powershell
python tools\ingest_raw_batch.py --raw-root "E:\Jarvis-Raw" --limit 20
```

保留原文件在 `00_inbox`，不自动搬走：

```powershell
python tools\ingest_raw_batch.py --raw-root "E:\Jarvis-Raw" --no-move
```

## 当前自动规则

- 文档：按扩展名识别，走 `ingest_document`
- 图片：按扩展名识别，优先读取同名 OCR 辅助文件，例如 `xxx.jpg.txt`
- 视频：按扩展名识别，要求同目录存在字幕或转写，例如 `xxx.srt` / `xxx.vtt` / `xxx.txt`
- 聊天：`.json` 会优先尝试识别 `messages` 结构；`.txt/.md` 会按对话格式尝试识别

## 建议约束

- 不要把整个 `E:\Jarvis-Raw` 放进 git
- 不要一次导几千个文件，先按 10 到 50 个一批验收
- 视频尽量先准备好字幕或转写
- 图片尽量配 OCR 或人工 caption
- 有高价值聊天记录时，优先整理成 JSON 或清晰的对话文本

## 验收点

导入后主要看 4 个位置：

- `data/source_packs/packs`
- `data/source_packs/texts`
- `knowledge_wiki/sources`
- OpenClaw / JARVIS 的 QMD 搜索结果
