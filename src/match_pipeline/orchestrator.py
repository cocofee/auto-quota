# -*- coding: utf-8 -*-
"""Thin orchestration layer for the match pipeline package."""

from contextlib import nullcontext

from loguru import logger

import config

from src.ambiguity_gate import analyze_ambiguity
from src.candidate_scoring import (
    compute_candidate_rank_score,
    explain_candidate_rank_score,
    has_exact_experience_anchor,
    has_exact_universal_kb_anchor,
)
from src.context_builder import summarize_batch_context_for_trace
from src.explicit_equipment_family_pickers import _promote_explicit_distribution_box_candidate
from src.match_core import (
    _append_trace_step,
    _is_measure_item,
    _summarize_candidates_for_trace,
    calculate_confidence,
    infer_confidence_family_alignment,
    summarize_candidate_reasoning,
)
from src.performance_monitor import PerformanceMonitor
from src.policy_engine import PolicyEngine
from src.rule_validator import RuleValidator
from src.reason_taxonomy import merge_reason_tags

from .classifiers import _build_classification, _build_item_context, _prepare_rule_match
from .gates import (
    _append_item_review_rejection_trace,
    _build_input_gate_abstain_result,
    _evaluate_context_gate,
    _review_check_match_result,
)
from .pickers import _pick_category_safe_candidate
from .reasons import (
    DEFAULT_ALTERNATIVE_COUNT,
    _build_alternatives,
    _build_ranked_candidate_snapshots,
    _build_skip_measure_result,
    _set_result_reason,
)
from .reconcilers import (
    _apply_price_validation,
    _inject_rule_backup_candidate,
    _reconcile_search_and_experience,
)
from .scope import (
    _annotate_candidate_scope_signals,
    _apply_plugin_candidate_biases,
    _apply_plugin_route_gate,
    _merge_arbiter_annotations,
    _top_candidate_id,
)


def _api():
    import src.match_pipeline as api

    return api


def _merge_explicit_annotations(base_candidates: list[dict], explicit_candidates: list[dict]) -> list[dict]:
    ordered = [dict(candidate) for candidate in (base_candidates or [])]
    if not ordered or not explicit_candidates:
        return ordered

    explicit_by_quota_id: dict[str, dict] = {}
    for candidate in explicit_candidates:
        if not isinstance(candidate, dict):
            continue
        quota_id = str(candidate.get("quota_id", "") or "").strip()
        if quota_id:
            explicit_by_quota_id[quota_id] = candidate

    if not explicit_by_quota_id:
        return ordered

    for candidate in ordered:
        quota_id = str(candidate.get("quota_id", "") or "").strip()
        hinted = explicit_by_quota_id.get(quota_id)
        if not hinted:
            continue
        if "explicit_signals" in hinted:
            candidate["explicit_signals"] = list(hinted.get("explicit_signals") or [])
        if "explicit_recommended" in hinted:
            candidate["explicit_recommended"] = bool(hinted.get("explicit_recommended"))
    return ordered


def _init_ranking_meta() -> dict:
    return {
        "pre_ltr_top1_id": "",
        "post_ltr_top1_id": "",
        "post_cgr_top1_id": "",
        "post_arbiter_top1_id": "",
        "post_explicit_top1_id": "",
        "post_anchor_top1_id": "",
        "selected_top1_id": "",
        "legacy_top1_id": "",
        "post_final_top1_id": "",
        "final_changed_by": "",
        "candidate_count": 0,
        "ltr": {},
        "explicit_override": {},
        "unified_ranking_enabled": False,
        "unified_ranking_shadow_mode": False,
        "unified_ranking_mode": "disabled",
        "unified_ranking_executed": False,
        "unified_result_used": False,
        "unified_top1_id": "",
        "unified_top1_score": 0.0,
        "unified_top1_confidence": 0.0,
        "unified_top1_matches_selected": False,
        "unified_top1_matches_legacy": False,
        "legacy_top1_unified_score": None,
        "legacy_top1_unified_confidence": None,
        "unified_legacy_score_gap": None,
        "unified_ranking_diagnostics": {},
        "unified_ranking_error": "",
    }


def _resolve_unified_ranking_flags() -> dict:
    enabled = bool(getattr(config, "UNIFIED_RANKING_ENABLED", False))
    shadow_mode = bool(getattr(config, "UNIFIED_RANKING_SHADOW_MODE", False))
    if shadow_mode:
        mode = "shadow"
    elif enabled:
        mode = "enabled"
    else:
        mode = "disabled"
    return {
        "enabled": enabled,
        "shadow_mode": shadow_mode,
        "mode": mode,
    }


_UNIFIED_RANKING_PIPELINE = None


def _get_unified_ranking_pipeline():
    global _UNIFIED_RANKING_PIPELINE
    if _UNIFIED_RANKING_PIPELINE is None:
        from src.unified_ranking_pipeline import UnifiedRankingPipeline

        _UNIFIED_RANKING_PIPELINE = UnifiedRankingPipeline()
    return _UNIFIED_RANKING_PIPELINE


def _run_unified_ranking_shadow(item: dict, candidates: list[dict], *, top_k: int = 5) -> dict:
    pipeline = _get_unified_ranking_pipeline()
    return pipeline.rank_candidates(item, candidates, top_k=top_k)


def _build_unified_shadow_comparison(shadow_result: dict, ranking_meta: dict) -> dict:
    legacy_top1_id = str(ranking_meta.get("legacy_top1_id", "") or ranking_meta.get("selected_top1_id", "") or "")
    unified_top1_id = str(ranking_meta.get("unified_top1_id", "") or "")
    top1_score = float(shadow_result.get("top1_score", 0.0) or 0.0)
    legacy_candidate = None
    for candidate in list(shadow_result.get("candidates") or []):
        if str(candidate.get("quota_id", "") or "") == legacy_top1_id:
            legacy_candidate = candidate
            break

    legacy_score = None
    legacy_confidence = None
    score_gap = None
    if legacy_candidate:
        legacy_score = float(legacy_candidate.get("filtered_score", legacy_candidate.get("unified_score", 0.0)) or 0.0)
        legacy_confidence = float(legacy_candidate.get("confidence", 0.0) or 0.0)
        score_gap = top1_score - legacy_score

    return {
        "legacy_top1_id": legacy_top1_id,
        "unified_top1_id": unified_top1_id,
        "matches_legacy": bool(legacy_top1_id and unified_top1_id and legacy_top1_id == unified_top1_id),
        "legacy_candidate_present": legacy_candidate is not None,
        "legacy_top1_unified_score": legacy_score,
        "legacy_top1_unified_confidence": legacy_confidence,
        "score_gap": score_gap,
        "failure_reason": str(ranking_meta.get("unified_ranking_error", "") or ""),
    }


def _apply_unified_ranking_shadow(item: dict, candidates: list[dict], ranking_meta: dict) -> dict:
    if not candidates:
        return {}
    if str(ranking_meta.get("unified_ranking_mode") or "disabled") == "disabled":
        return {}
    top_k = len(candidates)
    try:
        shadow_result = _run_unified_ranking_shadow(item, candidates, top_k=top_k)
    except Exception as exc:  # pragma: no cover
        ranking_meta["unified_ranking_error"] = str(exc)
        ranking_meta["unified_ranking_executed"] = False
        return {}

    top_candidate = (shadow_result.get("candidates") or [None])[0]
    unified_top1_id = str((top_candidate or {}).get("quota_id", "") or "")
    ranking_meta["unified_ranking_executed"] = True
    ranking_meta["unified_result_used"] = False
    ranking_meta["unified_top1_id"] = unified_top1_id
    ranking_meta["unified_top1_score"] = float(shadow_result.get("top1_score", 0.0) or 0.0)
    ranking_meta["unified_top1_confidence"] = float(shadow_result.get("top1_confidence", 0.0) or 0.0)
    comparison = _build_unified_shadow_comparison(shadow_result, ranking_meta)
    ranking_meta["unified_top1_matches_selected"] = bool(
        unified_top1_id and unified_top1_id == str(ranking_meta.get("selected_top1_id", "") or "")
    )
    ranking_meta["unified_top1_matches_legacy"] = bool(comparison.get("matches_legacy"))
    ranking_meta["legacy_top1_unified_score"] = comparison.get("legacy_top1_unified_score")
    ranking_meta["legacy_top1_unified_confidence"] = comparison.get("legacy_top1_unified_confidence")
    ranking_meta["unified_legacy_score_gap"] = comparison.get("score_gap")
    ranking_meta["unified_ranking_diagnostics"] = dict(shadow_result.get("diagnostics") or {})
    ranking_meta["unified_ranking_error"] = ""
    return shadow_result


def _merge_unified_candidate(base_candidate: dict | None, unified_candidate: dict | None) -> dict | None:
    if not isinstance(unified_candidate, dict):
        return dict(base_candidate) if isinstance(base_candidate, dict) else None
    merged = dict(base_candidate or {})
    merged.update(dict(unified_candidate))
    return merged


def _apply_unified_candidate_order(base_candidates: list[dict], unified_candidates: list[dict]) -> list[dict]:
    base_by_quota_id = {
        str(candidate.get("quota_id", "") or "").strip(): candidate
        for candidate in (base_candidates or [])
        if str(candidate.get("quota_id", "") or "").strip()
    }
    ordered: list[dict] = []
    seen: set[str] = set()
    for unified_candidate in unified_candidates or []:
        quota_id = str(unified_candidate.get("quota_id", "") or "").strip()
        if not quota_id or quota_id in seen:
            continue
        ordered_candidate = _merge_unified_candidate(base_by_quota_id.get(quota_id), unified_candidate)
        if ordered_candidate:
            ordered.append(ordered_candidate)
            seen.add(quota_id)
    for candidate in base_candidates or []:
        quota_id = str(candidate.get("quota_id", "") or "").strip()
        if quota_id and quota_id in seen:
            continue
        ordered.append(dict(candidate))
    return ordered


def _format_unified_selection_explanation(unified_result: dict, candidate: dict | None) -> str:
    top_driver = str(((candidate or {}).get("explanation") or {}).get("top_driver") or "")
    score = float(unified_result.get("top1_score", 0.0) or 0.0)
    if top_driver:
        return f"unified_ranking: top_driver={top_driver}; filtered_score={score:.3f}"
    return f"unified_ranking: filtered_score={score:.3f}"


def _apply_unified_enabled_selection(item: dict,
                                     valid_candidates: list[dict],
                                     matched_candidates: list[dict],
                                     ranking_meta: dict,
                                     arbitration: dict,
                                     unified_result: dict,
                                     best: dict | None,
                                     confidence: float,
                                     explanation: str,
                                     reasoning_decision: dict) -> tuple[list[dict], list[dict], dict | None, float, str, dict]:
    if str(ranking_meta.get("unified_ranking_mode") or "disabled") != "enabled":
        return valid_candidates, matched_candidates, best, confidence, explanation, reasoning_decision

    unified_candidates = list((unified_result or {}).get("candidates") or [])
    if not unified_candidates:
        return valid_candidates, matched_candidates, best, confidence, explanation, reasoning_decision

    reordered_valid_candidates = _apply_unified_candidate_order(valid_candidates, unified_candidates)
    unified_best = reordered_valid_candidates[0] if reordered_valid_candidates else None
    if not unified_best:
        return valid_candidates, matched_candidates, best, confidence, explanation, reasoning_decision

    reordered_matched_candidates = list(matched_candidates or [])
    if matched_candidates:
        reordered_matched_candidates = _apply_unified_candidate_order(matched_candidates, unified_candidates)

    ranking_meta["unified_result_used"] = True
    ranking_meta["final_changed_by"] = "unified_ranking"
    ranking_meta["selected_top1_id"] = str(unified_best.get("quota_id", "") or "")
    ranking_meta["unified_top1_matches_selected"] = bool(
        ranking_meta["selected_top1_id"]
        and ranking_meta["selected_top1_id"] == str(ranking_meta.get("unified_top1_id", "") or "")
    )

    selected_confidence = float(
        unified_best.get("confidence", (unified_result or {}).get("top1_confidence", confidence)) or confidence
    )
    selected_explanation = _format_unified_selection_explanation(unified_result, unified_best)
    selected_reasoning = analyze_ambiguity(
        reordered_valid_candidates,
        route_profile=item.get("query_route"),
        arbitration=arbitration,
    ).as_dict()
    return (
        reordered_valid_candidates,
        reordered_matched_candidates,
        unified_best,
        selected_confidence,
        selected_explanation,
        selected_reasoning,
    )

def _build_parser_trace_diagnostics(item: dict) -> dict:
    canonical_query = item.get("canonical_query") or {}
    primary_query_profile = dict(canonical_query.get("primary_query_profile") or {})
    return {
        "search_query": str(canonical_query.get("search_query") or item.get("search_query") or item.get("name") or ""),
        "validation_query": str(canonical_query.get("validation_query") or ""),
        "route_query": str(canonical_query.get("normalized_query") or ""),
        "primary_subject": str(primary_query_profile.get("primary_subject") or ""),
        "decisive_terms": list(primary_query_profile.get("decisive_terms") or []),
        "quota_aliases": list(primary_query_profile.get("quota_aliases") or []),
        "noise_marker": str(primary_query_profile.get("noise_marker") or ""),
        "query_route": dict(item.get("query_route") or {}),
    }


def _build_router_trace_diagnostics(item: dict) -> dict:
    classification = dict(item.get("classification") or {})
    search_books = [
        str(book).strip()
        for book in list(classification.get("search_books") or [])
        if str(book).strip()
    ]
    hard_search_books = [
        str(book).strip()
        for book in list(
            classification.get("hard_search_books")
            or classification.get("hard_book_constraints")
            or []
        )
        if str(book).strip()
    ]
    advisory_search_books = [
        book for book in search_books
        if book not in hard_search_books
    ]
    unified_plan = dict(item.get("unified_plan") or {})
    plugin_hints = dict(item.get("plugin_hints") or {})
    classification_reason = str(classification.get("reason") or "").strip()
    if classification_reason.startswith("unified_plan"):
        effective_owner = "unified_plan"
    elif classification_reason in {"item_specialty", "soft_item_specialty"}:
        effective_owner = "seeded_specialty"
    elif classification.get("primary"):
        effective_owner = "specialty_classifier"
    else:
        effective_owner = "open_search"

    advisory_owner = ""
    if unified_plan and (
        unified_plan.get("preferred_books")
        or unified_plan.get("hard_books")
        or unified_plan.get("search_aliases")
    ):
        advisory_owner = "unified_plan"
    elif plugin_hints and (
        plugin_hints.get("preferred_books")
        or plugin_hints.get("preferred_specialties")
        or plugin_hints.get("synonym_aliases")
    ):
        advisory_owner = "province_plugin"
    elif item.get("specialty"):
        advisory_owner = "seeded_specialty"

    return {
        "query_route": dict(item.get("query_route") or {}),
        "plugin_hints": plugin_hints,
        "unified_plan": unified_plan,
        "advisory_owner": advisory_owner,
        "effective_owner": effective_owner,
        "effective_reason": classification_reason,
        "classification": {
            "primary": str(classification.get("primary") or ""),
            "fallbacks": list(classification.get("fallbacks") or []),
            "candidate_books": list(classification.get("candidate_books") or []),
            "search_books": search_books,
            "hard_book_constraints": list(classification.get("hard_book_constraints") or []),
            "hard_search_books": hard_search_books,
            "advisory_search_books": advisory_search_books,
            "route_mode": str(classification.get("route_mode") or ""),
        },
    }


def _build_retriever_trace_diagnostics(item: dict,
                                       valid_candidates: list[dict],
                                       matched_candidates: list[dict],
                                       router_diagnostics: dict | None = None) -> dict:
    classification = dict(item.get("classification") or {})
    resolution = dict(classification.get("retrieval_resolution") or {})
    calls = list(resolution.get("calls") or [])
    main_calls = [call for call in calls if str(call.get("target") or "").strip() == "main"]
    escape_used = any(str(call.get("stage") or "").strip() == "escape" for call in main_calls)
    open_used = any(
        str(call.get("stage") or "").strip() in {"escape", "open"}
        for call in main_calls
    )
    resolved_main_books = []
    for call in main_calls:
        resolved_books = [
            str(book).strip()
            for book in list(call.get("resolved_books") or [])
            if str(book).strip()
        ]
        if resolved_books:
            resolved_main_books = resolved_books
            break
    router_effective_owner = str((router_diagnostics or {}).get("effective_owner") or "")
    scope_owner = "retriever_main_escape" if escape_used else (router_effective_owner or "router")
    return {
        "candidate_count": len(valid_candidates or []),
        "matched_candidate_count": len(matched_candidates or []),
        "candidate_ids": [
            str(candidate.get("quota_id", "") or "").strip()
            for candidate in (valid_candidates or [])
            if str(candidate.get("quota_id", "") or "").strip()
        ],
        "authority_hit": any(has_exact_experience_anchor(candidate) for candidate in (valid_candidates or [])),
        "kb_hit": any(has_exact_universal_kb_anchor(candidate) for candidate in (valid_candidates or [])),
        "scope_owner": scope_owner,
        "escape_owner": "retriever_main_escape" if escape_used else "",
        "used_open_search": open_used,
        "resolved_main_books": resolved_main_books,
        "route_scope_filter": dict(classification.get("route_scope_filter") or {}),
        "candidate_scope_guard": dict(classification.get("candidate_scope_guard") or {}),
        "search_resolution": resolution,
    }


def _build_ranker_trace_diagnostics(candidates: list[dict], best: dict | None, ranking_meta: dict, arbitration: dict) -> dict:
    ordered = list(candidates or [])
    selected = best or (ordered[0] if ordered else None)
    second = ordered[1] if len(ordered) > 1 else None
    selected_score = compute_candidate_rank_score(selected) if selected else 0.0
    second_score = compute_candidate_rank_score(second) if second else 0.0

    timeline = [
        {"stage": "pre_ltr_seed", "quota_id": str(ranking_meta.get("pre_ltr_top1_id", "") or "")},
        {"stage": "ltr", "quota_id": str(ranking_meta.get("post_ltr_top1_id", "") or "")},
        {"stage": "cgr_ranker", "quota_id": str(ranking_meta.get("post_cgr_top1_id", "") or "")},
        {"stage": "candidate_arbiter", "quota_id": str(ranking_meta.get("post_arbiter_top1_id", "") or "")},
        {"stage": "explicit_override", "quota_id": str(ranking_meta.get("post_explicit_top1_id", "") or "")},
        {"stage": "experience_anchor", "quota_id": str(ranking_meta.get("post_anchor_top1_id", "") or "")},
        {
            "stage": "unified_ranking",
            "quota_id": str(ranking_meta.get("unified_top1_id", "") or "") if ranking_meta.get("unified_result_used") else "",
        },
        {"stage": "selected", "quota_id": str(ranking_meta.get("selected_top1_id", "") or "")},
    ]

    rank_timeline_changes = []
    prev_quota_id = ""
    decision_owner = "pre_ltr_seed"
    for entry in timeline:
        quota_id = str(entry.get("quota_id", "") or "")
        if not quota_id:
            continue
        if not prev_quota_id:
            prev_quota_id = quota_id
            continue
        if quota_id != prev_quota_id:
            rank_timeline_changes.append({
                "stage": entry["stage"],
                "from_quota_id": prev_quota_id,
                "to_quota_id": quota_id,
            })
            decision_owner = entry["stage"]
            prev_quota_id = quota_id

    if decision_owner == "selected":
        decision_owner = rank_timeline_changes[-1]["stage"] if rank_timeline_changes else "pre_ltr_seed"

    return {
        "selected_quota": str((selected or {}).get("quota_id", "") or ""),
        "selected_rank_score": selected_score,
        "second_rank_score": second_score,
        "score_gap": max(selected_score - second_score, 0.0),
        "selected_rank_breakdown": explain_candidate_rank_score(selected or {}),
        "second_rank_breakdown": explain_candidate_rank_score(second or {}) if second else {"rank_score": 0.0, "stage_priority": {}},
        "decision_owner": decision_owner,
        "top1_flip_count": len(rank_timeline_changes),
        "rank_timeline": timeline,
        "rank_timeline_changes": rank_timeline_changes,
        "arbitration": dict(arbitration or {}),
        "unified_ranking": {
            "enabled": bool(ranking_meta.get("unified_ranking_enabled")),
            "shadow_mode": bool(ranking_meta.get("unified_ranking_shadow_mode")),
            "mode": str(ranking_meta.get("unified_ranking_mode") or "disabled"),
            "executed": bool(ranking_meta.get("unified_ranking_executed")),
            "legacy_selected_quota": str(ranking_meta.get("legacy_top1_id", "") or ""),
            "selected_quota": str(ranking_meta.get("unified_top1_id", "") or ""),
            "score": float(ranking_meta.get("unified_top1_score", 0.0) or 0.0),
            "confidence": float(ranking_meta.get("unified_top1_confidence", 0.0) or 0.0),
            "matches_selected": bool(ranking_meta.get("unified_top1_matches_selected")),
            "matches_legacy": bool(ranking_meta.get("unified_top1_matches_legacy")),
            "legacy_score": ranking_meta.get("legacy_top1_unified_score"),
            "legacy_confidence": ranking_meta.get("legacy_top1_unified_confidence"),
            "score_gap_vs_legacy": ranking_meta.get("unified_legacy_score_gap"),
            "result_used": bool(ranking_meta.get("unified_result_used")),
            "diagnostics": dict(ranking_meta.get("unified_ranking_diagnostics") or {}),
            "error": str(ranking_meta.get("unified_ranking_error", "") or ""),
        },
    }


def _run_rank_pipeline(item: dict,
                       decision_candidates: list[dict],
                       *,
                       reservoir: list[dict],
                       allow_arbiter: bool,
                       allow_explicit: bool) -> tuple[list[dict], dict, dict, dict, dict | None]:
    ordered = list(decision_candidates or [])
    ranking_meta = _init_ranking_meta()
    ranking_meta["candidate_count"] = len(reservoir or [])
    unified_ranking_flags = _resolve_unified_ranking_flags()
    ranking_meta["unified_ranking_enabled"] = unified_ranking_flags["enabled"]
    ranking_meta["unified_ranking_shadow_mode"] = unified_ranking_flags["shadow_mode"]
    ranking_meta["unified_ranking_mode"] = unified_ranking_flags["mode"]
    arbitration: dict = {}
    explicit_override: dict = {}

    if not ordered:
        return ordered, ranking_meta, arbitration, explicit_override, None

    ranking_meta["pre_ltr_top1_id"] = _top_candidate_id(ordered)
    if ranking_meta["unified_ranking_mode"] == "enabled":
        seed_top1_id = ranking_meta["pre_ltr_top1_id"]
        ranking_meta["ltr"] = {
            "skipped_by_unified_primary": True,
            "legacy_stage_disabled": True,
        }
        ranking_meta["post_ltr_top1_id"] = seed_top1_id
        ranking_meta["post_cgr_top1_id"] = seed_top1_id
        ranking_meta["post_arbiter_top1_id"] = seed_top1_id
        ranking_meta["post_explicit_top1_id"] = seed_top1_id
        ranking_meta["post_anchor_top1_id"] = seed_top1_id
        arbitration = {
            "applied": False,
            "advisory_applied": False,
            "reason": "skipped_by_unified_primary",
            "legacy_stage_disabled": True,
        }
        explicit_override = {
            "applied": False,
            "advisory_applied": False,
            "reason": "skipped_by_unified_primary",
            "legacy_stage_disabled": True,
        }
        best = _pick_category_safe_candidate(item, ordered) if ordered else None
        if best:
            ranking_meta["selected_top1_id"] = str(best.get("quota_id", "") or "")
        return ordered, ranking_meta, arbitration, explicit_override, best

    api = _api()
    ordered, ltr_meta = api.rerank_candidates_with_ltr(item, ordered, {"item": item})
    ranking_meta["ltr"] = ltr_meta
    ranking_meta["post_ltr_top1_id"] = str((ltr_meta.get("post_ltr_top1_id") or _top_candidate_id(ordered)) or "")
    ranking_meta["post_cgr_top1_id"] = str((ltr_meta.get("post_cgr_top1_id") or ranking_meta["post_ltr_top1_id"]) or "")

    if allow_arbiter:
        arbiter_candidates, arbitration = api.arbitrate_candidates(item, ordered, route_profile=item.get("query_route"))
        ordered = _merge_arbiter_annotations(ordered, arbiter_candidates)
        if arbitration.get("applied"):
            arbitration = {
                **dict(arbitration or {}),
                "applied": False,
                "reason": str(arbitration.get("reason") or "structured_candidate_swap_advisory"),
                "reorder_ignored_by_pipeline": True,
            }
        ranking_meta["post_arbiter_top1_id"] = _top_candidate_id(ordered)
    else:
        arbitration = {
            "applied": False,
            "advisory_applied": False,
            "route": str((item.get("query_route") or {}).get("route") or ""),
            "reason": "no_param_matched_candidates",
        }
        ranking_meta["post_arbiter_top1_id"] = ranking_meta["post_ltr_top1_id"]

    if allow_explicit:
        explicit_result = _promote_explicit_distribution_box_candidate(item, ordered)
        if isinstance(explicit_result, tuple) and len(explicit_result) == 2:
            explicit_candidates, explicit_override = explicit_result
        else:
            explicit_candidates = list(explicit_result or [])
            explicit_override = {}
        ordered = _merge_explicit_annotations(ordered, explicit_candidates)
        if explicit_override.get("applied"):
            explicit_override = {
                **dict(explicit_override or {}),
                "applied": False,
                "reason": str(explicit_override.get("reason") or "explicit_advisory"),
                "reorder_ignored_by_pipeline": True,
            }
        ranking_meta["explicit_override"] = explicit_override
        ranking_meta["post_explicit_top1_id"] = _top_candidate_id(ordered)
    else:
        ranking_meta["post_explicit_top1_id"] = ranking_meta["post_arbiter_top1_id"]

    ranking_meta["post_anchor_top1_id"] = _top_candidate_id(ordered)

    best = _pick_category_safe_candidate(item, ordered) if ordered else None
    if best is None and ordered:
        best = ordered[0]
    if best:
        ranking_meta["selected_top1_id"] = str(best.get("quota_id", "") or "")
    return ordered, ranking_meta, arbitration, explicit_override, best


def _assemble_search_result_payload(item: dict,
                                    *,
                                    candidates: list[dict],
                                    valid_candidates: list[dict],
                                    matched_candidates: list[dict],
                                    best: dict | None,
                                    confidence: float,
                                    explanation: str,
                                    arbitration: dict,
                                    explicit_override: dict,
                                    plugin_route_gate: dict,
                                    reasoning_decision: dict,
                                    ranking_meta: dict) -> dict:
    all_candidate_ids = [
        str(candidate.get("quota_id", "")).strip()
        for candidate in valid_candidates
        if str(candidate.get("quota_id", "")).strip()
    ]
    parser_diagnostics = _build_parser_trace_diagnostics(item)
    router_diagnostics = _build_router_trace_diagnostics(item)
    retriever_diagnostics = _build_retriever_trace_diagnostics(
        item,
        valid_candidates,
        matched_candidates if valid_candidates else [],
        router_diagnostics,
    )
    ranker_candidates = valid_candidates if ranking_meta.get("unified_result_used") else (
        matched_candidates if matched_candidates else valid_candidates
    )
    ranker_diagnostics = _build_ranker_trace_diagnostics(ranker_candidates, best, ranking_meta, arbitration)

    quotas = [{
        "quota_id": best["quota_id"],
        "name": best["name"],
        "unit": best.get("unit", ""),
        "reason": explanation,
        "reasoning": summarize_candidate_reasoning(best),
        "db_id": best.get("id"),
    }] if best else []
    supplemental_quotas = item.get("_supplemental_quotas") if isinstance(item, dict) else []
    if quotas and isinstance(supplemental_quotas, list):
        seen_ids = {str(quota.get("quota_id", "")).strip() for quota in quotas if str(quota.get("quota_id", "")).strip()}
        for quota in supplemental_quotas:
            quota_id = str((quota or {}).get("quota_id", "")).strip()
            quota_name = str((quota or {}).get("name", "")).strip()
            if not quota_id or not quota_name or quota_id in seen_ids:
                continue
            quotas.append(dict(quota))
            seen_ids.add(quota_id)

    result = {
        "bill_item": item,
        "quotas": quotas,
        "confidence": confidence,
        "explanation": explanation,
        "candidates_count": len(valid_candidates),
        "candidate_count": len(valid_candidates),
        "all_candidate_ids": all_candidate_ids,
        "candidate_snapshots": _build_ranked_candidate_snapshots(valid_candidates, top_n=20),
        "match_source": "search",
        "arbitration": arbitration,
        "explicit_override": explicit_override,
        "plugin_route_gate": plugin_route_gate,
        "reasoning_decision": reasoning_decision,
        "needs_reasoning": bool(reasoning_decision.get("is_ambiguous")),
        "require_final_review": bool(reasoning_decision.get("require_final_review")),
        "pre_ltr_top1_id": ranking_meta["pre_ltr_top1_id"],
        "post_ltr_top1_id": ranking_meta["post_ltr_top1_id"],
        "post_cgr_top1_id": ranking_meta["post_cgr_top1_id"],
        "post_arbiter_top1_id": ranking_meta["post_arbiter_top1_id"],
        "post_explicit_top1_id": ranking_meta["post_explicit_top1_id"],
        "post_anchor_top1_id": ranking_meta["post_anchor_top1_id"],
        "selected_top1_id": ranking_meta["selected_top1_id"],
        "legacy_top1_id": ranking_meta["legacy_top1_id"],
        "unified_ranking_enabled": ranking_meta["unified_ranking_enabled"],
        "unified_ranking_shadow_mode": ranking_meta["unified_ranking_shadow_mode"],
        "unified_ranking_mode": ranking_meta["unified_ranking_mode"],
        "unified_ranking_executed": ranking_meta["unified_ranking_executed"],
        "unified_result_used": ranking_meta["unified_result_used"],
        "unified_top1_id": ranking_meta["unified_top1_id"],
        "unified_top1_score": ranking_meta["unified_top1_score"],
        "unified_top1_confidence": ranking_meta["unified_top1_confidence"],
        "unified_top1_matches_selected": ranking_meta["unified_top1_matches_selected"],
        "unified_top1_matches_legacy": ranking_meta["unified_top1_matches_legacy"],
        "legacy_top1_unified_score": ranking_meta["legacy_top1_unified_score"],
        "legacy_top1_unified_confidence": ranking_meta["legacy_top1_unified_confidence"],
        "unified_legacy_score_gap": ranking_meta["unified_legacy_score_gap"],
        "unified_shadow_comparison": {
            "legacy_top1_id": ranking_meta["legacy_top1_id"],
            "unified_top1_id": ranking_meta["unified_top1_id"],
            "matches": ranking_meta["unified_top1_matches_legacy"],
            "legacy_top1_unified_score": ranking_meta["legacy_top1_unified_score"],
            "legacy_top1_unified_confidence": ranking_meta["legacy_top1_unified_confidence"],
            "score_gap": ranking_meta["unified_legacy_score_gap"],
            "failure_reason": ranking_meta["unified_ranking_error"],
        },
        "unified_ranking_diagnostics": ranking_meta["unified_ranking_diagnostics"],
        "unified_ranking_error": ranking_meta["unified_ranking_error"],
        "post_final_top1_id": str((quotas[0].get("quota_id", "") if quotas else "") or ""),
        "final_changed_by": ranking_meta["final_changed_by"],
        "ltr_rerank": ranking_meta["ltr"],
        "rank_decision_owner": ranker_diagnostics.get("decision_owner", ""),
        "rank_top1_flip_count": ranker_diagnostics.get("top1_flip_count", 0),
    }

    _append_trace_step(
        result,
        "search_select",
        selected_quota=best.get("quota_id") if best else "",
        selected_reasoning=summarize_candidate_reasoning(best) if best else {},
        pre_ltr_top1_id=result.get("pre_ltr_top1_id", ""),
        post_ltr_top1_id=result.get("post_ltr_top1_id", ""),
        post_cgr_top1_id=result.get("post_cgr_top1_id", ""),
        post_arbiter_top1_id=result.get("post_arbiter_top1_id", ""),
        post_explicit_top1_id=result.get("post_explicit_top1_id", ""),
        post_anchor_top1_id=result.get("post_anchor_top1_id", ""),
        selected_top1_id=result.get("selected_top1_id", ""),
        arbitration=arbitration,
        explicit_override=explicit_override,
        plugin_route_gate=plugin_route_gate,
        reasoning_decision=reasoning_decision,
        parser=parser_diagnostics,
        router=router_diagnostics,
        retriever=retriever_diagnostics,
        ranker=ranker_diagnostics,
        query_route=item.get("query_route") or {},
        batch_context=summarize_batch_context_for_trace(item),
        ltr_rerank=result.get("ltr_rerank", {}),
        candidates_count=len(valid_candidates),
        candidates=_summarize_candidates_for_trace(candidates),
    )
    return result


def _finalize_search_result_payload(result: dict,
                                    *,
                                    item: dict,
                                    candidates: list[dict],
                                    valid_candidates: list[dict],
                                    best: dict | None,
                                    explanation: str,
                                    reasoning_decision: dict) -> dict:
    input_gate = item.get("_input_gate") or {}
    if best and valid_candidates and any(candidate.get("param_match", True) for candidate in valid_candidates):
        _set_result_reason(result, "structured_selection", ["retrieved", "validated"], explanation or "selected from structured candidates")
    elif best and valid_candidates:
        _set_result_reason(result, "param_conflict", ["retrieved", "param_conflict", "manual_review"], explanation or "fallback to best candidate")
    elif candidates and not valid_candidates:
        _set_result_reason(result, "candidate_invalid", ["retrieved", "candidate_invalid", "manual_review"], "candidates missing quota_id/name")
    else:
        _set_result_reason(result, "recall_failure", ["recall_failure", "no_candidates"], "search found no candidates")

    if input_gate:
        _set_result_reason(
            result,
            result.get("primary_reason", ""),
            list(input_gate.get("reason_tags") or []),
            result.get("reason_detail", "") or str(input_gate.get("detail") or ""),
        )
    if reasoning_decision.get("is_ambiguous"):
        ambiguity_tags = ["ambiguous_candidates"]
        if reasoning_decision.get("require_final_review"):
            ambiguity_tags.append("manual_review")
        _set_result_reason(result, result.get("primary_reason", ""), ambiguity_tags, result.get("reason_detail", "") or explanation)

    result = _apply_price_validation(result, item, best)

    if best and valid_candidates:
        result["alternatives"] = _build_alternatives(valid_candidates, skip_obj=best, top_n=DEFAULT_ALTERNATIVE_COUNT)
    if not best:
        result["no_match_reason"] = explanation or "搜索无匹配结果"
    return result


def _build_ranked_selection_decision(item: dict,
                                     *,
                                     best: dict | None,
                                     decision_candidates: list[dict],
                                     candidates_count: int,
                                     param_match: bool,
                                     arbitration: dict) -> tuple[float, str, dict]:
    if not best:
        return 0.0, "no safe candidate selected", {}

    if param_match:
        best_composite = compute_candidate_rank_score(best)
        others = [candidate for candidate in decision_candidates if candidate is not best]
        second_composite = max((compute_candidate_rank_score(candidate) for candidate in others), default=0)
        confidence = calculate_confidence(
            best.get("param_score", 0.5),
            param_match=True,
            name_bonus=best.get("name_bonus", 0.0),
            score_gap=best_composite - second_composite,
            rerank_score=best.get("rerank_score", best.get("hybrid_score", 0.0)),
            candidates_count=candidates_count,
            is_ambiguous_short=item.get("_is_ambiguous_short", False),
        )
        explanation = best.get("param_detail", "")
    else:
        confidence = calculate_confidence(
            best.get("param_score", 0.0),
            param_match=False,
            name_bonus=best.get("name_bonus", 0.0),
            rerank_score=best.get("rerank_score", best.get("hybrid_score", 0.0)),
            family_aligned=infer_confidence_family_alignment(best),
            family_hard_conflict=bool(best.get("family_gate_hard_conflict", False)),
            candidates_count=candidates_count,
            is_ambiguous_short=item.get("_is_ambiguous_short", False),
        )
        explanation = f"fallback_to_candidate: {best.get('param_detail', '')}"

    reasoning_decision = analyze_ambiguity(
        decision_candidates,
        route_profile=item.get("query_route"),
        arbitration=arbitration,
    ).as_dict()
    return confidence, explanation, reasoning_decision


def _build_search_result_from_candidates_legacy(item: dict, candidates: list[dict]) -> dict:
    return _build_search_result_from_candidates(item, candidates)


def _build_search_result_from_candidates(item: dict, candidates: list[dict]) -> dict:
    performance_monitor = PerformanceMonitor()
    best = None
    confidence = 0.0
    explanation = ""
    arbitration: dict = {}
    explicit_override: dict = {}
    reasoning_decision: dict = {}
    matched_candidates: list[dict] = []
    ranking_meta = _init_ranking_meta()

    with performance_monitor.measure("search_candidates_validate"):
        valid_candidates = [
            candidate
            for candidate in (candidates or [])
            if str(candidate.get("quota_id", "")).strip() and str(candidate.get("name", "")).strip()
        ]
    with performance_monitor.measure("search_plugin_route_gate"):
        valid_candidates, plugin_route_gate = _apply_plugin_route_gate(item, valid_candidates)
    with performance_monitor.measure("search_plugin_bias"):
        valid_candidates = _apply_plugin_candidate_biases(item, valid_candidates)
    with performance_monitor.measure("search_scope_annotate"):
        valid_candidates = _annotate_candidate_scope_signals(item, valid_candidates)
    ranking_meta["candidate_count"] = len(valid_candidates)
    if candidates and not valid_candidates:
        logger.warning("candidate list exists but all items miss quota_id/name; treat as no-match")

    if valid_candidates:
        with performance_monitor.measure("search_param_match_filter"):
            matched_candidates = [candidate for candidate in valid_candidates if candidate.get("param_match", True)]
        decision_candidates = matched_candidates if matched_candidates else valid_candidates
        with performance_monitor.measure("search_rank_pipeline"):
            ranked_candidates, ranking_meta, arbitration, explicit_override, best = _run_rank_pipeline(
                item,
                decision_candidates,
                reservoir=valid_candidates,
                allow_arbiter=bool(matched_candidates),
                allow_explicit=bool(matched_candidates),
            )
        if matched_candidates:
            matched_candidates = ranked_candidates
        else:
            valid_candidates = ranked_candidates

        if best:
            ranking_meta["selected_top1_id"] = str(best.get("quota_id", "") or "")
            with performance_monitor.measure("search_selection_decision"):
                confidence, explanation, reasoning_decision = _build_ranked_selection_decision(
                    item,
                    best=best,
                    decision_candidates=ranked_candidates,
                    candidates_count=len(valid_candidates),
                    param_match=bool(matched_candidates),
                    arbitration=arbitration,
                )
        else:
            explanation = "no safe candidate selected from ranked results"

    ranking_meta["legacy_top1_id"] = str(ranking_meta.get("selected_top1_id", "") or "")
    unified_result = _apply_unified_ranking_shadow(item, valid_candidates, ranking_meta)
    valid_candidates, matched_candidates, best, confidence, explanation, reasoning_decision = _apply_unified_enabled_selection(
        item,
        valid_candidates,
        matched_candidates,
        ranking_meta,
        arbitration,
        unified_result,
        best,
        confidence,
        explanation,
        reasoning_decision,
    )

    with performance_monitor.measure("search_result_payload_assemble"):
        result = _assemble_search_result_payload(
            item,
            candidates=candidates,
            valid_candidates=valid_candidates,
            matched_candidates=matched_candidates,
            best=best,
            confidence=confidence,
            explanation=explanation,
            arbitration=arbitration,
            explicit_override=explicit_override,
            plugin_route_gate=plugin_route_gate,
            reasoning_decision=reasoning_decision,
            ranking_meta=ranking_meta,
        )
    result["search_candidate_stage_performance"] = {
        "stages": performance_monitor.snapshot(),
        "total": sum(performance_monitor.snapshot().values()),
    }
    return _finalize_search_result_payload(
        result,
        item=item,
        candidates=candidates,
        valid_candidates=valid_candidates,
        best=best,
        explanation=explanation,
        reasoning_decision=reasoning_decision,
    )
def _resolve_search_mode_result(item: dict, candidates: list[dict],
                                exp_backup: dict, rule_backup: dict,
                                exp_hits: int, rule_hits: int):
    """search模式统一结果决策：搜索结果 + 经验/规则兜底。"""
    performance_monitor = PerformanceMonitor()
    active_candidates = list(candidates or [])
    injected_rule_qid = ""
    with performance_monitor.measure("search_rule_backup_injection"):
        if rule_backup:
            active_candidates, injected_rule_qid = _inject_rule_backup_candidate(
                item, active_candidates, rule_backup
            )
    with performance_monitor.measure("search_result_build"):
        result = _build_search_result_from_candidates(item, active_candidates)
    _append_item_review_rejection_trace(result, item)
    with performance_monitor.measure("search_experience_reconcile"):
        result, exp_hits = _reconcile_search_and_experience(result, exp_backup, exp_hits)
    if injected_rule_qid:
        selected_qid = str((result.get("quotas") or [{}])[0].get("quota_id", "") or "").strip()
        if selected_qid == injected_rule_qid:
            result["match_source"] = "rule_injected"
            rule_hits += 1
        _append_trace_step(
            result,
            "rule_backup_injected",
            injected_quota_id=injected_rule_qid,
            backup_confidence=rule_backup.get("confidence", 0),
            selected_rule_candidate=bool(selected_qid and selected_qid == injected_rule_qid),
        )
    elif rule_backup:
        _append_trace_step(
            result,
            "rule_backup_rejected",
            backup_confidence=rule_backup.get("confidence", 0),
            current_confidence=result.get("confidence", 0),
        )
    result["search_stage_performance"] = {
        "stages": performance_monitor.snapshot(),
        "total": sum(performance_monitor.snapshot().values()),
    }
    _append_trace_step(
        result,
        "search_mode_final",
        final_source=result.get("match_source", ""),
        final_confidence=result.get("confidence", 0),
        search_stage_performance=result.get("search_stage_performance") or {},
    )
    return result, exp_hits, rule_hits


# ============================================================
# 统一前置处理
# ============================================================

def _prepare_item_for_matching(item: dict, experience_db, rule_validator: RuleValidator,
                               province: str = None, exact_exp_direct: bool = False,
                               lightweight_experience: bool = False,
                               lightweight_rule_prematch: bool = False,
                               performance_monitor: PerformanceMonitor | None = None) -> dict:
    """
    三种模式统一的前置处理：
    1) 措施项跳过
    2) 专业分类
    3) 经验库预匹配（可配置精确命中是否直通）
    4) 规则预匹配（高置信直通、低置信备选）
    """
    if province and not item.get("_resolved_province"):
        item["_resolved_province"] = province
    ctx = _build_item_context(item, performance_monitor=performance_monitor)
    item["query_route"] = ctx.get("query_route")
    item["plugin_hints"] = ctx.get("plugin_hints") or {}
    item["unified_plan"] = ctx.get("unified_plan") or {}
    item["context_prior"] = ctx.get("context_prior") or item.get("context_prior") or {}
    item["canonical_query"] = ctx.get("canonical_query") or {}
    name = ctx["name"]
    desc = ctx["desc"]
    canonical_query = ctx.get("canonical_query") or {}
    full_query = canonical_query.get("validation_query") or ctx["full_query"]
    search_query = canonical_query.get("search_query") or ctx["search_query"]
    normalized_query = canonical_query.get("normalized_query") or ctx["normalized_query"]
    input_gate = ctx.get("input_gate") or {}

    if _is_measure_item(name, desc, ctx["unit"], ctx["quantity"]):
        return {
            "early_result": _build_skip_measure_result(item),
            "early_type": "skip_measure",
        }

    if input_gate.get("should_abstain"):
        return {
            "early_result": _build_input_gate_abstain_result(
                item,
                primary_reason=str(input_gate.get("primary_reason") or "dirty_input"),
                detail=str(input_gate.get("detail") or "输入质量不足，转人工审核"),
                reason_tags=list(input_gate.get("reason_tags") or []),
            ),
            "early_type": "input_gate_abstain",
        }

    if input_gate.get("is_dirty_code"):
        current_gate = dict(item.get("_input_gate") or {})
        current_gate["primary_reason"] = current_gate.get("primary_reason") or input_gate.get("primary_reason", "dirty_input")
        current_gate["reason_tags"] = merge_reason_tags(
            current_gate.get("reason_tags") or [],
            input_gate.get("reason_tags") or [],
        )
        if input_gate.get("detail") and not current_gate.get("detail"):
            current_gate["detail"] = input_gate.get("detail", "")
        item["_input_gate"] = current_gate

    with (
        performance_monitor.measure("专业分类")
        if performance_monitor is not None else nullcontext()
    ):
        classification = _build_classification(
            item, name, desc, ctx["section"], ctx.get("sheet_name", ""), province=province
        )
    item["classification"] = classification
    item["_trace_classification"] = dict(classification or {})

    adaptive_meta = dict(item.get("_adaptive_strategy_meta") or item.get("adaptive_strategy_meta") or {})
    if not adaptive_meta:
        adaptive_meta = dict(_api()._ADAPTIVE_STRATEGY.evaluate(item))
    adaptive_strategy = str(adaptive_meta.get("strategy") or item.get("adaptive_strategy") or "standard").strip().lower()
    if adaptive_strategy not in {"fast", "standard", "deep"}:
        adaptive_strategy = "standard"
    adaptive_meta["strategy"] = adaptive_strategy
    if adaptive_strategy == "fast" and experience_db is None:
        adaptive_meta["downgraded_from"] = "fast"
        adaptive_meta["downgrade_reason"] = "missing_experience_db"
        adaptive_meta["strategy"] = "standard"
        adaptive_strategy = "standard"
    item["adaptive_strategy"] = adaptive_strategy
    item["adaptive_strategy_meta"] = adaptive_meta
    item["_adaptive_strategy_meta"] = adaptive_meta

    context_gate = _evaluate_context_gate(name, desc, ctx["section"], classification)
    if context_gate.get("should_abstain"):
        return {
            "early_result": _build_input_gate_abstain_result(
                item,
                primary_reason=str(context_gate.get("primary_reason") or "context_missing"),
                detail=str(context_gate.get("detail") or "上下文不足，转人工审核"),
                reason_tags=list(context_gate.get("reason_tags") or []),
            ),
            "early_type": "input_gate_abstain",
        }

    if context_gate.get("reason_tags"):
        current_gate = dict(item.get("_input_gate") or {})
        current_gate["primary_reason"] = current_gate.get("primary_reason") or context_gate.get("primary_reason", "")
        current_gate["reason_tags"] = merge_reason_tags(
            current_gate.get("reason_tags") or [],
            context_gate.get("reason_tags") or [],
        )
        if context_gate.get("detail") and not current_gate.get("detail"):
            current_gate["detail"] = context_gate.get("detail", "")
        item["_input_gate"] = current_gate

    if adaptive_strategy == "fast":
        exp_result = _api().try_experience_match(
            normalized_query, item, experience_db, rule_validator, province=province)
    elif lightweight_experience:
        exp_result = _api().try_experience_exact_match(
            normalized_query,
            item,
            experience_db,
            rule_validator,
            province=province,
            authority_only=True,
        )
    else:
        exp_result = _api().try_experience_match(
            normalized_query, item, experience_db, rule_validator, province=province)

    # 审核规则检查：经验库命中后，用审核规则验证一遍
    # 防止错误数据进入权威层后被无限复制
    if exp_result:
        review_error = _api()._review_check_match_result(exp_result, item)
        if review_error:
            # 在 item 上标记审核拦截（后续统计时从 result.bill_item 中读取）
            item["_review_rejected"] = True
            top_quota = ((exp_result.get("quotas") or [{}])[0] or {})
            item["_experience_review_rejection"] = {
                "type": review_error.get("type"),
                "reason": review_error.get("reason"),
                "match_source": exp_result.get("match_source", ""),
                "quota_id": str(top_quota.get("quota_id", "") or ""),
            }
            bill_name = item.get("name", "")
            logger.warning(
                f"经验库匹配被审核规则拦截: '{bill_name[:40]}' "
                f"→ {review_error.get('type')}: {review_error.get('reason')}")
            _append_trace_step(exp_result, "experience_review_rejected",
                               error_type=review_error.get("type"),
                               error_reason=review_error.get("reason"))
            exp_result = None  # 丢弃，走搜索兜底

    exp_backup = exp_result if exp_result else None

    if adaptive_strategy == "fast" and exp_result is None:
        adaptive_meta["downgraded_from"] = "fast"
        adaptive_meta["downgrade_reason"] = "experience_miss"
        adaptive_meta["strategy"] = "standard"
        item["adaptive_strategy"] = "standard"
        item["adaptive_strategy_meta"] = adaptive_meta
        item["_adaptive_strategy_meta"] = adaptive_meta
        adaptive_strategy = "standard"

    if exact_exp_direct and exp_result and exp_result.get("match_source") == "experience_exact":
        _append_trace_step(exp_result, "experience_exact_direct_return")
        return {
            "early_result": exp_result,
            "early_type": "experience_exact",
        }

    if lightweight_rule_prematch:
        rule_direct, rule_backup = None, None
    else:
        rule_direct, rule_backup = _prepare_rule_match(
            rule_validator, full_query, item, search_query, classification,
            route_profile=ctx.get("query_route"))
    if rule_direct:
        # 审核规则检查：规则直通也要过安检（与经验库直通一致）
        review_error = _api()._review_check_match_result(rule_direct, item)
        if review_error:
            bill_name = item.get("name", "")
            logger.warning(
                f"规则直通被审核规则拦截: '{bill_name[:40]}' "
                f"→ {review_error.get('type')}: {review_error.get('reason')}")
            _append_trace_step(rule_direct, "rule_direct_review_rejected",
                               error_type=review_error.get("type"),
                               error_reason=review_error.get("reason"))
            # 已被审核规则判错的规则直通结果不能再回流为备选，
            # 否则后续可能反向覆盖掉更安全的搜索结果。
            rule_backup = None
            rule_direct = None
        else:
            _append_trace_step(rule_direct, "rule_direct_return")
            return {
                "early_result": rule_direct,
                "early_type": "rule_direct",
            }

    return {
        "early_result": None,
        "early_type": None,
        "ctx": ctx,
        "classification": classification,
        "exp_backup": exp_backup,
        "rule_backup": rule_backup,
    }
