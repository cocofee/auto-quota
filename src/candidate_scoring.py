from __future__ import annotations


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp_family_gate(value) -> float:
    return max(min(_safe_float(value, 0.0), 2.0), -2.0)


def compute_candidate_search_score(candidate: dict, *, include_plugin: bool = True) -> float:
    rerank_score = _safe_float(candidate.get("rerank_score", candidate.get("hybrid_score", 0.0)))
    name_bonus = _safe_float(candidate.get("name_bonus"))
    param_score = _safe_float(candidate.get("param_score"))
    plugin_score = _safe_float(candidate.get("plugin_score")) if include_plugin else 0.0
    return (
        rerank_score * 0.76
        + name_bonus * 0.16
        + param_score * 0.06
        + plugin_score * 0.02
    )


def compute_candidate_structured_score(candidate: dict, *, include_plugin: bool = True) -> float:
    score = (
        _safe_float(candidate.get("param_score"), 0.0) * 0.36
        + _safe_float(candidate.get("logic_score"), 0.5) * 0.24
        + _safe_float(candidate.get("feature_alignment_score"), 0.5) * 0.18
        + _safe_float(candidate.get("context_alignment_score"), 0.5) * 0.08
        + _safe_float(candidate.get("rerank_score", candidate.get("hybrid_score", 0.0)), 0.0) * 0.08
        + _safe_float(candidate.get("name_bonus"), 0.0) * 0.04
        + _clamp_family_gate(candidate.get("family_gate_score", 0.0)) * 0.015
        + (_safe_float(candidate.get("plugin_score"), 0.0) if include_plugin else 0.0) * 0.01
    )
    if candidate.get("logic_exact_primary_match"):
        score += 0.06
    if candidate.get("feature_alignment_hard_conflict") or candidate.get("logic_hard_conflict"):
        score -= 0.20
    if not candidate.get("param_match", True):
        score -= 0.12
    return score


def _band_score(target_value: float, candidate_value: float | None) -> tuple[float, bool]:
    if candidate_value is None:
        return 0.45, False
    if candidate_value == target_value:
        return 1.0, True
    if candidate_value > target_value:
        ratio = candidate_value / max(target_value, 1.0)
        if ratio <= 1.10:
            return 0.90, False
        if ratio <= 1.35:
            return 0.78, False
        if ratio <= 2.0:
            return 0.60, False
        return 0.35, False
    return 0.0, False


def compute_candidate_total_score(candidate: dict,
                                  *,
                                  main_param_key: str | None = None,
                                  target_value: float | None = None,
                                  candidate_value: float | None = None,
                                  include_plugin: bool = True) -> tuple[float, float, bool]:
    structured = compute_candidate_structured_score(candidate, include_plugin=include_plugin)
    if not main_param_key or target_value is None:
        return structured, 0.0, False
    band_score, exact_match = _band_score(target_value, candidate_value)
    return structured + band_score * 0.22, band_score, exact_match


def compute_candidate_rank_score(candidate: dict) -> float:
    return compute_candidate_structured_score(candidate)


def compute_candidate_sort_key(candidate: dict) -> tuple[float, float]:
    return (
        _safe_float(candidate.get("param_tier", 1), 1.0),
        compute_candidate_rank_score(candidate),
    )
