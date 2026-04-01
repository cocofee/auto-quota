import json
import shutil
from pathlib import Path

from tools.diagnose_recall_gap import (
    analyze_recall_gaps,
    classify_recall_issue,
    filter_recall_records,
    load_records,
)


def _sample_records():
    return [
        {
            "province": "广东省通用安装工程综合定额(2018)",
            "bill_name": "KL",
            "bill_text": "KL 控制电缆",
            "stored_ids": ["C4-2-1"],
            "stored_names": ["控制电缆敷设"],
            "algo_id": "C4-9-9",
            "algo_name": "电缆中间头",
            "cause": "synonym_gap",
            "oracle_in_candidates": False,
            "miss_category": "recall_miss",
            "trace_path": ["canonical", "retriever"],
        },
        {
            "province": "北京市建设工程施工消耗量标准(2024)",
            "bill_name": "配电箱",
            "bill_text": "配电箱 成套 安装 调试",
            "stored_ids": ["C4-5-8"],
            "stored_names": ["配电箱安装"],
            "algo_id": "C4-8-1",
            "algo_name": "电气调试",
            "cause": "search_word_miss",
            "oracle_in_candidates": False,
            "miss_category": "recall_miss",
            "trace_path": ["canonical", "retriever"],
        },
        {
            "province": "江西省通用安装工程消耗量定额及统一基价表(2017)",
            "bill_name": "给水管",
            "bill_text": "PP-R给水管 De50",
            "stored_ids": ["C10-3-2"],
            "stored_names": ["给排水管道安装"],
            "algo_id": "C8-7-1",
            "algo_name": "市政管道",
            "cause": "wrong_book",
            "oracle_in_candidates": False,
            "miss_category": "recall_miss",
            "trace_path": ["router", "retriever"],
            "specialty": "C10",
        },
        {
            "province": "北京市建设工程施工消耗量标准(2024)",
            "bill_name": "截止阀 DN50",
            "bill_text": "截止阀 DN50",
            "stored_ids": ["C10-1-1"],
            "stored_names": ["截止阀安装 DN50"],
            "algo_id": "C10-1-2",
            "algo_name": "截止阀安装 DN80",
            "cause": "wrong_tier",
            "oracle_in_candidates": True,
            "miss_category": "rank_miss",
            "trace_path": ["canonical", "retriever", "validator"],
        },
    ]


def test_classify_recall_issue_splits_synonym_dilution_and_rule():
    records = _sample_records()

    assert classify_recall_issue(records[0]) == "synonym_gap"
    assert classify_recall_issue(records[1]) == "query_dilution"
    assert classify_recall_issue(records[2]) == "rule_mislead"


def test_analyze_recall_gaps_builds_three_rankings():
    report = analyze_recall_gaps(_sample_records(), top_synonyms=10, top_terms=10, top_rules=10)

    assert report["recall_miss_total"] == 3
    assert report["category_counts"] == {
        "synonym_gap": 1,
        "query_dilution": 1,
        "rule_mislead": 1,
    }
    assert report["top_missing_synonyms"][0]["key"] == "KL -> 控制电缆敷设"
    assert report["top_missing_synonyms"][0]["source_term"] == "KL"
    assert report["top_missing_synonyms"][0]["target_term"] == "控制电缆敷设"
    assert report["top_missing_synonyms"][0]["suggested_fix"] == "add_synonym:KL->控制电缆敷设"
    assert report["top_query_dilution_terms"][0]["key"] in {"成套", "安装", "调试"}
    assert report["top_query_dilution_terms"][0]["suggested_fix"] == "trim_query_builder_append_terms"
    assert "avg_added_length_ratio" in report["top_query_dilution_terms"][0]
    assert report["top_rule_misleads"][0]["key"] == "book:C8->C10"
    assert report["top_rule_misleads"][0]["predicted_book"] == "C8"
    assert report["top_rule_misleads"][0]["expected_books"] == ["C10"]
    assert report["province_recall_miss_counts"]
    assert report["priority_actions"][0]["category"] == "synonym_gap"


def test_filter_recall_records_excludes_oracle_in_candidates():
    records = filter_recall_records(_sample_records())

    assert len(records) == 3
    assert all(not row["oracle_in_candidates"] for row in records)


def test_load_records_supports_asset_dir_and_latest_result_json():
    root = Path("output/_tmp_recall_gap_diagnose")
    shutil.rmtree(root, ignore_errors=True)
    try:
        asset_dir = root / "assets"
        asset_dir.mkdir(parents=True, exist_ok=True)
        (asset_dir / "all_errors.jsonl").write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in _sample_records()),
            encoding="utf-8",
        )

        loaded_from_dir = load_records(asset_dir)
        assert len(loaded_from_dir) == 4

        latest_result_path = root / "_latest_result.json"
        latest_result_path.write_text(
            json.dumps(
                {
                    "results": [
                        {
                            "province": "广东省通用安装工程综合定额(2018)",
                            "details": _sample_records(),
                        }
                    ]
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        loaded_from_json = load_records(latest_result_path)
        assert len(loaded_from_json) == 4
        assert loaded_from_json[0]["expected_quota_names"][0]
    finally:
        shutil.rmtree(root, ignore_errors=True)
