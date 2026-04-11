# JARVIS 日常使用说明

## 1. 你现在这套系统怎么理解

日常主链是：

```text
外部原始资料
  -> E:\Jarvis-Raw\00_inbox
  -> 批量导入
  -> data/source_packs
  -> knowledge_wiki
  -> QMD 检索
  -> OpenClaw / JARVIS 使用
```

角色分工：

- `E:\Jarvis-Raw`：原始资料库
- `data/source_packs`：结构化抽取结果
- `knowledge_wiki`：正式 Wiki
- `QMD`：语义检索层
- `OpenClaw`：调度和审核入口
- `JARVIS`：业务执行和回答

原则只有一条：

- 原始资料不直接堆进仓库

## 2. 原始资料放哪里

统一放外部目录：

```text
E:\Jarvis-Raw
  00_inbox
  10_docs
  20_images
  30_videos
  40_chats
  90_done
```

含义：

- `00_inbox`：新资料入口
- `10_docs`：归档后的文档
- `20_images`：归档后的图片
- `30_videos`：归档后的视频
- `40_chats`：归档后的聊天
- `90_done`：导入报告

## 3. 日常最常用操作

### 3.1 先预演

```powershell
cd C:\Users\Administrator\Documents\trae_projects\auto-quota
run_raw_ingest.bat dry
```

作用：

- 扫描 `E:\Jarvis-Raw\00_inbox`
- 看哪些文件会被识别
- 不真正写入系统

### 3.2 正式导入

```powershell
run_raw_ingest.bat full
```

作用：

- 导入 `00_inbox` 文件
- 生成 `source_pack`
- 编译 `knowledge_wiki`
- 重建 QMD

导入成功后，原始文件会自动移动到：

- 文档 -> `10_docs`
- 图片 -> `20_images`
- 视频 -> `30_videos`
- 聊天 -> `40_chats`

导入报告会写到：

- `E:\Jarvis-Raw\90_done`

## 4. 支持什么资料

### 4.1 文档

支持常见类型：

- `.pdf`
- `.docx`
- `.xlsx`
- `.csv`
- `.md`
- `.txt`
- `.json`

### 4.2 图片

支持常见图片文件。

建议：

- 图片旁边配一个同名 OCR 文件
- 例如 `现场照片.jpg.txt`

这样导入质量更稳。

### 4.3 视频

视频必须尽量配字幕或转写。

推荐同目录准备：

- `xxx.mp4`
- `xxx.srt`

或者：

- `xxx.mp4`
- `xxx.vtt`

如果只有视频、没有 transcript，批量导入会跳过。

### 4.4 聊天

优先这两种：

- `messages` 结构的 `.json`
- 格式清晰的 `.txt` / `.md`

## 5. 导入后看哪里

导入后主要看这几个地方：

- `data/source_packs/packs`
- `data/source_packs/texts`
- `knowledge_wiki/sources`
- `E:\Jarvis-Raw\90_done`

如果这些都有内容，说明资料已经进系统了。

## 6. 怎么检查 Wiki 有没有问题

运行：

```powershell
python tools\lint_wiki.py
```

如果要输出报告：

```powershell
python tools\lint_wiki.py --json --report reports\wiki-lint.json
```

这个脚本会检查：

- frontmatter 是否完整
- `type` 和目录是否匹配
- `confidence` 是否合法
- `source_refs` 是否失效
- `related` 是否指向不存在页面
- manifest 是否引用缺失文件

## 7. 怎么让已审核候选正式晋升

如果 staging 里的 promotion 已经人工审核通过，运行：

```powershell
python tools\import_wiki_promotions.py --dry-run
```

先预演没问题，再正式执行：

```powershell
python tools\import_wiki_promotions.py
```

如果执行后要顺手刷新 wiki 和 QMD：

```powershell
python tools\import_wiki_promotions.py --refresh-wiki --build-qmd
```

## 8. OpenClaw / JARVIS 什么时候能用到这些资料

当资料完成：

1. 导入
2. 编译 wiki
3. 重建 QMD

之后，OpenClaw / JARVIS 才能通过 QMD 检索到它们。

## 9. 最推荐的日常节奏

每天按这个顺序：

1. 把新资料丢进 `E:\Jarvis-Raw\00_inbox`
2. 跑 `run_raw_ingest.bat dry`
3. 确认识别正常
4. 跑 `run_raw_ingest.bat full`
5. 跑 `python tools\lint_wiki.py`
6. 在 OpenClaw / JARVIS 里验证检索是否正常
7. 对已确认 promotion 再跑 `import_wiki_promotions.py`

## 10. 最容易犯的错

- 把整个 RAW 资料库复制进仓库
- 视频没有 transcript 就直接导入
- 图片没有 OCR 也没有说明
- 导入后不跑 wiki lint
- promotion 还没审核通过就直接执行
- 还没重建 QMD 就去问 JARVIS 为什么搜不到

## 11. 出问题先查什么

先查这 5 个位置：

1. `E:\Jarvis-Raw\90_done` 的导入报告
2. `data/source_packs/packs`
3. `knowledge_wiki/sources`
4. `python tools\lint_wiki.py` 输出
5. `python tools\search_qmd.py "你的查询词"`

## 12. 最短命令清单

```powershell
run_raw_ingest.bat dry
run_raw_ingest.bat full
python tools\lint_wiki.py
python tools\import_wiki_promotions.py --dry-run
python tools\import_wiki_promotions.py
python tools\search_qmd.py "桥架 电缆 现场照片"
```
