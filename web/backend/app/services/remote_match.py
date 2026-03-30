"""Remote match client used by the LazyCat backend worker."""

from __future__ import annotations

import time
from pathlib import Path

import httpx
from loguru import logger

from app.services.local_http import local_match_request


class RemoteMatchClient:
    """Synchronous HTTP client for forwarding jobs to a LAN match service."""

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._headers = {"X-API-Key": api_key}

    def check_health(self, timeout: float = 5.0) -> dict | None:
        """Return remote health payload when reachable, otherwise ``None``."""
        try:
            resp = local_match_request(
                "GET",
                f"{self.base_url}/health",
                headers=self._headers,
                timeout=timeout,
            )
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 401:
                logger.error("Remote match API key rejected by local service")
                return None
            logger.warning(f"Remote match health check failed: HTTP {resp.status_code}")
            return None
        except httpx.ConnectError:
            logger.warning("Cannot connect to local match service")
            return None
        except httpx.TimeoutException:
            logger.warning("Local match service health check timed out")
            return None

    def submit_match(self, file_path: str, params: dict) -> str:
        """Upload an Excel file to the local match API and return its ``match_id``."""
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"Excel file not found: {file_path}")

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
                resp = local_match_request(
                    "POST",
                    f"{self.base_url}/match",
                    headers=self._headers,
                    data=form_data,
                    files={"file": (file_path.name, f, "application/octet-stream")},
                    timeout=60.0,
                )

            if resp.status_code == 200:
                return resp.json()["match_id"]

            detail = _extract_detail(resp)
            if resp.status_code == 401:
                raise RuntimeError(f"API key invalid: {detail}")
            if resp.status_code == 429:
                raise RuntimeError(f"Local match service busy: {detail}")
            raise RuntimeError(f"Submit match failed (HTTP {resp.status_code}): {detail}")
        except httpx.ConnectError:
            raise RuntimeError(
                "Cannot connect to local match service.\n"
                "1. Ensure the PC-side match service is running\n"
                "2. Ensure LOCAL_MATCH_URL points to the correct LAN IP\n"
                "3. Ensure the PC and LazyCat box are on the same LAN"
            )
        except httpx.TimeoutException:
            raise RuntimeError("Upload to local match service timed out (60s)")

    def poll_progress(self, match_id: str, retries: int = 3) -> dict:
        """Poll remote progress with retry."""
        last_error = None
        for attempt in range(retries):
            try:
                resp = local_match_request(
                    "GET",
                    f"{self.base_url}/match/{match_id}/progress",
                    headers=self._headers,
                    timeout=10.0,
                )
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code == 404:
                    raise RuntimeError(f"Task {match_id} not found in local service")
                raise RuntimeError(f"Poll progress failed (HTTP {resp.status_code})")
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                last_error = e
                if attempt < retries - 1:
                    wait = 2 ** attempt
                    logger.warning(f"Poll progress failed (attempt {attempt + 1}), retry in {wait}s: {e}")
                    time.sleep(wait)

        raise RuntimeError(f"Poll progress failed after {retries} retries: {last_error}")

    def get_results(self, match_id: str, retries: int = 3) -> dict:
        """Fetch match JSON results with retry."""
        last_error = None
        for attempt in range(retries):
            try:
                resp = local_match_request(
                    "GET",
                    f"{self.base_url}/match/{match_id}/results",
                    headers=self._headers,
                    timeout=120.0,
                )
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code == 409:
                    raise RuntimeError("Task is still running; results are not ready yet")
                detail = _extract_detail(resp)
                raise RuntimeError(f"Get results failed (HTTP {resp.status_code}): {detail}")
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                last_error = e
                if attempt < retries - 1:
                    wait = 2 ** attempt
                    logger.warning(f"Get results failed (attempt {attempt + 1}), retry in {wait}s: {e}")
                    time.sleep(wait)

        raise RuntimeError(f"Get results failed after {retries} retries: {last_error}")

    def download_excel(self, match_id: str, save_path: str, retries: int = 3) -> bool:
        """Download the generated Excel result if available."""
        last_error = None
        for attempt in range(retries):
            try:
                resp = local_match_request(
                    "GET",
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
                raise RuntimeError(f"Download Excel failed (HTTP {resp.status_code})")
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                last_error = e
                if attempt < retries - 1:
                    wait = 2 ** attempt
                    logger.warning(f"Download Excel failed (attempt {attempt + 1}), retry in {wait}s: {e}")
                    time.sleep(wait)

        logger.error(f"Download Excel failed after {retries} retries: {last_error}")
        return False


def _extract_detail(resp: httpx.Response) -> str:
    """Extract a compact error detail from an HTTP response."""
    try:
        data = resp.json()
        return data.get("detail", str(data))
    except Exception:
        return resp.text[:200]
