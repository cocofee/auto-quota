from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from loguru import logger

import config
from src.utils import safe_float


def _clip01(value: Any) -> float:
    return max(0.0, min(1.0, safe_float(value, 0.0)))


def _sigmoid(value: float) -> float:
    value = max(min(safe_float(value, 0.0), 40.0), -40.0)
    return 1.0 / (1.0 + math.exp(-value))


@dataclass(frozen=True)
class ConfidenceCalibrationSpec:
    intercept: float
    weights: dict[str, float]
    isotonic_points: tuple[tuple[float, float], ...]
    ambiguous_short_ceiling: int = 75

    @property
    def max_score(self) -> int:
        if not self.isotonic_points:
            return 100
        return int(round(self.isotonic_points[-1][1] * 100.0))


DEFAULT_CONFIDENCE_SPEC = ConfidenceCalibrationSpec(
    intercept=-3.5,
    weights={
        "param_match": 2.10,
        "param_score": 1.90,
        "name_bonus": 1.40,
        "score_gap": 1.20,
        "rerank_score": 0.90,
        "family_aligned": 1.00,
        "family_hard_conflict": -1.20,
        "candidate_support": 0.25,
        "ambiguous_short": -0.70,
    },
    isotonic_points=(
        (0.00, 0.05),
        (0.20, 0.16),
        (0.40, 0.35),
        (0.55, 0.55),
        (0.70, 0.72),
        (0.82, 0.84),
        (0.90, 0.90),
        (0.96, 0.95),
        (1.00, 0.95),
    ),
    ambiguous_short_ceiling=75,
)


def _normalize_isotonic_points(points: list[Any] | tuple[Any, ...] | None) -> tuple[tuple[float, float], ...]:
    normalized: list[tuple[float, float]] = []
    for point in points or []:
        if isinstance(point, dict):
            raw = _clip01(point.get("raw"))
            calibrated = _clip01(point.get("calibrated"))
        elif isinstance(point, (list, tuple)) and len(point) >= 2:
            raw = _clip01(point[0])
            calibrated = _clip01(point[1])
        else:
            continue
        normalized.append((raw, calibrated))

    if len(normalized) < 2:
        return DEFAULT_CONFIDENCE_SPEC.isotonic_points

    normalized.sort(key=lambda row: row[0])
    deduped: list[tuple[float, float]] = []
    running_max = 0.0
    for raw, calibrated in normalized:
        running_max = max(running_max, calibrated)
        if deduped and abs(deduped[-1][0] - raw) <= 1e-9:
            deduped[-1] = (raw, running_max)
            continue
        deduped.append((raw, running_max))

    if deduped[0][0] > 0.0:
        deduped.insert(0, (0.0, deduped[0][1]))
    if deduped[-1][0] < 1.0:
        deduped.append((1.0, deduped[-1][1]))
    return tuple(deduped)


def _load_spec_from_payload(payload: dict[str, Any]) -> ConfidenceCalibrationSpec:
    model_payload = payload.get("model") if isinstance(payload.get("model"), dict) else payload
    weights_payload = model_payload.get("weights") if isinstance(model_payload, dict) else {}
    weights = dict(DEFAULT_CONFIDENCE_SPEC.weights)
    if isinstance(weights_payload, dict):
        for name, value in weights_payload.items():
            weights[str(name)] = safe_float(value, weights.get(str(name), 0.0))

    intercept = safe_float(
        model_payload.get("intercept") if isinstance(model_payload, dict) else None,
        DEFAULT_CONFIDENCE_SPEC.intercept,
    )
    isotonic_points = _normalize_isotonic_points(payload.get("isotonic"))
    ambiguous_short_ceiling = int(
        max(
            0,
            min(
                100,
                safe_float(
                    payload.get("ambiguous_short_ceiling"),
                    DEFAULT_CONFIDENCE_SPEC.ambiguous_short_ceiling,
                ),
            ),
        )
    )
    return ConfidenceCalibrationSpec(
        intercept=intercept,
        weights=weights,
        isotonic_points=isotonic_points,
        ambiguous_short_ceiling=ambiguous_short_ceiling,
    )


@lru_cache(maxsize=1)
def get_confidence_calibration_spec() -> ConfidenceCalibrationSpec:
    calibration_path = Path(getattr(config, "CONFIDENCE_CALIBRATION_PATH", ""))
    if not calibration_path:
        return DEFAULT_CONFIDENCE_SPEC
    try:
        if not calibration_path.exists():
            return DEFAULT_CONFIDENCE_SPEC
        payload = json.loads(calibration_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return DEFAULT_CONFIDENCE_SPEC
        return _load_spec_from_payload(payload)
    except Exception as exc:
        logger.warning(f"failed to load confidence calibration artifact from {calibration_path}: {exc}")
        return DEFAULT_CONFIDENCE_SPEC


def calibrate_confidence_probability(raw_probability: float, *, spec: ConfidenceCalibrationSpec | None = None) -> float:
    resolved = spec or get_confidence_calibration_spec()
    probability = _clip01(raw_probability)
    points = resolved.isotonic_points
    if not points:
        return probability
    if probability <= points[0][0]:
        return points[0][1]
    for index in range(1, len(points)):
        left_raw, left_cal = points[index - 1]
        right_raw, right_cal = points[index]
        if probability <= right_raw:
            span = max(right_raw - left_raw, 1e-9)
            ratio = (probability - left_raw) / span
            return max(0.0, min(1.0, left_cal + (right_cal - left_cal) * ratio))
    return points[-1][1]


def build_confidence_features(
    *,
    param_score: float,
    param_match: bool,
    name_bonus: float,
    score_gap: float,
    rerank_score: float,
    family_aligned: bool,
    family_hard_conflict: bool,
    candidates_count: int,
    is_ambiguous_short: bool,
) -> dict[str, float]:
    candidate_support = _clip01((safe_float(candidates_count, 0.0) - 2.0) / 4.0)
    return {
        "param_match": 1.0 if param_match else 0.0,
        "param_score": _clip01(param_score),
        "name_bonus": _clip01(name_bonus),
        "score_gap": _clip01(score_gap),
        "rerank_score": _clip01(rerank_score),
        "family_aligned": 1.0 if family_aligned else 0.0,
        "family_hard_conflict": 1.0 if family_hard_conflict else 0.0,
        "candidate_support": candidate_support,
        "ambiguous_short": 1.0 if is_ambiguous_short else 0.0,
    }


def compute_confidence_probability(
    *,
    param_score: float,
    param_match: bool,
    name_bonus: float,
    score_gap: float,
    rerank_score: float,
    family_aligned: bool,
    family_hard_conflict: bool,
    candidates_count: int,
    is_ambiguous_short: bool,
    spec: ConfidenceCalibrationSpec | None = None,
) -> dict[str, Any]:
    resolved = spec or get_confidence_calibration_spec()
    features = build_confidence_features(
        param_score=param_score,
        param_match=param_match,
        name_bonus=name_bonus,
        score_gap=score_gap,
        rerank_score=rerank_score,
        family_aligned=family_aligned,
        family_hard_conflict=family_hard_conflict,
        candidates_count=candidates_count,
        is_ambiguous_short=is_ambiguous_short,
    )
    logit = resolved.intercept
    contributions: dict[str, float] = {}
    for name, value in features.items():
        weight = safe_float(resolved.weights.get(name), 0.0)
        contribution = weight * value
        contributions[name] = contribution
        logit += contribution
    raw_probability = _sigmoid(logit)
    calibrated_probability = calibrate_confidence_probability(raw_probability, spec=resolved)
    return {
        "features": features,
        "contributions": contributions,
        "logit": logit,
        "raw_probability": raw_probability,
        "calibrated_probability": calibrated_probability,
        "max_score": resolved.max_score,
        "ambiguous_short_ceiling": resolved.ambiguous_short_ceiling,
    }


def compute_confidence_score(**kwargs: Any) -> int:
    details = compute_confidence_probability(**kwargs)
    score = int(round(details["calibrated_probability"] * 100.0))
    score = max(0, min(details["max_score"], score))
    if kwargs.get("is_ambiguous_short"):
        score = min(score, int(details["ambiguous_short_ceiling"]))
    return score
