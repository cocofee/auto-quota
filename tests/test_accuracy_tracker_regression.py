import src.accuracy_tracker as accuracy_tracker_mod
from src.accuracy_tracker import AccuracyTracker


def test_record_regression_run_and_get_latest(tmp_path, monkeypatch):
    db_path = tmp_path / "run_history.db"
    monkeypatch.setattr(accuracy_tracker_mod, "_DB_PATH", db_path)

    tracker = AccuracyTracker()
    tracker.record_regression_run(
        pipeline_version="v1",
        dataset_path="eval/golden_set.jsonl",
        eval_mode="closed_book",
        profile="dev",
        baseline_version="v0",
        deltas={
            "top1_accuracy": 0.05,
            "top3_accuracy": 0.03,
            "fastpath_precision": 0.02,
            "confidence_calibration_ece": -0.01,
        },
        metrics={
            "total": 12,
            "top1_accuracy": 0.75,
            "top3_accuracy": 0.83,
            "fastpath_precision": 0.9,
            "fastpath_count": 5,
            "confidence_calibration_ece": 0.11,
            "per_specialty_accuracy": {
                "C10": {"total": 8, "top1_accuracy": 0.75, "top3_accuracy": 0.875},
            },
        },
    )

    latest = tracker.get_latest_regression_run(
        dataset_path="eval/golden_set.jsonl",
        eval_mode="closed_book",
    )

    assert latest is not None
    assert latest["pipeline_version"] == "v1"
    assert latest["top1_accuracy"] == 0.75
    assert latest["top3_accuracy"] == 0.83
    assert latest["fastpath_precision"] == 0.9
    assert latest["confidence_calibration_ece"] == 0.11
    assert latest["baseline_version"] == "v0"
    assert latest["delta"] == {
        "top1_accuracy": 0.05,
        "top3_accuracy": 0.03,
        "fastpath_precision": 0.02,
        "confidence_calibration_ece": -0.01,
    }
    assert latest["per_specialty_accuracy"]["C10"]["top1_accuracy"] == 0.75
    assert latest["metrics"]["fastpath_count"] == 5


def test_get_recent_regression_runs_returns_latest_first(tmp_path, monkeypatch):
    db_path = tmp_path / "run_history.db"
    monkeypatch.setattr(accuracy_tracker_mod, "_DB_PATH", db_path)

    tracker = AccuracyTracker()
    tracker.record_regression_run(
        pipeline_version="v1",
        dataset_path="eval/golden_set.jsonl",
        eval_mode="closed_book",
        metrics={"total": 10, "top1_accuracy": 0.6, "top3_accuracy": 0.8, "fastpath_precision": 0.7, "confidence_calibration_ece": 0.2},
    )
    tracker.record_regression_run(
        pipeline_version="v2",
        dataset_path="eval/golden_set.jsonl",
        eval_mode="closed_book",
        metrics={"total": 10, "top1_accuracy": 0.7, "top3_accuracy": 0.9, "fastpath_precision": 0.8, "confidence_calibration_ece": 0.1},
    )

    rows = tracker.get_recent_regression_runs(
        dataset_path="eval/golden_set.jsonl",
        eval_mode="closed_book",
        limit=2,
    )

    assert [row["pipeline_version"] for row in rows] == ["v2", "v1"]


def test_get_latest_regression_run_scopes_profile_and_excludes_active_version(tmp_path, monkeypatch):
    db_path = tmp_path / "run_history.db"
    monkeypatch.setattr(accuracy_tracker_mod, "_DB_PATH", db_path)

    tracker = AccuracyTracker()
    tracker.record_regression_run(
        pipeline_version="v1",
        dataset_path="eval/golden_set.jsonl",
        eval_mode="closed_book",
        profile="dev",
        metrics={"total": 10, "top1_accuracy": 0.6, "top3_accuracy": 0.8, "fastpath_precision": 0.7, "confidence_calibration_ece": 0.2},
    )
    tracker.record_regression_run(
        pipeline_version="v1",
        dataset_path="eval/golden_set.jsonl",
        eval_mode="closed_book",
        profile="full",
        metrics={"total": 10, "top1_accuracy": 0.65, "top3_accuracy": 0.82, "fastpath_precision": 0.72, "confidence_calibration_ece": 0.18},
    )
    tracker.record_regression_run(
        pipeline_version="v2",
        dataset_path="eval/golden_set.jsonl",
        eval_mode="closed_book",
        profile="dev",
        metrics={"total": 10, "top1_accuracy": 0.7, "top3_accuracy": 0.9, "fastpath_precision": 0.8, "confidence_calibration_ece": 0.1},
    )

    latest = tracker.get_latest_regression_run(
        dataset_path="eval/golden_set.jsonl",
        eval_mode="closed_book",
        profile="dev",
        exclude_pipeline_version="v2",
    )

    assert latest is not None
    assert latest["pipeline_version"] == "v1"
    assert latest["profile"] == "dev"
    assert latest["top1_accuracy"] == 0.6
