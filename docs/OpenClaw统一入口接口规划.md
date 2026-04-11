# OpenClaw 统一入口接口规划

## 1. 目标

将 OpenClaw 定位为 JARVIS 的`统一外部入口`。

也就是：

- 外部系统先把文件交给 OpenClaw 入口
- OpenClaw 不直接理解所有业务细节
- OpenClaw 负责调用 JARVIS 的统一文件入口和参考能力
- JARVIS 内部再做识别、解析、分流、入库、任务创建、参考查询

一句话：

`OpenClaw = 统一接入面`

而不是：

`OpenClaw = 只做定额任务桥接`

## 2. 当前现状

当前代码中，OpenClaw 桥接已存在，入口文件为：

- [openclaw.py](C:/Users/Administrator/Documents/trae_projects/auto-quota/web/backend/app/api/openclaw.py)

已经桥接的能力主要包括：

- 任务创建/查看
- 结果查看
- OpenClaw 审核草稿/确认
- 定额搜索
- 省份列表
- OpenAPI 暴露

已确认存在的 OpenClaw 路由包括：

- `/api/openclaw/health`
- `/api/openclaw/provinces`
- `/api/openclaw/quota-search`
- `/api/openclaw/quota-search/by-id`
- `/api/openclaw/quota-search/smart`
- `/api/openclaw/tasks`
- `/api/openclaw/tasks/{task_id}`
- `/api/openclaw/tasks/{task_id}/results`
- `/api/openclaw/tasks/{task_id}/results/{result_id}`
- `/api/openclaw/tasks/{task_id}/results/{result_id}/review-draft`
- `/api/openclaw/tasks/{task_id}/results/{result_id}/review-confirm`
- `/api/openclaw/tasks/{task_id}/results/confirm`
- `/api/openclaw/tasks/{task_id}/export`
- `/api/openclaw/tasks/{task_id}/export-final`

## 3. 现有能力分层判断

### 3.1 已有能力

当前已经可用：

- 定额任务主链
- 定额搜索
- 结果审核与确认
- 广联达结果回填
- 知识暂存/提升
- OpenClaw 对接这些已有能力

### 3.2 勉强可复用能力

以下接口不需要推翻，可作为下游能力继续复用：

- `POST /api/tasks`
- `GET /api/quota-search/smart`
- `POST /api/tools/price-backfill/preview`
- `POST /api/tools/price-backfill/execute`
- `/api/admin/knowledge-staging/*`

### 3.3 缺失能力

真正缺的是一整层：

- 统一文件入口
- 历史价格参考库入口
- 综合单价参考入口
- 统一参考查询口
- 批量辅助填表入口

这层目前不在现有 OpenClaw 桥接范围内。

## 4. 架构建议

建议采用两层接口架构。

### 4.1 JARVIS 内部原生接口层

JARVIS 内部先建设完整业务接口，例如：

- `/api/file-intake/*`
- `/api/price-documents/*`
- `/api/reference/*`

### 4.2 OpenClaw 外部统一入口层

OpenClaw 再桥接这些内部接口，对外暴露稳定入口：

- `/api/openclaw/file-intake/*`
- `/api/openclaw/reference/*`

这样做的好处：

- 内部业务接口可以逐步演进
- OpenClaw 外部契约稳定
- 权限、审计、服务账号、OpenAPI 统一放在 OpenClaw 层

## 5. 接口分层建议

### 5.1 内部原生接口

#### P0：统一文件入口

- `POST /api/file-intake/upload`
- `GET /api/file-intake/{file_id}`
- `POST /api/file-intake/{file_id}/classify`
- `POST /api/file-intake/{file_id}/parse`
- `POST /api/file-intake/{file_id}/route`

职责：

- 接收任意文件
- 识别文件类型
- 做结构化抽取
- 分流到对应下游

#### P1：历史价格文档与设备价参考

- `POST /api/price-documents`
- `GET /api/price-documents`
- `GET /api/price-documents/{id}`
- `POST /api/price-documents/{id}/parse`
- `GET /api/price-items/search`
- `GET /api/reference/item-price`

职责：

- 接收历史价格文档
- 搜设备/材料报价条目
- 查单价参考

#### P2：综合单价参考

- `POST /api/bill-price-documents`
- `POST /api/bill-price-documents/{id}/parse`
- `GET /api/composite-price/search`
- `GET /api/reference/composite-price`

职责：

- 接收带定额清单/综合单价文档
- 解析清单项和综合单价
- 返回综合单价参考

#### P3：统一参考与批量填表

- `GET /api/reference/search`
- `POST /api/reference/batch-fill`
- `POST /api/reference/batch-preview`
- `POST /api/reference/batch-execute`

职责：

- 统一返回设备价参考 + 综合单价参考 + 历史样本
- 批量辅助新工程填表

### 5.2 OpenClaw 统一桥接接口

在内部原生接口稳定后，OpenClaw 再桥接一层。

#### P0：文件统一入口桥接

- `POST /api/openclaw/file-intake/upload`
- `GET /api/openclaw/file-intake/{file_id}`
- `POST /api/openclaw/file-intake/{file_id}/classify`
- `POST /api/openclaw/file-intake/{file_id}/parse`
- `POST /api/openclaw/file-intake/{file_id}/route`

#### P1：价格参考桥接

- `GET /api/openclaw/reference/item-price`
- `GET /api/openclaw/reference/composite-price`
- `GET /api/openclaw/reference/search`

#### P2：批量填表桥接

- `POST /api/openclaw/reference/batch-fill`
- `POST /api/openclaw/reference/batch-preview`
- `POST /api/openclaw/reference/batch-execute`

## 6. 为什么要让 OpenClaw 做统一入口

### 6.1 对外契约统一

外部系统不用区分：

- 这是定额任务
- 这是历史价格文档
- 这是综合单价样本
- 这是要做批量填表

只需要先把文件或查询请求交给 OpenClaw。

### 6.2 权限和审计统一

OpenClaw 这一层已经有：

- 服务账号
- API key
- OpenAPI 暴露
- 人审确认语义

继续承接统一入口最自然。

### 6.3 前后演进解耦

内部 JARVIS 可以逐步增加：

- 文件分类器
- 价格解析器
- 统一参考搜索器

OpenClaw 对外保持稳定，不要求外部跟着内部结构变化频繁调整。

## 7. 文件入口的标准分流

统一文件入口识别后，至少分为以下类型：

- `quota_task_file`
- `historical_quote_file`
- `priced_bill_file`
- `knowledge_source_file`
- `other`

### 7.1 `quota_task_file`

分流到：

- `POST /api/tasks`

### 7.2 `historical_quote_file`

分流到：

- `POST /api/price-documents`

### 7.3 `priced_bill_file`

分流到：

- 学习层
- 综合单价参考层

也就是复用“带定额清单统一接头方案”。

### 7.4 `knowledge_source_file`

分流到：

- `knowledge staging`

## 8. 与已有接口的关系

建议原则：

- 不推翻已有主链
- 不直接让 OpenClaw 重写内部业务逻辑
- OpenClaw 只做桥接和统一接入面

也就是说：

- `tasks/results/quota-search` 继续保留
- `price-backfill` 继续保留
- `knowledge-staging` 继续保留
- 新增的是统一入口层和参考层

## 9. 命名建议

如果决定让 OpenClaw 成为统一入口，建议命名上保持两件事：

### 9.1 内部接口用业务名

例如：

- `/api/file-intake/*`
- `/api/reference/*`
- `/api/price-documents/*`

### 9.2 OpenClaw 接口用桥接名

例如：

- `/api/openclaw/file-intake/*`
- `/api/openclaw/reference/*`

不要把业务接口本身都塞进 `openclaw.py` 里直接实现。

## 10. 开发顺序建议

### P0

先做内部统一文件入口：

- `POST /api/file-intake/upload`
- `GET /api/file-intake/{file_id}`
- `POST /api/file-intake/{file_id}/classify`
- `POST /api/file-intake/{file_id}/parse`

然后 OpenClaw 桥接同名能力。

### P1

做内部价格参考主线：

- `POST /api/price-documents`
- `POST /api/price-documents/{id}/parse`
- `GET /api/reference/item-price`

然后 OpenClaw 桥接：

- `GET /api/openclaw/reference/item-price`

### P2

做综合单价参考：

- `POST /api/bill-price-documents`
- `POST /api/bill-price-documents/{id}/parse`
- `GET /api/reference/composite-price`

然后 OpenClaw 桥接：

- `GET /api/openclaw/reference/composite-price`

### P3

做统一查询和批量辅助填表：

- `GET /api/reference/search`
- `POST /api/reference/batch-fill`

然后 OpenClaw 桥接：

- `GET /api/openclaw/reference/search`
- `POST /api/openclaw/reference/batch-fill`

## 11. 最终判断

你的判断是对的：

- 当前 JARVIS 已经有“定额任务 + 定额搜索 + 结果审核 + 回填广联达结果”
- 但还没有“统一文件入口 + 历史价格参考库 + 综合单价参考 + 辅助填新表”

所以最合理的路线不是继续往旧任务接口硬塞，而是：

- 内部先补统一文件入口和参考层
- OpenClaw 对外做统一桥接入口

## 12. 一句话结论

如果要让 OpenClaw 做统一入口，正确做法是：

- `OpenClaw 负责统一接入`
- `JARVIS 内部负责文件识别、解析、分流、学习、参考查询`
- `新能力先做内部原生接口，再做 OpenClaw 桥接`
