# OpenClaw 接入说明

## 目标

让懒猫微服里的 OpenClaw 直接操作 `auto-quota`，不依赖网页登录态；同时保留“OpenClaw 先给建议，管理员再确认”的审核边界。

## 当前真实链路

现在的链路不是 “OpenClaw 拿到 Key 后直接写正式知识”，而是：

1. OpenClaw 用 `X-OpenClaw-Key` 调 `/api/openclaw/...`
2. OpenClaw 提交审核草稿 `review-draft`
3. 管理员在结果页执行人工二次确认 `review-confirm`
4. `review-confirm` 通过后，系统自动把这次真实审核结果映射进 staging
5. 管理员再到“候选确认与晋升”页确认、驳回或执行晋升

也就是说：

- `X-OpenClaw-Key` 解决的是 “OpenClaw 能不能接入桥接 API”
- 真正写入 `audit_errors / promotion_queue`，发生在 **管理员确认之后**

## 配置

在 `auto-quota` 的环境变量里至少配置：

```env
OPENCLAW_API_KEY=换成你自己的随机长字符串
```

懒猫部署时：

- `backend` 和 `celery-worker` 必须配置成同一个 `OPENCLAW_API_KEY`
- 不要继续使用仓库里的示例明文值

另外，懒猫入口层默认会先走微服登录门禁。要让 OpenClaw 直接访问桥接接口，需要在
`application` 下增加：

```yml
public_path:
  - /api/openclaw/
```

这一步只表示放行 `/api/openclaw/` 这组路径，真正的业务鉴权仍然由
`X-OpenClaw-Key` 完成。

## 给 OpenClaw 的入口

把下面这个 OpenAPI 地址给 OpenClaw：

```text
https://你的-autoquota-域名/api/openclaw/openapi.json
```

请求头统一带：

```text
X-OpenClaw-Key: 你配置的 OPENCLAW_API_KEY
```

## 当前可用桥接接口

- `GET /api/openclaw/health`
- `GET /api/openclaw/provinces`
- `GET /api/openclaw/quota-search`
- `GET /api/openclaw/quota-search/by-id`
- `GET /api/openclaw/quota-search/smart`
- `POST /api/openclaw/tasks`
- `GET /api/openclaw/tasks`
- `GET /api/openclaw/tasks/{task_id}`
- `GET /api/openclaw/tasks/{task_id}/results`
- `GET /api/openclaw/tasks/{task_id}/review-items`
- `GET /api/openclaw/tasks/{task_id}/review-pending`
- `PUT /api/openclaw/tasks/{task_id}/results/{result_id}/review-draft`
- `POST /api/openclaw/tasks/{task_id}/results/{result_id}/review-confirm`
- `POST /api/openclaw/tasks/{task_id}/results/auto-confirm-green`
- `POST /api/openclaw/tasks/{task_id}/results/confirm`
- `GET /api/openclaw/tasks/{task_id}/export`
- `GET /api/openclaw/tasks/{task_id}/export-final`

## 默认审核策略

- 绿灯：`>= 90`，允许自动确认
- 黄灯：`70-89`，允许 OpenClaw 提交审核草稿，等待人工二次确认
- 红灯：`< 70`，只允许诊断，不允许 OpenClaw 直接提交修正建议

## 推荐接法

### 1. 只做查询 / 调用主链

1. 先调 `/api/openclaw/provinces` 确认可用定额库
2. 查询类需求优先走 `/api/openclaw/quota-search/smart`
3. 整表套定额走 `POST /api/openclaw/tasks`
4. 用 `GET /api/openclaw/tasks/{task_id}` 轮询任务状态
5. 用 `GET /api/openclaw/tasks/{task_id}/results` 取结果

### 2. 走 OpenClaw 审核建议

1. OpenClaw 对某条结果调用 `review-draft`
2. 管理员进入结果页，人工执行 `review-confirm`
3. 系统自动把这次确认结果写进 `audit_errors / promotion_queue`
4. 管理员再去 “候选确认与晋升” 页处理晋升

## 当前限制

- 现在只有 `OPENCLAW_API_KEY` 环境变量方案
- 还没有“后台生成 / 轮换 / 废止 OpenClaw Key”的管理页
- `review-confirm` 目前仍然要求管理员身份，不是单靠 OpenClaw Key 就能直接完成
- 所以当前实现是 “OpenClaw 提建议 + 管理员确认”，不是“OpenClaw 全自动直接入正式知识层”

## 说明

- 这套接口使用独立的 OpenClaw 服务账号，不依赖网页登录 Cookie
- `public_path` 只会关闭懒猫入口层对该路径的强制登录，不会替代应用自身鉴权
- `OPENCLAW_API_KEY` 没配时，接口会返回 `503`
- 这把 Key 等同于内部自动化凭证，不要暴露到公开页面
