from __future__ import annotations

from dataclasses import asdict, dataclass

from collections.abc import Mapping

from src.query_router import normalize_query_route
from src.text_parser import parser as text_parser


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class ArbitrationDecision:
    applied: bool
    route: str
    reason: str
    original_top_quota_id: str
    selected_quota_id: str
    structured_gap: float
    search_gap: float
    compared_count: int
    main_param_key: str = ""
    target_param_value: float = 0.0
    top_band_score: float = 0.0
    selected_band_score: float = 0.0

    def as_dict(self) -> dict:
        return asdict(self)


def _candidate_entity(candidate: dict) -> str:
    features = candidate.get("candidate_canonical_features") or candidate.get("canonical_features") or {}
    return str(features.get("entity") or "").strip()


def _candidate_family(candidate: dict) -> str:
    features = candidate.get("candidate_canonical_features") or candidate.get("canonical_features") or {}
    return str(features.get("family") or "").strip()


def _candidate_system(candidate: dict) -> str:
    features = candidate.get("candidate_canonical_features") or candidate.get("canonical_features") or {}
    return str(features.get("system") or "").strip()


def _search_score(candidate: dict) -> float:
    return (
        _safe_float(candidate.get("rerank_score", candidate.get("hybrid_score", 0.0))) * 0.65
        + _safe_float(candidate.get("name_bonus")) * 0.20
        + _safe_float(candidate.get("param_score")) * 0.15
    )


def _structured_score(candidate: dict) -> float:
    score = (
        _safe_float(candidate.get("param_score"), 0.0) * 0.34
        + _safe_float(candidate.get("logic_score"), 0.5) * 0.28
        + _safe_float(candidate.get("feature_alignment_score"), 0.5) * 0.20
        + _safe_float(candidate.get("context_alignment_score"), 0.5) * 0.18
    )
    if candidate.get("logic_exact_primary_match"):
        score += 0.08
    if candidate.get("feature_alignment_hard_conflict") or candidate.get("logic_hard_conflict"):
        score -= 0.20
    if not candidate.get("param_match", True):
        score -= 0.12
    return score


_MAIN_PARAM_KEYS = (
    "dn",
    "cable_section",
    "cable_cores",
    "kw",
    "kva",
    "circuits",
    "port_count",
    "ampere",
    "weight_t",
    "perimeter",
    "half_perimeter",
    "large_side",
    "switch_gangs",
)


def _item_main_param(item: dict) -> tuple[str, float] | tuple[None, None]:
    item = item or {}
    params = dict(item.get("params") or {})
    canonical = item.get("canonical_features") or {}
    numeric_params = canonical.get("numeric_params") or {}
    text = " ".join(
        str(part or "")
        for part in (item.get("name"), item.get("description"))
        if part
    ).strip()
    terminal_like = any(keyword in text for keyword in ("终端头", "电缆头", "中间头"))
    key_order = list(_MAIN_PARAM_KEYS)
    if terminal_like:
        key_order = [
            "cable_cores",
            "cable_section",
            *[key for key in _MAIN_PARAM_KEYS if key not in {"cable_cores", "cable_section"}],
        ]

    for key in key_order:
        value = params.get(key)
        if value is None:
            value = numeric_params.get(key)
        if value is None:
            continue
        return key, float(value)

    if not text:
        return None, None
    parsed = text_parser.parse(text)
    for key in key_order:
        value = parsed.get(key)
        if value is None:
            continue
        return key, float(value)
    return None, None


def _candidate_param_value(candidate: dict, key: str) -> float | None:
    value = candidate.get(key)
    if value is not None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    parsed = text_parser.parse(str(candidate.get("name", "") or ""))
    value = parsed.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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


def _candidate_total_score(candidate: dict, *, main_param_key: str | None, target_value: float | None) -> tuple[float, float, bool]:
    structured = _structured_score(candidate)
    if not main_param_key or target_value is None:
        return structured, 0.0, False
    band_score, exact_match = _band_score(
        target_value,
        _candidate_param_value(candidate, main_param_key),
    )
    # 主参数仅作为“精排增强”，不应压过主体检索分。
    return structured + band_score * 0.22, band_score, exact_match


def _share_anchor_family(top: dict, challenger: dict) -> bool:
    top_family = _candidate_family(top)
    challenger_family = _candidate_family(challenger)
    if top_family and challenger_family and top_family == challenger_family:
        return True

    top_entity = _candidate_entity(top)
    challenger_entity = _candidate_entity(challenger)
    if top_entity and challenger_entity and top_entity == challenger_entity:
        return True

    top_system = _candidate_system(top)
    challenger_system = _candidate_system(challenger)
    if (not top_entity or not challenger_entity) and top_system and challenger_system:
        return top_system == challenger_system

    top_prefix = str(top.get("quota_id", "")).strip().split("-")[:2]
    challenger_prefix = str(challenger.get("quota_id", "")).strip().split("-")[:2]
    return bool(top_prefix and challenger_prefix and top_prefix == challenger_prefix)


def _route_enabled(route: str) -> bool:
    return route in {"installation_spec", "spec_heavy", "ambiguous_short"}


def _route_ready(route_profile, route: str) -> bool:
    if route == "ambiguous_short":
        return True
    if not isinstance(route_profile, Mapping):
        return route == "spec_heavy"
    spec_signal_count = int(route_profile.get("spec_signal_count", 0) or 0)
    has_complex_install_spec = bool(route_profile.get("has_complex_install_spec", False))
    return spec_signal_count >= 2 or has_complex_install_spec


def arbitrate_candidates(item: dict, candidates: list[dict], route_profile=None) -> tuple[list[dict], dict]:
    route = normalize_query_route(route_profile or (item or {}).get("query_route"))
    main_param_key, target_param_value = _item_main_param(item or {})
    if not _route_enabled(route) or len(candidates or []) < 2:
        return candidates, ArbitrationDecision(
            applied=False,
            route=route,
            reason="route_disabled" if not _route_enabled(route) else "insufficient_candidates",
            original_top_quota_id="",
            selected_quota_id="",
            structured_gap=0.0,
            search_gap=0.0,
            compared_count=len(candidates or []),
            main_param_key=main_param_key or "",
            target_param_value=target_param_value or 0.0,
        ).as_dict()
    if not _route_ready(route_profile or (item or {}).get("query_route"), route):
        return candidates, ArbitrationDecision(
            applied=False,
            route=route,
            reason="route_not_ready",
            original_top_quota_id="",
            selected_quota_id="",
            structured_gap=0.0,
            search_gap=0.0,
            compared_count=len(candidates or []),
            main_param_key=main_param_key or "",
            target_param_value=target_param_value or 0.0,
        ).as_dict()

    top = candidates[0]
    original_top_id = str(top.get("quota_id", "")).strip()
    top_structured = _structured_score(top)
    top_search = _search_score(top)
    top_tier = int(top.get("param_tier", 1) or 1)
    top_total, top_band_score, top_band_exact = _candidate_total_score(
        top,
        main_param_key=main_param_key,
        target_value=target_param_value,
    )

    best_idx = -1
    best_score = top_total
    best_band_score = top_band_score
    for idx, candidate in enumerate(candidates[1:5], start=1):
        if not candidate.get("param_match", True):
            continue
        if int(candidate.get("param_tier", 1) or 1) < top_tier:
            continue
        if not (_share_anchor_family(top, candidate) or candidate.get("logic_exact_primary_match")):
            continue
        total_score, band_score, _ = _candidate_total_score(
            candidate,
            main_param_key=main_param_key,
            target_value=target_param_value,
        )
        # 有明确主参数时，不接受明显更差的档位候选。
        if main_param_key and band_score + 0.12 < top_band_score:
            continue
        if total_score > best_score:
            best_idx = idx
            best_score = total_score
            best_band_score = band_score

    if best_idx <= 0:
        return candidates, ArbitrationDecision(
            applied=False,
            route=route,
            reason="no_better_structured_candidate",
            original_top_quota_id=original_top_id,
            selected_quota_id=original_top_id,
            structured_gap=0.0,
            search_gap=0.0,
            compared_count=min(len(candidates), 5),
            main_param_key=main_param_key or "",
            target_param_value=target_param_value or 0.0,
            top_band_score=top_band_score,
            selected_band_score=top_band_score,
        ).as_dict()

    challenger = candidates[best_idx]
    challenger_id = str(challenger.get("quota_id", "")).strip()
    challenger_structured = _structured_score(challenger)
    challenger_search = _search_score(challenger)
    challenger_total, challenger_band_score, challenger_band_exact = _candidate_total_score(
        challenger,
        main_param_key=main_param_key,
        target_value=target_param_value,
    )
    band_gap = challenger_band_score - top_band_score
    structured_gap = challenger_total - top_total
    search_gap = top_search - challenger_search

    min_structured_gap = 0.12 if route == "installation_spec" else 0.14
    max_search_gap = 0.12 if route == "installation_spec" else 0.08
    if challenger.get("logic_exact_primary_match") and not top.get("logic_exact_primary_match"):
        min_structured_gap = min(min_structured_gap, 0.06)
        max_search_gap = max(max_search_gap, 0.18)
    if main_param_key:
        if challenger_band_exact and not top_band_exact:
            min_structured_gap = min(min_structured_gap, 0.05)
            max_search_gap = max(max_search_gap, 0.16)
        elif band_gap >= 0.22:
            min_structured_gap = min(min_structured_gap, 0.08)
        elif band_gap < 0.12:
            min_structured_gap = max(min_structured_gap, 0.14)

    if structured_gap < min_structured_gap:
        return candidates, ArbitrationDecision(
            applied=False,
            route=route,
            reason="structured_gap_too_small",
            original_top_quota_id=original_top_id,
            selected_quota_id=original_top_id,
            structured_gap=structured_gap,
            search_gap=search_gap,
            compared_count=min(len(candidates), 5),
            main_param_key=main_param_key or "",
            target_param_value=target_param_value or 0.0,
            top_band_score=top_band_score,
            selected_band_score=challenger_band_score,
        ).as_dict()

    if search_gap > max_search_gap:
        return candidates, ArbitrationDecision(
            applied=False,
            route=route,
            reason="search_gap_too_large",
            original_top_quota_id=original_top_id,
            selected_quota_id=original_top_id,
            structured_gap=structured_gap,
            search_gap=search_gap,
            compared_count=min(len(candidates), 5),
            main_param_key=main_param_key or "",
            target_param_value=target_param_value or 0.0,
            top_band_score=top_band_score,
            selected_band_score=challenger_band_score,
        ).as_dict()

    reordered = list(candidates)
    reordered.insert(0, reordered.pop(best_idx))
    return reordered, ArbitrationDecision(
        applied=True,
        route=route,
        reason="structured_candidate_swap",
        original_top_quota_id=original_top_id,
        selected_quota_id=challenger_id,
        structured_gap=structured_gap,
        search_gap=search_gap,
        compared_count=min(len(candidates), 5),
        main_param_key=main_param_key or "",
        target_param_value=target_param_value or 0.0,
        top_band_score=top_band_score,
        selected_band_score=best_band_score,
    ).as_dict()
