import httpx
try:
    r = httpx.get("http://47.243.74.21:8080", timeout=10)
    print(f"OK: status={r.status_code}")
except Exception as e:
    print(f"FAIL: {e}")
