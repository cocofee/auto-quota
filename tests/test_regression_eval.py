from __future__ import annotations

from pathlib import Path

import pytest

from eval.run_regression import evaluate_on_golden_set


class _FakeTracker:
    def __init__(self, baseline: dict | None = None):
        self._baseline = baseline
        self.recorded: dict | None = None
        self.latest_lookup: dict | None = None

    def get_latest_regression_run(
        self,
        *,
        dataset_path: str = "",
        eval_mode: str = "",
        profile: str = "",
        exclude_pipeline_version: str = "",
    ) -> dict | None:
        self.latest_lookup = {
            "dataset_path": dataset_path,
            "eval_mode": eval_mode,
            "profile": profile,
            "exclude_pipeline_version": exclude_pipeline_version,
        }
        return self._baseline

    def record_regression_run(self, **kwargs):
        self.recorded = kwargs


def test_evaluate_on_golden_set_computes_metrics_and_persists_delta(monkeypatch, tmp_path):
    dataset_path = tmp_path / "golden_set.jsonl"
    dataset_path.write_text("", encoding="utf-8")

    payload = {
        "dataset_path": str(dataset_path),
        "profile": "dev",
        "eval_mode": "closed_book",
        "skipped_provinces": [],
        "province_results": [
            {
                "province": "P1",
                "details": [
                    {
                        "specialty": "C10",
                        "oracle_quota_ids": ["Q1"],
                        "all_candidate_ids": ["Q1", "Q9", "Q8"],
                        "is_match": True,
                        "confidence": 90,
                        "accept_reason": "accept_head_confident",
                        "reasoning_decision": {"reason": "accept_head_confident"},
                        "match_source": "agent_fastpath",
                    },
                    {
                        "specialty": "C10",
                        "oracle_quota_ids": ["Q2"],
                        "all_candidate_ids": ["Q3", "Q2", "Q7"],
                        "is_match": False,
                        "confidence": 80,
                        "accept_reason": "accept_head_confident",
                        "reasoning_decision": {"reason": "accept_head_confident"},
                        "match_source": "agent_fastpath",
                    },
                ],
            },
            {
                "province": "P2",
                "details": [
                    {
                        "specialty": "C20",
                        "oracle_quota_ids": ["Q4"],
                        "all_candidate_ids": ["Q4", "Q5", "Q6"],
                        "is_match": True,
                        "confidence": 60,
                        "accept_reason": "",
                        "reasoning_decision": {},
                        "match_source": "search",
                    },
                    {
                        "specialty": "C20",
                        "oracle_quota_ids": ["Q8"],
                        "all_candidate_ids": ["Q9", "Q10", "Q11"],
                        "is_match": False,
                        "confidence": 20,
                        "accept_reason": "",
                        "reasoning_decision": {},
                        "match_source": "search",
                    },
                ],
            },
        ],
    }

    monkeypatch.setattr("eval.run_regression.run_real_eval", lambda *args, **kwargs: payload)

    tracker = _FakeTracker(
        baseline={
            "pipeline_version": "v-prev",
            "top1_accuracy": 0.25,
            "top3_accuracy": 0.50,
            "fastpath_precision": 0.25,
            "confidence_calibration_ece": 0.40,
        }
    )

    result = evaluate_on_golden_set(
        "v-next",
        dataset_path=dataset_path,
        tracker=tracker,
        persist=True,
    )

    assert result["top1_accuracy"] == 0.5
    assert result["top3_accuracy"] == 0.75
    assert result["fastpath_precision"] == 0.5
    assert result["fastpath_count"] == 2
    assert result["confidence_calibration_ece"] == pytest.approx(0.375, abs=1e-6)
    assert result["baseline_version"] == "v-prev"
    assert result["delta"] == {
        "top1_accuracy": 0.25,
        "top3_accuracy": 0.25,
        "fastpath_precision": 0.25,
        "confidence_calibration_ece": -0.025,
    }
    assert result["per_specialty_accuracy"] == {
        "C10": {"total": 2, "top1_accuracy": 0.5, "top3_accuracy": 1.0},
        "C20": {"total": 2, "top1_accuracy": 0.5, "top3_accuracy": 0.5},
    }

    assert tracker.recorded is not None
    assert tracker.latest_lookup == {
        "dataset_path": str(dataset_path),
        "eval_mode": "closed_book",
        "profile": "dev",
        "exclude_pipeline_version": "v-next",
    }
    assert tracker.recorded["pipeline_version"] == "v-next"
    assert tracker.recorded["baseline_version"] == "v-prev"
    assert tracker.recorded["deltas"]["top1_accuracy"] == 0.25


def test_evaluate_on_golden_set_handles_empty_payload(monkeypatch, tmp_path):
    dataset_path = tmp_path / "golden_set.jsonl"
    dataset_path.write_text("", encoding="utf-8")

    monkeypatch.setattr(
        "eval.run_regression.run_real_eval",
        lambda *args, **kwargs: {
            "dataset_path": str(dataset_path),
            "profile": "dev",
            "eval_mode": "closed_book",
            "province_results": [],
            "skipped_provinces": [],
        },
    )

    tracker = _FakeTracker()
    result = evaluate_on_golden_set("v-empty", dataset_path=dataset_path, tracker=tracker)

    assert result["total"] == 0
    assert result["top1_accuracy"] == 0.0
    assert result["top3_accuracy"] == 0.0
    assert result["fastpath_precision"] == 0.0
    assert result["confidence_calibration_ece"] == 0.0
