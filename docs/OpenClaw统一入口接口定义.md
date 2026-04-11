# OpenClaw 统一入口接口定义

## 1. 文档目标

本文件用于把“OpenClaw 作为统一入口”的方案落成可开发的接口定义。

覆盖内容：

- P0/P1/P2/P3 接口清单
- 每个接口的请求体/响应体
- 状态流转字段
- OpenClaw 桥接透传规则

默认约定：

- 内部原生接口前缀：`/api/...`
- OpenClaw 桥接接口前缀：`/api/openclaw/...`
- 响应风格参考现有 `task.py` / `result.py`

## 2. 统一约定

### 2.1 文件标识

- `file_id`: 统一文件入口生成的 ID
- `document_id`: 历史价格文档或综合单价文档记录 ID

### 2.2 文件类型枚举

建议枚举：

- `quota_task_file`
- `historical_quote_file`
- `priced_bill_file`
- `knowledge_source_file`
- `other`

### 2.3 解析状态枚举

- `uploaded`
- `classifying`
- `classified`
- `parsing`
- `parsed`
- `routing`
- `routed`
- `failed`

### 2.4 路由目标枚举

- `task_pipeline`
- `price_reference_quote`
- `price_reference_boq`
- `knowledge_staging`
- `manual_review`

### 2.5 OpenClaw 桥接原则

OpenClaw 层分两类字段：

- 原样透传字段
- 桥接包装字段

原样透传：

- 查询参数
- 请求体业务字段
- 分页参数
- 文件上传内容

桥接包装：

- `actor`
- `request_id`
- `openclaw_trace_id`
- `source="openclaw"`

## 3. P0：统一文件入口

### 3.1 `POST /api/file-intake/upload`

用途：

- 上传任意待识别文件
- 创建统一入口记录

请求：

- `multipart/form-data`
- 字段：
  - `file`: 文件
  - `province`: 可选
  - `project_name`: 可选
  - `project_stage`: 可选
  - `source_hint`: 可选，外部系统给的初判提示

响应模型建议：

```json
{
  "file_id": "fi_123",
  "filename": "示例.xlsx",
  "status": "uploaded",
  "file_type": "",
  "source_hint": "priced_bill_file",
  "project_name": "示例项目",
  "project_stage": "bid",
  "province": "北京市建设工程施工消耗量标准(2024)",
  "created_at": "2026-03-26T15:00:00Z"
}
```

### 3.2 `GET /api/file-intake/{file_id}`

用途：

- 查询文件状态
- 查看识别结果、解析结果、分流结果

响应模型建议：

```json
{
  "file_id": "fi_123",
  "filename": "示例.xlsx",
  "status": "parsed",
  "file_type": "priced_bill_file",
  "classify_result": {
    "file_type": "priced_bill_file",
    "confidence": 0.93,
    "signals": ["项目特征", "定额编号", "综合单价"]
  },
  "parse_summary": {
    "bill_items": 126,
    "quote_items": 0,
    "warnings": []
  },
  "route_result": {
    "targets": ["price_reference_boq", "learning_pipeline"]
  },
  "created_at": "2026-03-26T15:00:00Z",
  "updated_at": "2026-03-26T15:01:12Z"
}
```

### 3.3 `POST /api/file-intake/{file_id}/classify`

用途：

- 显式触发文件识别

请求体建议：

```json
{
  "force": false
}
```

响应体建议：

```json
{
  "file_id": "fi_123",
  "status": "classified",
  "file_type": "priced_bill_file",
  "confidence": 0.93,
  "signals": ["项目特征", "定额编号", "综合单价"]
}
```

### 3.4 `POST /api/file-intake/{file_id}/parse`

用途：

- 做结构化抽取

请求体建议：

```json
{
  "force": false,
  "parser_profile": "",
  "target_mode": "auto"
}
```

响应体建议：

```json
{
  "file_id": "fi_123",
  "status": "parsed",
  "file_type": "priced_bill_file",
  "parse_summary": {
    "bill_items": 126,
    "quote_items": 0,
    "warnings": []
  }
}
```

### 3.5 `POST /api/file-intake/{file_id}/route`

用途：

- 送往对应下游

请求体建议：

```json
{
  "route_targets": ["price_reference_boq", "learning_pipeline"],
  "auto_create_task": false
}
```

响应体建议：

```json
{
  "file_id": "fi_123",
  "status": "routed",
  "targets": [
    {
      "target": "price_reference_boq",
      "status": "ok",
      "document_id": "pd_1001"
    },
    {
      "target": "learning_pipeline",
      "status": "ok",
      "imported": 126
    }
  ]
}
```

### 3.6 OpenClaw 桥接

对应桥接接口：

- `POST /api/openclaw/file-intake/upload`
- `GET /api/openclaw/file-intake/{file_id}`
- `POST /api/openclaw/file-intake/{file_id}/classify`
- `POST /api/openclaw/file-intake/{file_id}/parse`
- `POST /api/openclaw/file-intake/{file_id}/route`

桥接规则：

- 请求参数原样透传
- 响应字段原样返回
- 增加：
  - `source="openclaw"`
  - `openclaw_trace_id`

## 4. P1：历史价格文档与设备单价参考

### 4.1 `POST /api/price-documents`

用途：

- 创建历史价格文档记录

请求体建议：

```json
{
  "file_id": "fi_123",
  "document_type": "historical_quote_file",
  "project_name": "示例项目",
  "project_stage": "bid",
  "province": "广东省通用安装工程综合定额(2018)",
  "specialty": "弱电"
}
```

响应体建议：

```json
{
  "id": "pd_1001",
  "file_id": "fi_123",
  "document_type": "historical_quote_file",
  "status": "created"
}
```

### 4.2 `GET /api/price-documents`

用途：

- 查看历史价格文档列表

查询参数建议：

- `page`
- `size`
- `document_type`
- `project_name`
- `specialty`
- `status`

响应体建议：

```json
{
  "items": [],
  "total": 0,
  "page": 1,
  "size": 20
}
```

### 4.3 `GET /api/price-documents/{id}`

用途：

- 查看历史价格文档详情

响应体建议：

```json
{
  "id": "pd_1001",
  "document_type": "historical_quote_file",
  "project_name": "示例项目",
  "status": "parsed",
  "parse_summary": {
    "quote_items": 89
  }
}
```

### 4.4 `POST /api/price-documents/{id}/parse`

用途：

- 解析设备/材料报价文档

请求体建议：

```json
{
  "force": false
}
```

响应体建议：

```json
{
  "id": "pd_1001",
  "status": "parsed",
  "parse_summary": {
    "quote_items": 89,
    "warnings": []
  }
}
```

### 4.5 `GET /api/price-items/search`

用途：

- 搜设备/材料报价条目

查询参数建议：

- `q`
- `specialty`
- `system_name`
- `brand`
- `model`
- `page`
- `size`

响应体建议：

```json
{
  "items": [
    {
      "item_name_raw": "400万全彩POE网络枪机",
      "item_name_normalized": "网络枪式摄像机",
      "brand": "海康",
      "model": "DS-2CD...",
      "unit": "台",
      "unit_price": 365.0,
      "source_date": "2025-10-01",
      "project_name": "XX项目"
    }
  ],
  "total": 1,
  "page": 1,
  "size": 20
}
```

### 4.6 `GET /api/reference/item-price`

用途：

- 返回设备/材料单价参考

查询参数建议：

- `q`
- `specialty`
- `brand`
- `model`
- `region`

响应体建议：

```json
{
  "query": "海康400万枪机",
  "reference_type": "item_price",
  "summary": {
    "min_unit_price": 320.0,
    "max_unit_price": 420.0,
    "median_unit_price": 365.0,
    "sample_count": 12
  },
  "samples": []
}
```

### 4.7 OpenClaw 桥接

对应桥接接口：

- `GET /api/openclaw/reference/item-price`

P1 阶段不必桥接 `price-documents` 的后台管理接口，可先只桥接查询口。

## 5. P2：综合单价参考

### 5.1 `POST /api/bill-price-documents`

用途：

- 创建带定额清单/综合单价文档记录

请求体建议：

```json
{
  "file_id": "fi_456",
  "document_type": "priced_bill_file",
  "project_name": "示例项目",
  "project_stage": "settlement",
  "province": "北京市建设工程施工消耗量标准(2024)",
  "specialty": "安装"
}
```

### 5.2 `POST /api/bill-price-documents/{id}/parse`

用途：

- 解析清单项、项目特征、综合单价、定额编号

请求体建议：

```json
{
  "force": false,
  "write_learning": true,
  "write_price_reference": true
}
```

响应体建议：

```json
{
  "id": "bd_2001",
  "status": "parsed",
  "parse_summary": {
    "bill_items": 126,
    "learning_written": 126,
    "price_reference_written": 126,
    "warnings": []
  }
}
```

### 5.3 `GET /api/composite-price/search`

用途：

- 搜综合单价条目

查询参数建议：

- `q`
- `quota_code`
- `specialty`
- `region`
- `page`
- `size`

响应体建议：

```json
{
  "items": [
    {
      "boq_name_raw": "摄像机安装",
      "boq_name_normalized": "监控摄像机安装",
      "unit": "点",
      "composite_unit_price": 180.0,
      "quota_code": "C10-1-5",
      "quota_name": "XXX",
      "project_name": "XX项目"
    }
  ],
  "total": 1,
  "page": 1,
  "size": 20
}
```

### 5.4 `GET /api/reference/composite-price`

用途：

- 返回清单综合单价参考值

查询参数建议：

- `q`
- `quota_code`
- `region`
- `specialty`

响应体建议：

```json
{
  "query": "视频监控点位",
  "reference_type": "composite_price",
  "summary": {
    "min_composite_unit_price": 160.0,
    "max_composite_unit_price": 230.0,
    "median_composite_unit_price": 185.0,
    "sample_count": 18
  },
  "samples": []
}
```

### 5.5 OpenClaw 桥接

对应桥接接口：

- `GET /api/openclaw/reference/composite-price`

## 6. P3：统一参考与批量辅助填表

### 6.1 `GET /api/reference/search`

用途：

- 统一查询入口

查询参数建议：

- `q`
- `region`
- `specialty`
- `top_k`

响应体建议：

```json
{
  "query": "五方对讲系统综合单价",
  "item_price_reference": {
    "summary": {},
    "samples": []
  },
  "composite_price_reference": {
    "summary": {},
    "samples": []
  },
  "related_samples": []
}
```

### 6.2 `POST /api/reference/batch-fill`

用途：

- 批量返回参考价候选

请求体建议：

```json
{
  "items": [
    {
      "name": "400万全彩POE网络枪机",
      "description": "",
      "unit": "台",
      "quantity": 12
    },
    {
      "name": "视频监控点位",
      "description": "含设备安装调试",
      "unit": "点",
      "quantity": 20
    }
  ],
  "region": "北京",
  "specialty": "弱电"
}
```

响应体建议：

```json
{
  "items": [
    {
      "index": 0,
      "query_type": "item_price",
      "recommended_unit_price": 365.0,
      "candidates": []
    },
    {
      "index": 1,
      "query_type": "composite_price",
      "recommended_composite_unit_price": 185.0,
      "candidates": []
    }
  ],
  "summary": {
    "total": 2,
    "resolved": 2
  }
}
```

### 6.3 `POST /api/reference/batch-preview`

用途：

- 预览批量参考价结果，不落任何回填输出

请求体与 `batch-fill` 相同。

### 6.4 `POST /api/reference/batch-execute`

用途：

- 真正把参考价写回新表

请求体建议：

- 文件上传版本：

```json
{
  "file_id": "fi_789",
  "region": "北京",
  "specialty": "弱电"
}
```

- 或结构化 items 版本：

```json
{
  "items": [],
  "output_format": "xlsx"
}
```

响应体建议：

```json
{
  "job_id": "rf_3001",
  "status": "completed",
  "output_file_id": "fi_out_001"
}
```

### 6.5 OpenClaw 桥接

对应桥接接口：

- `GET /api/openclaw/reference/search`
- `POST /api/openclaw/reference/batch-fill`
- `POST /api/openclaw/reference/batch-preview`
- `POST /api/openclaw/reference/batch-execute`

## 7. 状态流转

### 7.1 文件入口状态机

```text
uploaded
  -> classifying
  -> classified
  -> parsing
  -> parsed
  -> routing
  -> routed
```

异常统一转：

```text
failed
```

### 7.2 价格文档状态机

```text
created
  -> parsing
  -> parsed
  -> indexed
```

### 7.3 批量填表任务状态机

```text
queued
  -> running
  -> completed
```

异常统一转：

```text
failed
```

## 8. 推荐的 Pydantic 模型拆分

建议新增 schema 文件：

- `web/backend/app/schemas/file_intake.py`
- `web/backend/app/schemas/reference.py`
- `web/backend/app/schemas/price_document.py`

### 8.1 `file_intake.py`

建议模型：

- `FileIntakeResponse`
- `FileClassifyRequest`
- `FileClassifyResponse`
- `FileParseRequest`
- `FileParseResponse`
- `FileRouteRequest`
- `FileRouteResponse`

### 8.2 `reference.py`

建议模型：

- `ItemPriceReferenceResponse`
- `CompositePriceReferenceResponse`
- `UnifiedReferenceSearchResponse`
- `BatchFillRequest`
- `BatchFillResponse`

### 8.3 `price_document.py`

建议模型：

- `PriceDocumentCreateRequest`
- `PriceDocumentResponse`
- `PriceDocumentListResponse`
- `BillPriceDocumentParseRequest`
- `BillPriceDocumentParseResponse`

## 9. OpenClaw 透传细则

### 9.1 查询接口

以下字段原样透传：

- `q`
- `region`
- `specialty`
- `top_k`
- `page`
- `size`

新增桥接字段：

- `source="openclaw"`
- `openclaw_trace_id`

### 9.2 上传接口

以下内容原样透传：

- 文件二进制
- `province`
- `project_name`
- `project_stage`
- `source_hint`

新增桥接字段：

- `actor`
- `source="openclaw"`

### 9.3 批量接口

请求体中的业务字段原样透传。

仅在服务端日志和审计中增加：

- `openclaw_trace_id`
- `service_user`

## 10. 开发优先级

### P0 必做

- `file-intake` 全套 schema
- `file-intake` 全套 API
- OpenClaw `file-intake` 桥接

### P1 必做

- `item-price` 查询 schema
- `price-documents` 基础 schema
- OpenClaw `reference/item-price` 桥接

### P2 必做

- `composite-price` 查询 schema
- `bill-price-documents` 解析 schema
- OpenClaw `reference/composite-price` 桥接

### P3 必做

- `reference/search`
- `reference/batch-fill`
- `reference/batch-preview`
- `reference/batch-execute`

## 11. 一句话结论

要让 OpenClaw 成为统一入口，接口上最重要的不是继续扩 `tasks`，而是补齐：

- `file-intake`
- `reference`
- `price-documents`

然后由 OpenClaw 稳定桥接这些内部原生能力。
