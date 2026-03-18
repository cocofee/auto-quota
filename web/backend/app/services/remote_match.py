"""
远程匹配客户端 — 懒猫端通过HTTP调用本地电脑的匹配API

用法（在 Celery worker 中）：
    from app.services.remote_match import RemoteMatchClient

    client = RemoteMatchClient(url="http://192.168.1.100:9100", api_key="xxx")
    if not client.check_health():
        raise RuntimeError("本地匹配服务未启动")

    match_id = client.submit_match("/path/to/input.xlsx", {"province": "北京2024"})
    while True:
        prog = client.poll_progress(match_id)
        if prog["status"] != "running":
            break
        time.sleep(3)
    results = client.get_results(match_id)
"""

import time
from pathlib import Path

import httpx
from loguru import logger


class RemoteMatchClient:
    """同步HTTP客户端，供 Celery worker 调用本地匹配API"""

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._headers = {"X-API-Key": api_key}

    # ----------------------------------------------------------
    # 健康检查
    # ----------------------------------------------------------

    def check_health(self, timeout: float = 5.0) -> dict | None:
        """检查本地匹配服务是否在线

        返回：健康信息字典（在线时），None（离线时）
        """
        try:
            resp = httpx.get(
                f"{self.base_url}/health",
                headers=self._headers,
                timeout=timeout,
            )
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 401:
                logger.error("远程匹配API密钥不正确")
                return None
            logger.warning(f"远程匹配API健康检查异常: HTTP {resp.status_code}")
            return None
        except httpx.ConnectError:
            logger.warning("无法连接本地匹配服务，请确认电脑上的匹配服务已启动")
            return None
        except httpx.TimeoutException:
            logger.warning("本地匹配服务响应超时")
            return None

    # ----------------------------------------------------------
    # 提交匹配任务
    # ----------------------------------------------------------

    def submit_match(self, file_path: str, params: dict) -> str:
        """上传Excel到本地匹配API，返回 match_id

        参数:
            file_path: 本地Excel文件路径
            params: 匹配参数 {"province", "mode", "sheet", "limit", "no_experience", "agent_llm"}

        返回: match_id（字符串）

        异常: RuntimeError（连接失败、鉴权失败、服务忙等）
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"Excel文件不存在: {file_path}")

        # 构建 multipart 表单数据
        form_data = {"province": params.get("province", "")}
        if params.get("mode"):
            form_data["mode"] = params["mode"]
        if params.get("sheet"):
            form_data["sheet"] = params["sheet"]
        if params.get("limit"):
            form_data["limit"] = str(params["limit"])
        if params.get("no_experience"):
            form_data["no_experience"] = "true"
        if params.get("agent_llm"):
            form_data["agent_llm"] = params["agent_llm"]

        try:
            with open(file_path, "rb") as f:
                resp = httpx.post(
                    f"{self.base_url}/match",
                    headers=self._headers,
                    data=form_data,
                    files={"file": (file_path.name, f, "application/octet-stream")},
                    timeout=60.0,  # 上传可能需要一点时间
                )

            if resp.status_code == 200:
                return resp.json()["match_id"]

            # 错误处理
            detail = _extract_detail(resp)
            if resp.status_code == 401:
                raise RuntimeError(f"API Key不正确: {detail}")
            if resp.status_code == 429:
                raise RuntimeError(f"本地匹配服务繁忙: {detail}")
            raise RuntimeError(f"提交匹配失败(HTTP {resp.status_code}): {detail}")

        except httpx.ConnectError:
            raise RuntimeError(
                "无法连接本地匹配服务，请确认：\n"
                "1. 电脑上的匹配服务已启动（双击「启动匹配服务.bat」）\n"
                "2. LOCAL_MATCH_URL 配置的IP地址正确\n"
                "3. 电脑和懒猫盒子在同一个局域网"
            )
        except httpx.TimeoutException:
            raise RuntimeError("上传Excel到本地匹配服务超时（60秒），请检查网络连接")

    # ----------------------------------------------------------
    # 查询进度
    # ----------------------------------------------------------

    def poll_progress(self, match_id: str, retries: int = 3) -> dict:
        """查询匹配进度（带重试）

        返回: {"status", "progress", "current_idx", "message", "error"}
        """
        last_error = None
        for attempt in range(retries):
            try:
                resp = httpx.get(
                    f"{self.base_url}/match/{match_id}/progress",
                    headers=self._headers,
                    timeout=10.0,
                )
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code == 404:
                    raise RuntimeError(f"任务 {match_id} 在本地服务中不存在（可能已过期）")
                raise RuntimeError(f"查询进度失败(HTTP {resp.status_code})")

            except (httpx.ConnectError, httpx.TimeoutException) as e:
                last_error = e
                if attempt < retries - 1:
                    wait = 2 ** attempt  # 指数退避：1, 2, 4秒
                    logger.warning(f"查询进度失败（第{attempt+1}次），{wait}秒后重试: {e}")
                    time.sleep(wait)

        raise RuntimeError(f"查询进度失败（重试{retries}次仍无法连接）: {last_error}")

    # ----------------------------------------------------------
    # 获取结果
    # ----------------------------------------------------------

    def get_results(self, match_id: str, retries: int = 3) -> dict:
        """获取匹配结果JSON（带重试）

        返回: {"results": [...], "stats": {...}}
        """
        last_error = None
        for attempt in range(retries):
            try:
                resp = httpx.get(
                    f"{self.base_url}/match/{match_id}/results",
                    headers=self._headers,
                    timeout=120.0,  # 结果可能很大，给足时间
                )
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code == 409:
                    raise RuntimeError("任务还在执行中，请等待完成后再获取结果")
                detail = _extract_detail(resp)
                raise RuntimeError(f"获取结果失败(HTTP {resp.status_code}): {detail}")

            except (httpx.ConnectError, httpx.TimeoutException) as e:
                last_error = e
                if attempt < retries - 1:
                    wait = 2 ** attempt
                    logger.warning(f"获取结果失败（第{attempt+1}次），{wait}秒后重试: {e}")
                    time.sleep(wait)

        raise RuntimeError(f"获取结果失败（重试{retries}次仍无法连接）: {last_error}")

    def download_excel(self, match_id: str, save_path: str, retries: int = 3) -> bool:
        """下载匹配结果Excel文件

        返回: True（成功），False（文件不存在）
        """
        last_error = None
        for attempt in range(retries):
            try:
                resp = httpx.get(
                    f"{self.base_url}/match/{match_id}/output.xlsx",
                    headers=self._headers,
                    timeout=60.0,
                )
                if resp.status_code == 200:
                    Path(save_path).parent.mkdir(parents=True, exist_ok=True)
                    Path(save_path).write_bytes(resp.content)
                    return True
                if resp.status_code == 404:
                    return False
                raise RuntimeError(f"下载Excel失败(HTTP {resp.status_code})")

            except (httpx.ConnectError, httpx.TimeoutException) as e:
                last_error = e
                if attempt < retries - 1:
                    wait = 2 ** attempt
                    logger.warning(f"下载Excel失败（第{attempt+1}次），{wait}秒后重试: {e}")
                    time.sleep(wait)

        logger.error(f"下载Excel失败（重试{retries}次）: {last_error}")
        return False


def _extract_detail(resp: httpx.Response) -> str:
    """从HTTP响应中提取错误详情"""
    try:
        data = resp.json()
        return data.get("detail", str(data))
    except Exception:
        return resp.text[:200]
