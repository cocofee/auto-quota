# -*- coding: utf-8 -*-
"""评分/置信度漂移监控（P2-3c）

每次运行测评后，自动对比基线，检测指标退化。
当 ECE 漂移超过 0.05 或 top1 下降超过 0.03 时发出告警。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

import config


BASELINE_METRICS_PATH = config.OUTPUT_DIR / "regression" / "baseline_metrics.json"
DRIFT_LOG_PATH = config.OUTPUT_DIR / "regression" / "drift_log.jsonl"


@dataclass
class CalibrationAlert:
    metric: str
    baseline: float
    current: float
    drift: float
    threshold: float
    level: str  # "warning" or "error"

    def to_dict(self) -> dict:
        return {
            "metric": self.metric,
            "baseline": self.baseline,
            "current": self.current,
            "drift": self.drift,
            "threshold": self.threshold,
            "level": self.level,
        }


def save_baseline(metrics: dict) -> None:
    BASELINE_METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_METRICS_PATH.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info(f"基线已保存: {BASELINE_METRICS_PATH}")


def load_baseline() -> dict | None:
    if not BASELINE_METRICS_PATH.exists():
        return None
    try:
        return json.loads(BASELINE_METRICS_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(f"无法加载基线: {exc}")
        return None


def check_drift(current_metrics: dict, baseline: dict | None = None) -> list[CalibrationAlert]:
    if baseline is None:
        baseline = load_baseline()
    if baseline is None:
        logger.info("无基线数据，跳过漂移检测")
        return []

    alerts = []

    ece_baseline = float(baseline.get("confidence_calibration_ece", 0) or 0)
    ece_current = float(current_metrics.get("confidence_calibration_ece", 0) or 0)
    ece_drift = ece_current - ece_baseline
    if ece_drift > 0.05:
        alerts.append(CalibrationAlert(
            metric="confidence_calibration_ece", baseline=ece_baseline,
            current=ece_current, drift=ece_drift, threshold=0.05,
            level="error" if ece_drift > 0.10 else "warning"))

    top1_baseline = float(baseline.get("top1_accuracy", 0) or 0)
    top1_current = float(current_metrics.get("top1_accuracy", 0) or 0)
    top1_drift = top1_current - top1_baseline
    if top1_drift < -0.03:
        alerts.append(CalibrationAlert(
            metric="top1_accuracy", baseline=top1_baseline,
            current=top1_current, drift=top1_drift, threshold=-0.03,
            level="error" if top1_drift < -0.05 else "warning"))

    top3_baseline = float(baseline.get("top3_accuracy", 0) or 0)
    top3_current = float(current_metrics.get("top3_accuracy", 0) or 0)
    top3_drift = top3_current - top3_baseline
    if top3_drift < -0.05:
        alerts.append(CalibrationAlert(
            metric="top3_accuracy", baseline=top3_baseline,
            current=top3_current, drift=top3_drift, threshold=-0.05,
            level="error" if top3_drift < -0.10 else "warning"))

    _log_drift(current_metrics, baseline, alerts)

    if alerts:
        for a in alerts:
            logger.warning(f"[DRIFT-{a.level.upper()}] {a.metric}: "
                           f"{a.baseline:.4f} -> {a.current:.4f} ({a.drift:+.4f})")
    else:
        logger.info(f"[DRIFT-OK] top1={top1_drift:+.4f} top3={top3_drift:+.4f} ece={ece_drift:+.4f}")
    return alerts


def _log_drift(current: dict, baseline: dict, alerts: list[CalibrationAlert]) -> None:
    DRIFT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "pipeline_version": str(current.get("pipeline_version", "")),
        "baseline_version": str(baseline.get("pipeline_version", "")),
        "top1_current": current.get("top1_accuracy"),
        "top1_baseline": baseline.get("top1_accuracy"),
        "top3_current": current.get("top3_accuracy"),
        "top3_baseline": baseline.get("top3_accuracy"),
        "ece_current": current.get("confidence_calibration_ece"),
        "ece_baseline": baseline.get("confidence_calibration_ece"),
        "alerts": [a.to_dict() for a in alerts],
        "alert_count": len(alerts),
    }
    with DRIFT_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def check_latest_regression() -> list[CalibrationAlert]:
    summary_path = config.OUTPUT_DIR / "regression" / "latest_regression_summary.json"
    if not summary_path.exists():
        logger.warning("无回归结果文件")
        return []
    current = json.loads(summary_path.read_text(encoding="utf-8"))
    return check_drift(current)
