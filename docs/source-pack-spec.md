# Source Pack 规范

## 目的

Source Pack 是 LLM 的直接学习对象。所有原始资料必须先转成统一结构，再允许进入 Wiki 编译流程。

## 最小结构

```json
{
  "source_id": "",
  "source_kind": "doc|video|chat|image|system",
  "title": "",
  "summary": "",
  "full_text_path": "",
  "evidence_refs": [],
  "province": "",
  "specialty": "",
  "tags": [],
  "created_at": "",
  "confidence": 0
}
```

## 字段说明

- `source_id`：稳定唯一 ID
- `source_kind`：资料类型
- `title`：标题
- `summary`：简要摘要
- `full_text_path`：正文或转写文本路径
- `evidence_refs`：原始文件、截图、时间戳、URL 等证据引用
- `province`：省份，可为空
- `specialty`：专业，可为空
- `tags`：主题标签
- `created_at`：生成时间
- `confidence`：抽取置信度

## 各资料类型要求

### 文档

- 必须提取纯文本
- 原始文件路径写入 `evidence_refs`

### 视频

- 必须先产出 transcript
- 必须提供时间轴引用

### 聊天

- 只保留有效结论和高价值问答
- 闲聊不进入 source pack

### 图片

- 必须先 OCR
- 必须保留原图路径

### 系统产出

- review confirm、audit error、promotion queue 可直接转 source pack

## 约束

- 不允许无正文路径的 source pack 进入 Wiki 编译
- 不允许没有 `evidence_refs` 的 source pack 进入正式知识晋升