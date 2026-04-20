from __future__ import annotations

import json
import time

import config
from src.experience_db import ExperienceDB


def _insert_experience_row(db: ExperienceDB, **overrides) -> int:
    now = time.time()
    payload = {
        "bill_text": "测试清单",
        "bill_name": "测试清单",
        "bill_code": "",
        "bill_unit": "m",
        "quota_ids": json.dumps(["Q-1"], ensure_ascii=False),
        "quota_names": json.dumps(["测试定额1"], ensure_ascii=False),
        "source": "project_import",
        "confidence": 90,
        "confirm_count": 1,
        "province": "测试省",
        "project_name": "测试项目",
        "created_at": now,
        "updated_at": now,
        "notes": "",
        "quota_db_version": "test-v1",
        "layer": "candidate",
        "specialty": "C10",
        "normalized_text": "测试清单",
        "feature_text": "",
        "materials_signature": "",
        "install_method": "",
        "quota_fingerprint": "fp-1",
        "quota_codes_sorted": json.dumps(["Q-1"], ensure_ascii=False),
    }
    payload.update(overrides)
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
                payload["bill_text"],
                payload["bill_name"],
                payload["bill_code"],
                payload["bill_unit"],
                payload["quota_ids"],
                payload["quota_names"],
                payload["source"],
                payload["confidence"],
                payload["confirm_count"],
                payload["province"],
                payload["project_name"],
                payload["created_at"],
                payload["updated_at"],
                payload["notes"],
                payload["quota_db_version"],
                payload["layer"],
                payload["specialty"],
                payload["normalized_text"],
                payload["feature_text"],
                payload["materials_signature"],
                payload["install_method"],
                payload["quota_fingerprint"],
                payload["quota_codes_sorted"],
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)
    finally:
        conn.close()


def test_offline_learning_sources_always_map_to_candidate():
    assert ExperienceDB._source_to_layer("completed_project") == "candidate"
    assert ExperienceDB._source_to_layer("reviewed_import") == "candidate"


def test_init_demotes_legacy_completed_project_verified_rows(tmp_path):
    db_path = tmp_path / "experience.db"
    db = ExperienceDB(province="测试省", db_path=db_path)
    record_id = _insert_experience_row(
        db,
        source="completed_project",
        layer="verified",
    )

    reloaded = ExperienceDB(province="测试省", db_path=db_path)
    conn = reloaded._connect(row_factory=True)
    try:
        row = conn.execute(
            "SELECT source, layer FROM experiences WHERE id = ?",
            (record_id,),
        ).fetchone()
    finally:
        conn.close()

    assert row["source"] == "completed_project"
    assert row["layer"] == "candidate"


def test_search_experience_excludes_offline_learning_sources(tmp_path, monkeypatch):
    db = ExperienceDB(province="测试省", db_path=tmp_path / "experience.db")
    monkeypatch.setattr(config, "VECTOR_ENABLED", False)
    monkeypatch.setattr(config, "get_current_quota_version", lambda province=None: "test-v1")

    _insert_experience_row(
        db,
        source="completed_project",
        layer="candidate",
        confidence=98,
        quota_ids=json.dumps(["Q-OFFLINE"], ensure_ascii=False),
        quota_names=json.dumps(["离线回填定额"], ensure_ascii=False),
        quota_fingerprint="fp-offline",
        quota_codes_sorted=json.dumps(["Q-OFFLINE"], ensure_ascii=False),
    )
    _insert_experience_row(
        db,
        source="project_import",
        layer="candidate",
        confidence=82,
        quota_ids=json.dumps(["Q-ONLINE"], ensure_ascii=False),
        quota_names=json.dumps(["在线候选定额"], ensure_ascii=False),
        quota_fingerprint="fp-online",
        quota_codes_sorted=json.dumps(["Q-ONLINE"], ensure_ascii=False),
    )

    records = db.search_experience("测试清单", top_k=5, province="测试省")

    assert len(records) == 1
    assert records[0]["source"] == "project_import"
    assert records[0]["quota_ids"] == ["Q-ONLINE"]


def test_find_experience_only_excludes_offline_sources_when_online_only_enabled(tmp_path):
    db = ExperienceDB(province="测试省", db_path=tmp_path / "experience.db")
    _insert_experience_row(
        db,
        source="completed_project",
        layer="candidate",
        confidence=95,
        quota_ids=json.dumps(["Q-OFFLINE"], ensure_ascii=False),
        quota_names=json.dumps(["离线回填定额"], ensure_ascii=False),
        quota_fingerprint="fp-offline",
        quota_codes_sorted=json.dumps(["Q-OFFLINE"], ensure_ascii=False),
    )
    _insert_experience_row(
        db,
        source="project_import",
        layer="candidate",
        confidence=80,
        quota_ids=json.dumps(["Q-ONLINE"], ensure_ascii=False),
        quota_names=json.dumps(["在线候选定额"], ensure_ascii=False),
        quota_fingerprint="fp-online",
        quota_codes_sorted=json.dumps(["Q-ONLINE"], ensure_ascii=False),
    )

    all_records = db.find_experience("测试清单", province="测试省", limit=10)
    online_records = db.find_experience(
        "测试清单",
        province="测试省",
        limit=10,
        online_only=True,
    )

    assert {row["source"] for row in all_records} == {"completed_project", "project_import"}
    assert len(online_records) == 1
    assert online_records[0]["source"] == "project_import"


def test_first_user_confirmed_insert_stays_verified_not_authority(tmp_path):
    db = ExperienceDB(province="测试省", db_path=tmp_path / "experience.db")

    record_id = db.add_experience(
        bill_text="给水管道 DN25",
        bill_name="给水管道 DN25",
        bill_unit="m",
        quota_ids=["Q-UC-1"],
        quota_names=["管道安装 DN25"],
        source="user_confirmed",
        confidence=95,
        province="测试省",
        specialty="C10",
    )

    conn = db._connect(row_factory=True)
    try:
        row = conn.execute(
            "SELECT source, layer, confidence, confirm_count FROM experiences WHERE id = ?",
            (record_id,),
        ).fetchone()
    finally:
        conn.close()

    assert row["source"] == "user_confirmed"
    assert row["layer"] == "verified"
    assert row["confirm_count"] == 1


def test_first_user_confirmed_update_stays_verified_and_not_authority_exact(tmp_path, monkeypatch):
    db = ExperienceDB(province="测试省", db_path=tmp_path / "experience.db")
    monkeypatch.setattr(config, "VECTOR_ENABLED", False)
    monkeypatch.setattr(config, "get_current_quota_version", lambda province=None: "test-v1")

    _insert_experience_row(
        db,
        bill_text="给水管道 DN25",
        bill_name="给水管道 DN25",
        normalized_text="给水管道dn25",
        source="project_import",
        layer="candidate",
        confidence=88,
        confirm_count=1,
        quota_ids=json.dumps(["Q-BASE"], ensure_ascii=False),
        quota_names=json.dumps(["管道安装 DN25"], ensure_ascii=False),
        quota_fingerprint="fp-base",
        quota_codes_sorted=json.dumps(["Q-BASE"], ensure_ascii=False),
    )

    updated_id = db.add_experience(
        bill_text="给水管道 DN25",
        bill_name="给水管道 DN25",
        bill_unit="m",
        quota_ids=["Q-BASE"],
        quota_names=["管道安装 DN25"],
        source="user_confirmed",
        confidence=95,
        province="测试省",
        specialty="C10",
    )

    conn = db._connect(row_factory=True)
    try:
        row = conn.execute(
            "SELECT source, layer, confidence, confirm_count FROM experiences WHERE id = ?",
            (updated_id,),
        ).fetchone()
    finally:
        conn.close()

    assert row["source"] == "user_confirmed"
    assert row["layer"] == "verified"
    assert row["confirm_count"] == 1

    exact = db._find_exact_match("给水管道 DN25", "测试省", authority_only=True)
    assert exact is None

    records = db.search_experience("给水管道 DN25", top_k=3, min_confidence=70, province="测试省")
    assert len(records) == 1
    assert records[0]["layer"] == "verified"
    assert records[0]["match_type"] == "similar"


def test_user_confirmed_update_preserves_promoted_source_and_confirm_count(tmp_path, monkeypatch):
    db = ExperienceDB(province="测试省", db_path=tmp_path / "experience.db")
    monkeypatch.setattr(config, "VECTOR_ENABLED", False)
    monkeypatch.setattr(config, "get_current_quota_version", lambda province=None: "test-v1")

    record_id = _insert_experience_row(
        db,
        bill_text="排水管 DN100",
        bill_name="排水管 DN100",
        normalized_text="排水管dn100",
        source="multi_project_promoted",
        layer="authority",
        confidence=92,
        confirm_count=2,
        quota_ids=json.dumps(["Q-PROMO"], ensure_ascii=False),
        quota_names=json.dumps(["排水管安装 DN100"], ensure_ascii=False),
        quota_fingerprint="fp-promo",
        quota_codes_sorted=json.dumps(["Q-PROMO"], ensure_ascii=False),
    )

    updated_id = db.add_experience(
        bill_text="排水管 DN100",
        bill_name="排水管 DN100",
        bill_unit="m",
        quota_ids=["Q-PROMO"],
        quota_names=["排水管安装 DN100"],
        source="user_confirmed",
        confidence=95,
        province="测试省",
        specialty="C10",
    )

    assert updated_id == record_id

    conn = db._connect(row_factory=True)
    try:
        row = conn.execute(
            "SELECT source, layer, confidence, confirm_count FROM experiences WHERE id = ?",
            (record_id,),
        ).fetchone()
    finally:
        conn.close()

    assert row["source"] == "multi_project_promoted"
    assert row["layer"] == "authority"
    assert int(row["confirm_count"]) == 3

    records = db.search_experience("排水管 DN100", top_k=3, min_confidence=70, province="测试省")
    assert any(record["id"] == record_id for record in records)
