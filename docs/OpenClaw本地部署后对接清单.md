# OpenClaw 本地部署后对接清单

本文只解决一件事:

- 你已经把 OpenClaw 部署到本地
- 现在要让它接入 `auto-quota`
- 并且明确后续应该怎么测、怎么跑

## 1. 先明确关系

本地 OpenClaw 不是让 `auto-quota` 主动连你。

现在的模式是:

1. 你的本地 OpenClaw 主动请求 `auto-quota`
2. 请求入口统一走 `/api/openclaw/...`
3. 请求头统一带 `X-OpenClaw-Key`
4. OpenClaw 负责读取任务、提交审核建议
5. 最终是否采纳，仍然由人工确认

所以:

- 不需要额外给 OpenClaw 开回调端口
- 不需要让 OpenClaw 直连数据库
- 不需要网页登录后再调用

## 2. 你要连的地址

如果你接的是当前懒猫线上环境，用这个:

```text
https://autoquota.microfeicat2025.heiyu.space/api/openclaw/openapi.json
```

如果你接的是本机本地启动的 `auto-quota` 后端，用这个:

```text
http://127.0.0.1:8000/api/openclaw/openapi.json
```

说明:

- OpenClaw 应该吃的是 OpenAPI 地址
- 真正业务接口也都在同一个前缀下: `/api/openclaw/...`

## 3. auto-quota 侧必须具备的配置

至少要有:

```env
OPENCLAW_API_KEY=换成你自己的随机长字符串
```

如果你跑的是本地 `auto-quota`:

- `backend` 要配这个 key
- `celery-worker` 也要配同一个 key
- 改完后要重启服务

可选配置:

```env
OPENCLAW_SERVICE_EMAIL=openclaw@system.local
OPENCLAW_SERVICE_NICKNAME=OpenClaw
OPENCLAW_SERVICE_QUOTA=1000000
```

## 4. OpenClaw 侧最小配置

OpenClaw 里至少要配置两样:

1. OpenAPI URL
2. 请求头 `X-OpenClaw-Key`

最小可用示意:

```text
OpenAPI URL:
https://autoquota.microfeicat2025.heiyu.space/api/openclaw/openapi.json

Header:
X-OpenClaw-Key: 你在 auto-quota 里配置的 OPENCLAW_API_KEY
```

如果你接本地后端，就把 URL 换成本机地址:

```text
http://127.0.0.1:8000/api/openclaw/openapi.json
```

## 5. 第一轮只做连通性验证

先不要一上来跑整表。

先用下面 3 个接口验证:

1. `GET /api/openclaw/health`
2. `GET /api/openclaw/provinces`
3. `GET /api/openclaw/tasks`

### Windows PowerShell 示例

先设变量:

```powershell
$base = "https://autoquota.microfeicat2025.heiyu.space"
$key = "替换成你的 OPENCLAW_API_KEY"
$headers = @{ "X-OpenClaw-Key" = $key }
```

测健康:

```powershell
Invoke-RestMethod -Uri "$base/api/openclaw/health" -Headers $headers
```

测省份:

```powershell
Invoke-RestMethod -Uri "$base/api/openclaw/provinces" -Headers $headers
```

拉任务:

```powershell
Invoke-RestMethod -Uri "$base/api/openclaw/tasks" -Headers $headers
```

预期:

- `health` 返回 `status=ok`
- `provinces` 能拿到定额库列表
- `tasks` 能正常返回任务列表或空列表

## 6. 连通后再跑正式流程

标准顺序如下:

1. `GET /api/openclaw/provinces`
2. `POST /api/openclaw/tasks`
3. `GET /api/openclaw/tasks/{task_id}`
4. `GET /api/openclaw/tasks/{task_id}/results`
5. `GET /api/openclaw/tasks/{task_id}/review-items`
6. `PUT /api/openclaw/tasks/{task_id}/results/{result_id}/review-draft`
7. 人工在结果页确认，或调用 `POST /api/openclaw/tasks/{task_id}/results/{result_id}/review-confirm`

你可以把它理解成:

- Jarvis 先跑
- OpenClaw 再复核
- 人工最后拍板

## 7. 你本地 OpenClaw 现在最适合怎么用

建议直接按这个顺序接:

### 场景 A: 只想让 OpenClaw 查和审

适合先跑通:

- 读 `tasks`
- 读 `results`
- 读 `review-items`
- 提交 `review-draft`

这样最稳，不会一开始就把流程做重。

### 场景 B: 让 OpenClaw 发起整表套定额

再增加:

- `POST /api/openclaw/tasks`

然后再轮询状态、拉结果。

## 8. 常见报错怎么判断

### 401 `OpenClaw API Key 无效`

说明:

- `X-OpenClaw-Key` 不对
- 或者 OpenClaw 没带这个 header

先查:

- OpenClaw 请求头
- `auto-quota` 当前生效的 `OPENCLAW_API_KEY`

### 503 `OPENCLAW_API_KEY 未配置`

说明:

- `auto-quota` 后端没读到环境变量
- 或者只改了一个服务，没有同时改 `backend` 和 `celery-worker`

处理:

- 同时改两个服务
- 重启或重新部署

### `当前结果为红灯(<75)，保持现有规则不变，OpenClaw 不能提交审核建议`

说明:

- 桥是通的
- key 也是对的
- 卡在服务端审核策略

这不是 OpenClaw 本地部署问题，而是 `auto-quota` 后端策略限制。

### 能读不能写

优先检查:

1. 是否只接了读接口
2. 是否打到了错误环境
3. `review-draft` / `review-confirm` 是否被策略拦截

## 9. 现在这套系统里的职责边界

当前不是:

- OpenClaw 直接写正式知识库

当前是:

1. OpenClaw 提建议
2. 人工确认
3. 系统再把确认结果映射进 staging / promotion 流程

所以你后面让 OpenClaw 干活时，建议就按下面这句话定义:

```text
先读取 Jarvis 任务和结果，再对需要复核的结果提交 review-draft，不要直接假设自己已经完成最终确认。
```

## 10. 最小落地建议

如果你现在想最快跑起来，就只做下面 4 步:

1. 在 `auto-quota` 里确认 `OPENCLAW_API_KEY`
2. 在本地 OpenClaw 配置 OpenAPI URL + `X-OpenClaw-Key`
3. 先打通 `health / provinces / tasks`
4. 再让 OpenClaw 从 `review-items` 开始做复核建议

这样最省事，也最不容易把问题混到一起。

## 11. 给 OpenClaw 的任务指令模板

你后面可以直接这样告诉本地 OpenClaw:

```text
请连接 auto-quota 的 OpenClaw 桥接接口，先读取待处理任务和 review-items。
优先处理需要复核的结果，只提交 review-draft，不要直接假设已经最终确认。
如果存在 OpenClaw 建议定额，就给出结构化建议；如果候选不足，再尝试 quota-search。
```

如果你要它直接复核某个任务，可以这样说:

```text
请复核 auto-quota 任务 {task_id}。
先读取任务结果和 review-items，重点检查 Jarvis 结果与候选定额是否明显错大类、错专业、错对象。
对需要修改的结果提交 review-draft；对没有把握的结果保持维持建议。
```

如果你只想让它查正确定额，可以这样说:

```text
请不要直接提交审核建议。
只使用 auto-quota 的 quota-search / quota-search/smart 帮我查这条清单更合适的定额，并返回候选理由。
```
