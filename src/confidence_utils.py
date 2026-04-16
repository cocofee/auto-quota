from __future__ import annotations


def clamp_confidence(value) -> float:
    try:
        return max(0.0, min(100.0, float(value or 0.0)))
    except (TypeError, ValueError):
        return 0.0


def apply_confidence_penalty(confidence, penalty) -> float:
    """Apply percentage-point evidence as probabilistic confidence decay/boost."""

    base_confidence = clamp_confidence(confidence)
    try:
        penalty_value = float(penalty or 0.0)
    except (TypeError, ValueError):
        return base_confidence

    if penalty_value == 0.0:
        return base_confidence

    if penalty_value < 0.0:
        survival_rate = max(0.0, min(1.0, 1.0 + (penalty_value / 100.0)))
        return clamp_confidence(base_confidence * survival_rate)

    boost_rate = max(0.0, min(1.0, penalty_value / 100.0))
    return clamp_confidence(100.0 - ((100.0 - base_confidence) * (1.0 - boost_rate)))
