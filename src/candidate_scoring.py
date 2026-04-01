from __future__ import annotations

from collections import defaultdict
import os

from src.utils import safe_float

SCORING_MODE_ENV = "AUTO_QUOTA_SCORING_MODE"
DEFAULT_SCORING_MODE = "two_stage"


def normalize_scoring_mode(scoring_mode: str | None = None) -> str:
    value = str(
        scoring_mode
        or os.getenv(SCORING_MODE_ENV, "")
        or DEFAULT_SCORING_MODE
    ).strip().lower()
    alias_map = {
        "two-stage": "two_stage",
        "two stage": "two_stage",
        "family_first": "two_stage",
        "single-stage": "single_stage",
        "single stage": "single_stage",
        "legacy": "single_stage",
    }
    normalized = alias_map.get(value, value)
    if normalized not in {"single_stage", "two_stage"}:
        return DEFAULT_SCORING_MODE
    return normalized


def _clamp_family_gate(value) -> float:
    return max(min(safe_float(value, 0.0), 2.0), -2.0)


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _candidate_identity_key(candidate: dict) -> tuple[str, str, str]:
    return (
        str(candidate.get("quota_id", "") or "").strip(),
        str(candidate.get("name", "") or "").strip(),
        str(candidate.get("id", "") or candidate.get("db_id", "") or "").strip(),
    )


def resolve_candidate_rerank_score(candidate: dict) -> float:
    return safe_float(
        candidate.get(
            "active_rerank_score",
            candidate.get("rerank_score", candidate.get("hybrid_score", 0.0)),
        ),
        0.0,
    )


def _main_param_band_score(candidate: dict) -> float:
    explicit = candidate.get("main_param_band_score")
    if explicit is not None:
        return max(0.0, min(safe_float(explicit, 0.0), 1.0))

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

    param_score = safe_float(candidate.get("param_score"), 0.0)
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


def _has_fatal_rank_conflict(candidate: dict) -> bool:
    return any(
        bool(candidate.get(flag))
        for flag in (
            "family_gate_hard_conflict",
            "feature_alignment_hard_conflict",
            "logic_hard_conflict",
        )
    )


def _family_aligned(candidate: dict) -> bool:
    if candidate.get("logic_exact_primary_match"):
        return True
    if safe_float(candidate.get("family_gate_score"), 0.0) >= 1.0:
        return True
    if _safe_int(candidate.get("feature_alignment_exact_anchor_count"), 0) > 0:
        return True
    features = candidate.get("candidate_canonical_features") or candidate.get("canonical_features") or {}
    return bool(features.get("entity") or features.get("family") or features.get("system"))


def _family_stage_supported(candidate: dict) -> bool:
    if candidate.get("logic_exact_primary_match"):
        return True
    if safe_float(candidate.get("family_gate_score"), 0.0) >= 1.0:
        return True
    if _safe_int(candidate.get("feature_alignment_exact_anchor_count"), 0) > 0:
        return True
    return (
        safe_float(candidate.get("feature_alignment_score"), 0.0) >= 0.85
        and _family_aligned(candidate)
    )


def _get_family(candidate: dict) -> str:
    features = candidate.get("candidate_canonical_features") or candidate.get("canonical_features") or {}
    family = str(features.get("family") or "").strip()
    entity = str(features.get("entity") or "").strip()
    if not family:
        return ""
    return f"{family}:{entity}" if entity else family


def _family_bucket_key(candidate: dict) -> str:
    family = _get_family(candidate)
    if family:
        return family
    quota_id, name, row_id = _candidate_identity_key(candidate)
    return f"__singleton__:{quota_id}|{name}|{row_id}"


def _advisory_rule_count(candidate: dict) -> int:
    rules = candidate.get("param_rectify_selected_rules") or []
    if not isinstance(rules, list):
        return 0
    return len([rule for rule in rules if str(rule).strip()])


def _candidate_scope_match_score(candidate: dict) -> float:
    return max(0.0, min(safe_float(candidate.get("candidate_scope_match"), 0.0), 1.0))


def _candidate_scope_conflict(candidate: dict) -> bool:
    return bool(candidate.get("candidate_scope_conflict"))


def _family_match_score(candidate: dict) -> float:
    feature_score = safe_float(candidate.get("feature_alignment_score"), 0.5)
    context_score = safe_float(candidate.get("context_alignment_score"), 0.5)
    scope_match = _candidate_scope_match_score(candidate)
    family_gate = _clamp_family_gate(candidate.get("family_gate_score", 0.0))
    exact_anchor_count = min(_safe_int(candidate.get("feature_alignment_exact_anchor_count"), 0), 3)

    gate_score = 0.5
    if family_gate >= 1.0:
        gate_score = 1.0
    elif family_gate <= -1.0:
        gate_score = 0.0
    elif family_gate > 0:
        gate_score = min(1.0, 0.6 + family_gate * 0.2)
    elif family_gate < 0:
        gate_score = max(0.0, 0.4 + family_gate * 0.2)

    anchor_score = exact_anchor_count / 3.0 if exact_anchor_count else 0.0
    return max(
        0.0,
        min(
            1.0,
            feature_score * 0.45
            + context_score * 0.20
            + gate_score * 0.20
            + scope_match * 0.10
            + anchor_score * 0.05,
        ),
    )


def _family_confidence_penalty(candidate: dict) -> float:
    if _has_exact_experience_anchor(candidate):
        return 0.0

    family_gate = _clamp_family_gate(candidate.get("family_gate_score", 0.0))
    feature_score = safe_float(candidate.get("feature_alignment_score"), 0.5)
    context_score = safe_float(candidate.get("context_alignment_score"), 0.5)
    exact_anchor_count = _safe_int(candidate.get("feature_alignment_exact_anchor_count"), 0)

    penalty = 0.0
    if family_gate <= -0.5:
        penalty += 0.12
    elif family_gate < 0:
        penalty += 0.06

    if exact_anchor_count == 0:
        if feature_score < 0.45:
            penalty += 0.08
        elif feature_score < 0.60 and family_gate <= 0:
            penalty += 0.04

    if feature_score < 0.50 and context_score < 0.55 and family_gate <= 0:
        penalty += 0.04

    if _candidate_scope_conflict(candidate):
        penalty += 0.04
    return penalty


def _param_tier_score(candidate: dict) -> float:
    tier = _safe_int(candidate.get("param_tier"), 1)
    if tier >= 2:
        return 1.0
    if tier == 1:
        return 0.65
    return 0.35


def _family_selection_score(candidate: dict, *, primary_score: float | None = None) -> float:
    if primary_score is None:
        primary_score = compute_candidate_rank_score(candidate)

    rerank_score = resolve_candidate_rerank_score(candidate)
    hybrid_score = safe_float(candidate.get("hybrid_score"), rerank_score)
    feature_score = safe_float(candidate.get("feature_alignment_score"), 0.5)
    context_score = safe_float(candidate.get("context_alignment_score"), 0.5)
    logic_score = safe_float(candidate.get("logic_score"), 0.5)
    param_score = safe_float(candidate.get("param_score"), 0.0)
    name_bonus = safe_float(candidate.get("name_bonus"), 0.0)
    family_gate = _clamp_family_gate(candidate.get("family_gate_score", 0.0))
    scope_match = _candidate_scope_match_score(candidate)
    prior_score = compute_candidate_prior_score(candidate)
    family_match = _family_match_score(candidate)
    family_penalty = _family_confidence_penalty(candidate)

    score = (
        rerank_score * 0.40
        + hybrid_score * 0.14
        + family_match * 0.26
        + param_score * 0.08
        + logic_score * 0.04
        + feature_score * 0.03
        + context_score * 0.02
        + name_bonus * 0.02
        + scope_match * 0.02
        + safe_float(primary_score, 0.0) * 0.01
        + prior_score
    )
    if _family_stage_supported(candidate):
        score += 0.10
    if family_gate > 0:
        score += family_gate * 0.03
    elif family_gate < 0:
        score += family_gate * 0.06
    if candidate.get("logic_exact_primary_match"):
        score += 0.03
    if candidate.get("family_gate_hard_conflict"):
        score -= 0.40
    if candidate.get("feature_alignment_hard_conflict"):
        score -= 0.25
    if candidate.get("logic_hard_conflict"):
        score -= 0.15
    if _candidate_scope_conflict(candidate) and not _has_exact_experience_anchor(candidate):
        score -= 0.08
    if not candidate.get("param_match", True):
        score -= 0.03
    score -= family_penalty
    return score


def _within_family_score(candidate: dict, *, primary_score: float | None = None) -> float:
    if primary_score is None:
        primary_score = compute_candidate_rank_score(candidate)

    param_score = safe_float(candidate.get("param_score"), 0.0)
    logic_score = safe_float(candidate.get("logic_score"), 0.5)
    feature_score = safe_float(candidate.get("feature_alignment_score"), 0.5)
    context_score = safe_float(candidate.get("context_alignment_score"), 0.5)
    name_bonus = safe_float(candidate.get("name_bonus"), 0.0)
    main_param_band = _main_param_band_score(candidate)
    param_tier_score = _param_tier_score(candidate)

    score = (
        param_score * 0.50
        + logic_score * 0.30
        + param_tier_score * 0.20
        + main_param_band * 0.08
        + feature_score * 0.03
        + context_score * 0.02
        + name_bonus * 0.01
    )
    if candidate.get("logic_exact_primary_match"):
        score += 0.10
    if candidate.get("logic_exact_primary_match") and logic_score >= 0.95:
        score += 0.04
    if candidate.get("feature_alignment_hard_conflict") or candidate.get("logic_hard_conflict"):
        score -= 0.25
    if not candidate.get("param_match", True):
        score -= 0.18
    if candidate.get("family_gate_hard_conflict"):
        score -= 0.10
    if _candidate_scope_conflict(candidate) and not _has_exact_experience_anchor(candidate):
        score -= 0.02
    return score


def _family_stage_rank_key(candidate: dict, *, primary_score: float | None = None) -> tuple:
    if primary_score is None:
        primary_score = compute_candidate_rank_score(candidate)
    family_score = _family_selection_score(candidate, primary_score=primary_score)
    family_supported = _family_stage_supported(candidate)
    family_stage_priority = family_score
    return (
        1 if not _has_fatal_rank_conflict(candidate) else 0,
        1 if _has_exact_experience_anchor(candidate) else 0,
        1 if family_supported else 0,
        family_stage_priority,
        family_score,
        safe_float(primary_score, 0.0),
        _candidate_scope_match_score(candidate),
        0 if _candidate_scope_conflict(candidate) else 1,
        _safe_int(candidate.get("feature_alignment_exact_anchor_count"), 0),
        safe_float(candidate.get("feature_alignment_score"), 0.5),
        resolve_candidate_rerank_score(candidate),
        safe_float(candidate.get("name_bonus"), 0.0),
    )


def _within_family_rank_key(candidate: dict, *, primary_score: float | None = None) -> tuple:
    if primary_score is None:
        primary_score = compute_candidate_rank_score(candidate)
    within_score = _within_family_score(candidate, primary_score=primary_score)
    return (
        1 if not _has_fatal_rank_conflict(candidate) else 0,
        1 if candidate.get("param_match", True) else 0,
        within_score,
        1 if candidate.get("logic_exact_primary_match") else 0,
        _safe_int(candidate.get("param_tier"), 1),
        _param_tier_score(candidate),
        _main_param_band_score(candidate),
        safe_float(candidate.get("logic_score"), 0.5),
        safe_float(candidate.get("param_score"), 0.0),
        safe_float(candidate.get("feature_alignment_score"), 0.5),
        safe_float(primary_score, 0.0),
        resolve_candidate_rerank_score(candidate),
    )


def compute_candidate_prior_score(candidate: dict) -> float:
    knowledge_sources = {
        str(value).strip().lower()
        for value in list(candidate.get("knowledge_prior_sources") or [])
        if str(value).strip()
    }
    if not knowledge_sources:
        return 0.0

    prior_score = max(0.0, min(safe_float(candidate.get("knowledge_prior_score"), 0.0), 1.2))
    match_source = str(candidate.get("match_source", "") or "").strip().lower()
    experience_layer = str(candidate.get("experience_layer") or candidate.get("layer") or "").strip().lower()

    if "experience" in knowledge_sources:
        exact_experience = match_source == "experience_injected_exact" or prior_score >= 1.05
        authority_experience = exact_experience or experience_layer == "authority"
        if authority_experience:
            return 0.10 * prior_score
        return 0.06 * prior_score

    if "universal_kb" in knowledge_sources:
        exact_kb = match_source == "kb_injected_exact" or prior_score >= 1.0
        if exact_kb:
            return 0.05 * prior_score
        return 0.03 * prior_score

    if "quota_alias" in knowledge_sources:
        exact_alias = match_source == "quota_alias_exact" or prior_score >= 0.95
        if exact_alias:
            return 0.08 * prior_score
        return 0.04 * prior_score

    return 0.0


def _has_exact_experience_anchor(candidate: dict) -> bool:
    knowledge_sources = {
        str(value).strip().lower()
        for value in list(candidate.get("knowledge_prior_sources") or [])
        if str(value).strip()
    }
    if "experience" not in knowledge_sources:
        return False

    prior_score = safe_float(candidate.get("knowledge_prior_score"), 0.0)
    match_source = str(candidate.get("match_source", "") or "").strip().lower()
    return match_source == "experience_injected_exact" or prior_score >= 1.05


def has_exact_experience_anchor(candidate: dict) -> bool:
    return _has_exact_experience_anchor(candidate)


def has_exact_universal_kb_anchor(candidate: dict) -> bool:
    knowledge_sources = {
        str(value).strip().lower()
        for value in list(candidate.get("knowledge_prior_sources") or [])
        if str(value).strip()
    }
    if "universal_kb" not in knowledge_sources:
        return False

    prior_score = safe_float(candidate.get("knowledge_prior_score"), 0.0)
    match_source = str(candidate.get("match_source", "") or "").strip().lower()
    return match_source == "kb_injected_exact" or prior_score >= 1.0


def compute_candidate_advisory_score(candidate: dict) -> float:
    # Advisory signals are trace-only. They must not influence ranking.
    del candidate
    return 0.0


def compute_candidate_search_score(candidate: dict, *, include_plugin: bool = True) -> float:
    rerank_score = safe_float(candidate.get("rerank_score", candidate.get("hybrid_score", 0.0)))
    name_bonus = safe_float(candidate.get("name_bonus"))
    param_score = safe_float(candidate.get("param_score"))
    plugin_score = safe_float(candidate.get("plugin_score")) if include_plugin else 0.0
    return (
        rerank_score * 0.76
        + name_bonus * 0.16
        + param_score * 0.06
        + plugin_score * 0.02
    )


def explain_candidate_search_score(candidate: dict, *, include_plugin: bool = True) -> dict:
    rerank_score = safe_float(candidate.get("rerank_score", candidate.get("hybrid_score", 0.0)))
    name_bonus = safe_float(candidate.get("name_bonus"))
    param_score = safe_float(candidate.get("param_score"))
    plugin_score = safe_float(candidate.get("plugin_score")) if include_plugin else 0.0

    return {
        "score": compute_candidate_search_score(candidate, include_plugin=include_plugin),
        "components": {
            "rerank": {
                "value": rerank_score,
                "weight": 0.76,
                "contribution": rerank_score * 0.76,
            },
            "name_bonus": {
                "value": name_bonus,
                "weight": 0.16,
                "contribution": name_bonus * 0.16,
            },
            "param": {
                "value": param_score,
                "weight": 0.06,
                "contribution": param_score * 0.06,
            },
            "plugin": {
                "value": plugin_score,
                "weight": 0.02 if include_plugin else 0.0,
                "contribution": plugin_score * 0.02 if include_plugin else 0.0,
            },
        },
    }


def compute_candidate_structured_score(candidate: dict, *, include_plugin: bool = True) -> float:
    param_score = safe_float(candidate.get("param_score"), 0.0)
    logic_score = safe_float(candidate.get("logic_score"), 0.5)
    feature_score = safe_float(candidate.get("feature_alignment_score"), 0.5)
    context_score = safe_float(candidate.get("context_alignment_score"), 0.5)
    rerank_score = safe_float(candidate.get("rerank_score", candidate.get("hybrid_score", 0.0)), 0.0)
    name_bonus = safe_float(candidate.get("name_bonus"), 0.0)
    family_gate = _clamp_family_gate(candidate.get("family_gate_score", 0.0))
    plugin_score = safe_float(candidate.get("plugin_score"), 0.0) if include_plugin else 0.0
    prior_score = compute_candidate_prior_score(candidate)
    scope_match = _candidate_scope_match_score(candidate)
    scope_conflict = _candidate_scope_conflict(candidate)
    param_tier = _safe_int(candidate.get("param_tier"), 1)
    strong_family = (
        safe_float(candidate.get("family_gate_score"), 0.0) >= 1.0
        and candidate.get("param_match", True)
        and param_score >= 0.5
    )

    if strong_family:
        w_param = 0.28
        w_rerank = 0.22
    else:
        w_param = 0.10
        w_rerank = 0.40

    scope_weight = 0.26
    scope_conflict_penalty = 0.08

    score = (
        param_score * w_param
        + logic_score * 0.18
        + feature_score * 0.14
        + context_score * 0.08
        + rerank_score * w_rerank
        + name_bonus * 0.06
        + family_gate * 0.015
        + plugin_score * 0.01
        + prior_score
        + scope_match * scope_weight
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
    if scope_conflict and not _has_exact_experience_anchor(candidate):
        score -= scope_conflict_penalty
    return score


def explain_candidate_structured_score(candidate: dict, *, include_plugin: bool = True) -> dict:
    param_score = safe_float(candidate.get("param_score"), 0.0)
    logic_score = safe_float(candidate.get("logic_score"), 0.5)
    feature_score = safe_float(candidate.get("feature_alignment_score"), 0.5)
    context_score = safe_float(candidate.get("context_alignment_score"), 0.5)
    rerank_score = safe_float(candidate.get("rerank_score", candidate.get("hybrid_score", 0.0)), 0.0)
    name_bonus = safe_float(candidate.get("name_bonus"), 0.0)
    family_gate = _clamp_family_gate(candidate.get("family_gate_score", 0.0))
    plugin_score = safe_float(candidate.get("plugin_score"), 0.0) if include_plugin else 0.0
    prior_score = compute_candidate_prior_score(candidate)
    scope_match = _candidate_scope_match_score(candidate)
    scope_conflict = _candidate_scope_conflict(candidate)
    param_tier = _safe_int(candidate.get("param_tier"), 1)
    strong_family = (
        safe_float(candidate.get("family_gate_score"), 0.0) >= 1.0
        and candidate.get("param_match", True)
        and param_score >= 0.5
    )

    if strong_family:
        w_param = 0.28
        w_rerank = 0.22
    else:
        w_param = 0.10
        w_rerank = 0.40

    scope_weight = 0.26
    scope_conflict_penalty = 0.08

    components = {
        "param": {
            "value": param_score,
            "weight": w_param,
            "contribution": param_score * w_param,
        },
        "logic": {
            "value": logic_score,
            "weight": 0.18,
            "contribution": logic_score * 0.18,
        },
        "feature": {
            "value": feature_score,
            "weight": 0.14,
            "contribution": feature_score * 0.14,
        },
        "context": {
            "value": context_score,
            "weight": 0.08,
            "contribution": context_score * 0.08,
        },
        "rerank": {
            "value": rerank_score,
            "weight": w_rerank,
            "contribution": rerank_score * w_rerank,
        },
        "name_bonus": {
            "value": name_bonus,
            "weight": 0.06,
            "contribution": name_bonus * 0.06,
        },
        "family_gate": {
            "value": family_gate,
            "weight": 0.015,
            "contribution": family_gate * 0.015,
        },
        "plugin": {
            "value": plugin_score,
            "weight": 0.01 if include_plugin else 0.0,
            "contribution": plugin_score * 0.01 if include_plugin else 0.0,
        },
        "prior": {
            "value": prior_score,
            "weight": 1.0,
            "contribution": prior_score,
        },
        "scope_match": {
            "value": scope_match,
            "weight": scope_weight,
            "contribution": scope_match * scope_weight,
        },
    }

    bonuses: dict[str, float] = {}
    if param_tier >= 2:
        bonuses["param_tier"] = 0.04
    if candidate.get("logic_exact_primary_match"):
        bonuses["logic_exact_primary_match"] = 0.08
    if candidate.get("logic_exact_primary_match") and logic_score >= 0.95:
        bonuses["logic_exact_primary_match_high_conf"] = 0.04

    penalties: dict[str, float] = {}
    if candidate.get("feature_alignment_hard_conflict") or candidate.get("logic_hard_conflict"):
        penalties["fatal_structured_conflict"] = -0.20
    if not candidate.get("param_match", True):
        penalties["param_mismatch"] = -0.12
    if scope_conflict and not _has_exact_experience_anchor(candidate):
        penalties["scope_conflict"] = -scope_conflict_penalty

    base_score = sum(part["contribution"] for part in components.values())
    bonus_total = sum(bonuses.values())
    penalty_total = sum(penalties.values())
    score = base_score + bonus_total + penalty_total

    return {
        "score": score,
        "base_score": base_score,
        "components": components,
        "bonuses": bonuses,
        "penalties": penalties,
        "flags": {
            "strong_family": strong_family,
            "param_match": bool(candidate.get("param_match", True)),
            "logic_exact_primary_match": bool(candidate.get("logic_exact_primary_match", False)),
            "fatal_rank_conflict": _has_fatal_rank_conflict(candidate),
            "hard_conflict": _has_hard_conflict(candidate),
            "family_aligned": _family_aligned(candidate),
            "exact_experience_anchor": _has_exact_experience_anchor(candidate),
            "scope_match": scope_match,
            "scope_conflict": scope_conflict,
        },
    }


def two_stage_sort(candidates: list[dict], *, primary_score_field: str | None = None) -> list[dict]:
    ordered = list(candidates or [])
    if len(ordered) <= 1:
        for candidate in ordered:
            candidate["_stage_rank_mode"] = "two_stage"
        return ordered

    ordered.sort(key=_candidate_identity_key)
    primary_scores = {
        _candidate_identity_key(candidate): (
            safe_float(candidate.get(primary_score_field), 0.0)
            if primary_score_field
            else compute_candidate_rank_score(candidate)
        )
        for candidate in ordered
    }

    family_groups: dict[str, list[dict]] = defaultdict(list)
    for candidate in ordered:
        bucket = _family_bucket_key(candidate)
        candidate["_family_bucket"] = bucket
        family_groups[bucket].append(candidate)

    ranked_groups: list[tuple[tuple, list[dict]]] = []
    for family_bucket, group in family_groups.items():
        representative = max(
            group,
            key=lambda candidate: _family_stage_rank_key(
                candidate,
                primary_score=primary_scores[_candidate_identity_key(candidate)],
            ),
        )
        ranked_group = list(group)
        ranked_group.sort(key=_candidate_identity_key)
        ranked_group.sort(
            key=lambda candidate: _within_family_rank_key(
                candidate,
                primary_score=primary_scores[_candidate_identity_key(candidate)],
            ),
            reverse=True,
        )
        ranked_groups.append(
            (
                _family_stage_rank_key(
                    representative,
                    primary_score=primary_scores[_candidate_identity_key(representative)],
                ),
                ranked_group,
            )
        )

    ranked_groups.sort(key=lambda item: item[0], reverse=True)
    flattened: list[dict] = []
    for family_rank, (_, group) in enumerate(ranked_groups, start=1):
        representative_key = _candidate_identity_key(group[0])
        for within_rank, candidate in enumerate(group, start=1):
            candidate["_stage_rank_mode"] = "two_stage"
            candidate["two_stage_family_rank"] = family_rank
            candidate["two_stage_within_family_rank"] = within_rank
            candidate["two_stage_family_score"] = _family_selection_score(
                candidate,
                primary_score=primary_scores[_candidate_identity_key(candidate)],
            )
            candidate["two_stage_within_family_score"] = _within_family_score(
                candidate,
                primary_score=primary_scores[_candidate_identity_key(candidate)],
            )
            candidate["two_stage_primary_score"] = primary_scores[_candidate_identity_key(candidate)]
            candidate["two_stage_family_supported"] = _family_stage_supported(candidate)
            candidate["two_stage_family_winner"] = _candidate_identity_key(candidate) == representative_key
        flattened.extend(group)
    return flattened


def single_stage_sort(candidates: list[dict], *, primary_score_field: str | None = None) -> list[dict]:
    ordered = list(candidates or [])
    if len(ordered) <= 1:
        for candidate in ordered:
            candidate["_stage_rank_mode"] = "single_stage"
        return ordered

    ordered.sort(key=_candidate_identity_key)
    ordered.sort(
        key=lambda candidate: compute_candidate_stage_rank_key(
            candidate,
            primary_score=(
                safe_float(candidate.get(primary_score_field), 0.0)
                if primary_score_field
                else compute_candidate_rank_score(candidate)
            ),
        ),
        reverse=True,
    )
    for rank, candidate in enumerate(ordered, start=1):
        primary_score = (
            safe_float(candidate.get(primary_score_field), 0.0)
            if primary_score_field
            else compute_candidate_rank_score(candidate)
        )
        candidate["_stage_rank_mode"] = "single_stage"
        candidate["single_stage_rank"] = rank
        candidate["single_stage_primary_score"] = primary_score
    return ordered


def score_candidates_two_stage(
    candidates: list[dict],
    bill_item: dict | None = None,
    *,
    primary_score_field: str | None = None,
) -> list[dict]:
    ranked = two_stage_sort(candidates, primary_score_field=primary_score_field)
    bill_features = dict((bill_item or {}).get("canonical_features") or {})
    bill_family = str(bill_features.get("family") or "").strip()
    bill_entity = str(bill_features.get("entity") or "").strip()
    if bill_family or bill_entity:
        for candidate in ranked:
            candidate["two_stage_bill_family"] = bill_family
            candidate["two_stage_bill_entity"] = bill_entity
    return ranked


def score_candidates_single_stage(
    candidates: list[dict],
    *,
    primary_score_field: str | None = None,
) -> list[dict]:
    return single_stage_sort(candidates, primary_score_field=primary_score_field)


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
        return safe_float(candidate.get("cgr_score"), 0.0)
    if candidate.get("_rank_score_source") == "ltr" and candidate.get("ltr_score") is not None:
        return safe_float(candidate.get("ltr_score"), 0.0)
    if candidate.get("_rank_score_source") == "manual" and candidate.get("manual_structured_score") is not None:
        return safe_float(candidate.get("manual_structured_score"), 0.0)
    return compute_candidate_structured_score(candidate)


def explain_candidate_stage_rank_key(candidate: dict, primary_score: float | None = None) -> dict:
    if primary_score is None:
        primary_score = compute_candidate_rank_score(candidate)
    return {
        "fatal_conflict_free": not _has_fatal_rank_conflict(candidate),
        "exact_experience_anchor": _has_exact_experience_anchor(candidate),
        "param_match": bool(candidate.get("param_match", True)),
        "primary_score": safe_float(primary_score, 0.0),
        "family_bucket": _family_bucket_key(candidate),
        "family_selection_score": _family_selection_score(candidate, primary_score=primary_score),
        "within_family_score": _within_family_score(candidate, primary_score=primary_score),
        "logic_exact_primary_match": bool(candidate.get("logic_exact_primary_match", False)),
        "param_tier": _safe_int(candidate.get("param_tier"), 1),
        "main_param_band_score": _main_param_band_score(candidate),
        "prior_score": compute_candidate_prior_score(candidate),
        "scope_match": _candidate_scope_match_score(candidate),
        "scope_conflict": _candidate_scope_conflict(candidate),
        "family_aligned": _family_aligned(candidate),
        "feature_exact_anchor_count": _safe_int(candidate.get("feature_alignment_exact_anchor_count"), 0),
        "feature_alignment_score": safe_float(candidate.get("feature_alignment_score"), 0.5),
        "logic_score": safe_float(candidate.get("logic_score"), 0.5),
        "context_alignment_score": safe_float(candidate.get("context_alignment_score"), 0.5),
        "manual_structured_score": safe_float(candidate.get("manual_structured_score"), 0.0),
        "name_bonus": safe_float(candidate.get("name_bonus"), 0.0),
        "active_rerank_score": resolve_candidate_rerank_score(candidate),
        "ltr_score": safe_float(candidate.get("ltr_score"), 0.0),
        "hybrid_score": safe_float(candidate.get("hybrid_score"), 0.0),
    }


def explain_candidate_rank_score(candidate: dict) -> dict:
    rank_score_source = str(candidate.get("_rank_score_source", "") or "structured")
    rank_score = compute_candidate_rank_score(candidate)
    structured = explain_candidate_structured_score(candidate)
    search = explain_candidate_search_score(candidate)
    return {
        "scoring_mode": normalize_scoring_mode(),
        "rank_score": rank_score,
        "rank_score_source": rank_score_source,
        "structured": structured,
        "search": search,
        "stage_priority": explain_candidate_stage_rank_key(candidate, primary_score=rank_score),
    }


def compute_candidate_stage_rank_key(candidate: dict, primary_score: float | None = None) -> tuple:
    if primary_score is None:
        primary_score = compute_candidate_rank_score(candidate)
    return (
        1 if not _has_fatal_rank_conflict(candidate) else 0,
        1 if _has_exact_experience_anchor(candidate) else 0,
        1 if candidate.get("param_match", True) else 0,
        safe_float(primary_score, 0.0),
        1 if candidate.get("logic_exact_primary_match") else 0,
        _safe_int(candidate.get("param_tier"), 1),
        _main_param_band_score(candidate),
        compute_candidate_prior_score(candidate),
        _candidate_scope_match_score(candidate),
        0 if _candidate_scope_conflict(candidate) else 1,
        1 if _family_aligned(candidate) else 0,
        _safe_int(candidate.get("feature_alignment_exact_anchor_count"), 0),
        safe_float(candidate.get("feature_alignment_score"), 0.5),
        safe_float(candidate.get("logic_score"), 0.5),
        safe_float(candidate.get("context_alignment_score"), 0.5),
        safe_float(candidate.get("manual_structured_score"), 0.0),
        safe_float(candidate.get("name_bonus"), 0.0),
        resolve_candidate_rerank_score(candidate),
        safe_float(candidate.get("ltr_score"), 0.0),
        safe_float(candidate.get("hybrid_score"), 0.0),
    )


def compute_candidate_rank_tuple(candidate: dict) -> tuple:
    return compute_candidate_stage_rank_key(candidate)


def sort_candidates_with_stage_priority(
    candidates: list[dict],
    *,
    primary_score_field: str | None = None,
    scoring_mode: str | None = None,
) -> list[dict]:
    normalized_mode = normalize_scoring_mode(scoring_mode)
    if normalized_mode == "single_stage":
        return single_stage_sort(candidates, primary_score_field=primary_score_field)
    return two_stage_sort(candidates, primary_score_field=primary_score_field)


def compute_candidate_sort_key(candidate: dict) -> tuple[float, float]:
    return (
        safe_float(candidate.get("param_tier", 1), 1.0),
        compute_candidate_rank_score(candidate),
    )
