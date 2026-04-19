"""Shared confidence heuristics for ExperienceDB direct hits."""

from __future__ import annotations

import time

import config


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_confidence_value(value) -> int:
    numeric = _safe_float(value, 0.0)
    if 0.0 <= numeric <= 1.0:
        numeric *= 100.0
    return max(0, min(int(round(numeric)), 100))


def _reviewer_weight(record: dict) -> float:
    source = str(record.get("source") or "").strip().lower()
    layer = str(record.get("layer") or "").strip().lower()

    if source in {"user_correction", "openclaw_approved"}:
        return 1.00
    if source == "user_confirmed":
        return 0.99
    if source in {"multi_project_promoted", "promote_from_candidate"}:
        return 0.98
    if source in {"project_import", "completed_project", "reviewed_import"}:
        return 0.93
    if layer == "authority":
        return 0.96
    if layer == "verified":
        return 0.90
    if source in {"batch_import", "auto_review"}:
        return 0.86
    if source in {"project_import_suspect", "auto_match"}:
        return 0.80
    if layer == "candidate":
        return 0.84
    return 0.90


def _confirm_count_weight(record: dict) -> float:
    confirm_count = max(_safe_int(record.get("confirm_count"), 0), 0)
    if confirm_count <= 0:
        return 0.72
    if confirm_count == 1:
        return 0.82
    if confirm_count == 2:
        return 0.97
    if confirm_count == 3:
        return 0.99
    return 1.00


def _time_decay_weight(record: dict, *, now: float | None = None) -> float:
    updated_at = _safe_float(record.get("updated_at"), 0.0)
    created_at = _safe_float(record.get("created_at"), 0.0)
    timestamp = updated_at if updated_at > 0 else created_at
    if timestamp <= 0:
        return 1.00

    current_time = _safe_float(now, 0.0) if now is not None else time.time()
    age_days = max(0.0, (current_time - timestamp) / 86400.0)

    if age_days <= 30:
        return 1.00
    if age_days <= 180:
        return 0.99
    if age_days <= 365:
        return 0.97
    if age_days <= 730:
        return 0.93
    if age_days <= 1825:
        return 0.82
    return 0.68


def describe_effective_confidence(record: dict, *, now: float | None = None) -> dict:
    base_confidence = normalize_confidence_value(record.get("confidence", 0))
    time_decay = _time_decay_weight(record, now=now)
    reviewer_weight = _reviewer_weight(record)
    confirm_count_weight = _confirm_count_weight(record)
    effective_confidence = normalize_confidence_value(
        (base_confidence / 100.0)
        * time_decay
        * reviewer_weight
        * confirm_count_weight
    )
    return {
        "base_confidence": base_confidence,
        "time_decay": round(time_decay, 6),
        "reviewer_weight": round(reviewer_weight, 6),
        "confirm_count_weight": round(confirm_count_weight, 6),
        "effective_confidence": effective_confidence,
    }


def compute_effective_confidence(record: dict, *, now: float | None = None) -> int:
    return int(describe_effective_confidence(record, now=now)["effective_confidence"])


def allows_direct_pass(
    record: dict,
    *,
    threshold: int | None = None,
    min_confirmations: int | None = None,
    now: float | None = None,
) -> bool:
    direct_threshold = normalize_confidence_value(
        threshold
        if threshold is not None
        else getattr(config, "EXPERIENCE_DIRECT_THRESHOLD", 90)
    )
    required_confirmations = max(
        1,
        _safe_int(
            min_confirmations
            if min_confirmations is not None
            else getattr(config, "EXPERIENCE_DIRECT_MIN_CONFIRMATIONS", 2),
            2,
        ),
    )
    confirm_count = max(_safe_int(record.get("confirm_count"), 0), 0)
    if confirm_count < required_confirmations:
        return False
    return compute_effective_confidence(record, now=now) >= direct_threshold
