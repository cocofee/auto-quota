"""
OpenClaw structured review context service.

This layer does not call any model or external service yet.
Its job is to normalize Jarvis task/result data into a stable payload that
later OpenClaw review executors and APIs can consume.
"""

from __future__ import annotations

from typing import Any


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _clean_str(value: Any) -> str:
    return str(value or "").strip()


def _trace_steps(match_result) -> list[dict[str, Any]]:
    trace = _as_dict(getattr(match_result, "trace", None))
    return [step for step in _as_list(trace.get("steps")) if isinstance(step, dict)]


def _top_quota(items: list[dict[str, Any]] | None) -> dict[str, Any]:
    if not items:
        return {}
    first = items[0] or {}
    return first if isinstance(first, dict) else {}


def _extract_latest_trace_dict(match_result, key: str) -> dict[str, Any]:
    for step in reversed(_trace_steps(match_result)):
        value = step.get(key)
        if isinstance(value, dict) and value:
            return value
    return {}


def _extract_reasoning_summary(match_result) -> dict[str, Any]:
    for step in reversed(_trace_steps(match_result)):
        engaged = bool(step.get("reasoning_engaged"))
        conflicts = _as_list(step.get("reasoning_conflicts"))
        decision = _as_dict(step.get("reasoning_decision"))
        compare_points = _as_list(step.get("reasoning_compare_points"))
        if engaged or conflicts or decision or compare_points:
            return {
                "engaged": engaged,
                "decision": decision,
                "conflict_summaries": conflicts,
                "compare_points": compare_points,
            }
    return {}


def _extract_stage_top1_chain(match_result) -> dict[str, str]:
    chain = {
        "pre_ltr_top1_id": "",
        "post_ltr_top1_id": "",
        "post_cgr_top1_id": "",
        "post_arbiter_top1_id": "",
        "post_explicit_top1_id": "",
        "post_anchor_top1_id": "",
        "post_final_top1_id": "",
        "selected_top1_id": "",
        "final_changed_by": "",
    }
    for step in _trace_steps(match_result):
        for key in chain:
            value = _clean_str(step.get(key))
            if value:
                chain[key] = value
    return chain


def _candidate_key(item: dict[str, Any]) -> str:
    quota_id = _clean_str(item.get("quota_id"))
    if quota_id:
        return f"quota:{quota_id}"
    quota_name = _clean_str(item.get("name"))
    if quota_name:
        return f"name:{quota_name}"
    return ""


def _normalize_candidate(item: dict[str, Any], *, rank: int) -> dict[str, Any]:
    return {
        "rank": rank,
        "quota_id": _clean_str(item.get("quota_id")),
        "name": _clean_str(item.get("name")),
        "unit": _clean_str(item.get("unit")),
        "source": _clean_str(item.get("source")),
        "param_score": item.get("param_score"),
        "rerank_score": item.get("rerank_score"),
        "reason": _clean_str(item.get("reason")),
    }


def _build_candidate_pool(match_result) -> list[dict[str, Any]]:
    pool: list[dict[str, Any]] = []
    seen: set[str] = set()
    next_rank = 1

    for quota in _as_list(getattr(match_result, "quotas", None)):
        if not isinstance(quota, dict):
            continue
        key = _candidate_key(quota)
        if not key or key in seen:
            continue
        seen.add(key)
        pool.append(_normalize_candidate(quota, rank=next_rank))
        next_rank += 1

    for alt in _as_list(getattr(match_result, "alternatives", None)):
        if not isinstance(alt, dict):
            continue
        key = _candidate_key(alt)
        if not key or key in seen:
            continue
        seen.add(key)
        pool.append(_normalize_candidate(alt, rank=next_rank))
        next_rank += 1
        if next_rank > 10:
            return pool

    for step in reversed(_trace_steps(match_result)):
        candidates = step.get("candidates")
        if not isinstance(candidates, list):
            continue
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            key = _candidate_key(candidate)
            if not key or key in seen:
                continue
            seen.add(key)
            pool.append(_normalize_candidate(candidate, rank=next_rank))
            next_rank += 1
            if next_rank > 10:
                return pool
        if pool:
            break
    return pool


class OpenClawReviewService:
    """
    Normalize one Jarvis result into a stable OpenClaw review context and draft payload.

    This service does not decide the review result by itself.
    It only prepares a structured envelope so later auto-review logic can plug in
    without changing API contracts again.
    """

    def build_review_context(self, task, match_result) -> dict[str, Any]:
        trace = _as_dict(getattr(match_result, "trace", None))
        top_quota = _top_quota(getattr(match_result, "quotas", None) or [])
        candidate_pool = _build_candidate_pool(match_result)
        knowledge_basis = _extract_latest_trace_dict(match_result, "knowledge_basis")
        knowledge_summary = _extract_latest_trace_dict(match_result, "knowledge_summary")
        final_validation = _extract_latest_trace_dict(match_result, "final_validation")
        final_review_correction = _extract_latest_trace_dict(match_result, "final_review_correction")
        reasoning_summary = _extract_reasoning_summary(match_result)
        query_route = _extract_latest_trace_dict(match_result, "query_route")
        batch_context = _extract_latest_trace_dict(match_result, "batch_context")
        stage_top1_chain = _extract_stage_top1_chain(match_result)

        return {
            "task": {
                "task_id": _clean_str(getattr(task, "id", "")),
                "name": _clean_str(getattr(task, "name", "")),
                "province": _clean_str(getattr(task, "province", "")),
                "mode": _clean_str(getattr(task, "mode", "")),
                "original_filename": _clean_str(getattr(task, "original_filename", "")),
            },
            "result": {
                "result_id": _clean_str(getattr(match_result, "id", "")),
                "index": getattr(match_result, "index", None),
                "bill_code": _clean_str(getattr(match_result, "bill_code", "")),
                "bill_name": _clean_str(getattr(match_result, "bill_name", "")),
                "bill_description": _clean_str(getattr(match_result, "bill_description", "")),
                "bill_unit": _clean_str(getattr(match_result, "bill_unit", "")),
                "bill_quantity": getattr(match_result, "bill_quantity", None),
                "specialty": _clean_str(getattr(match_result, "specialty", "")),
                "sheet_name": _clean_str(getattr(match_result, "sheet_name", "")),
                "section": _clean_str(getattr(match_result, "section", "")),
                "match_source": _clean_str(getattr(match_result, "match_source", "")),
                "confidence": getattr(match_result, "confidence", 0),
                "confidence_score": getattr(match_result, "confidence_score", 0),
                "review_risk": _clean_str(getattr(match_result, "review_risk", "")),
                "light_status": _clean_str(getattr(match_result, "light_status", "")),
                "review_status": _clean_str(getattr(match_result, "review_status", "")),
                "explanation": _clean_str(getattr(match_result, "explanation", "")),
                "candidates_count": getattr(match_result, "candidates_count", 0),
            },
            "jarvis_result": {
                "top1_quota_id": _clean_str(top_quota.get("quota_id")),
                "top1_quota_name": _clean_str(top_quota.get("name")),
                "top1_unit": _clean_str(top_quota.get("unit")),
                "quotas": list(getattr(match_result, "quotas", None) or []),
                "alternatives": list(getattr(match_result, "alternatives", None) or []),
            },
            "candidate_pool": candidate_pool,
            "trace_summary": {
                "path": list(trace.get("path") or []),
                "final_source": _clean_str(trace.get("final_source")),
                "final_confidence": trace.get("final_confidence"),
                "stage_top1_chain": stage_top1_chain,
                "final_validation": final_validation,
                "final_review_correction": final_review_correction,
                "reasoning_summary": reasoning_summary,
                "query_route": query_route,
                "batch_context": batch_context,
                "knowledge_basis": knowledge_basis,
                "knowledge_summary": knowledge_summary,
            },
            "review_constraints": {
                "formal_write_allowed": False,
                "must_require_human_confirm": True,
                "can_override_within_candidates": True,
                "can_suggest_retry_query": True,
                "candidate_pool_required": True,
            },
        }

    def build_structured_draft(
        self,
        task,
        match_result,
        *,
        decision_type: str,
        suggested_quotas: list[dict[str, Any]] | None = None,
        review_confidence: int | None = None,
        error_stage: str = "",
        error_type: str = "",
        retry_query: str = "",
        reason_codes: list[str] | None = None,
        note: str = "",
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        context = self.build_review_context(task, match_result)
        draft_quotas = list(suggested_quotas or getattr(match_result, "quotas", None) or [])
        return {
            "openclaw_suggested_quotas": draft_quotas,
            "openclaw_review_note": note,
            "openclaw_review_confidence": review_confidence,
            "openclaw_decision_type": _clean_str(decision_type),
            "openclaw_error_stage": _clean_str(error_stage),
            "openclaw_error_type": _clean_str(error_type),
            "openclaw_retry_query": _clean_str(retry_query),
            "openclaw_reason_codes": list(reason_codes or []),
            "openclaw_review_payload": {
                "decision_type": _clean_str(decision_type),
                "error_stage": _clean_str(error_stage),
                "error_type": _clean_str(error_type),
                "retry_query": _clean_str(retry_query),
                "reason_codes": list(reason_codes or []),
                "review_confidence": review_confidence,
                "note": _clean_str(note),
                "suggested_quotas": draft_quotas,
                "evidence": _as_dict(evidence),
                "review_context": context,
            },
        }
