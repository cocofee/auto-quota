import sqlite3
from unittest.mock import patch

import pytest
from src.quota_search import search_by_id, search_quota_db, search_series


def _build_quota_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE quotas (
            quota_id TEXT,
            name TEXT,
            unit TEXT
        )
        """
    )
    conn.executemany(
        "INSERT INTO quotas (quota_id, name, unit) VALUES (?, ?, ?)",
        [
            ("C1-4-1", "电梯安装 基础型", "台"),
            ("C1-4-2", "电梯安装 进阶型", "台"),
            ("C10-2-1", "排水塑料管安装", "m"),
        ],
    )
    conn.commit()
    return conn


def test_search_quota_db_empty_keywords_without_section_returns_empty():
    conn = _build_quota_conn()
    try:
        results = search_quota_db([], conn=conn)
        assert results == []
    finally:
        conn.close()


def test_search_quota_db_empty_keywords_with_section_works():
    conn = _build_quota_conn()
    try:
        results = search_quota_db([], section="C1-4", conn=conn, limit=10)
        assert [r[0] for r in results] == ["C1-4-1", "C1-4-2"]
    finally:
        conn.close()


def test_search_quota_db_closes_own_connection_on_sql_error():
    class _BadCursor:
        def execute(self, *args, **kwargs):
            raise sqlite3.OperationalError("boom")

    class _BadConn:
        def __init__(self):
            self.closed = False

        def cursor(self):
            return _BadCursor()

        def close(self):
            self.closed = True

    bad_conn = _BadConn()
    with patch("src.quota_search.get_quota_db_path", return_value="dummy.db"):
        with patch("src.quota_search.os.path.exists", return_value=True):
            with patch("src.quota_search._db_connect", return_value=bad_conn):
                with pytest.raises(sqlite3.OperationalError):
                    search_quota_db(["电梯"], conn=None)

    assert bad_conn.closed is True


def test_search_by_id_closes_own_connection_on_sql_error():
    class _BadCursor:
        def execute(self, *args, **kwargs):
            raise sqlite3.OperationalError("boom")

        def fetchone(self):
            return None

    class _BadConn:
        def __init__(self):
            self.closed = False

        def cursor(self):
            return _BadCursor()

        def close(self):
            self.closed = True

    bad_conn = _BadConn()
    with patch("src.quota_search.get_quota_db_path", return_value="dummy.db"):
        with patch("src.quota_search.os.path.exists", return_value=True):
            with patch("src.quota_search._db_connect", return_value=bad_conn):
                with pytest.raises(sqlite3.OperationalError):
                    search_by_id("C1-1", conn=None)

    assert bad_conn.closed is True


def test_search_series_closes_own_connection_on_sql_error():
    class _BadCursor:
        def execute(self, *args, **kwargs):
            raise sqlite3.OperationalError("boom")

    class _BadConn:
        def __init__(self):
            self.closed = False

        def cursor(self):
            return _BadCursor()

        def close(self):
            self.closed = True

    bad_conn = _BadConn()
    with patch("src.quota_search.get_quota_db_path", return_value="dummy.db"):
        with patch("src.quota_search.os.path.exists", return_value=True):
            with patch("src.quota_search._db_connect", return_value=bad_conn):
                with pytest.raises(sqlite3.OperationalError):
                    search_series("C1-1", conn=None)

    assert bad_conn.closed is True
