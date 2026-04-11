from __future__ import annotations

import json
import tempfile
from pathlib import Path

from tools import autoresearch_manager as arm
from tools.run_benchmark import (
    _build_json_overall,
    _build_by_province_summary,
    _get_baseline_json_hit_rate,
)


def test_build_by_province_summary_preserves_status():
    json_results = [
        {
            "province": "福建省市政工程预算定额(2017)",
            "total": 262,
            "correct": 61,
            "hit_rate": 23.3,
        }
    ]
    previous = {
        "by_province": {
            "福建省市政工程预算定额(2017)": {
                "status": "阶段一完成"
            }
        }
    }

    summary = _build_by_province_summary(json_results, previous)

    assert summary["福建省市政工程预算定额(2017)"]["score"] == 0.233
    assert summary["福建省市政工程预算定额(2017)"]["hit_rate"] == 23.3
    assert summary["福建省市政工程预算定额(2017)"]["status"] == "阶段一完成"


def test_get_baseline_json_hit_rate_supports_new_and_old_shapes():
    baseline_new = {
        "by_province": {
            "福建省房屋建筑与装饰工程预算定额(2017)": {
                "score": 0.394,
                "hit_rate": 39.4,
            }
        }
    }
    baseline_old = {
        "json_papers": {
            "福建省房屋建筑与装饰工程预算定额(2017)": {
                "hit_rate": 39.4,
            }
        }
    }

    assert _get_baseline_json_hit_rate(baseline_new, "福建省房屋建筑与装饰工程预算定额(2017)") == 39.4
    assert _get_baseline_json_hit_rate(baseline_old, "福建省房屋建筑与装饰工程预算定额(2017)") == 39.4


def test_build_by_province_summary_keeps_adaptive_strategy():
    json_results = [
        {
            "province": "测试省",
            "total": 10,
            "correct": 6,
            "hit_rate": 60.0,
            "adaptive_strategy": {
                "distribution": {
                    "fast": {
                        "count": 4,
                        "matched": 3,
                        "total_time_sec": 1.2,
                        "observed_time_count": 4,
                        "avg_time_sec": 0.3,
                        "rate": 40.0,
                        "matched_rate": 75.0,
                    },
                    "unknown": {
                        "count": 0,
                        "matched": 0,
                        "total_time_sec": 0.0,
                        "observed_time_count": 0,
                        "avg_time_sec": None,
                        "rate": 0.0,
                        "matched_rate": 0.0,
                    },
                }
            },
        }
    ]

    summary = _build_by_province_summary(json_results, {})
    assert summary["测试省"]["adaptive_strategy"]["distribution"]["fast"]["count"] == 4


def test_build_json_overall_merges_adaptive_strategy_summary():
    overall = _build_json_overall([
        {
            "total": 2,
            "correct": 1,
            "recall_miss_count": 1,
            "rank_miss_count": 0,
            "post_rank_miss_count": 0,
            "oracle_in_candidates": 1,
            "in_pool_top1_acc": 1.0,
            "adaptive_strategy": {
                "total": 2,
                "with_strategy_count": 2,
                "missing_strategy_count": 0,
                "observed_time_count": 2,
                "overall_avg_time_sec": 0.75,
                "distribution": {
                    "fast": {
                        "count": 1,
                        "matched": 1,
                        "total_time_sec": 0.5,
                        "observed_time_count": 1,
                        "avg_time_sec": 0.5,
                        "rate": 50.0,
                        "matched_rate": 100.0,
                    },
                    "standard": {
                        "count": 1,
                        "matched": 0,
                        "total_time_sec": 1.0,
                        "observed_time_count": 1,
                        "avg_time_sec": 1.0,
                        "rate": 50.0,
                        "matched_rate": 0.0,
                    },
                    "unknown": {
                        "count": 0,
                        "matched": 0,
                        "total_time_sec": 0.0,
                        "observed_time_count": 0,
                        "avg_time_sec": None,
                        "rate": 0.0,
                        "matched_rate": 0.0,
                    },
                },
            },
        },
        {
            "total": 1,
            "correct": 1,
            "recall_miss_count": 0,
            "rank_miss_count": 0,
            "post_rank_miss_count": 0,
            "oracle_in_candidates": 1,
            "in_pool_top1_acc": 1.0,
            "adaptive_strategy": {
                "total": 1,
                "with_strategy_count": 1,
                "missing_strategy_count": 0,
                "observed_time_count": 1,
                "overall_avg_time_sec": 2.0,
                "distribution": {
                    "deep": {
                        "count": 1,
                        "matched": 1,
                        "total_time_sec": 2.0,
                        "observed_time_count": 1,
                        "avg_time_sec": 2.0,
                        "rate": 100.0,
                        "matched_rate": 100.0,
                    },
                    "unknown": {
                        "count": 0,
                        "matched": 0,
                        "total_time_sec": 0.0,
                        "observed_time_count": 0,
                        "avg_time_sec": None,
                        "rate": 0.0,
                        "matched_rate": 0.0,
                    },
                },
            },
        },
    ])

    adaptive = overall["adaptive_strategy"]
    assert overall["total"] == 3
    assert adaptive["with_strategy_count"] == 3
    assert adaptive["distribution"]["fast"]["count"] == 1
    assert adaptive["distribution"]["standard"]["count"] == 1
    assert adaptive["distribution"]["deep"]["count"] == 1
    assert adaptive["overall_avg_time_sec"] == 1.167


def test_autoresearch_manager_queue_and_marginal(monkeypatch):
    with tempfile.TemporaryDirectory() as temp_dir:
        state_path = Path(temp_dir) / "autoresearch_state.json"
        monkeypatch.setattr(arm, "STATE_PATH", state_path)

        arm.update_queue(
            active=["P1: 同义词缺口"],
            carry_over=["P2: 搜索词偏差", "P3: 排序偏差"],
        )
        arm.record_round("P1: 同义词缺口", 0.08, "keep", carry_over=["P2: 搜索词偏差"])
        arm.record_round("P1: 同义词缺口", 0.06, "keep")
        arm.record_round("P1: 同义词缺口", 0.04, "keep")
        arm.record_round("P1: 同义词缺口", 0.03, "keep")
        arm.record_round("P1: 同义词缺口", 0.02, "keep")

        state = json.loads(Path(state_path).read_text(encoding="utf-8"))
        assert state["current_priority_queue"]["active"] == ["P1: 同义词缺口"]
        assert state["current_priority_queue"]["carry_over"] == ["P2: 搜索词偏差", "P3: 排序偏差"]

        analysis = arm.marginal_analysis(state)
        assert analysis["recommendation"] == "switch_direction"
        assert analysis["avg5"] == 0.046
