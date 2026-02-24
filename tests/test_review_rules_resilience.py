import shutil
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest

import tools.test_review_rules as review_rules


def _new_tmp_dir() -> Path:
    root = Path("output") / "test_tmp"
    root.mkdir(parents=True, exist_ok=True)
    d = root / f"review-rules-{uuid4().hex}"
    d.mkdir(parents=True, exist_ok=False)
    return d


class _FailingConn:
    def __init__(self):
        self.closed = False

    def cursor(self):
        raise RuntimeError("cursor init failed")

    def close(self):
        self.closed = True


def test_run_db_sample_test_closes_connection_on_cursor_error():
    tmp_dir = _new_tmp_dir()
    try:
        db_path = tmp_dir / "quota.db"
        db_path.write_text("x", encoding="utf-8")
        conn = _FailingConn()

        with patch.object(review_rules, "get_quota_db_path", return_value=str(db_path)):
            with patch.object(review_rules, "_db_connect", return_value=conn):
                with pytest.raises(RuntimeError, match="cursor init failed"):
                    review_rules.run_db_sample_test("测试省份")

        assert conn.closed is True
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
