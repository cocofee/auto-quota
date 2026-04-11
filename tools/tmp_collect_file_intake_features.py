from pathlib import Path
import json
import sys

PROJECT_ROOT = Path(r"C:\Users\Administrator\Documents\trae_projects\auto-quota")
BACKEND_ROOT = PROJECT_ROOT / "web" / "backend"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))

from app.api.file_intake import _load_headers_from_excel

root = Path(r"F:\jarvis")
results = []
keywords = ["工程量", "清单", "预算", "报价", "审核", "安装工程", "土建工程", "电缆", "配电", "图纸"]
for path in root.rglob("*.xlsx"):
    text = str(path)
    if not any(word in text for word in keywords):
        continue
    try:
        headers = _load_headers_from_excel(path)[:30]
    except Exception:
        continue
    results.append({
        "path": text,
        "name": path.name,
        "headers": headers,
    })
    if len(results) >= 8:
        break
print(json.dumps(results, ensure_ascii=False, indent=2))
