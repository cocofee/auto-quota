import json
import shutil
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest

import tools._select_province as sp
import tools.experience_promote as experience_promote
import tools.import_reference as import_reference
import tools.jarvis_auto_review as auto_review_mod
import tools.test_review_rules as review_rules


def _new_tmp_dir(prefix: str) -> Path:
    root = Path("output") / "test_tmp"
    root.mkdir(parents=True, exist_ok=True)
    d = root / f"{prefix}-{uuid4().hex}"
    d.mkdir(parents=True, exist_ok=False)
    return d


class _FailingSelectProvinceConn:
    def __init__(self):
        self.closed = False

    def execute(self, *args, **kwargs):
        raise RuntimeError("db read failed")

    def close(self):
        self.closed = True


class _FailingImportReferenceConn:
    def __init__(self):
        self.closed = False

    def execute(self, *args, **kwargs):
        raise RuntimeError("count failed")

    def close(self):
        self.closed = True


class _FailingReviewRulesConn:
    def __init__(self):
        self.closed = False

    def cursor(self):
        raise RuntimeError("cursor init failed")

    def close(self):
        self.closed = True


class _FailingPromoteConn:
    def __init__(self):
        self.closed = False

    def execute(self, *args, **kwargs):
        raise RuntimeError("delete failed")

    def commit(self):
        return None

    def close(self):
        self.closed = True


class _AutoReviewConn:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _FakePromoteDB:
    def __init__(self, conn):
        self._conn = conn

    def get_candidate_records(self, province=None, limit=50):
        return [
            {
                "id": 1,
                "bill_name": "测试清单",
                "bill_text": "测试清单 描述",
                "quota_ids": ["C10-1-1"],
                "quota_names": ["定额A"],
                "source": "auto_review",
                "confidence": 80,
                "notes": "",
            }
        ]

    def _connect(self):
        return self._conn


def test_get_db_count_closes_connection_on_error():
    tmp_dir = _new_tmp_dir("select-province")
    try:
        db_path = tmp_dir / "quota.db"
        db_path.write_text("x", encoding="utf-8")
        failing_conn = _FailingSelectProvinceConn()

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


def test_select_quota_db_closes_connection_on_count_error():
    tmp_dir = _new_tmp_dir("import-reference")
    try:
        provinces = ["省A", "省B"]
        db_paths = {}
        for p in provinces:
            db_path = tmp_dir / f"{p}.db"
            db_path.write_text("x", encoding="utf-8")
            db_paths[p] = db_path

        conn_a = _FailingImportReferenceConn()
        conn_b = _FailingImportReferenceConn()

        with patch.object(import_reference.config, "list_db_provinces", return_value=provinces):
            with patch.object(import_reference.config, "get_quota_db_path", side_effect=lambda p: db_paths[p]):
                with patch("db.sqlite.connect", side_effect=[conn_a, conn_b]):
                    with patch("builtins.input", return_value="1"):
                        selected = import_reference._select_quota_db()

        assert selected == "省A"
        assert conn_a.closed is True
        assert conn_b.closed is True
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_run_db_sample_test_closes_connection_on_cursor_error():
    tmp_dir = _new_tmp_dir("review-rules")
    try:
        db_path = tmp_dir / "quota.db"
        db_path.write_text("x", encoding="utf-8")
        conn = _FailingReviewRulesConn()

        with patch.object(review_rules, "get_quota_db_path", return_value=str(db_path)):
            with patch.object(review_rules, "_db_connect", return_value=conn):
                with pytest.raises(RuntimeError, match="cursor init failed"):
                    review_rules.run_db_sample_test("测试省份")

        assert conn.closed is True
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_interactive_review_closes_connection_when_delete_fails():
    conn = _FailingPromoteConn()
    fake_db = _FakePromoteDB(conn)

    with patch.object(experience_promote, "ExperienceDB", return_value=fake_db):
        with patch("builtins.input", side_effect=["d"]):
            experience_promote.interactive_review()

    assert conn.closed is True


def test_auto_review_closes_db_connection_on_correct_phase_error():
    tmp_dir = _new_tmp_dir("jarvis-auto-review")
    try:
        review_path = tmp_dir / "review_sample.json"
        review_path.write_text(
            json.dumps({"results": []}, ensure_ascii=False),
            encoding="utf-8",
        )

        db_path = tmp_dir / "quota.db"
        db_path.write_text("db", encoding="utf-8")
        conn = _AutoReviewConn()

        with patch.object(auto_review_mod, "get_quota_db_path", return_value=str(db_path)):
            with patch.object(auto_review_mod, "_db_connect", return_value=conn):
                with patch.object(auto_review_mod, "_correct_phase", side_effect=RuntimeError("phase2 failed")):
                    with pytest.raises(RuntimeError, match="phase2 failed"):
                        auto_review_mod.auto_review(str(review_path), province="测试省份")

        assert conn.closed is True
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
