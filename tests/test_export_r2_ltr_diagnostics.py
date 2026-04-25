from __future__ import annotations

from tools.export_r2_ltr_diagnostics import (
    build_r2_ltr_diagnostics,
    classify_r2_bucket,
)


def _candidate(quota_id: str, **overrides):
    base = {
        "quota_id": quota_id,
        "name": f"name-{quota_id}",
        "param_score": 0.5,
        "feature_alignment_score": 0.5,
        "manual_structured_score": 0.5,
        "rerank_score": 0.5,
        "ltr_score": 0.5,
        "ltr_feature_snapshot": {
            "semantic_rerank_zscore": 0.0,
            "hybrid_zscore": 0.0,
            "entity_match": 0,
            "canonical_name_match": 0,
            "system_match": 0,
            "family_match": 0,
            "entity_conflict": 0,
            "canonical_name_conflict": 0,
            "system_conflict": 0,
            "family_conflict": 0,
        },
    }
    base.update(overrides)
    return base


def _detail(**overrides):
    base = {
        "bill_id": "b-1",
        "bill_name": "DN100 pipe",
        "specialty": "C6",
        "stored_ids": ["Q-CORRECT"],
        "stored_names": ["correct quota"],
        "algo_id": "Q-WRONG",
        "algo_name": "wrong quota",
        "is_match": False,
        "recall_rank": 2,
        "pre_ltr_top1_id": "Q-WRONG",
        "post_ltr_top1_id": "Q-WRONG",
        "post_cgr_top1_id": "Q-WRONG",
        "post_final_top1_id": "Q-WRONG",
        "candidate_snapshots": [
            _candidate("Q-WRONG", rerank_score=0.9, ltr_score=0.9),
            _candidate("Q-CORRECT", param_score=0.9, feature_alignment_score=0.9),
        ],
    }
    base.update(overrides)
    return base


def test_build_r2_ltr_diagnostics_exports_in_pool_rows():
    payload = {
        "results": [
            {
                "province": "Test Province",
                "details": [
                    _detail(),
                    _detail(bill_id="matched", is_match=True),
                    _detail(bill_id="r1", recall_rank=-1),
                    _detail(bill_id="r3", post_ltr_top1_id="Q-CORRECT", post_cgr_top1_id="Q-WRONG"),
                ],
            }
        ]
    }

    rows, summary = build_r2_ltr_diagnostics(payload)

    assert summary["r2_total"] == 1
    assert summary["r2_type_counts"] == {"in_pool_not_ltr_top1": 1}
    assert rows[0]["correct_snapshot_rank"] == 2
    assert rows[0]["selected_snapshot_rank"] == 1
    assert rows[0]["param_gap_selected_minus_correct"] == -0.4
    assert rows[0]["bucket"] == "structure_signal_sparse"


def test_build_r2_ltr_diagnostics_detects_ltr_bad_flip():
    rows, summary = build_r2_ltr_diagnostics({
        "results": [
            {
                "province": "Test Province",
                "details": [
                    _detail(
                        pre_ltr_top1_id="Q-CORRECT",
                        post_ltr_top1_id="Q-WRONG",
                        candidate_snapshots=[
                            _candidate(
                                "Q-WRONG",
                                ltr_feature_snapshot={"entity_conflict": 1},
                            ),
                            _candidate(
                                "Q-CORRECT",
                                ltr_feature_snapshot={
                                    "entity_match": 1,
                                    "canonical_name_match": 1,
                                    "system_match": 1,
                                },
                            ),
                        ],
                    )
                ],
            }
        ]
    })

    assert summary["r2_type_counts"] == {"ltr_bad_flip_pre_correct": 1}
    assert rows[0]["bucket"] == "selected_struct_conflict"
    assert "pre_ltr_was_correct" in rows[0]["tags"]
    assert rows[0]["selected_struct_conflict_count"] == 1


def test_classify_r2_bucket_marks_positive_outside_snapshot_window():
    bucket, tags = classify_r2_bucket(
        r2_type="in_pool_not_ltr_top1",
        recall_rank=30,
        candidates=[_candidate(str(i)) for i in range(20)],
        correct_candidate={},
        selected_candidate=_candidate("Q-WRONG"),
        correct_rank=0,
        selected_rank=1,
    )

    assert bucket == "oracle_beyond_snapshot_window"
    assert tags == ["positive_not_in_top_snapshot_window"]
