from __future__ import annotations

import json
import tempfile
from pathlib import Path

from tools import autoresearch_manager as arm
from tools.run_benchmark import (
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
