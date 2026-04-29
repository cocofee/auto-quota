from pathlib import Path

from tools.build_global_repair_decision import (
    ACTION_BY_BUCKET,
    CSV_FIELDS,
    build_next_action,
    build_rows,
    build_summary,
)


def test_global_repair_decision_rows_keep_contract_fields():
    rows = build_rows(
        [
            {
                "sample_id": "s1",
                "province": "北京",
                "passed": False,
                "expected_quota_ids": ["Q1"],
                "predicted_quota_id": "W1",
                "all_candidate_ids": ["W1", "Q1"],
                "error_stage": "ltr_ranker",
                "miss_category": "confidence_miss",
                "pre_ltr_top1_id": "Q1",
                "post_ltr_top1_id": "W1",
                "post_final_top1_id": "W1",
            }
        ]
    )

    assert list(rows[0]) == CSV_FIELDS
    assert rows[0]["sample_id"] == "s1"
    assert rows[0]["expected_ids"] == "Q1"
    assert rows[0]["selected_id"] == "W1"
    assert rows[0]["recall_rank"] == "2"
    assert rows[0]["selected_prefix"] == "W1"
    assert rows[0]["expected_prefixes"] == "Q1"
    assert rows[0]["common_issue_key"]


def test_global_repair_next_action_targets_largest_common_issue_cluster():
    rows = build_rows(
        [
            {
                "sample_id": "r2-shared-1",
                "is_match": False,
                "stored_ids": ["C4-4-38"],
                "algo_id": "C4-10-114",
                "error_stage": "ltr_ranker",
                "miss_category": "confidence_miss",
                "recall_rank": 1,
                "pre_ltr_top1_id": "C4-4-38",
                "post_ltr_top1_id": "C4-10-114",
                "post_final_top1_id": "C4-10-114",
            },
            {
                "sample_id": "r1-1",
                "is_match": False,
                "stored_ids": ["Q2"],
                "algo_id": "W2",
                "error_stage": "retriever",
                "miss_category": "recall_miss",
                "recall_rank": -1,
                "pre_ltr_top1_id": "W2",
                "post_ltr_top1_id": "W2",
                "post_final_top1_id": "W2",
            },
            {
                "sample_id": "r2-shared-2",
                "is_match": False,
                "stored_ids": ["C4-4-39"],
                "algo_id": "C4-10-115",
                "error_stage": "post_ltr",
                "miss_category": "confidence_miss",
                "recall_rank": 1,
                "pre_ltr_top1_id": "C4-4-39",
                "post_ltr_top1_id": "C4-10-115",
                "post_final_top1_id": "C4-10-115",
            },
            {
                "sample_id": "r2-singleton",
                "is_match": False,
                "stored_ids": ["C4-5-1"],
                "algo_id": "C4-11-1",
                "error_stage": "post_ltr",
                "miss_category": "confidence_miss",
                "recall_rank": 1,
                "pre_ltr_top1_id": "C4-5-1",
                "post_ltr_top1_id": "C4-11-1",
                "post_final_top1_id": "C4-11-1",
            },
        ]
    )

    summary = build_summary(rows, Path("latest.jsonl"), Path("attr.json"))
    action = build_next_action(summary, rows)

    assert summary["largest_bucket"] == "R2"
    assert summary["target_common_issue"]["sample_count"] == 2
    assert summary["target_common_issue"]["commonality"] == "shared"
    assert action["action"] == ACTION_BY_BUCKET["R2"]
    assert action["representative_sample_ids"] == ["r2-shared-1", "r2-shared-2"]
    assert action["cluster_sample_ids"] == ["r2-shared-1", "r2-shared-2"]
    assert action["suggested_validation_scope"]["filter_common_issue_key"]
    assert action["full_validation_status"] == "pending"


def test_global_repair_bucket_prefers_actionable_attribution_over_generic_stage():
    rows = build_rows(
        [
            {
                "sample_id": "r1-post-final",
                "is_match": False,
                "stored_ids": ["Q1"],
                "algo_id": "W1",
                "error_stage": "post_final",
                "miss_category": "recall_miss",
                "recall_rank": -1,
                "pre_ltr_top1_id": "W1",
                "post_ltr_top1_id": "W1",
                "post_final_top1_id": "W1",
            },
            {
                "sample_id": "r2-post-final",
                "is_match": False,
                "stored_ids": ["Q2"],
                "algo_id": "W2",
                "error_stage": "post_final",
                "attribution_category": "R2_LTR选错",
                "recall_rank": 1,
                "pre_ltr_top1_id": "Q2",
                "post_ltr_top1_id": "W2",
                "post_final_top1_id": "W2",
            },
        ]
    )

    summary = build_summary(rows, Path("latest.jsonl"), Path("attr.json"))
    action = build_next_action(summary, rows)

    assert summary["stage_counts"] == {"R1": 1, "R2": 1}
    assert rows[0]["common_issue_key"].startswith("R1::")
    assert rows[1]["common_issue_key"].startswith("R2::")
    assert action["action"] in {ACTION_BY_BUCKET["R1"], ACTION_BY_BUCKET["R2"]}


def test_global_repair_next_action_marks_singleton_when_no_shared_cluster():
    rows = build_rows(
        [
            {
                "sample_id": "r2-1",
                "is_match": False,
                "stored_ids": ["C4-4-38"],
                "algo_id": "C4-10-114",
                "error_stage": "ltr_ranker",
                "miss_category": "confidence_miss",
                "recall_rank": 1,
                "pre_ltr_top1_id": "C4-4-38",
                "post_ltr_top1_id": "C4-10-114",
                "post_final_top1_id": "C4-10-114",
            },
            {
                "sample_id": "r2-2",
                "is_match": False,
                "stored_ids": ["C5-2-10"],
                "algo_id": "C5-3-12",
                "error_stage": "post_ltr",
                "miss_category": "confidence_miss",
                "recall_rank": 1,
                "pre_ltr_top1_id": "C5-2-10",
                "post_ltr_top1_id": "C5-3-12",
                "post_final_top1_id": "C5-3-12",
            },
        ]
    )

    summary = build_summary(rows, Path("latest.jsonl"), Path("attr.json"))
    action = build_next_action(summary, rows)

    assert summary["target_common_issue"]["commonality"] == "singleton_only"
    assert action["action"] == ACTION_BY_BUCKET["R2"]
    assert action["sample_count"] == 1


def test_global_repair_next_action_forces_diagnostics_when_fields_missing():
    rows = build_rows(
        [
            {
                "sample_id": "r2-1",
                "is_match": False,
                "stored_ids": ["Q1"],
                "algo_id": "W1",
                "error_stage": "ltr_ranker",
            }
        ]
    )

    summary = build_summary(rows, Path("latest.jsonl"), Path("attr.json"))
    action = build_next_action(summary, rows)

    assert summary["missing_field_rate"] == 1.0
    assert action["action"] == "improve_diagnostics"
