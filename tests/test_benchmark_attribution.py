from tools.run_benchmark import (
    _build_attribution_summary,
    _build_r2_ltr_feature_rows,
    _classify_attribution_category,
    _detail_recall_rank,
    build_benchmark_summary,
)


def _rank_steps(*, ltr: str = "", cgr: str = "", arbiter: str = "", explicit: str = "", final: str = ""):
    return [
        {"stage": "rank_stage", "name": "ltr", "top1_id": ltr},
        {"stage": "rank_stage", "name": "cgr_ranker", "top1_id": cgr},
        {"stage": "rank_stage", "name": "candidate_arbiter", "top1_id": arbiter},
        {"stage": "rank_stage", "name": "explicit_picker", "top1_id": explicit},
        {"stage": "rank_stage", "name": "category_safe", "top1_id": final},
    ]


def test_experience_exact_miss_ignores_cached_recall_rank():
    detail = {
        "bill_id": "exp-1",
        "bill_name": "experience-direct miss",
        "stored_ids": ["Q-CORRECT"],
        "algo_id": "Q-WRONG",
        "is_match": False,
        "match_source": "experience_exact",
        "recall_rank": 1,
        "all_candidate_ids": ["Q-CORRECT", "Q-WRONG"],
    }

    assert _detail_recall_rank(detail) is None
    assert _classify_attribution_category(detail) == "R5_\u7ecf\u9a8c\u5e93\u76f4\u901a\u9519"


def test_build_attribution_summary_groups_r1_to_r6_and_keeps_examples():
    json_results = [
        {
            "province": "广东",
            "details": [
                {
                    "bill_id": "gd-1",
                    "bill_name": "召回失败样例",
                    "stored_ids": ["Q-R1"],
                    "algo_id": "W-R1",
                    "is_match": False,
                    "match_source": "search",
                    "recall_topk_ids": ["W-R1", "W-R1B"],
                    "rank_stage_steps": _rank_steps(ltr="W-R1", cgr="W-R1", arbiter="W-R1", explicit="W-R1", final="W-R1"),
                },
                {
                    "bill_id": "gd-2",
                    "bill_name": "经验直通错误",
                    "stored_ids": ["Q-R5"],
                    "algo_id": "W-R5",
                    "is_match": False,
                    "match_source": "experience_exact",
                    "recall_topk_ids": [],
                    "rank_stage_steps": [],
                },
                {
                    "bill_id": "gd-3",
                    "bill_name": "命中样例",
                    "stored_ids": ["Q-HIT"],
                    "algo_id": "Q-HIT",
                    "is_match": True,
                    "match_source": "search",
                    "recall_topk_ids": ["Q-HIT", "W-HIT"],
                },
            ],
        },
        {
            "province": "浙江",
            "details": [
                {
                    "bill_id": "zj-1",
                    "bill_name": "picker 推翻正确",
                    "stored_ids": ["Q-R4"],
                    "algo_id": "W-R4",
                    "is_match": False,
                    "match_source": "search",
                    "recall_topk_ids": ["Q-R4", "W-R4"],
                    "rank_stage_steps": _rank_steps(ltr="Q-R4", cgr="Q-R4", arbiter="Q-R4", explicit="W-R4", final="W-R4"),
                },
                {
                    "bill_id": "zj-2",
                    "bill_name": "CGR 推翻正确",
                    "stored_ids": ["Q-R3"],
                    "algo_id": "W-R3",
                    "is_match": False,
                    "match_source": "search",
                    "recall_topk_ids": ["Q-R3", "W-R3"],
                    "rank_stage_steps": _rank_steps(ltr="Q-R3", cgr="W-R3", arbiter="W-R3", explicit="W-R3", final="W-R3"),
                },
                {
                    "bill_id": "zj-3",
                    "bill_name": "LTR 选错",
                    "stored_ids": ["Q-R2"],
                    "algo_id": "W-R2",
                    "is_match": False,
                    "match_source": "search",
                    "recall_topk_ids": ["Q-R2", "W-R2"],
                    "rank_stage_steps": _rank_steps(ltr="W-R2", cgr="W-R2", arbiter="W-R2", explicit="W-R2", final="W-R2"),
                },
                {
                    "bill_id": "zj-4",
                    "bill_name": "其它兜底",
                    "stored_ids": ["Q-R6"],
                    "algo_id": "W-R6",
                    "is_match": False,
                    "match_source": "search",
                    "recall_topk_ids": ["Q-R6", "W-R6"],
                    "rank_stage_steps": _rank_steps(ltr="Q-R6", cgr="Q-R6", arbiter="Q-R6", explicit="Q-R6", final="W-R6"),
                },
            ],
        },
    ]

    summary = _build_attribution_summary(json_results)

    assert summary["total"] == 7
    assert summary["correct_total"] == 1
    assert summary["wrong_total"] == 6
    assert summary["recall_eligible_total"] == 6
    assert summary["recall_hit_count"] == 5
    assert summary["recall_hit_rate"] == 83.3
    assert summary["counts"] == {
        "R1_召回未命中": 1,
        "R5_经验库直通错": 1,
        "R4_Picker推翻正确": 1,
        "R3_CGR推翻正确": 1,
        "R2_LTR选错": 1,
        "R6_其它": 1,
    }
    assert summary["self_check"]["counts_match_wrong_total"] is True
    assert summary["categories"]["R4_Picker推翻正确"]["samples"][0]["bill_id"] == "zj-1"
    assert summary["categories"]["R5_经验库直通错"]["samples"][0]["correct_quota_id"] == "Q-R5"


def test_build_attribution_summary_uses_trace_rank_stage_steps_when_flat_fields_missing():
    json_results = [
        {
            "province": "江苏",
            "details": [
                {
                    "bill_id": "js-1",
                    "bill_name": "trace-only",
                    "stored_ids": ["Q-CGR"],
                    "algo_id": "W-CGR",
                    "is_match": False,
                    "match_source": "search",
                    "recall_topk_ids": ["Q-CGR", "W-CGR"],
                    "post_ltr_top1_id": "",
                    "post_cgr_top1_id": "",
                    "post_explicit_top1_id": "",
                    "rank_stage_steps": _rank_steps(ltr="Q-CGR", cgr="W-CGR", arbiter="W-CGR", explicit="W-CGR", final="W-CGR"),
                }
            ],
        }
    ]

    summary = _build_attribution_summary(json_results)

    assert summary["counts"]["R3_CGR推翻正确"] == 1
    assert summary["counts"]["R6_其它"] == 0


def test_build_benchmark_summary_embeds_attribution_summary():
    json_results = [
        {
            "province": "北京",
            "total": 2,
            "correct": 1,
            "hit_rate": 50.0,
            "recall_miss_count": 1,
            "rank_miss_count": 0,
            "post_rank_miss_count": 0,
            "oracle_in_candidates": 1,
            "in_pool_top1_acc": 1.0,
            "details": [
                {
                    "bill_id": "bj-1",
                    "bill_name": "召回失败",
                    "stored_ids": ["Q1"],
                    "algo_id": "W1",
                    "is_match": False,
                    "match_source": "search",
                    "recall_topk_ids": ["W1"],
                },
                {
                    "bill_id": "bj-2",
                    "bill_name": "命中",
                    "stored_ids": ["Q2"],
                    "algo_id": "Q2",
                    "is_match": True,
                    "match_source": "search",
                    "recall_topk_ids": ["Q2"],
                },
            ],
            "adaptive_strategy": {
                "distribution": {
                    "unknown": {
                        "count": 0,
                        "matched": 0,
                        "total_time_sec": 0.0,
                        "observed_time_count": 0,
                        "avg_time_sec": None,
                        "rate": 0.0,
                        "matched_rate": 0.0,
                    }
                }
            },
        }
    ]

    summary = build_benchmark_summary(json_results, {}, None)

    assert summary["attribution"]["overall_hit_rate"] == 50.0
    assert summary["attribution"]["counts"]["R1_召回未命中"] == 1


def test_build_r2_ltr_feature_rows_extracts_top3_candidate_features():
    json_results = [
        {
            "province": "北京",
            "details": [
                {
                    "bill_id": "bj-r2-1",
                    "bill_name": "控制箱",
                    "stored_ids": ["Q-CORRECT"],
                    "algo_id": "Q-WRONG",
                    "is_match": False,
                    "match_source": "search",
                    "recall_topk_ids": ["Q-CORRECT", "Q-WRONG", "Q-ALT"],
                    "post_ltr_top1_id": "Q-WRONG",
                    "trace": {
                        "steps": [
                            {
                                "stage": "search_select",
                                "ranker": {
                                    "top_candidates": [
                                        {
                                            "quota_id": "Q-WRONG",
                                            "name": "错误候选",
                                            "bm25_score": 12.3,
                                            "vector_score": 0.44,
                                            "param_score": 0.81,
                                            "name_bonus": 0.25,
                                            "rerank_score": 0.93,
                                            "rank_stage": "ltr",
                                        },
                                        {
                                            "quota_id": "Q-CORRECT",
                                            "name": "正确候选",
                                            "bm25_score": 11.1,
                                            "vector_score": 0.52,
                                            "param_score": 0.92,
                                            "name_bonus": 0.12,
                                            "rerank_score": 0.88,
                                            "rank_stage": "ltr",
                                        },
                                    ]
                                },
                            }
                        ]
                    },
                    "candidate_snapshots": [],
                }
            ],
        }
    ]

    rows = _build_r2_ltr_feature_rows(json_results)

    assert len(rows) == 2
    assert rows[0]["bill_id"] == "bj-r2-1"
    assert rows[0]["candidate_rank"] == 1
    assert rows[0]["candidate_quota_id"] == "Q-WRONG"
    assert rows[0]["is_predicted_candidate"] is True
    assert rows[1]["candidate_quota_id"] == "Q-CORRECT"
    assert rows[1]["is_correct_candidate"] is True
    assert rows[1]["param_score"] == 0.92
