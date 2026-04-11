from __future__ import annotations

import httpx


def local_match_async_client(*, timeout: float | httpx.Timeout) -> httpx.AsyncClient:
    """Build an HTTP client for local/LAN match-service calls.

    `trust_env=False` avoids accidental proxy use from HTTP_PROXY/HTTPS_PROXY,
    which breaks access to LAN endpoints such as `http://192.168.x.x:9300`.
    """
    return httpx.AsyncClient(timeout=timeout, trust_env=False)


def local_match_request(method: str, url: str, **kwargs) -> httpx.Response:
    """Send a sync request to the local/LAN match-service without env proxies."""
    return httpx.request(method, url, trust_env=False, **kwargs)
