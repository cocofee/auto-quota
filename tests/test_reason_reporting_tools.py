import json
import shutil
import uuid
from pathlib import Path

from tools import batch_digest, batch_report, jarvis_pipeline


def test_batch_report_analyze_patterns_includes_reason_summary():
    report = batch_report.analyze_patterns([
        {
            "province": "测试省",
            "specialty": "C10",
            "results": [
                {
                    "name": "0005002",
                    "confidence": 22,
                    "match_source": "input_gate_abstain",
                    "primary_reason": "dirty_input",
                    "reason_tags": ["dirty_input", "numeric_code", "manual_review"],
                },
                {
                    "name": "给水管",
                    "confidence": 91,
                    "match_source": "search",
                    "primary_reason": "structured_selection",
                    "reason_tags": ["retrieved", "validated"],
                },
            ],
        }
    ])

    assert report["reason_summary"]["primary"][0]["key"] in {"dirty_input", "structured_selection"}
    assert report["low_confidence_reason_summary"]["primary"][0]["key"] == "dirty_input"
    assert report["by_province"]["测试省"]["top_reasons"]["primary"]


def test_batch_digest_scan_results_collects_reason_breakdown(monkeypatch):
    temp_root = Path("test_artifacts") / f"reason_tools_{uuid.uuid4().hex}"
    results_dir = temp_root / "results"
    province_dir = results_dir / "测试安装"
    province_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "province": "测试安装",
        "results": [
            {
                "name": "0005002",
                "confidence": 18,
                "primary_reason": "dirty_input",
                "reason_tags": ["dirty_input", "numeric_code"],
            },
            {
                "name": "配管",
                "confidence": 88,
                "primary_reason": "structured_selection",
                "reason_tags": ["retrieved", "validated"],
                "quotas": [{"quota_id": "Q1", "name": "配管安装"}],
            },
        ],
    }
    with open(province_dir / "sample.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)

    try:
        monkeypatch.setattr(batch_digest, "RESULTS_DIR", results_dir)
        report = batch_digest.scan_results()
        assert report["overall"]["reason_summary"]["primary"]
        assert report["overall"]["low_reason_summary"]["primary"][0]["key"] == "dirty_input"
        assert report["provinces"]["测试安装"]["low_reason_summary"]["primary"][0]["key"] == "dirty_input"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_pipeline_stats_include_reason_summary():
    stats = jarvis_pipeline._build_pipeline_stats(
        results=[
            {
                "confidence": 20,
                "primary_reason": "dirty_input",
                "reason_tags": ["dirty_input", "numeric_code"],
                "quotas": [],
            },
            {
                "confidence": 92,
                "primary_reason": "structured_selection",
                "reason_tags": ["retrieved", "validated"],
                "quotas": [{"quota_id": "Q1", "name": "给水管安装"}],
            },
        ],
        auto_corrections=[],
        manual_items=[],
        measure_items=[],
    )

    assert stats["reason_summary"]["overall"]["primary"]
    assert stats["reason_summary"]["low_confidence"]["primary"][0]["key"] == "dirty_input"
    assert stats["reason_summary"]["no_match"]["primary"][0]["key"] == "dirty_input"
