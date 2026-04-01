from __future__ import annotations

import json
import sqlite3
import shutil
from pathlib import Path

from tools.export_real_eval_set import export_real_eval_set, fetch_real_eval_records
from tools.run_real_eval import (
    _detail_from_result,
    _build_keyword_miss_export_rows,
    _build_mode_comparison,
    run_real_eval,
    summarize_real_eval_details,
)


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
            "error_stage": "correct",
            "error_type": "",
            "oracle_status": "ok",
        },
        {
            "is_match": False,
            "oracle_in_candidates": True,
            "accepted": False,
            "cause": "wrong_tier",
            "source": "project_import",
            "miss_stage": "rank_miss",
            "error_stage": "ranker",
            "error_type": "oracle_in_candidates_but_not_top1",
            "oracle_status": "ok",
        },
        {
            "is_match": False,
            "oracle_in_candidates": False,
            "accepted": False,
            "cause": "wrong_book",
            "source": "user_confirmed",
            "miss_stage": "recall_miss",
            "error_stage": "retriever",
            "error_type": "oracle_not_in_candidates",
            "oracle_status": "name_mismatch",
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
    assert summary["error_stage_counts"] == {"ranker": 1, "retriever": 1}
    assert summary["error_type_counts"] == {
        "oracle_in_candidates_but_not_top1": 1,
        "oracle_not_in_candidates": 1,
    }
    assert summary["diagnosis"] == {"wrong_tier": 1, "wrong_book": 1}
    assert summary["oracle_alignment"] == {"name_mismatch": 1, "ok": 2}
    assert summary["aligned_total"] == 2
    assert summary["aligned_correct"] == 1
    assert summary["aligned_hit_rate"] == 50.0
    assert summary["aligned_oracle_in_candidates"] == 2
    assert summary["aligned_oracle_not_in_candidates"] == 0
    assert summary["aligned_rank_miss_count"] == 1
    assert summary["aligned_recall_miss_count"] == 0


def test_detail_from_result_diagnoses_final_validator_stage():
    record = {
        "sample_id": "s1",
        "province": "广东",
        "source": "project_import",
        "project_name": "项目A",
        "bill_name": "截止阀",
        "bill_text": "截止阀 DN50 丝接",
        "section": "给排水",
        "sheet_name": "安装",
        "specialty": "C10",
        "oracle_quota_ids": ["C10-1-2"],
        "oracle_quota_names": ["截止阀安装 DN50"],
    }
    result = {
        "quotas": [{"quota_id": "C10-1-3", "name": "截止阀安装 DN65"}],
        "all_candidate_ids": ["C10-1-2", "C10-1-3"],
        "pre_ltr_top1_id": "C10-1-3",
        "post_ltr_top1_id": "C10-1-2",
        "post_cgr_top1_id": "C10-1-2",
        "post_arbiter_top1_id": "C10-1-2",
        "post_explicit_top1_id": "C10-1-2",
        "post_anchor_top1_id": "C10-1-2",
        "post_final_top1_id": "C10-1-3",
        "final_validation": {"status": "vetoed", "vetoed": True},
        "reasoning_decision": {},
        "match_source": "search",
        "confidence": 71,
    }

    detail = _detail_from_result(record, result)

    assert detail["miss_stage"] == "post_rank_miss"
    assert detail["error_stage"] == "final_validator"
    assert detail["error_type"] == "post_anchor_correct_but_final_changed"


def test_detail_from_result_no_longer_reports_experience_anchor_stage():
    record = {
        "sample_id": "s-anchor",
        "province": "广东",
        "source": "project_import",
        "project_name": "项目A",
        "bill_name": "电气配管",
        "bill_text": "SC32 暗敷设",
        "section": "电气",
        "sheet_name": "安装",
        "specialty": "C4",
        "oracle_quota_ids": ["C4-12-177"],
        "oracle_quota_names": ["波纹电线管敷设 ≤32"],
    }
    result = {
        "quotas": [{"quota_id": "Q-WRONG", "name": "错误定额"}],
        "all_candidate_ids": ["C4-12-177", "Q-WRONG"],
        "pre_ltr_top1_id": "Q-SEARCH",
        "post_ltr_top1_id": "Q-SEARCH",
        "post_cgr_top1_id": "Q-SEARCH",
        "post_arbiter_top1_id": "Q-SEARCH",
        "post_explicit_top1_id": "C4-12-177",
        "post_anchor_top1_id": "Q-WRONG",
        "post_final_top1_id": "Q-WRONG",
        "reasoning_decision": {},
        "match_source": "search",
        "confidence": 63,
    }

    detail = _detail_from_result(record, result)

    assert detail["miss_stage"] == "post_rank_miss"
    assert detail["error_stage"] == "ranker"
    assert detail["error_type"] == "oracle_in_candidates_but_not_top1"


def test_detail_from_result_keeps_search_trace_and_candidates():
    record = {
        "sample_id": "s2",
        "province": "广东",
        "source": "project_import",
        "project_name": "项目B",
        "bill_name": "配管",
        "bill_text": "JDG20 配管",
        "section": "电气",
        "sheet_name": "安装",
        "specialty": "C4",
        "oracle_quota_ids": ["C4-1-1"],
        "oracle_quota_names": ["配管"],
    }
    result = {
        "quotas": [{"quota_id": "C4-1-2", "name": "错误定额"}],
        "all_candidate_ids": ["C4-1-2", "C4-1-1"],
        "candidate_snapshots": [
            {"quota_id": "C4-1-2", "name": "错误定额"},
            {"quota_id": "C4-1-1", "name": "配管"},
        ],
        "pre_ltr_top1_id": "C4-1-2",
        "post_ltr_top1_id": "C4-1-2",
        "post_arbiter_top1_id": "C4-1-2",
        "post_final_top1_id": "C4-1-2",
        "reasoning_decision": {},
        "match_source": "search",
        "confidence": 62,
        "trace": {
            "path": ["search_select"],
            "steps": [
                {
                    "stage": "search_select",
                    "parser": {"search_query": "JDG20 配管", "entity": "配管"},
                    "router": {"primary_book": "C4"},
                    "retriever": {"candidate_count": 2},
                    "ranker": {"selected_quota": "C4-1-2"},
                }
            ],
        },
    }

    detail = _detail_from_result(record, result)

    assert detail["search_query"] == "JDG20 配管"
    assert detail["parser"]["entity"] == "配管"
    assert detail["router"]["primary_book"] == "C4"
    assert detail["retriever"]["candidate_count"] == 2
    assert detail["ranker"]["selected_quota"] == "C4-1-2"
    assert detail["candidate_snapshots"][1]["quota_id"] == "C4-1-1"


def test_detail_from_result_keeps_experience_review_rejection_trace():
    record = {
        "sample_id": "s3",
        "province": "广东",
        "source": "project_import",
        "project_name": "项目C",
        "bill_name": "组串式逆变器",
        "bill_text": "组串式逆变器 150kW",
        "section": "安装",
        "sheet_name": "安装",
        "specialty": "C5",
        "oracle_quota_ids": ["C5-1-1"],
        "oracle_quota_names": ["光伏逆变器安装"],
    }
    result = {
        "quotas": [{"quota_id": "C5-1-1", "name": "光伏逆变器安装"}],
        "all_candidate_ids": ["C5-1-1"],
        "reasoning_decision": {},
        "match_source": "search",
        "confidence": 88,
        "trace": {
            "path": ["experience_review_rejected", "search_select"],
            "steps": [
                {
                    "stage": "experience_review_rejected",
                    "error_type": "category_mismatch",
                    "error_reason": "review rejected experience direct hit",
                    "experience_source": "experience_exact",
                    "quota_id": "Q-EXP-1",
                },
                {
                    "stage": "search_select",
                    "parser": {"search_query": "逆变器 150kW", "entity": "逆变器"},
                    "router": {"primary_book": "C5"},
                    "retriever": {"candidate_count": 1},
                    "ranker": {"selected_quota": "C5-1-1"},
                },
            ],
        },
    }

    detail = _detail_from_result(record, result)

    assert detail["experience_review_rejected"] is True
    assert detail["experience_review_rejected_type"] == "category_mismatch"
    assert detail["experience_review_rejected_reason"] == "review rejected experience direct hit"
    assert detail["experience_review_rejected_quota_id"] == "Q-EXP-1"
    assert detail["experience_review_rejected_source"] == "experience_exact"


def test_build_mode_comparison_summarizes_closed_book_vs_with_memory():
    closed_payload = {
        "profile": "smoke",
        "dataset_path": "output/real_eval/demo.jsonl",
        "eval_mode": "closed_book",
        "total": 40,
        "correct": 12,
        "hit_rate": 30.0,
        "province_results": [
            {"province": "黑龙江", "total": 20, "correct": 6, "hit_rate": 30.0, "details": [{"x": 1}]},
            {"province": "宁夏", "total": 20, "correct": 6, "hit_rate": 30.0, "details": [{"x": 2}]},
        ],
    }
    with_memory_payload = {
        "profile": "smoke",
        "dataset_path": "output/real_eval/demo.jsonl",
        "eval_mode": "with_memory",
        "total": 40,
        "correct": 36,
        "hit_rate": 90.0,
        "province_results": [
            {"province": "黑龙江", "total": 20, "correct": 20, "hit_rate": 100.0, "details": [{"y": 1}]},
            {"province": "宁夏", "total": 20, "correct": 16, "hit_rate": 80.0, "details": [{"y": 2}]},
        ],
    }

    payload = _build_mode_comparison(closed_payload, with_memory_payload)

    assert payload["comparison_mode"] == "closed_book_vs_with_memory"
    assert payload["closed_book"]["eval_mode"] == "closed_book"
    assert payload["with_memory"]["eval_mode"] == "with_memory"
    assert "details" not in payload["closed_book"]["province_results"][0]
    assert payload["delta"]["hit_rate_gain"] == 60.0
    assert payload["delta"]["correct_gain"] == 24
    assert payload["province_deltas"] == [
        {
            "province": "宁夏",
            "closed_book_total": 20,
            "with_memory_total": 20,
            "closed_book_hit_rate": 30.0,
            "with_memory_hit_rate": 80.0,
            "hit_rate_gain": 50.0,
            "closed_book_correct": 6,
            "with_memory_correct": 16,
            "correct_gain": 10,
        },
        {
            "province": "黑龙江",
            "closed_book_total": 20,
            "with_memory_total": 20,
            "closed_book_hit_rate": 30.0,
            "with_memory_hit_rate": 100.0,
            "hit_rate_gain": 70.0,
            "closed_book_correct": 6,
            "with_memory_correct": 20,
            "correct_gain": 14,
        },
    ]


def test_build_keyword_miss_export_rows_keeps_only_synonym_gap_recall_cases():
    payload = {
        "province_results": [
            {
                "province": "上海",
                "details": [
                    {
                        "sample_id": "exp:1",
                        "province": "上海",
                        "source": "user_confirmed",
                        "project_name": "项目A",
                        "bill_name": "",
                        "bill_text": "墙面装饰板 WD-201",
                        "specialty": "A",
                        "oracle_quota_ids": ["03-2-5-38"],
                        "oracle_quota_names": ["衬微晶板"],
                        "search_query": "墙面装饰板 WD-201",
                        "parser": {"search_query": "墙面装饰板 WD-201"},
                        "router": {
                            "classification": {
                                "primary": "C3",
                                "search_books": ["C3"],
                                "route_mode": "strict",
                                "allow_cross_book_escape": False,
                            }
                        },
                        "retriever": {
                            "candidate_count": 12,
                            "matched_candidate_count": 10,
                            "candidate_ids": ["03-9-7-5", "03-2-5-38"],
                            "authority_hit": False,
                            "kb_hit": True,
                        },
                        "ranker": {
                            "selected_quota": "03-9-7-5",
                            "score_gap": 0.3,
                            "decision_owner": "pre_ltr_seed",
                            "top1_flip_count": 0,
                        },
                        "candidate_count": 12,
                        "cause": "synonym_gap",
                        "miss_stage": "recall_miss",
                        "error_stage": "retriever",
                        "error_type": "oracle_not_in_candidates",
                        "algo_id": "03-9-7-5",
                        "algo_name": "防火涂料",
                        "match_source": "search",
                        "confidence": 45.0,
                    },
                    {
                        "sample_id": "exp:2",
                        "province": "上海",
                        "bill_text": "截止阀 DN50",
                        "cause": "wrong_tier",
                        "miss_stage": "rank_miss",
                    },
                ],
            }
        ]
    }

    rows = _build_keyword_miss_export_rows(payload)

    assert len(rows) == 1
    assert rows[0]["sample_id"] == "exp:1"
    assert rows[0]["cause"] == "synonym_gap"
    assert rows[0]["miss_stage"] == "recall_miss"
    assert rows[0]["router"] == {
        "primary_book": "C3",
        "search_books": ["C3"],
        "hard_search_books": [],
        "advisory_search_books": [],
        "route_mode": "strict",
        "advisory_owner": "",
        "effective_owner": "",
        "allow_cross_book_escape": False,
    }
    assert rows[0]["retriever"] == {
        "candidate_count": 12,
        "matched_candidate_count": 10,
        "candidate_ids": ["03-9-7-5", "03-2-5-38"],
        "authority_hit": False,
        "kb_hit": True,
        "scope_owner": "",
        "escape_owner": "",
        "used_open_search": False,
        "resolved_main_books": [],
    }
    assert rows[0]["ranker"] == {
        "selected_quota": "03-9-7-5",
        "score_gap": 0.3,
        "decision_owner": "pre_ltr_seed",
        "top1_flip_count": 0,
    }


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
