# -*- coding: utf-8 -*-
"""Result reason tagging and payload helpers."""

from loguru import logger

from src.candidate_scoring import (
    compute_candidate_rank_score,
    compute_candidate_sort_key,
    explain_candidate_rank_score,
    has_exact_experience_anchor,
    has_exact_universal_kb_anchor,
    sort_candidates_with_stage_priority,
)
from src.match_core import (
    _append_trace_step,
    _safe_json_materials,
    calculate_confidence,
    infer_confidence_family_alignment,
    summarize_candidate_reasoning,
)

from .gates import _set_result_reason

DEFAULT_ALTERNATIVE_COUNT = 9


def _build_ranked_candidate_snapshots(candidates: list[dict], top_n: int = 20) -> list[dict]:
    snapshots = []
    for candidate in list(candidates or [])[:top_n]:
        snapshots.append({
            "quota_id": str(candidate.get("quota_id", "") or ""),
            "name": str(candidate.get("name", "") or ""),
            "unit": str(candidate.get("unit", "") or ""),
            "param_match": bool(candidate.get("param_match", True)),
            "param_tier": int(candidate.get("param_tier", 1) or 1),
            "bm25_score": candidate.get("bm25_score"),
            "vector_score": candidate.get("vector_score"),
            "hybrid_score": candidate.get("hybrid_score"),
            "rerank_score": candidate.get("rerank_score"),
            "semantic_rerank_score": candidate.get("semantic_rerank_score"),
            "spec_rerank_score": candidate.get("spec_rerank_score"),
            "param_score": candidate.get("param_score"),
            "logic_score": candidate.get("logic_score"),
            "feature_alignment_score": candidate.get("feature_alignment_score"),
            "manual_structured_score": candidate.get("manual_structured_score"),
            "ltr_score": candidate.get("ltr_score"),
            "rank_stage": str(candidate.get("rank_stage", "") or ""),
            "rank_score_source": str(candidate.get("_rank_score_source", "") or ""),
            "rank_score": candidate.get("rank_score", compute_candidate_rank_score(candidate)),
            "rank_score_breakdown": explain_candidate_rank_score(candidate),
            "cgr_score": candidate.get("cgr_score"),
            "cgr_probability": candidate.get("cgr_probability"),
            "cgr_feasible": candidate.get("cgr_feasible"),
            "cgr_fatal_hard_conflict": candidate.get("cgr_fatal_hard_conflict"),
            "cgr_high_conf_wrong_book": candidate.get("cgr_high_conf_wrong_book"),
            "cgr_high_conf_family_book_conflict": candidate.get("cgr_high_conf_family_book_conflict"),
            "cgr_sem_score": candidate.get("cgr_sem_score"),
            "cgr_str_score": candidate.get("cgr_str_score"),
            "cgr_prior_score": candidate.get("cgr_prior_score"),
            "cgr_tier_penalty": candidate.get("cgr_tier_penalty"),
            "cgr_generic_penalty": candidate.get("cgr_generic_penalty"),
            "cgr_soft_conflict_penalty": candidate.get("cgr_soft_conflict_penalty"),
            "candidate_major_prefix": str(candidate.get("candidate_major_prefix", "") or ""),
            "target_db_type": str(candidate.get("target_db_type", "") or ""),
            "candidate_scope_match": candidate.get("candidate_scope_match"),
            "candidate_scope_conflict": candidate.get("candidate_scope_conflict"),
            "candidate_canonical_features": dict(
                candidate.get("candidate_canonical_features") or candidate.get("canonical_features") or {}
            ),
            "ltr_feature_snapshot": dict(candidate.get("ltr_feature_snapshot") or {}),
        })
    return snapshots


def _build_alternatives(candidates: list[dict], selected_ids: set = None,
                        skip_obj=None, top_n: int = DEFAULT_ALTERNATIVE_COUNT) -> list[dict]:
    """从候选中构建备选定额列表。"""
    if not candidates:
        return []
    selected_ids = selected_ids or set()
    filtered = []
    for c in candidates:
        if skip_obj is not None and c is skip_obj:
            continue
        if selected_ids and c.get("quota_id") in selected_ids:
            continue
        filtered.append(c)
    alternatives = []
    for alt in filtered[:top_n]:
        quota_id = str(alt.get("quota_id", "")).strip()
        quota_name = str(alt.get("name", "")).strip()
        if not quota_id or not quota_name:
            logger.warning(f"跳过异常候选（缺少quota_id/name）: {alt}")
            continue
        alt_ps = alt.get("param_score", 0.5)
        alt_conf = calculate_confidence(
            alt_ps, alt.get("param_match", True),
            name_bonus=alt.get("name_bonus", 0.0),
            rerank_score=alt.get("rerank_score", alt.get("hybrid_score", 0.0)),
            family_aligned=infer_confidence_family_alignment(alt),
            family_hard_conflict=bool(alt.get("family_gate_hard_conflict", False)),
        )
        alternatives.append({
            "quota_id": quota_id,
            "name": quota_name,
            "unit": alt.get("unit", ""),
            "confidence": alt_conf,
            "reason": alt.get("param_detail", ""),
            "reasoning": summarize_candidate_reasoning(alt),
        })
    return alternatives


def _build_skip_measure_result(item: dict) -> dict:
    """构建措施项跳过结果。"""
    result = {
        "bill_item": item,
        "quotas": [],
        "alternatives": [],
        "confidence": 0,
        "match_source": "skip_measure",
        "explanation": "措施项（管理费用），不套安装定额",
    }
    _set_result_reason(
        result,
        "measure_item",
        ["measure_item", "abstained"],
        "措施项（管理费用），不套安装定额",
    )
    _append_trace_step(result, "skip_measure", reason="管理费用类条目")
    return result


def _build_empty_match_result(item: dict, reason: str, source: str = "search") -> dict:
    """构建空匹配结果（用于无候选时兜底）。"""
    result = {
        "bill_item": item,
        "quotas": [],
        "confidence": 0,
        "explanation": reason,
        "no_match_reason": reason,
        "match_source": source,
    }
    _set_result_reason(result, "recall_failure", ["recall_failure", "no_candidates"], reason)
    _append_trace_step(result, "empty_result", reason=reason)
    return result


def _result_top1_id(result: dict | None) -> str:
    quotas = (result or {}).get("quotas") or []
    if not quotas:
        return ""
    return str(quotas[0].get("quota_id", "") or "").strip()


def _carry_ranking_snapshot(target: dict, source: dict, *, changed_by: str = ""):
    if not isinstance(target, dict) or not isinstance(source, dict):
        return
    for key in (
        "pre_ltr_top1_id",
        "post_ltr_top1_id",
        "post_arbiter_top1_id",
        "candidate_count",
        "candidates_count",
        "ltr_rerank",
    ):
        if not target.get(key):
            target[key] = source.get(key)
    if changed_by and _result_top1_id(target) != _result_top1_id(source):
        target["final_changed_by"] = target.get("final_changed_by") or changed_by
    target["post_final_top1_id"] = _result_top1_id(target)



def _append_backup_advisory(result: dict, advisory_type: str, backup: dict, stage: str) -> None:
    if not result or not backup:
        return

    quotas = list(backup.get("quotas") or [])
    top_quota = dict(quotas[0] or {}) if quotas else {}
    advisories = list(result.get("backup_advisories") or [])
    advisories.append({
        "type": str(advisory_type or ""),
        "match_source": str(backup.get("match_source", "") or ""),
        "confidence": backup.get("confidence", 0),
        "quota_id": str(top_quota.get("quota_id", "") or ""),
        "quota_name": str(top_quota.get("name", "") or ""),
    })
    result["backup_advisories"] = advisories
    _append_trace_step(
        result,
        stage,
        backup_type=str(advisory_type or ""),
        backup_confidence=backup.get("confidence", 0),
        backup_quota_id=str(top_quota.get("quota_id", "") or ""),
    )


