from __future__ import annotations


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp_family_gate(value) -> float:
    return max(min(_safe_float(value, 0.0), 2.0), -2.0)


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def resolve_candidate_rerank_score(candidate: dict) -> float:
    return _safe_float(
        candidate.get(
            "active_rerank_score",
            candidate.get("rerank_score", candidate.get("hybrid_score", 0.0)),
        ),
        0.0,
    )


def _main_param_band_score(candidate: dict) -> float:
    explicit = candidate.get("main_param_band_score")
    if explicit is not None:
        return max(0.0, min(_safe_float(explicit, 0.0), 1.0))

    ltr_param = candidate.get("_ltr_param") or {}
    if ltr_param.get("param_main_exact"):
        return 1.0

    rel_dist = ltr_param.get("param_main_rel_dist")
    if rel_dist is not None:
        try:
            rel_dist = float(rel_dist)
        except (TypeError, ValueError):
            rel_dist = 1.0
        return max(0.0, min(1.0 - rel_dist, 1.0))

    param_score = _safe_float(candidate.get("param_score"), 0.0)
    if candidate.get("param_tier", 1) >= 2:
        return min(param_score, 1.0)
    return min(param_score * 0.8, 1.0)


def _has_hard_conflict(candidate: dict) -> bool:
    return any(
        bool(candidate.get(flag))
        for flag in (
            "family_gate_hard_conflict",
            "feature_alignment_hard_conflict",
            "logic_hard_conflict",
            "context_alignment_hard_conflict",
        )
    )


def _family_aligned(candidate: dict) -> bool:
    if candidate.get("logic_exact_primary_match"):
        return True
    if _safe_float(candidate.get("family_gate_score"), 0.0) >= 1.0:
        return True
    if _safe_int(candidate.get("feature_alignment_exact_anchor_count"), 0) > 0:
        return True
    features = candidate.get("candidate_canonical_features") or candidate.get("canonical_features") or {}
    return bool(features.get("entity") or features.get("family") or features.get("system"))


def _get_family(candidate: dict) -> str:
    features = candidate.get("candidate_canonical_features") or candidate.get("canonical_features") or {}
    family = str(features.get("family") or "").strip()
    entity = str(features.get("entity") or "").strip()
    if not family:
        return ""
    return f"{family}:{entity}" if entity else family


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
    param_score = _safe_float(candidate.get("param_score"), 0.0)
    logic_score = _safe_float(candidate.get("logic_score"), 0.5)
    feature_score = _safe_float(candidate.get("feature_alignment_score"), 0.5)
    context_score = _safe_float(candidate.get("context_alignment_score"), 0.5)
    rerank_score = _safe_float(candidate.get("rerank_score", candidate.get("hybrid_score", 0.0)), 0.0)
    name_bonus = _safe_float(candidate.get("name_bonus"), 0.0)
    family_gate = _clamp_family_gate(candidate.get("family_gate_score", 0.0))
    plugin_score = _safe_float(candidate.get("plugin_score"), 0.0) if include_plugin else 0.0
    param_tier = _safe_int(candidate.get("param_tier"), 1)

    strong_family = (
        _safe_float(candidate.get("family_gate_score"), 0.0) >= 1.0
        and candidate.get("param_match", True)
        and param_score >= 0.5
    )

    if strong_family:
        w_param = 0.28
        w_rerank = 0.22
    else:
        w_param = 0.10
        w_rerank = 0.40

    score = (
        param_score * w_param
        + logic_score * 0.18
        + feature_score * 0.14
        + context_score * 0.08
        + rerank_score * w_rerank
        + name_bonus * 0.06
        + family_gate * 0.015
        + plugin_score * 0.01
    )
    if param_tier >= 2:
        score += 0.04
    if candidate.get("logic_exact_primary_match"):
        score += 0.08
    if candidate.get("logic_exact_primary_match") and logic_score >= 0.95:
        score += 0.04
    if candidate.get("feature_alignment_hard_conflict") or candidate.get("logic_hard_conflict"):
        score -= 0.20
    if not candidate.get("param_match", True):
        score -= 0.12
    return score


def two_stage_sort(candidates: list[dict]) -> list[dict]:
    if not candidates or len(candidates) <= 1:
        return candidates

    ordered = list(candidates)
    for candidate in ordered:
        stage1_score = (
            _safe_float(candidate.get("rerank_score", candidate.get("hybrid_score", 0.0)), 0.0) * 0.55
            + _safe_float(candidate.get("name_bonus"), 0.0) * 0.15
            + _safe_float(candidate.get("feature_alignment_score"), 0.5) * 0.12
            + _safe_float(candidate.get("logic_score"), 0.5) * 0.08
            + _safe_float(candidate.get("context_alignment_score"), 0.5) * 0.05
            + _clamp_family_gate(candidate.get("family_gate_score", 0.0)) * 0.03
            + _safe_float(candidate.get("param_score"), 0.0) * 0.02
        )
        if candidate.get("feature_alignment_hard_conflict") or candidate.get("logic_hard_conflict"):
            stage1_score -= 0.20
        if candidate.get("family_gate_hard_conflict"):
            stage1_score -= 0.30
        candidate["_stage1_score"] = stage1_score

    ordered.sort(key=lambda candidate: candidate.get("_stage1_score", 0.0), reverse=True)

    top_family = _get_family(ordered[0])
    if not top_family:
        for candidate in ordered:
            candidate.pop("_stage1_score", None)
        return ordered

    same_family: list[dict] = []
    others: list[dict] = []
    for candidate in ordered:
        if _get_family(candidate) == top_family:
            same_family.append(candidate)
        else:
            others.append(candidate)

    for candidate in same_family:
        param_tier = _safe_int(candidate.get("param_tier"), 1)
        logic_score = _safe_float(candidate.get("logic_score"), 0.5)
        param_score = _safe_float(candidate.get("param_score"), 0.0)

        stage2_score = (
            param_score * 0.35
            + logic_score * 0.30
            + _safe_float(candidate.get("feature_alignment_score"), 0.5) * 0.15
            + _safe_float(candidate.get("rerank_score", candidate.get("hybrid_score", 0.0)), 0.0) * 0.10
            + _safe_float(candidate.get("name_bonus"), 0.0) * 0.05
            + _safe_float(candidate.get("context_alignment_score"), 0.5) * 0.05
        )
        if param_tier >= 2:
            stage2_score += 0.06
        if candidate.get("logic_exact_primary_match"):
            stage2_score += 0.08
        if candidate.get("logic_exact_primary_match") and logic_score >= 0.95:
            stage2_score += 0.04
        if not candidate.get("param_match", True):
            stage2_score -= 0.15
        candidate["_stage2_score"] = stage2_score

    same_family.sort(key=lambda candidate: candidate.get("_stage2_score", 0.0), reverse=True)
    result = same_family + others

    for candidate in result:
        candidate.pop("_stage1_score", None)
        candidate.pop("_stage2_score", None)
    return result


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
    if candidate.get("_rank_score_source") == "cgr" and candidate.get("cgr_score") is not None:
        return _safe_float(candidate.get("cgr_score"), 0.0)
    if candidate.get("_rank_score_source") == "ltr" and candidate.get("ltr_score") is not None:
        return _safe_float(candidate.get("ltr_score"), 0.0)
    return compute_candidate_structured_score(candidate)


def compute_candidate_rank_tuple(candidate: dict) -> tuple:
    return (
        1 if not _has_hard_conflict(candidate) else 0,
        1 if candidate.get("param_match", True) else 0,
        1 if _family_aligned(candidate) else 0,
        1 if candidate.get("logic_exact_primary_match") else 0,
        _safe_int(candidate.get("param_tier"), 1),
        _main_param_band_score(candidate),
        _safe_int(candidate.get("feature_alignment_exact_anchor_count"), 0),
        _safe_float(candidate.get("feature_alignment_score"), 0.5),
        _safe_float(candidate.get("logic_score"), 0.5),
        _safe_float(candidate.get("context_alignment_score"), 0.5),
        _safe_float(candidate.get("name_bonus"), 0.0),
        resolve_candidate_rerank_score(candidate),
        _safe_float(candidate.get("ltr_score"), 0.0),
        _safe_float(candidate.get("hybrid_score"), 0.0),
    )


def compute_candidate_sort_key(candidate: dict) -> tuple[float, float]:
    return (
        _safe_float(candidate.get("param_tier", 1), 1.0),
        compute_candidate_rank_score(candidate),
    )
