from __future__ import annotations

from dataclasses import asdict, dataclass

from collections.abc import Mapping

from src.candidate_scoring import (
    compute_candidate_search_score,
    compute_candidate_structured_score,
    compute_candidate_total_score,
)
from src.query_router import normalize_query_route
from src.text_parser import parser as text_parser
from src.utils import safe_float


@dataclass(frozen=True)
class ArbitrationDecision:
    applied: bool
    advisory_applied: bool
    reorder_enabled: bool
    route: str
    reason: str
    original_top_quota_id: str
    selected_quota_id: str
    recommended_quota_id: str
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
    return compute_candidate_search_score(candidate, include_plugin=False)


def _structured_score(candidate: dict) -> float:
    return compute_candidate_structured_score(candidate, include_plugin=False)


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
_ARBITER_REORDER_ENABLED = False


def _reset_arbiter_signals(candidates: list[dict]) -> None:
    for candidate in candidates or []:
        candidate["arbiter_signals"] = []
        candidate["arbiter_recommended"] = False


def _record_arbiter_signal(candidates: list[dict], decision: ArbitrationDecision) -> None:
    if not candidates or not decision.advisory_applied:
        return

    top1 = candidates[0]
    recommended_id = str(decision.recommended_quota_id or "").strip()
    if not recommended_id:
        return

    winner = next(
        (candidate for candidate in candidates if str(candidate.get("quota_id", "")).strip() == recommended_id),
        None,
    )
    if winner is None or winner is top1:
        return

    signal = {
        "reason": decision.reason,
        "route": decision.route,
        "original_top_quota_id": decision.original_top_quota_id,
        "recommended_quota_id": decision.recommended_quota_id,
        "structured_gap": decision.structured_gap,
        "search_gap": decision.search_gap,
        "main_param_key": decision.main_param_key,
        "target_param_value": decision.target_param_value,
        "top_band_score": decision.top_band_score,
        "selected_band_score": decision.selected_band_score,
        "applied": decision.applied,
        "advisory_applied": decision.advisory_applied,
        "reorder_enabled": decision.reorder_enabled,
    }
    winner["arbiter_recommended"] = True
    winner.setdefault("arbiter_signals", []).append({
        **signal,
        "role": "recommended",
    })
    top1.setdefault("arbiter_signals", []).append({
        **signal,
        "role": "current_top1",
    })


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


def _candidate_total_score(candidate: dict, *, main_param_key: str | None, target_value: float | None) -> tuple[float, float, bool]:
    return compute_candidate_total_score(
        candidate,
        main_param_key=main_param_key,
        target_value=target_value,
        candidate_value=_candidate_param_value(candidate, main_param_key) if main_param_key else None,
        include_plugin=False,
    )


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
    resolved_candidates = list(candidates or [])
    _reset_arbiter_signals(resolved_candidates)
    route = normalize_query_route(route_profile or (item or {}).get("query_route"))
    main_param_key, target_param_value = _item_main_param(item or {})
    if not _route_enabled(route) or len(resolved_candidates) < 2:
        return resolved_candidates, ArbitrationDecision(
            applied=False,
            advisory_applied=False,
            reorder_enabled=_ARBITER_REORDER_ENABLED,
            route=route,
            reason="route_disabled" if not _route_enabled(route) else "insufficient_candidates",
            original_top_quota_id="",
            selected_quota_id="",
            recommended_quota_id="",
            structured_gap=0.0,
            search_gap=0.0,
            compared_count=len(resolved_candidates),
            main_param_key=main_param_key or "",
            target_param_value=target_param_value or 0.0,
        ).as_dict()
    if not _route_ready(route_profile or (item or {}).get("query_route"), route):
        return resolved_candidates, ArbitrationDecision(
            applied=False,
            advisory_applied=False,
            reorder_enabled=_ARBITER_REORDER_ENABLED,
            route=route,
            reason="route_not_ready",
            original_top_quota_id="",
            selected_quota_id="",
            recommended_quota_id="",
            structured_gap=0.0,
            search_gap=0.0,
            compared_count=len(resolved_candidates),
            main_param_key=main_param_key or "",
            target_param_value=target_param_value or 0.0,
        ).as_dict()

    top = resolved_candidates[0]
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
    for idx, candidate in enumerate(resolved_candidates[1:5], start=1):
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
        return resolved_candidates, ArbitrationDecision(
            applied=False,
            advisory_applied=False,
            reorder_enabled=_ARBITER_REORDER_ENABLED,
            route=route,
            reason="no_better_structured_candidate",
            original_top_quota_id=original_top_id,
            selected_quota_id=original_top_id,
            recommended_quota_id="",
            structured_gap=0.0,
            search_gap=0.0,
            compared_count=min(len(resolved_candidates), 5),
            main_param_key=main_param_key or "",
            target_param_value=target_param_value or 0.0,
            top_band_score=top_band_score,
            selected_band_score=top_band_score,
        ).as_dict()

    challenger = resolved_candidates[best_idx]
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
        return resolved_candidates, ArbitrationDecision(
            applied=False,
            advisory_applied=False,
            reorder_enabled=_ARBITER_REORDER_ENABLED,
            route=route,
            reason="structured_gap_too_small",
            original_top_quota_id=original_top_id,
            selected_quota_id=original_top_id,
            recommended_quota_id="",
            structured_gap=structured_gap,
            search_gap=search_gap,
            compared_count=min(len(resolved_candidates), 5),
            main_param_key=main_param_key or "",
            target_param_value=target_param_value or 0.0,
            top_band_score=top_band_score,
            selected_band_score=challenger_band_score,
        ).as_dict()

    if search_gap > max_search_gap:
        return resolved_candidates, ArbitrationDecision(
            applied=False,
            advisory_applied=False,
            reorder_enabled=_ARBITER_REORDER_ENABLED,
            route=route,
            reason="search_gap_too_large",
            original_top_quota_id=original_top_id,
            selected_quota_id=original_top_id,
            recommended_quota_id="",
            structured_gap=structured_gap,
            search_gap=search_gap,
            compared_count=min(len(resolved_candidates), 5),
            main_param_key=main_param_key or "",
            target_param_value=target_param_value or 0.0,
            top_band_score=top_band_score,
            selected_band_score=challenger_band_score,
        ).as_dict()

    decision = ArbitrationDecision(
        applied=False,
        advisory_applied=True,
        reorder_enabled=_ARBITER_REORDER_ENABLED,
        route=route,
        reason="structured_candidate_swap_advisory",
        original_top_quota_id=original_top_id,
        selected_quota_id=original_top_id,
        recommended_quota_id=challenger_id,
        structured_gap=structured_gap,
        search_gap=search_gap,
        compared_count=min(len(resolved_candidates), 5),
        main_param_key=main_param_key or "",
        target_param_value=target_param_value or 0.0,
        top_band_score=top_band_score,
        selected_band_score=best_band_score,
    )
    _record_arbiter_signal(resolved_candidates, decision)
    return resolved_candidates, decision.as_dict()
