from __future__ import annotations

import json
import sqlite3
import shutil
from pathlib import Path

from tools.export_real_eval_set import export_real_eval_set, fetch_real_eval_records
from tools.run_real_eval import run_real_eval, summarize_real_eval_details


def _create_experience_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE experiences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bill_text TEXT,
                bill_name TEXT,
                bill_code TEXT,
                quota_ids TEXT,
                quota_names TEXT,
                source TEXT,
                confidence INTEGER,
                confirm_count INTEGER,
                province TEXT,
                project_name TEXT,
                layer TEXT,
                specialty TEXT
            )
            """
        )
        rows = [
            (
                "钢管 DN25 明装",
                "钢管",
                "001",
                json.dumps(["C10-1-1"], ensure_ascii=False),
                json.dumps(["镀锌钢管 DN25 明装"], ensure_ascii=False),
                "project_import",
                95,
                3,
                "广东省通用安装工程综合定额(2018)",
                "项目A",
                "authority",
                "C10",
            ),
            (
                "钢管 DN50 明装",
                "钢管",
                "002",
                json.dumps(["C10-1-2"], ensure_ascii=False),
                json.dumps(["镀锌钢管 DN50 明装"], ensure_ascii=False),
                "promote_from_candidate",
                95,
                3,
                "广东省通用安装工程综合定额(2018)",
                "项目A",
                "authority",
                "C10",
            ),
            (
                "风口",
                "风口",
                "003",
                json.dumps(["C7-1-1"], ensure_ascii=False),
                json.dumps(["百叶风口安装"], ensure_ascii=False),
                "user_confirmed",
                90,
                1,
                "广东省通用安装工程综合定额(2018)",
                "项目B",
                "authority",
                "C7",
            ),
            (
                "水表",
                "水表",
                "004",
                json.dumps(["C10-5-1"], ensure_ascii=False),
                json.dumps(["水表组成安装"], ensure_ascii=False),
                "user_correction",
                100,
                1,
                "北京市建设工程施工消耗量标准(2024)",
                "项目C",
                "candidate",
                "C10",
            ),
        ]
        conn.executemany(
            """
            INSERT INTO experiences (
                bill_text, bill_name, bill_code, quota_ids, quota_names,
                source, confidence, confirm_count, province, project_name,
                layer, specialty
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def test_fetch_real_eval_records_filters_trusted_authority_rows():
    temp_root = Path("output/_tmp_real_eval_tools_fetch")
    shutil.rmtree(temp_root, ignore_errors=True)
    temp_root.mkdir(parents=True, exist_ok=True)
    try:
        db_path = temp_root / "experience.db"
        _create_experience_db(db_path)

        records = fetch_real_eval_records(
            db_path,
            min_confidence=95,
            sources=["project_import", "user_confirmed", "user_correction"],
        )

        assert len(records) == 1
        assert records[0]["sample_id"] == "exp:1"
        assert records[0]["oracle_quota_ids"] == ["C10-1-1"]
        assert records[0]["province"] == "广东省通用安装工程综合定额(2018)"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_export_real_eval_set_writes_manifest():
    temp_root = Path("output/_tmp_real_eval_tools_export")
    shutil.rmtree(temp_root, ignore_errors=True)
    temp_root.mkdir(parents=True, exist_ok=True)
    try:
        db_path = temp_root / "experience.db"
        out_path = temp_root / "real_eval.jsonl"
        _create_experience_db(db_path)

        written_path, manifest = export_real_eval_set(
            db_path,
            out_path,
            min_confidence=90,
            sources=["project_import", "user_confirmed"],
        )

        assert written_path == out_path
        assert manifest["count"] == 2
        assert manifest["by_source"] == {"project_import": 1, "user_confirmed": 1}
        assert out_path.exists()
        assert out_path.with_suffix(".manifest.json").exists()
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_fetch_real_eval_records_caps_per_province_with_diversification():
    temp_root = Path("output/_tmp_real_eval_tools_cap")
    shutil.rmtree(temp_root, ignore_errors=True)
    temp_root.mkdir(parents=True, exist_ok=True)
    try:
        db_path = temp_root / "experience.db"
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                """
                CREATE TABLE experiences (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bill_text TEXT,
                    bill_name TEXT,
                    bill_code TEXT,
                    quota_ids TEXT,
                    quota_names TEXT,
                    source TEXT,
                    confidence INTEGER,
                    confirm_count INTEGER,
                    province TEXT,
                    project_name TEXT,
                    layer TEXT,
                    specialty TEXT
                )
                """
            )
            rows = []
            for idx, project in enumerate(("项目A", "项目B", "项目C"), start=1):
                rows.append(
                    (
                        f"清单{idx}",
                        f"名称{idx}",
                        f"{idx:03d}",
                        json.dumps([f"C10-1-{idx}"], ensure_ascii=False),
                        json.dumps([f"定额{idx}"], ensure_ascii=False),
                        "project_import",
                        95,
                        5,
                        "广东省通用安装工程综合定额(2018)",
                        project,
                        "authority",
                        "C10",
                    )
                )
            conn.executemany(
                """
                INSERT INTO experiences (
                    bill_text, bill_name, bill_code, quota_ids, quota_names,
                    source, confidence, confirm_count, province, project_name,
                    layer, specialty
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()
        finally:
            conn.close()

        records = fetch_real_eval_records(
            db_path,
            sources=["project_import"],
            max_per_province=2,
        )

        assert len(records) == 2
        assert {record["project_name"] for record in records} == {"项目A", "项目B"}
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_summarize_real_eval_details_tracks_accept_and_error_buckets():
    details = [
        {
            "is_match": True,
            "oracle_in_candidates": True,
            "accepted": True,
            "cause": "",
            "source": "project_import",
            "miss_stage": "",
        },
        {
            "is_match": False,
            "oracle_in_candidates": True,
            "accepted": False,
            "cause": "wrong_tier",
            "source": "project_import",
            "miss_stage": "rank_miss",
        },
        {
            "is_match": False,
            "oracle_in_candidates": False,
            "accepted": False,
            "cause": "wrong_book",
            "source": "user_confirmed",
            "miss_stage": "recall_miss",
        },
    ]

    summary = summarize_real_eval_details("广东", details, elapsed=3.2)

    assert summary["total"] == 3
    assert summary["correct"] == 1
    assert summary["hit_rate"] == 33.3
    assert summary["accept_count"] == 1
    assert summary["accept_precision"] == 1.0
    assert summary["recall_miss_count"] == 1
    assert summary["rank_miss_count"] == 1
    assert summary["severe_error_count"] == 1
    assert summary["diagnosis"] == {"wrong_tier": 1, "wrong_book": 1}


def test_run_real_eval_can_skip_unavailable_provinces(monkeypatch):
    dataset_path = Path("output/_tmp_real_eval_skip.jsonl")
    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "sample_id": "s1",
            "province": "可用省份",
            "source": "project_import",
            "project_name": "项目A",
            "bill_name": "清单A",
            "bill_text": "清单A",
            "specialty": "C10",
            "oracle_quota_ids": ["C10-1-1"],
            "oracle_quota_names": ["定额A"],
        },
        {
            "sample_id": "s2",
            "province": "坏省份",
            "source": "project_import",
            "project_name": "项目B",
            "bill_name": "清单B",
            "bill_text": "清单B",
            "specialty": "C10",
            "oracle_quota_ids": ["C10-1-2"],
            "oracle_quota_names": ["定额B"],
        },
    ]
    dataset_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")

    def fake_evaluate(province, records, with_experience=False):
        if province == "坏省份":
            raise RuntimeError("BM25索引未就绪")
        return {
            "province": province,
            "total": 1,
            "correct": 1,
            "wrong": 0,
            "hit_rate": 100.0,
            "oracle_in_candidates": 1,
            "oracle_not_in_candidates": 0,
            "accept_count": 0,
            "accept_correct": 0,
            "accept_coverage": 0.0,
            "accept_precision": 0.0,
            "severe_error_count": 0,
            "diagnosis": {},
            "by_source": {"project_import": 1},
            "details": [],
        }

    monkeypatch.setattr("tools.run_real_eval.evaluate_province_records", fake_evaluate)

    payload = run_real_eval(
        dataset_path,
        profile="smoke",
        skip_unavailable_provinces=True,
    )

    assert payload["total"] == 1
    assert payload["correct"] == 1
    assert payload["skipped_provinces"] == [
        {"province": "坏省份", "reason": "BM25索引未就绪", "sample_count": 1}
    ]

    dataset_path.unlink(missing_ok=True)
