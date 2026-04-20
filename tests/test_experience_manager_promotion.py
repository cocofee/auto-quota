from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path

import config
from src.experience_db import ExperienceDB


def _make_db_path() -> Path:
    base = Path("test_artifacts") / f"experience_manager_{uuid.uuid4().hex}"
    base.mkdir(parents=True, exist_ok=True)
    return base / "experience.db"


def _insert_candidate(db: ExperienceDB, *, bill_text: str = "给水管道 DN25") -> int:
    now = time.time()
    conn = db._connect()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO experiences (
                bill_text, bill_name, bill_code, bill_unit,
                quota_ids, quota_names, source, confidence,
                confirm_count, province, project_name,
                created_at, updated_at, notes, quota_db_version,
                layer, specialty, normalized_text, feature_text,
                materials_signature, install_method,
                quota_fingerprint, quota_codes_sorted
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                bill_text,
                bill_text,
                "",
                "m",
                json.dumps(["Q-1"], ensure_ascii=False),
                json.dumps(["管道安装 DN25"], ensure_ascii=False),
                "project_import",
                88,
                1,
                "测试省",
                "测试项目",
                now,
                now,
                "",
                "test-v1",
                "candidate",
                "C10",
                bill_text.replace(" ", "").lower(),
                "",
                "",
                "",
                "fp-1",
                json.dumps(["Q-1"], ensure_ascii=False),
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)
    finally:
        conn.close()


def test_promote_to_authority_requires_second_confirmation(monkeypatch):
    db_path = _make_db_path()
    db_dir = db_path.parent
    monkeypatch.setattr(config, "VECTOR_ENABLED", False)
    monkeypatch.setattr(config, "get_current_quota_version", lambda province=None: "test-v1")

    try:
        db = ExperienceDB(province="测试省", db_path=db_path)
        record_id = _insert_candidate(db)

        assert db.promote_to_authority(record_id, reason="first review") is True

        conn = db._connect(row_factory=True)
        try:
            first = conn.execute(
                "SELECT layer, source, confidence, confirm_count FROM experiences WHERE id = ?",
                (record_id,),
            ).fetchone()
        finally:
            conn.close()

        assert first["layer"] == "verified"
        assert first["source"] == "user_confirmed"
        assert int(first["confidence"]) >= 95
        assert int(first["confirm_count"]) == 1

        assert db.promote_to_authority(record_id, reason="second review") is True

        conn = db._connect(row_factory=True)
        try:
            second = conn.execute(
                "SELECT layer, source, confidence, confirm_count, notes FROM experiences WHERE id = ?",
                (record_id,),
            ).fetchone()
        finally:
            conn.close()

        assert second["layer"] == "authority"
        assert second["source"] == "user_confirmed"
        assert int(second["confidence"]) >= 95
        assert int(second["confirm_count"]) == 2
        assert "first review" in str(second["notes"] or "")
        assert "second review" in str(second["notes"] or "")
    finally:
        shutil.rmtree(db_dir, ignore_errors=True)


def test_promote_to_authority_keeps_manual_correction_source(monkeypatch):
    db_path = _make_db_path()
    db_dir = db_path.parent
    monkeypatch.setattr(config, "VECTOR_ENABLED", False)
    monkeypatch.setattr(config, "get_current_quota_version", lambda province=None: "test-v1")

    try:
        db = ExperienceDB(province="测试省", db_path=db_path)
        record_id = _insert_candidate(db, bill_text="排水管道 DN50")

        conn = db._connect()
        try:
            conn.execute(
                "UPDATE experiences SET source = 'user_correction', layer = 'verified', confirm_count = 1 WHERE id = ?",
                (record_id,),
            )
            conn.commit()
        finally:
            conn.close()

        assert db.promote_to_authority(record_id, reason="manual correction review") is True

        conn = db._connect(row_factory=True)
        try:
            row = conn.execute(
                "SELECT layer, source, confirm_count FROM experiences WHERE id = ?",
                (record_id,),
            ).fetchone()
        finally:
            conn.close()

        assert row["layer"] == "authority"
        assert row["source"] == "user_correction"
        assert int(row["confirm_count"]) == 2
    finally:
        shutil.rmtree(db_dir, ignore_errors=True)


def test_get_candidate_records_keeps_verified_rows_visible_for_second_review(monkeypatch):
    db_path = _make_db_path()
    db_dir = db_path.parent
    monkeypatch.setattr(config, "VECTOR_ENABLED", False)
    monkeypatch.setattr(config, "get_current_quota_version", lambda province=None: "test-v1")

    try:
        db = ExperienceDB(province="测试省", db_path=db_path)
        record_id = _insert_candidate(db, bill_text="排水管 DN50")

        assert db.promote_to_authority(record_id, reason="first review") is True

        records = db.get_candidate_records(province="测试省", limit=10)
        target = next((record for record in records if record["id"] == record_id), None)

        assert target is not None
        assert target["layer"] == "verified"
        assert target["source"] == "user_confirmed"
    finally:
        shutil.rmtree(db_dir, ignore_errors=True)
