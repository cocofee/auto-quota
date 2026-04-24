from __future__ import annotations

import pandas as pd

from tools.train_ltr_v2 import (
    DEFAULT_PROTECT_GROUP_WEIGHTS,
    build_sample_weights,
    evaluate_do_not_break,
    infer_feature_names,
    split_queries,
    summarize_sample_sources,
)


def test_infer_feature_names_filters_metadata():
    df = pd.DataFrame(
        [
            {
                "query_id": 1,
                "province": "A",
                "candidate_quota_id": "Q1",
                "bm25_rank": 1.0,
                "semantic_rerank_zscore": 0.3,
                "label": 2,
            }
        ]
    )

    feature_names = infer_feature_names(df)

    assert feature_names == ["bm25_rank", "semantic_rerank_zscore"]


def test_split_queries_keeps_query_rows_together():
    df = pd.DataFrame(
        [
            {"query_id": 1, "label": 2, "feature_a": 1.0},
            {"query_id": 1, "label": 0, "feature_a": 0.2},
            {"query_id": 2, "label": 2, "feature_a": 1.0},
            {"query_id": 2, "label": 0, "feature_a": 0.2},
            {"query_id": 3, "label": 2, "feature_a": 1.0},
            {"query_id": 3, "label": 0, "feature_a": 0.2},
        ]
    )

    train_df, holdout_df = split_queries(df, holdout_ratio=0.34, seed=42)

    assert set(train_df["query_id"]).isdisjoint(set(holdout_df["query_id"]))
    assert train_df["query_id"].nunique() + holdout_df["query_id"].nunique() == 3


def test_build_sample_weights_uses_sample_source_defaults():
    df = pd.DataFrame(
        [
            {"query_id": 1, "sample_source": "benchmark_r2_silver", "protect_group": ""},
            {"query_id": 1, "sample_source": "benchmark_safety_correct", "protect_group": "socket_guard"},
            {"query_id": 2, "sample_source": "manual_targeted_safety_seed", "protect_group": "socket_guard"},
            {"query_id": 2, "sample_source": "other"},
        ]
    )

    weights = build_sample_weights(
        df,
        r2_weight=1.0,
        safety_weight=1.4,
        default_weight=0.9,
        protect_group_weights={"socket_guard": 1.5},
    )

    assert [round(value, 4) for value in weights] == [1.0, 2.1, 2.1, 0.9]


def test_default_protect_weights_prioritize_equipotential_guard():
    assert DEFAULT_PROTECT_GROUP_WEIGHTS["equipotential_guard"] == 4.0
    assert DEFAULT_PROTECT_GROUP_WEIGHTS["lighting_guard"] == 1.25


def test_summarize_sample_sources_counts_rows():
    df = pd.DataFrame(
        [
            {"sample_source": "benchmark_safety_correct"},
            {"sample_source": "benchmark_r2_silver"},
            {"sample_source": "benchmark_r2_silver"},
        ]
    )

    summary = summarize_sample_sources(df)

    assert summary == {
        "benchmark_r2_silver": 2,
        "benchmark_safety_correct": 1,
    }


def test_evaluate_do_not_break_reports_group_regression():
    df = pd.DataFrame(
        [
            {
                "query_id": 10,
                "candidate_quota_id": "Q-CORRECT",
                "predicted_quota_id": "Q-CORRECT",
                "label": 2,
                "ltr_score": 0.1,
                "manual_structured_score": 0.9,
            },
            {
                "query_id": 10,
                "candidate_quota_id": "Q-WRONG",
                "predicted_quota_id": "Q-CORRECT",
                "label": 0,
                "ltr_score": 0.9,
                "manual_structured_score": 0.1,
            },
        ]
    )
    do_not_break_records = [
        {
            "query_id": 10,
            "protect_group": "electrical_box_guard",
            "failure_target": "avoid_R4",
            "training_role": "train_and_eval",
        }
    ]

    summary = evaluate_do_not_break(df, do_not_break_records)

    assert summary["total"] == 1
    assert summary["baseline_hit_at_1"] == 1.0
    assert summary["hit_at_1"] == 0.0
    assert summary["regression_guard_failed"] is True
    assert summary["groups"]["electrical_box_guard"]["failure_targets"] == ["avoid_R4"]
    assert summary["violations"] == [
        {
            "query_id": 10,
            "protect_group": "electrical_box_guard",
            "failure_target": "avoid_R4",
            "training_role": "train_and_eval",
            "is_watch_only": False,
            "correct_quota_id": "Q-CORRECT",
            "baseline_top1_id": "Q-CORRECT",
            "model_top1_id": "Q-WRONG",
            "model_top1_score": 0.9,
            "baseline_hit": True,
            "model_hit": False,
            "candidate_count": 2,
        }
    ]
    assert summary["groups"]["electrical_box_guard"]["violations"] == summary["violations"]


def test_evaluate_do_not_break_does_not_block_on_eval_only_groups():
    df = pd.DataFrame(
        [
            {
                "query_id": 20,
                "candidate_quota_id": "Q-CORRECT",
                "predicted_quota_id": "Q-CORRECT",
                "label": 2,
                "ltr_score": 0.1,
                "manual_structured_score": 0.9,
            },
            {
                "query_id": 20,
                "candidate_quota_id": "Q-WRONG",
                "predicted_quota_id": "Q-CORRECT",
                "label": 0,
                "ltr_score": 0.9,
                "manual_structured_score": 0.1,
            },
        ]
    )
    do_not_break_records = [
        {
            "query_id": 20,
            "protect_group": "ventilation_guard",
            "failure_target": "avoid_any_regression",
            "training_role": "eval_only",
        }
    ]

    summary = evaluate_do_not_break(df, do_not_break_records)

    assert summary["regression_guard_failed"] is False
    assert summary["watch_only_groups"] == ["ventilation_guard"]
    assert summary["groups"]["ventilation_guard"]["is_watch_only"] is True
    assert summary["violations"][0]["protect_group"] == "ventilation_guard"
    assert summary["violations"][0]["is_watch_only"] is True
    assert summary["violations"][0]["model_top1_id"] == "Q-WRONG"
