import sqlite3

from src.quota_search import search_quota_db


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
