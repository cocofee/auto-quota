# QMD 搜索层说明

## 定位

`QMD` 是 `knowledge_wiki` 之上的语义检索层，不替代 `knowledge_wiki` 和 `Obsidian`。

- `knowledge_wiki` 是知识真源
- `Obsidian` 是浏览与验收层
- `QMD` 负责召回相关规则、案例、方法、审核沉淀和资料来源

## 检索范围

当前索引目录：

- `knowledge_wiki/sources`
- `knowledge_wiki/rules`
- `knowledge_wiki/cases`
- `knowledge_wiki/methods`
- `knowledge_wiki/reviews`

当前不索引：

- `knowledge_wiki/index.md`
- `knowledge_wiki/log.md`
- `knowledge_wiki/daily`

## 元数据

QMD 会从页面 frontmatter 提取以下过滤字段：

- `type`
- `status`
- `province`
- `specialty`
- `source_kind`

同时保留以下回溯信息：

- `path`
- `title`
- `heading`
- `source_refs_text`
- `preview`

## 分块规则

- 先按 Markdown 标题分段
- 再按段落合并成约 `900` 字符的 chunk
- 超长段落按滑窗切片，默认重叠 `120` 字符
- 每个 chunk 保留页面标题、标题路径、类别和 frontmatter 元数据

## 构建命令

```powershell
python tools\build_qmd_index.py
```

可选参数：

```powershell
python tools\build_qmd_index.py --chunk-size 900 --overlap 120 --batch-size 64
```

索引目录：

```text
db/chroma/<VECTOR_MODEL_KEY>/common_qmd
```

说明：

- 如果显式设置了 `VECTOR_MODEL_KEY`，QMD 跟随该配置
- 如果未设置，QMD 默认优先使用仓库内置的 `models/qwen3-embedding-quota-v3`

## 搜索命令

基础搜索：

```powershell
python tools\search_qmd.py "电缆敷设纠正规则"
```

按类型和专业过滤：

```powershell
python tools\search_qmd.py "桥架电缆现场照片" --type source --source-kind image --province 北京 --specialty 安装
```

按类别过滤：

```powershell
python tools\search_qmd.py "BV-2.5 穿管纠正" --category rules
```

返回 JSON：

```powershell
python tools\search_qmd.py "阀门安装案例" --json
```

## 接入建议

后续接入 `OpenClaw/JARVIS` 时，建议固定链路：

```text
用户问题
  -> QMD 召回
  -> JARVIS 专业判断
  -> 输出答案 + source_refs/path
  -> Obsidian 验收与沉淀
```
