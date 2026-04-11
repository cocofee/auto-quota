"""
OpenClaw structured review context service.

This layer does not call any model or external service yet.
Its job is to normalize Jarvis task/result data into a stable payload that
later OpenClaw review executors and APIs can consume.
"""

from __future__ import annotations

from typing import Any

from app.services.qmd_service import get_default_qmd_service
from app.text_utils import repair_mojibake_data


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _clean_str(value: Any) -> str:
    return str(value or "").strip()


def _quota_snapshot(item: dict[str, Any] | None) -> dict[str, Any]:
    source = item if isinstance(item, dict) else {}
    return {
        "quota_id": _clean_str(source.get("quota_id")),
        "name": _clean_str(source.get("name")),
        "unit": _clean_str(source.get("unit")),
        "source": _clean_str(source.get("source")),
        "param_score": source.get("param_score"),
        "rerank_score": source.get("rerank_score"),
        "reason": _clean_str(source.get("reason")),
    }


def _bill_text(result: dict[str, Any]) -> str:
    return " ".join(
        part for part in [
            _clean_str(result.get("bill_name")),
            _clean_str(result.get("bill_description")),
        ] if part
    ).strip()


def _candidate_rank(candidate_pool: list[dict[str, Any]], quota: dict[str, Any] | None) -> int | None:
    quota_id = _clean_str((quota or {}).get("quota_id"))
    quota_name = _clean_str((quota or {}).get("name"))
    if not quota_id and not quota_name:
        return None
    for item in candidate_pool:
        if not isinstance(item, dict):
            continue
        if quota_id and _clean_str(item.get("quota_id")) == quota_id:
            return item.get("rank")
        if quota_name and _clean_str(item.get("name")) == quota_name:
            return item.get("rank")
    return None


def _compact_candidates(candidate_pool: list[dict[str, Any]], *, limit: int = 5) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for item in candidate_pool[:limit]:
        if not isinstance(item, dict):
            continue
        compact.append({
            "rank": item.get("rank"),
            "quota_id": _clean_str(item.get("quota_id")),
            "name": _clean_str(item.get("name")),
            "unit": _clean_str(item.get("unit")),
            "source": _clean_str(item.get("source")),
        })
    return compact


def _unique_strings(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = _clean_str(value)
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


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
        qmd_recall = get_default_qmd_service().recall_for_review_context(task, match_result, top_k=3)

        payload = {
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
            "qmd_recall": qmd_recall,
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
                "candidate_pool_required": False,
                "independent_library_audit_enabled": True,
            },
        }
        return repair_mojibake_data(payload, preserve_newlines=True)

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
        allow_empty = _clean_str(decision_type) in {
            "candidate_pool_insufficient",
            "retry_search_then_select",
            "abstain",
        }
        draft_quotas = [] if allow_empty and not suggested_quotas else list(suggested_quotas or getattr(match_result, "quotas", None) or [])
        absorbable_report = self.build_absorbable_report(
            context,
            decision_type=decision_type,
            suggested_quotas=draft_quotas,
            review_confidence=review_confidence,
            error_stage=error_stage,
            error_type=error_type,
            retry_query=retry_query,
            reason_codes=reason_codes,
            note=note,
            evidence=evidence,
        )
        payload = {
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
                "jarvis_absorbable_report": absorbable_report,
            },
        }
        return repair_mojibake_data(payload, preserve_newlines=True)

    def build_absorbable_report(
        self,
        context: dict[str, Any],
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
        evidence_dict = _as_dict(evidence)
        task_info = _as_dict(context.get("task"))
        result_info = _as_dict(context.get("result"))
        jarvis_result = _as_dict(context.get("jarvis_result"))
        candidate_pool = [
            item for item in _as_list(context.get("candidate_pool"))
            if isinstance(item, dict)
        ]
        jarvis_quotas = _as_list(jarvis_result.get("quotas"))
        jarvis_top1 = _quota_snapshot(_as_dict(jarvis_quotas[0] if jarvis_quotas else {}))
        selected_source = list(suggested_quotas or []) or [jarvis_top1]
        selected_quota = _quota_snapshot(_as_dict(selected_source[0] if selected_source else {}))
        bill_text = _bill_text(result_info)
        current_rank = _candidate_rank(candidate_pool, jarvis_top1)
        selected_rank = _candidate_rank(candidate_pool, selected_quota)
        qmd_summary = _as_dict(evidence_dict.get("qmd_summary"))
        issue_types = _unique_strings(_as_list(evidence_dict.get("issue_types")))
        normalized_reason_codes = _unique_strings(reason_codes or [])
        keywords = _unique_strings([
            result_info.get("bill_name"),
            result_info.get("specialty"),
            selected_quota.get("quota_id"),
            selected_quota.get("name"),
        ])
        basis_points = _unique_strings([
            *[f"issue:{item}" for item in issue_types],
            *[f"reason:{item}" for item in normalized_reason_codes],
            f"decision:{_clean_str(decision_type)}" if _clean_str(decision_type) else "",
            f"error_type:{_clean_str(error_type)}" if _clean_str(error_type) else "",
            f"current_rank:{current_rank}" if current_rank else "",
            f"selected_rank:{selected_rank}" if selected_rank else "",
            f"qmd_hits:{int(qmd_summary.get('count') or 0)}" if qmd_summary else "",
        ])
        experience_record = {
            "province": _clean_str(task_info.get("province")),
            "specialty": _clean_str(result_info.get("specialty")),
            "bill_text": bill_text,
            "bill_name": _clean_str(result_info.get("bill_name")),
            "bill_desc": _clean_str(result_info.get("bill_description")),
            "bill_code": _clean_str(result_info.get("bill_code")),
            "bill_unit": _clean_str(result_info.get("bill_unit")) or _clean_str(selected_quota.get("unit")),
            "unit": _clean_str(selected_quota.get("unit")) or _clean_str(result_info.get("bill_unit")),
            "quota_ids": [selected_quota["quota_id"]] if selected_quota.get("quota_id") else [],
            "quota_names": [selected_quota["name"]] if selected_quota.get("name") else [],
            "final_quota_code": _clean_str(selected_quota.get("quota_id")),
            "final_quota_name": _clean_str(selected_quota.get("name")),
            "project_name": _clean_str(task_info.get("task_id")),
            "summary": _clean_str(note)[:300],
            "notes": _clean_str(note)[:300],
            "confidence": max(int(review_confidence or 0), 80),
        }
        report = {
            "schema_version": "openclaw_review_report.v1",
            "task_ref": {
                "task_id": _clean_str(task_info.get("task_id")),
                "province": _clean_str(task_info.get("province")),
                "mode": _clean_str(task_info.get("mode")),
            },
            "result_ref": {
                "result_id": _clean_str(result_info.get("result_id")),
                "bill_code": _clean_str(result_info.get("bill_code")),
                "specialty": _clean_str(result_info.get("specialty")),
            },
            "decision": {
                "decision_type": _clean_str(decision_type),
                "error_stage": _clean_str(error_stage),
                "error_type": _clean_str(error_type),
                "review_confidence": review_confidence,
                "retry_query": _clean_str(retry_query),
                "reason_codes": normalized_reason_codes,
            },
            "bill_context": {
                "bill_name": _clean_str(result_info.get("bill_name")),
                "bill_description": _clean_str(result_info.get("bill_description")),
                "bill_unit": _clean_str(result_info.get("bill_unit")),
                "sheet_name": _clean_str(result_info.get("sheet_name")),
                "section": _clean_str(result_info.get("section")),
                "bill_text": bill_text,
            },
            "jarvis_top1": jarvis_top1,
            "openclaw_top1": selected_quota,
            "candidate_snapshot": {
                "candidate_count": len(candidate_pool),
                "current_rank": current_rank,
                "selected_rank": selected_rank,
                "top_candidates": _compact_candidates(candidate_pool),
            },
            "judgment": {
                "basis_summary": _clean_str(note),
                "basis_points": basis_points,
                "issue_types": issue_types,
                "qmd_summary": qmd_summary,
            },
            "learning_record": experience_record,
            "promotion_hints": {
                "rule": {
                    "chapter": "OpenClaw Review Loop",
                    "section": _clean_str(error_type) or _clean_str(decision_type),
                    "rule_text": _clean_str(note),
                    "judgment_basis": _clean_str(note),
                    "core_knowledge_points": basis_points,
                    "exclusion_reasons": normalized_reason_codes,
                    "keywords": keywords,
                },
                "method": {
                    "category": _clean_str(result_info.get("bill_name")) or "OpenClaw Review",
                    "method_text": _clean_str(note),
                    "judgment_basis": _clean_str(note),
                    "keywords": keywords,
                    "pattern_keys": _unique_strings([
                        result_info.get("bill_name"),
                        result_info.get("specialty"),
                    ]),
                    "common_errors": "；".join(normalized_reason_codes),
                    "sample_count": 1,
                    "confirm_rate": round(max(float(review_confidence or 80) / 100.0, 0.7), 2),
                },
                "experience": experience_record,
            },
        }
        return repair_mojibake_data(report, preserve_newlines=True)
