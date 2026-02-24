import shutil
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import tools._select_province as sp


def _new_tmp_dir() -> Path:
    root = Path("output") / "test_tmp"
    root.mkdir(parents=True, exist_ok=True)
    d = root / f"select-province-{uuid4().hex}"
    d.mkdir(parents=True, exist_ok=False)
    return d


class _FailingConn:
    def __init__(self):
        self.closed = False

    def execute(self, *args, **kwargs):
        raise RuntimeError("db read failed")

    def close(self):
        self.closed = True


def test_get_db_count_closes_connection_on_error():
    tmp_dir = _new_tmp_dir()
    try:
        db_path = tmp_dir / "quota.db"
        db_path.write_text("x", encoding="utf-8")
        failing_conn = _FailingConn()

        with patch.object(sp.config, "get_quota_db_path", return_value=db_path):
            with patch.object(sp, "_db_connect", return_value=failing_conn):
                value = sp._get_db_count("测试省份")

        assert value == "已存在"
        assert failing_conn.closed is True
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_get_db_count_returns_first_import_when_missing(monkeypatch):
    missing_path = Path("this_path_should_not_exist_12345.db")
    monkeypatch.setattr(sp.config, "get_quota_db_path", lambda _: missing_path)
    assert sp._get_db_count("测试省份") == "首次导入"
