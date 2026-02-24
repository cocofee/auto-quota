from __future__ import annotations

import json
import shutil
import types
from pathlib import Path
from uuid import uuid4

import tools.run_benchmark as rb


def _new_tmp_dir() -> Path:
    root = Path("output") / "test_tmp"
    root.mkdir(parents=True, exist_ok=True)
    d = root / f"benchmark-guard-{uuid4().hex}"
    d.mkdir(parents=True, exist_ok=False)
    return d


def test_compare_with_baseline_marks_failed_dataset_as_regression():
    current = {"B2": {"_failed": True, "error": "boom"}}
    baseline = {
        "version": "v1",
        "date": "2026-02-23",
        "mode": "search",
        "datasets": {"B2": {"green_rate": 0.8, "red_rate": 0.1, "exp_hit_rate": 0.2}},
    }

    ok = rb.compare_with_baseline(current, baseline)

    assert ok is False


def test_save_baseline_excludes_failed_metrics(monkeypatch):
    tmp_dir = _new_tmp_dir()
    try:
        baseline_path = tmp_dir / "baseline.json"
        monkeypatch.setattr(rb, "BASELINE_PATH", baseline_path)

        all_metrics = {
            "B_ok": {"total": 10, "green_rate": 0.8, "yellow_rate": 0.1, "red_rate": 0.1,
                     "exp_hit_rate": 0.2, "fallback_rate": 0.0, "avg_time_sec": 0.1},
            "B_fail": {"_failed": True, "error": "x"},
        }
        rb.save_baseline(all_metrics, mode="search")

        data = json.loads(baseline_path.read_text(encoding="utf-8"))
        assert "B_ok" in data["datasets"]
        assert "B_fail" not in data["datasets"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_run_single_dataset_returns_failed_marker_on_runtime_error(monkeypatch):
    tmp_dir = _new_tmp_dir()
    try:
        input_path = tmp_dir / "sample.xlsx"
        input_path.write_bytes(b"dummy")

        fake_main = types.SimpleNamespace(
            run=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("simulated")),
            logger=types.SimpleNamespace(remove=lambda *a, **k: None, add=lambda *a, **k: None),
        )
        monkeypatch.setitem(__import__("sys").modules, "main", fake_main)

        metrics = rb.run_single_dataset(
            "X",
            {"path": str(input_path), "province": "test", "expected_items_range": [0, 10]},
            mode="search",
        )

        assert metrics is not None
        assert metrics.get("_failed") is True
        assert "simulated" in metrics.get("error", "")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_compute_metrics_counts_agent_error_as_fallback():
    results = [
        {"confidence": 90, "match_source": "agent"},
        {"confidence": 70, "match_source": "agent_fallback"},
        {"confidence": 30, "match_source": "agent_error"},
    ]

    metrics = rb.compute_metrics(results, elapsed=3.0)

    assert metrics["total"] == 3
    assert metrics["fallback_rate"] == 0.6667


def test_compute_metrics_handles_non_numeric_confidence():
    results = [
        {"confidence": "90", "match_source": "agent"},
        {"confidence": None, "match_source": "agent"},
        {"confidence": "bad", "match_source": "agent"},
    ]

    metrics = rb.compute_metrics(results, elapsed=3.0)

    assert metrics["total"] == 3
    assert metrics["green_rate"] == 0.3333
    assert metrics["yellow_rate"] == 0.0
    assert metrics["red_rate"] == 0.6667
