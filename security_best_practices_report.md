# 智能编清单安全审查报告

## Executive Summary

本次仅审查“智能编清单”相关链路：前端页面、Web API 代理层、以及 `local_match_server.py` 的 `compile-bill` 接口。

结论：**还有明显漏洞**。其中最严重的是远程编清单服务把上传文件名直接拼进本地路径，存在路径穿越和任意文件覆盖风险；其次是 Web API 未强制登录，导致外部请求可以直接打到这条高消耗链路。另有本地服务默认固定 API Key、上传校验/限流缺失等问题。

## Critical

### [BC-001] 远程编清单接口存在路径穿越，可覆盖本地任意可写文件

- Rule ID: FASTAPI-FILES-001
- Severity: Critical
- Location:
  - `local_match_server.py:267-270`
  - `local_match_server.py:343-346`
- Evidence:

```python
work_dir = TEMP_DIR / f"compile_{uuid.uuid4().hex[:8]}"
work_dir.mkdir(parents=True, exist_ok=True)
input_path = work_dir / filename
input_path.write_bytes(content)
```

- Impact:
  - `filename` 完全来自上传请求，未做 `basename`/规范化/越界校验。
  - 攻击者可构造 `..\..\..\..\..\config.py` 或绝对路径，覆盖服务进程有权限写入的文件。
  - 在 `MATCH_BACKEND=remote` 场景下，这条写入发生在用户本机运行的 `local_match_server.py` 上，影响面更大。
- Fix:
  - 绝对不要直接使用客户端文件名拼接路径。
  - 改成服务端生成固定文件名，例如 `input.xlsx` 或 `uuid + normalized_suffix`。
  - 对保存路径做 `resolve()` 后的父目录校验，确保目标路径始终位于 `work_dir` 内。
  - 复用现有 `/match` 接口中的 `validate_excel_upload()` 和安全命名逻辑。
- Mitigation:
  - 在修复前，不要把本地匹配服务暴露到局域网或公网。
  - 立即轮换 `LOCAL_MATCH_API_KEY`。
- False positive notes:
  - 我本地验证了 `Path(work_dir) / "..\\..\\..\\..\\..\\config.py"` 会解析到工作目录外；是否能覆盖某个具体文件，取决于目标目录是否存在且进程有写权限。

## High

### [BC-002] 编清单 Web API 未强制认证，外部可直接调用高消耗接口

- Rule ID: FASTAPI-AUTH-001
- Severity: High
- Location:
  - `web/backend/app/api/bill_library.py:227-299`
  - 对比前端登录保护：`web/frontend/src/routes/index.tsx:42-68`
- Evidence:

```python
@router.post("/bill-compiler/preview")
async def preview_compile(...):
    ...

@router.post("/bill-compiler/execute")
async def execute_compile(...):
    ...
```

- Impact:
  - 这两个接口没有 `Depends(get_current_user)`。
  - 虽然前端页面被 `RequireAuth` 包住，但 API 本身可被绕过前端直接访问。
  - 结果是未登录请求也能触发文件解析、编清单计算，以及 remote 模式下对本地服务的转发。
  - 和 [BC-001] 叠加时，攻击者不需要先拿到站内账号，就可能借 Web API 打到用户本机的编清单服务。
- Fix:
  - 给这两个路由增加 `user: User = Depends(get_current_user)`。
  - 如果工具功能只允许管理员使用，则改为 `Depends(require_admin)`。
  - 同时为这类工具接口增加请求频率和并发限制。
- Mitigation:
  - 在边界层先限制 `/api/tools/*` 仅对已登录会话开放。
- False positive notes:
  - 如果你在反向代理层已经额外做了鉴权，需运行时确认；当前仓库代码里看不到。

### [BC-003] 本地匹配服务存在硬编码默认 API Key，且默认监听 `0.0.0.0`

- Rule ID: FASTAPI-AUTH-002
- Severity: High
- Location:
  - `local_match_server.py:35`
  - `local_match_server.py:1221-1229`
  - `local_match_server.py:1235-1239`
- Evidence:

```python
API_KEY = os.getenv("LOCAL_MATCH_API_KEY", "f4d20d44381b4368")
...
print(f"  API Key: {API_KEY}")
...
uvicorn.run(
    app,
    host="0.0.0.0",
    port=PORT,
    log_level="info",
)
```

- Impact:
  - 一旦环境变量未配置，就退回到固定密钥。
  - 服务默认对所有网卡开放，局域网内任何拿到默认值的人都能直接调用 `/compile-bill/*`、`/match` 等接口。
  - 该问题会显著放大 [BC-001] 的危害。
- Fix:
  - 启动时强制要求显式配置 `LOCAL_MATCH_API_KEY`，不允许 fallback 到固定值。
  - 生产/共享网络环境默认仅监听 `127.0.0.1`，确需远程访问时再显式放开。
  - 不要在控制台打印完整 API Key。
- Mitigation:
  - 立刻改成高强度随机密钥，并检查局域网暴露范围。
- False positive notes:
  - 如果这台机器永远不出现在可信单机环境以外，风险会下降；但当前代码默认行为仍不安全。

## Medium

### [BC-004] 上传校验过弱，且缺少请求体/文件大小限制，易被用于 DoS

- Rule ID: FASTAPI-UPLOAD-001
- Severity: Medium
- Location:
  - `web/backend/app/api/bill_library.py:23-42`
  - `web/backend/app/api/bill_library.py:242-243`
  - `web/backend/app/api/bill_library.py:276-277`
  - `local_match_server.py:263-270`
  - `local_match_server.py:340-346`
- Evidence:

```python
def _validate_excel(file: UploadFile, label: str) -> None:
    filename = file.filename or ""
    valid_exts = (".xlsx", ".xls")
    if not any(filename.lower().endswith(ext) for ext in valid_exts):
        ...

content = await file.read()
```

- Impact:
  - Web API 只校验扩展名，不校验文件头/MIME/真实格式。
  - `compile-bill` 本地服务甚至连扩展名以外的内容校验都没有，直接整包读入内存并落盘。
  - 没看到应用层文件大小限制，攻击者可通过超大文件或畸形文件拖垮内存、CPU 或磁盘。
- Fix:
  - 统一改用 `validate_excel_upload()` 这类基于文件头的校验。
  - 为上传接口增加显式大小上限和失败快速返回。
  - 优先流式处理，避免 `await file.read()` 把整个文件一次性读入内存。
  - 在反向代理层同步配置 body size limit。
- Mitigation:
  - 先在网关层限制上传体积，并给工具接口加速率限制。
- False positive notes:
  - 如果网关已有限流/限体积，影响会降低；但应用代码本身仍无兜底。

## Open Questions / Assumptions

- 本报告假设 `MATCH_BACKEND=remote` 是“智能编清单”常见部署方式；从 `web/backend/app/api/bill_library.py` 的设计看，这一假设成立概率较高。
- 未在仓库内看到针对 `/api/tools/bill-compiler/*` 的统一网关鉴权、WAF 规则或上传大小限制；如线上有额外防护，需要结合部署配置再复核。

## Recommended Fix Order

1. 先修 [BC-001]：禁止客户端文件名参与路径拼接。
2. 再修 [BC-002]：给编清单 API 强制加登录依赖。
3. 同步修 [BC-003]：移除默认 API Key，收紧监听地址。
4. 最后补 [BC-004]：统一上传校验、大小限制和速率限制。
