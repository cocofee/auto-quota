import json
from pathlib import Path
import shutil
from uuid import uuid4
from unittest.mock import patch

from tools.jarvis_store import store_batch


def _new_tmp_dir() -> Path:
    root = Path("output") / "test_tmp"
    root.mkdir(parents=True, exist_ok=True)
    d = root / f"jarvis-store-{uuid4().hex}"
    d.mkdir(parents=True, exist_ok=False)
    return d


def test_store_batch_prefers_item_province():
    tmp_dir = _new_tmp_dir()
    payload = [
        {
            "name": "测试清单",
            "quota_id": "Q-1",
            "quota_name": "测试定额",
            "province": "兄弟库",
        }
    ]
    json_path = tmp_dir / "corrections.json"
    try:
        json_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        with patch("tools.jarvis_store.store_one", return_value=True) as mock_store:
            store_batch(str(json_path), province="主库", confirmed=False)

        _, kwargs = mock_store.call_args
        assert kwargs["province"] == "兄弟库"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
