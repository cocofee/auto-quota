import json
import shutil
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest

import tools.jarvis_auto_review as auto_review_mod


def _new_tmp_dir() -> Path:
    root = Path("output") / "test_tmp"
    root.mkdir(parents=True, exist_ok=True)
    d = root / f"jarvis-auto-review-{uuid4().hex}"
    d.mkdir(parents=True, exist_ok=False)
    return d


class _FakeConn:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_auto_review_closes_db_connection_on_correct_phase_error():
    tmp_dir = _new_tmp_dir()
    try:
        review_path = tmp_dir / "review_sample.json"
        review_path.write_text(
            json.dumps({"results": []}, ensure_ascii=False),
            encoding="utf-8",
        )

        db_path = tmp_dir / "quota.db"
        db_path.write_text("db", encoding="utf-8")
        conn = _FakeConn()

        with patch.object(auto_review_mod, "get_quota_db_path", return_value=str(db_path)):
            with patch.object(auto_review_mod, "_db_connect", return_value=conn):
                with patch.object(auto_review_mod, "_correct_phase", side_effect=RuntimeError("phase2 failed")):
                    with pytest.raises(RuntimeError, match="phase2 failed"):
                        auto_review_mod.auto_review(str(review_path), province="测试省份")

        assert conn.closed is True
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
