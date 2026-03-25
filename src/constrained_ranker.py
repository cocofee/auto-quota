from __future__ import annotations

import json
import math
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import config
from src.ltr_feature_extractor import extract_group_features
from src.query_router import normalize_query_route
from src.text_parser import parser as text_parser


def _cgr_enabled() -> bool:
    return bool(getattr(config, "CONSTRAINED_GATED_RANKER_ENABLED", False))


SEMANTIC_MODEL_FEATURES = (
    "hybrid_zscore",
    "rerank_score",
    "semantic_rerank_zscore",
    "spec_rerank_zscore",
    "name_bonus",
    "query_token_in_candidate_ratio",
    "candidate_token_in_query_ratio",
    "canonical_term_coverage",
    "core_term_bigram_jaccard",
    "inverse_hybrid_rank",
    "inverse_rrf_rank",
)

STRUCTURAL_MODEL_FEATURES = (
    "param_score",
    "logic_score",
    "feature_alignment_score",
    "context_alignment_score",
    "param_main_rel_score",
    "param_main_exact",
    "param_material_match",
    "candidate_specificity_score",
    "structural_anchor_confidence",
    "upward_nearest",
    "family_match",
    "entity_match",
    "material_match",
    "install_method_match",
    "system_match",
)

GATE_MODEL_FEATURES = (
    "family_confidence",
    "query_param_coverage",
    "group_ambiguity_score",
    "candidate_count",
    "has_material",
    "has_install_method",
    "route_installation_spec",
    "route_material",
    "route_semantic_description",
    "route_ambiguous_short",
)

ACCEPT_MODEL_FEATURES = (
    "p1",
    "p1_minus_p2",
    "p1_minus_p3",
    "candidate_count",
    "ambiguity",
    "hard_conflict_top1",
    "tier_penalty_top1",
    "generic_penalty_top1",
    "query_param_coverage",
    "family_confidence",
    "has_material",
    "has_install_method",
    "route_installation_spec",
    "route_material",
    "route_semantic_description",
    "route_ambiguous_short",
)


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


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, _safe_float(value, 0.0)))


def _sigmoid(value: float) -> float:
    value = max(min(_safe_float(value, 0.0), 40.0), -40.0)
    return 1.0 / (1.0 + math.exp(-value))


def _softmax(values: list[float], temperature: float) -> list[float]:
    if not values:
        return []
    temperature = max(_safe_float(temperature, 1.0), 0.2)
    finite_values = [value for value in values if math.isfinite(value)]
    if not finite_values:
        return [0.0 for _ in values]
    max_value = max(finite_values)
    exps = []
    total = 0.0
    for value in values:
        if not math.isfinite(value):
            exps.append(0.0)
            continue
        scaled = math.exp((value - max_value) / temperature)
        exps.append(scaled)
        total += scaled
    if total <= 0:
        return [0.0 for _ in values]
    return [value / total for value in exps]


def _group_minmax(values: list[float]) -> list[float]:
    if not values:
        return []
    low = min(values)
    high = max(values)
    if abs(high - low) <= 1e-9:
        return [0.5 for _ in values]
    return [(value - low) / (high - low) for value in values]


@lru_cache(maxsize=1)
def _load_cgr_model(path_str: str) -> dict:
    if not path_str:
        return {}
    path = Path(path_str)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _maybe_get_cgr_model() -> dict:
    path = getattr(config, "CGR_MODEL_PATH", None)
    if path is None:
        return {}
    return _load_cgr_model(str(path))


def _dot_with_linear_model(features: dict[str, float], model_section: dict) -> float:
    if not isinstance(model_section, dict):
        return 0.0
    names = list(model_section.get("feature_names") or [])
    weights = list(model_section.get("weights") or [])
    means = list(model_section.get("means") or [])
    scales = list(model_section.get("scales") or [])
    bias = _safe_float(model_section.get("bias"), 0.0)
    total = bias
    for index, name in enumerate(names):
        value = _safe_float(features.get(name), 0.0)
        mean = _safe_float(means[index], 0.0) if index < len(means) else 0.0
        scale = _safe_float(scales[index], 1.0) if index < len(scales) else 1.0
        weight = _safe_float(weights[index], 0.0) if index < len(weights) else 0.0
        if abs(scale) <= 1e-9:
            scale = 1.0
        total += ((value - mean) / scale) * weight
    return total


def _resolve_query_features(item: dict, context: dict | None) -> dict:
    item = item or {}
    context = context or {}
    explicit = item.get("canonical_features")
    if isinstance(explicit, dict) and explicit:
        return dict(explicit)
    explicit = context.get("canonical_features")
    if isinstance(explicit, dict) and explicit:
        return dict(explicit)
    query_text = " ".join(
        part for part in (item.get("name", ""), item.get("description", "")) if part
    ).strip()
    if not query_text:
        return {}
    try:
        return text_parser.parse_canonical(query_text, params=item.get("params") or {})
    except Exception:
        return {}


def _candidate_features(candidate: dict) -> dict:
    if not isinstance(candidate, dict):
        return {}
    return candidate.get("candidate_canonical_features") or candidate.get("canonical_features") or {}


def _route_adjustment(route: str) -> float:
    if route == "installation_spec":
        return -0.65
    if route == "material":
        return -0.25
    if route == "semantic_description":
        return 0.35
    if route == "ambiguous_short":
        return 0.50
    return 0.0


def _build_query_summary(
    item: dict,
    candidates: list[dict],
    feature_rows: list[dict],
    context: dict | None,
) -> dict:
    context = context or {}
    route = normalize_query_route(
        context.get("route_profile") or item.get("query_route") or context.get("query_route")
    )
    row0 = feature_rows[0] if feature_rows else {}
    query_features = _resolve_query_features(item, context)
    family_signal = max((_safe_float(c.get("family_gate_score"), 0.0) for c in candidates), default=0.0)
    exact_anchor_signal = max(
        (_safe_float(c.get("feature_alignment_exact_anchor_count"), 0.0) for c in candidates),
        default=0.0,
    )
    family_confidence = _clip01(0.35 + family_signal * 0.22 + exact_anchor_signal * 0.10)
    return {
        "route": route,
        "province": str(
            item.get("_resolved_province")
            or item.get("province")
            or context.get("province")
            or ""
        ),
        "query_param_coverage": _clip01(row0.get("query_param_coverage", 0.0)),
        "group_ambiguity_score": _clip01(row0.get("group_ambiguity_score", 0.5)),
        "candidate_count": len(candidates),
        "family_confidence": family_confidence,
        "has_material": int(bool(str(query_features.get("material") or "").strip())),
        "has_install_method": int(bool(str(query_features.get("install_method") or "").strip())),
        "search_books": list(context.get("search_books") or item.get("search_books") or []),
        "query_features": query_features,
    }


def _is_fatal_hard_conflict(candidate: dict) -> bool:
    return any(
        bool(candidate.get(flag))
        for flag in (
            "family_gate_hard_conflict",
            "feature_alignment_hard_conflict",
            "logic_hard_conflict",
        )
    )


def _compute_semantic_raw(candidate: dict, row: dict) -> float:
    hybrid_term = _sigmoid(_safe_float(row.get("hybrid_zscore"), 0.0) * 0.8)
    semantic_term = _sigmoid(_safe_float(row.get("semantic_rerank_zscore"), 0.0) * 0.8)
    spec_term = _sigmoid(_safe_float(row.get("spec_rerank_zscore"), 0.0) * 0.8)
    rank_term = (
        1.0 / max(_safe_int(row.get("hybrid_rank"), 1), 1)
        + 1.0 / max(_safe_int(row.get("rrf_rank"), 1), 1)
    ) / 2.0
    return (
        hybrid_term * 0.24
        + _clip01(candidate.get("rerank_score", candidate.get("hybrid_score", 0.0))) * 0.20
        + semantic_term * 0.16
        + spec_term * 0.10
        + _clip01(candidate.get("name_bonus")) * 0.08
        + _clip01(row.get("query_token_in_candidate_ratio", 0.0)) * 0.08
        + _clip01(row.get("candidate_token_in_query_ratio", 0.0)) * 0.05
        + _clip01(row.get("canonical_term_coverage", 0.0)) * 0.05
        + _clip01(row.get("core_term_bigram_jaccard", 0.0)) * 0.04
        + min(rank_term, 1.0) * 0.10
    )


def _build_semantic_model_features(candidate: dict, row: dict) -> dict[str, float]:
    return {
        "hybrid_zscore": _safe_float(row.get("hybrid_zscore"), 0.0),
        "rerank_score": _clip01(candidate.get("rerank_score", candidate.get("hybrid_score", 0.0))),
        "semantic_rerank_zscore": _safe_float(row.get("semantic_rerank_zscore"), 0.0),
        "spec_rerank_zscore": _safe_float(row.get("spec_rerank_zscore"), 0.0),
        "name_bonus": _clip01(candidate.get("name_bonus")),
        "query_token_in_candidate_ratio": _clip01(row.get("query_token_in_candidate_ratio", 0.0)),
        "candidate_token_in_query_ratio": _clip01(row.get("candidate_token_in_query_ratio", 0.0)),
        "canonical_term_coverage": _clip01(row.get("canonical_term_coverage", 0.0)),
        "core_term_bigram_jaccard": _clip01(row.get("core_term_bigram_jaccard", 0.0)),
        "inverse_hybrid_rank": 1.0 / max(_safe_int(row.get("hybrid_rank"), 1), 1),
        "inverse_rrf_rank": 1.0 / max(_safe_int(row.get("rrf_rank"), 1), 1),
    }


def _field_confidence_mean(row: dict, names: tuple[str, ...]) -> float:
    values = []
    for name in names:
        confidence = row.get(f"{name}_confidence")
        if confidence is not None:
            values.append(_clip01(confidence))
    if not values:
        return 0.0
    return sum(values) / len(values)


def _compute_structural_raw(candidate: dict, row: dict) -> float:
    ltr_param = candidate.get("_ltr_param") or {}
    rel_dist = _safe_float(ltr_param.get("param_main_rel_dist"), 1.0)
    rel_score = _clip01(1.0 - rel_dist)
    exact_match = _clip01(ltr_param.get("param_main_exact", 0.0))
    material_match = _clip01(max(_safe_float(ltr_param.get("param_material_match"), 0.0), 0.0))
    structural_anchor = _field_confidence_mean(
        row,
        ("family", "entity", "material", "install_method", "connection", "system"),
    )
    upward_nearest = max(
        (
            _safe_float(value, 0.0)
            for key, value in row.items()
            if key.endswith("_is_upward_nearest")
        ),
        default=0.0,
    )
    return (
        _clip01(candidate.get("param_score")) * 0.24
        + _clip01(candidate.get("logic_score", 0.5)) * 0.20
        + _clip01(candidate.get("feature_alignment_score", 0.5)) * 0.16
        + _clip01(candidate.get("context_alignment_score", 0.5)) * 0.10
        + rel_score * 0.10
        + exact_match * 0.08
        + material_match * 0.04
        + _clip01(row.get("candidate_specificity_score", 0.5)) * 0.04
        + structural_anchor * 0.02
        + upward_nearest * 0.02
    )


def _build_structural_model_features(candidate: dict, row: dict) -> dict[str, float]:
    ltr_param = candidate.get("_ltr_param") or {}
    rel_dist = _safe_float(ltr_param.get("param_main_rel_dist"), 1.0)
    upward_nearest = max(
        (
            _safe_float(value, 0.0)
            for key, value in row.items()
            if key.endswith("_is_upward_nearest")
        ),
        default=0.0,
    )
    return {
        "param_score": _clip01(candidate.get("param_score")),
        "logic_score": _clip01(candidate.get("logic_score", 0.5)),
        "feature_alignment_score": _clip01(candidate.get("feature_alignment_score", 0.5)),
        "context_alignment_score": _clip01(candidate.get("context_alignment_score", 0.5)),
        "param_main_rel_score": _clip01(1.0 - rel_dist),
        "param_main_exact": _clip01(ltr_param.get("param_main_exact", 0.0)),
        "param_material_match": _clip01(max(_safe_float(ltr_param.get("param_material_match"), 0.0), 0.0)),
        "candidate_specificity_score": _clip01(row.get("candidate_specificity_score", 0.5)),
        "structural_anchor_confidence": _field_confidence_mean(
            row,
            ("family", "entity", "material", "install_method", "connection", "system"),
        ),
        "upward_nearest": upward_nearest,
        "family_match": _safe_float(row.get("family_match"), 0.0),
        "entity_match": _safe_float(row.get("entity_match"), 0.0),
        "material_match": _safe_float(row.get("material_match"), 0.0),
        "install_method_match": _safe_float(row.get("install_method_match"), 0.0),
        "system_match": _safe_float(row.get("system_match"), 0.0),
    }


def _compute_gate(query_summary: dict) -> float:
    candidate_count = max(_safe_int(query_summary.get("candidate_count"), 0), 1)
    logit = (
        0.40
        - 1.60 * _safe_float(query_summary.get("query_param_coverage"), 0.0)
        + 1.25 * _safe_float(query_summary.get("group_ambiguity_score"), 0.5)
        - 0.55 * _safe_float(query_summary.get("family_confidence"), 0.0)
        - 0.30 * _safe_int(query_summary.get("has_material"), 0)
        - 0.25 * _safe_int(query_summary.get("has_install_method"), 0)
        + _route_adjustment(str(query_summary.get("route") or ""))
        + 0.03 * min(candidate_count, 20)
    )
    return _sigmoid(logit)


def _build_gate_model_features(query_summary: dict) -> dict[str, float]:
    route = str(query_summary.get("route") or "")
    return {
        "family_confidence": _safe_float(query_summary.get("family_confidence"), 0.0),
        "query_param_coverage": _safe_float(query_summary.get("query_param_coverage"), 0.0),
        "group_ambiguity_score": _safe_float(query_summary.get("group_ambiguity_score"), 0.5),
        "candidate_count": float(max(_safe_int(query_summary.get("candidate_count"), 0), 0)),
        "has_material": float(_safe_int(query_summary.get("has_material"), 0)),
        "has_install_method": float(_safe_int(query_summary.get("has_install_method"), 0)),
        "route_installation_spec": float(route == "installation_spec"),
        "route_material": float(route == "material"),
        "route_semantic_description": float(route == "semantic_description"),
        "route_ambiguous_short": float(route == "ambiguous_short"),
    }


def _compute_tier_penalty(candidate: dict, row: dict) -> float:
    ltr_param = candidate.get("_ltr_param") or {}
    rel_dist = min(_safe_float(ltr_param.get("param_main_rel_dist"), 1.0), 1.5)
    direction = _safe_float(ltr_param.get("param_main_direction"), 0.0)
    exact_match = bool(_safe_int(ltr_param.get("param_main_exact"), 0))
    tier_deltas = [
        abs(_safe_float(value, 0.0))
        for key, value in row.items()
        if key.endswith("_tier_delta") and _safe_float(value, 0.0) > 0
    ]
    upward_nearest = any(
        bool(_safe_int(value, 0))
        for key, value in row.items()
        if key.endswith("_is_upward_nearest")
    )
    penalty = rel_dist
    if tier_deltas:
        penalty += min(min(tier_deltas), 3.0) * 0.08
    if direction < 0 and not exact_match:
        penalty += 0.35
    if upward_nearest:
        penalty -= 0.12
    if exact_match:
        penalty -= 0.15
    return max(penalty, 0.0)


def _compute_generic_penalty(query_summary: dict, row: dict) -> float:
    specificity = _clip01(row.get("candidate_specificity_score", 0.5))
    genericity_index = _safe_float(row.get("candidate_genericity_index"), -1.0)
    if genericity_index >= 0:
        genericity = _clip01(genericity_index / 3.0)
    else:
        genericity = 1.0 - specificity
    spec_level = _sigmoid(
        -0.30
        + 2.00 * _safe_float(query_summary.get("query_param_coverage"), 0.0)
        + 0.55 * _safe_int(query_summary.get("has_material"), 0)
        + 0.55 * _safe_int(query_summary.get("has_install_method"), 0)
        - 1.10 * _safe_float(query_summary.get("group_ambiguity_score"), 0.5)
    )
    return spec_level * genericity


def _compute_soft_conflict_penalty(candidate: dict, row: dict) -> float:
    penalty = 0.0
    family_gate = _safe_float(candidate.get("family_gate_score"), 0.0)
    if family_gate < 0:
        penalty += min(abs(family_gate) / 2.0, 0.25)
    if not candidate.get("param_match", True):
        penalty += 0.18
    if candidate.get("context_alignment_hard_conflict"):
        penalty += 0.22
    for name in ("family", "entity", "material", "install_method", "connection", "system"):
        penalty += 0.04 * _safe_int(row.get(f"{name}_conflict"), 0)
    return min(penalty, 0.45)


def _compute_prior(candidate: dict, row: dict, query_summary: dict) -> float:
    prior = 0.0
    quota_id = str(candidate.get("quota_id", "") or "")
    search_books = [str(book).strip() for book in query_summary.get("search_books") or [] if str(book).strip()]
    if search_books and any(quota_id.startswith(book) for book in search_books):
        prior += 0.03
    if candidate.get("logic_exact_primary_match"):
        prior += 0.02
    prior += _clip01(candidate.get("plugin_score")) * 0.03
    prior += 0.01 * _safe_int(row.get("family_match"), 0)
    prior += 0.01 * _safe_int(row.get("entity_match"), 0)
    prior += 0.01 * _safe_int(row.get("system_match"), 0)
    return prior


def _attach_accept_head(
    ranked: list[dict],
    query_summary: dict,
    meta: dict,
    model_data: dict | None = None,
) -> None:
    if not ranked:
        return
    probabilities = [max(_safe_float(candidate.get("cgr_probability"), 0.0), 0.0) for candidate in ranked]
    p1 = probabilities[0] if probabilities else 0.0
    p2 = probabilities[1] if len(probabilities) > 1 else 0.0
    p3 = probabilities[2] if len(probabilities) > 2 else 0.0
    top = ranked[0]
    gap12 = p1 - p2
    gap13 = p1 - p3
    accept_features = _build_accept_model_features(top, query_summary, gap12, gap13)
    accept_model = (model_data or {}).get("accept_head") or {}
    if accept_model:
        accept_logit = _dot_with_linear_model(accept_features, accept_model)
    else:
        accept_logit = (
            -1.00
            + 4.20 * (p1 - 0.40)
            + 5.80 * gap12
            + 2.80 * gap13
            + 0.90 * _safe_float(query_summary.get("query_param_coverage"), 0.0)
            + 0.45 * _safe_float(query_summary.get("family_confidence"), 0.0)
            - 1.40 * _safe_float(query_summary.get("group_ambiguity_score"), 0.5)
            - 1.60 * _safe_float(top.get("cgr_tier_penalty"), 0.0)
            - 0.80 * _safe_float(top.get("cgr_generic_penalty"), 0.0)
            - 1.60 * int(_is_fatal_hard_conflict(top))
            + 0.15 * _safe_int(query_summary.get("has_material"), 0)
            + 0.15 * _safe_int(query_summary.get("has_install_method"), 0)
        )
        route = str(query_summary.get("route") or "")
        if route == "installation_spec":
            accept_logit += 0.20
        elif route == "ambiguous_short":
            accept_logit -= 0.20
    accept_score = _sigmoid(accept_logit)
    model_accept_threshold = _safe_float((model_data or {}).get("accept_threshold"), config.CGR_ACCEPT_THRESHOLD)
    model_min_top1_prob = _safe_float((model_data or {}).get("min_top1_prob"), config.CGR_MIN_TOP1_PROB)
    accept = (
        accept_score >= model_accept_threshold
        and p1 >= model_min_top1_prob
        and not _is_fatal_hard_conflict(top)
        and bool(top.get("cgr_feasible", True))
    )
    for candidate in ranked:
        candidate["cgr_accept_score"] = accept_score
        candidate["cgr_accept"] = accept
        candidate["cgr_prob_gap_top2"] = gap12
        candidate["cgr_prob_gap_top3"] = gap13
        candidate["cgr_query_param_coverage"] = _safe_float(
            query_summary.get("query_param_coverage"), 0.0
        )
        candidate["cgr_group_ambiguity_score"] = _safe_float(
            query_summary.get("group_ambiguity_score"), 0.5
        )
        candidate["cgr_family_confidence"] = _safe_float(
            query_summary.get("family_confidence"), 0.0
        )
    meta["accept_score"] = accept_score
    meta["accept"] = accept
    meta["prob_gap_top2"] = gap12
    meta["prob_gap_top3"] = gap13


def _build_accept_model_features(
    top: dict,
    query_summary: dict,
    gap12: float,
    gap13: float,
) -> dict[str, float]:
    route = str(query_summary.get("route") or "")
    return {
        "p1": _safe_float(top.get("cgr_probability"), 0.0),
        "p1_minus_p2": gap12,
        "p1_minus_p3": gap13,
        "candidate_count": float(max(_safe_int(query_summary.get("candidate_count"), 0), 0)),
        "ambiguity": _safe_float(query_summary.get("group_ambiguity_score"), 0.5),
        "hard_conflict_top1": float(int(_is_fatal_hard_conflict(top))),
        "tier_penalty_top1": _safe_float(top.get("cgr_tier_penalty"), 0.0),
        "generic_penalty_top1": _safe_float(top.get("cgr_generic_penalty"), 0.0),
        "query_param_coverage": _safe_float(query_summary.get("query_param_coverage"), 0.0),
        "family_confidence": _safe_float(query_summary.get("family_confidence"), 0.0),
        "has_material": float(_safe_int(query_summary.get("has_material"), 0)),
        "has_install_method": float(_safe_int(query_summary.get("has_install_method"), 0)),
        "route_installation_spec": float(route == "installation_spec"),
        "route_material": float(route == "material"),
        "route_semantic_description": float(route == "semantic_description"),
        "route_ambiguous_short": float(route == "ambiguous_short"),
    }


def _score_constrained_gated_ranker(
    item: dict,
    candidates: list[dict],
    context: dict | None = None,
) -> tuple[list[dict], dict, dict]:
    context = context or {}
    meta = {
        "enabled": _cgr_enabled(),
        "applied": False,
        "empty_feasible_set": False,
        "gate": 0.5,
        "accept": False,
        "accept_score": 0.0,
        "top_probability": 0.0,
        "top_quota_id": "",
        "query_summary": {},
    }
    if not candidates:
        return candidates, meta, {}

    model_data = _maybe_get_cgr_model()
    feature_rows = extract_group_features(item, candidates, context)
    query_summary = _build_query_summary(item, candidates, feature_rows, context)
    gate_model = model_data.get("gate") or {}
    if gate_model:
        gate = _sigmoid(_dot_with_linear_model(_build_gate_model_features(query_summary), gate_model))
    else:
        gate = _compute_gate(query_summary)
    meta["gate"] = gate
    meta["query_summary"] = dict(query_summary)

    semantic_model = model_data.get("semantic_expert") or {}
    structural_model = model_data.get("structural_expert") or {}
    semantic_raw = [
        _dot_with_linear_model(_build_semantic_model_features(candidate, row), semantic_model)
        if semantic_model else _compute_semantic_raw(candidate, row)
        for candidate, row in zip(candidates, feature_rows)
    ]
    structural_raw = [
        _dot_with_linear_model(_build_structural_model_features(candidate, row), structural_model)
        if structural_model else _compute_structural_raw(candidate, row)
        for candidate, row in zip(candidates, feature_rows)
    ]
    semantic_scores = _group_minmax(semantic_raw)
    structural_scores = _group_minmax(structural_raw)

    combined_scores: list[float] = []
    for index, (candidate, row, semantic_score, structural_score) in enumerate(zip(
        candidates, feature_rows, semantic_scores, structural_scores
    )):
        feasible = not _is_fatal_hard_conflict(candidate)
        tier_penalty = _compute_tier_penalty(candidate, row)
        generic_penalty = _compute_generic_penalty(query_summary, row)
        soft_conflict = _compute_soft_conflict_penalty(candidate, row)
        prior = _compute_prior(candidate, row, query_summary)
        score = (
            gate * semantic_score
            + (1.0 - gate) * structural_score
            + prior
            - 0.45 * tier_penalty
            - 0.20 * generic_penalty
            - 0.25 * soft_conflict
        )
        if not feasible:
            score = float("-inf")
        candidate["cgr_sem_score"] = semantic_score
        candidate["cgr_str_score"] = structural_score
        candidate["cgr_gate"] = gate
        candidate["cgr_prior_score"] = prior
        candidate["cgr_tier_penalty"] = tier_penalty
        candidate["cgr_generic_penalty"] = generic_penalty
        candidate["cgr_soft_conflict_penalty"] = soft_conflict
        candidate["cgr_feasible"] = feasible
        candidate["cgr_score"] = score if math.isfinite(score) else -1e9
        candidate["cgr_candidate_index"] = index
        candidate["cgr_feature_row"] = dict(row)
        candidate["rank_score"] = candidate["cgr_score"]
        candidate["_rank_score_source"] = "cgr"
        combined_scores.append(score)

    feasible_scores = [score for score in combined_scores if math.isfinite(score)]
    if not feasible_scores:
        meta["empty_feasible_set"] = True
        ranked = list(candidates)
        ranked.sort(key=lambda candidate: candidate.get("cgr_score", -1e9), reverse=True)
        _attach_accept_head(ranked, query_summary, meta, model_data)
        return ranked, meta, query_summary

    temperature = _safe_float(model_data.get("temperature"), config.CGR_TEMPERATURE)
    probabilities = _softmax(combined_scores, temperature)
    for candidate, probability in zip(candidates, probabilities):
        candidate["cgr_probability"] = probability if candidate.get("cgr_feasible", True) else 0.0

    ranked = list(candidates)
    ranked.sort(
        key=lambda candidate: (
            _safe_float(candidate.get("cgr_probability"), 0.0),
            _safe_float(candidate.get("cgr_score"), -1e9),
        ),
        reverse=True,
    )
    _attach_accept_head(ranked, query_summary, meta, model_data)
    meta["applied"] = True
    meta["top_probability"] = _safe_float(ranked[0].get("cgr_probability"), 0.0) if ranked else 0.0
    meta["top_quota_id"] = str(ranked[0].get("quota_id", "") or "") if ranked else ""
    return ranked, meta, query_summary


def apply_constrained_gated_ranker(
    item: dict,
    candidates: list[dict],
    context: dict | None = None,
) -> tuple[list[dict], dict]:
    ranked, meta, _ = _score_constrained_gated_ranker(item, candidates, context)
    return ranked, meta


def _clone_candidate(candidate: dict) -> dict:
    if not isinstance(candidate, dict):
        return {}
    clone = dict(candidate)
    for key in (
        "candidate_canonical_features",
        "canonical_features",
        "ltr_feature_snapshot",
        "_ltr_param",
        "reasoning",
    ):
        if isinstance(clone.get(key), dict):
            clone[key] = deepcopy(clone[key])
    return clone


def _export_candidate_payload(candidate: dict, oracle_quota_ids: set[str], rank: int) -> dict:
    payload = {
        "quota_id": str(candidate.get("quota_id", "") or ""),
        "name": str(candidate.get("name", "") or ""),
        "rank": rank,
        "is_oracle": int(str(candidate.get("quota_id", "") or "") in oracle_quota_ids),
        "cgr_probability": _safe_float(candidate.get("cgr_probability"), 0.0),
        "cgr_score": _safe_float(candidate.get("cgr_score"), -1e9),
        "cgr_sem_score": _safe_float(candidate.get("cgr_sem_score"), 0.0),
        "cgr_str_score": _safe_float(candidate.get("cgr_str_score"), 0.0),
        "cgr_prior_score": _safe_float(candidate.get("cgr_prior_score"), 0.0),
        "cgr_tier_penalty": _safe_float(candidate.get("cgr_tier_penalty"), 0.0),
        "cgr_generic_penalty": _safe_float(candidate.get("cgr_generic_penalty"), 0.0),
        "cgr_soft_conflict_penalty": _safe_float(candidate.get("cgr_soft_conflict_penalty"), 0.0),
        "cgr_feasible": bool(candidate.get("cgr_feasible", True)),
        "param_match": bool(candidate.get("param_match", True)),
        "family_gate_hard_conflict": bool(candidate.get("family_gate_hard_conflict", False)),
        "feature_alignment_hard_conflict": bool(candidate.get("feature_alignment_hard_conflict", False)),
        "logic_hard_conflict": bool(candidate.get("logic_hard_conflict", False)),
        "context_alignment_hard_conflict": bool(candidate.get("context_alignment_hard_conflict", False)),
        "bm25_score": _safe_float(candidate.get("bm25_score"), 0.0),
        "vector_score": _safe_float(candidate.get("vector_score"), 0.0),
        "hybrid_score": _safe_float(candidate.get("hybrid_score"), 0.0),
        "rerank_score": _safe_float(candidate.get("rerank_score"), 0.0),
        "semantic_rerank_score": _safe_float(candidate.get("semantic_rerank_score"), 0.0),
        "spec_rerank_score": _safe_float(candidate.get("spec_rerank_score"), 0.0),
        "param_score": _safe_float(candidate.get("param_score"), 0.0),
        "logic_score": _safe_float(candidate.get("logic_score"), 0.0),
        "feature_alignment_score": _safe_float(candidate.get("feature_alignment_score"), 0.0),
        "context_alignment_score": _safe_float(candidate.get("context_alignment_score"), 0.0),
        "manual_structured_score": _safe_float(candidate.get("manual_structured_score"), 0.0),
        "name_bonus": _safe_float(candidate.get("name_bonus"), 0.0),
        "plugin_score": _safe_float(candidate.get("plugin_score"), 0.0),
        "param_tier": _safe_int(candidate.get("param_tier"), 0),
        "family_gate_score": _safe_float(candidate.get("family_gate_score"), 0.0),
        "candidate_canonical_features": dict(candidate.get("candidate_canonical_features") or {}),
        "ltr_feature_snapshot": dict(candidate.get("ltr_feature_snapshot") or {}),
        "_ltr_param": dict(candidate.get("_ltr_param") or {}),
        "group_features": dict(candidate.get("cgr_feature_row") or {}),
    }
    return payload


def build_constrained_ranker_training_sample(
    item: dict,
    candidates: list[dict],
    context: dict | None = None,
    *,
    oracle_quota_ids: list[str] | None = None,
    sample_id: str = "",
    split: str = "",
    metadata: dict | None = None,
) -> dict:
    cloned_candidates = [_clone_candidate(candidate) for candidate in candidates]
    ranked, meta, query_summary = _score_constrained_gated_ranker(item or {}, cloned_candidates, context)
    oracle_ids = {str(value).strip() for value in (oracle_quota_ids or []) if str(value).strip()}
    top = ranked[0] if ranked else {}
    top_quota_id = str(top.get("quota_id", "") or "")
    top_correct = bool(top_quota_id and top_quota_id in oracle_ids)
    accept_row = {
        "sample_id": sample_id,
        "split": split,
        "top1_quota_id": top_quota_id,
        "top1_name": str(top.get("name", "") or ""),
        "top1_correct": top_correct,
        "accept_label": int(top_correct),
        "accept_score": _safe_float(meta.get("accept_score"), 0.0),
        "accept": bool(meta.get("accept", False)),
        "p1": _safe_float(top.get("cgr_probability"), 0.0),
        "p1_minus_p2": _safe_float(meta.get("prob_gap_top2"), 0.0),
        "p1_minus_p3": _safe_float(meta.get("prob_gap_top3"), 0.0),
        "candidate_count": _safe_int(query_summary.get("candidate_count"), len(ranked)),
        "ambiguity": _safe_float(query_summary.get("group_ambiguity_score"), 0.5),
        "hard_conflict_top1": int(_is_fatal_hard_conflict(top)),
        "tier_penalty_top1": _safe_float(top.get("cgr_tier_penalty"), 0.0),
        "generic_penalty_top1": _safe_float(top.get("cgr_generic_penalty"), 0.0),
        "query_param_coverage": _safe_float(query_summary.get("query_param_coverage"), 0.0),
        "family_confidence": _safe_float(query_summary.get("family_confidence"), 0.0),
        "province": str(query_summary.get("province") or ""),
        "route": str(query_summary.get("route") or ""),
    }
    payload = {
        "sample_id": sample_id,
        "split": split,
        "item": {
            "name": str((item or {}).get("name", "") or ""),
            "description": str((item or {}).get("description", "") or ""),
            "specialty": str((item or {}).get("specialty", "") or ""),
            "province": str((item or {}).get("province", "") or ""),
        },
        "metadata": dict(metadata or {}),
        "oracle_quota_ids": sorted(oracle_ids),
        "query_summary": dict(query_summary),
        "gate": _safe_float(meta.get("gate"), 0.5),
        "top1_quota_id": top_quota_id,
        "top1_correct": top_correct,
        "accept": dict(accept_row),
        "candidates": [
            _export_candidate_payload(candidate, oracle_ids, rank)
            for rank, candidate in enumerate(ranked, start=1)
        ],
    }
    return payload


__all__ = [
    "SEMANTIC_MODEL_FEATURES",
    "STRUCTURAL_MODEL_FEATURES",
    "GATE_MODEL_FEATURES",
    "ACCEPT_MODEL_FEATURES",
    "apply_constrained_gated_ranker",
    "build_constrained_ranker_training_sample",
]
