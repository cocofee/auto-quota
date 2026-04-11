"""
Best-effort mapping from confirmed OpenClaw reviews into knowledge staging.

This service must never block the main review-confirm flow.
"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from src.knowledge_promotion_rules import (
    build_openclaw_promotion_candidates,
    classify_openclaw_audit_error,
)
from src.knowledge_staging import KnowledgeStaging


def _top_quota(items: list[dict] | None) -> dict[str, Any]:
    if not items:
        return {}
    first = items[0] or {}
    return first if isinstance(first, dict) else {}


def _merge_notes(*parts: str) -> str:
    return "\n".join(part.strip() for part in parts if str(part or "").strip()).strip()


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _clean_str(value: Any) -> str:
    return str(value or "").strip()


def _extract_absorbable_report(match_result) -> dict[str, Any]:
    payload = _as_dict(getattr(match_result, "openclaw_review_payload", None))
    return _as_dict(payload.get("jarvis_absorbable_report"))


def record_openclaw_approved_review(task, match_result, *, actor: str, review_note: str = "") -> dict[str, Any]:
    """
    Map a confirmed OpenClaw review into staging.

    Returns a best-effort result summary and should not raise on staging failures
    in the calling flow unless explicitly desired by the caller.
    """
    staging = KnowledgeStaging()

    original_quota = _top_quota(match_result.quotas or [])
    corrected_quota = _top_quota(match_result.corrected_quotas or match_result.openclaw_suggested_quotas or [])
    final_note = _merge_notes(match_result.openclaw_review_note, review_note, match_result.review_note)
    audit_rule = classify_openclaw_audit_error(match_result.match_source or "")
    report = _extract_absorbable_report(match_result)
    report_decision = _as_dict(report.get("decision"))
    report_judgment = _as_dict(report.get("judgment"))
    report_learning = _as_dict(report.get("learning_record"))
    reason_codes = [
        _clean_str(item)
        for item in _as_list(report_decision.get("reason_codes"))
        if _clean_str(item)
    ]
    root_cause_tags = list(dict.fromkeys([*audit_rule["root_cause_tags"], *reason_codes]))
    decision_basis = _clean_str(report_judgment.get("basis_summary")) or final_note[:500]
    fix_target_name = _clean_str(corrected_quota.get("name")) or _clean_str(report_learning.get("final_quota_name"))
    fix_target_code = _clean_str(corrected_quota.get("quota_id")) or _clean_str(report_learning.get("final_quota_code"))

    audit_id = staging.create_audit_error({
        "source_id": str(task.id),
        "source_type": "openclaw_review_confirm",
        "source_table": "match_results",
        "source_record_id": str(match_result.id),
        "owner": actor,
        "evidence_ref": f"task:{task.id}/result:{match_result.id}",
        "status": "active",
        "review_status": "approved",
        "reviewer": actor,
        "task_id": str(task.id),
        "result_id": str(match_result.id),
        "project_id": str(task.id),
        "province": task.province or "",
        "specialty": match_result.specialty or "",
        "bill_name": match_result.bill_name or "",
        "bill_desc": match_result.bill_description or "",
        "predicted_quota_code": original_quota.get("quota_id", ""),
        "predicted_quota_name": original_quota.get("name", ""),
        "corrected_quota_code": corrected_quota.get("quota_id", ""),
        "corrected_quota_name": corrected_quota.get("name", ""),
        "match_source": match_result.match_source or "",
        "error_type": audit_rule["error_type"],
        "error_level": "high",
        "root_cause": decision_basis[:500],
        "root_cause_tags": root_cause_tags,
        "fix_suggestion": f"override_to:{fix_target_name}({fix_target_code})".strip(),
        "decision_basis": decision_basis[:500],
        "requires_manual_followup": 0,
        "can_promote_rule": 1 if audit_rule["can_promote_rule"] else 0,
        "can_promote_method": 1 if audit_rule["can_promote_method"] else 0,
    })

    promotion_ids: list[int] = []
    candidates = build_openclaw_promotion_candidates(
        task_id=str(task.id),
        province=task.province or "",
        specialty=match_result.specialty or "",
        bill_name=match_result.bill_name or "",
        bill_desc=match_result.bill_description or "",
        match_source=match_result.match_source or "",
        original_quota=original_quota,
        corrected_quota=corrected_quota,
        final_note=final_note,
        audit_id=audit_id,
        report=report,
    )
    queued_layers: set[str] = set()
    for candidate in candidates:
        payload = dict(candidate)
        payload["owner"] = actor
        payload["evidence_ref"] = f"task:{task.id}/result:{match_result.id}"
        queued_layers.add(str(payload.get("target_layer", "")))
        promotion_ids.append(staging.enqueue_promotion(payload))

    return {
        "audit_error_id": audit_id,
        "promotion_id": promotion_ids[0] if promotion_ids else None,
        "promotion_ids": promotion_ids,
        "queued_rule": "RuleKnowledge" in queued_layers,
        "queued_method": "MethodCards" in queued_layers,
        "queued_experience": "ExperienceDB" in queued_layers,
    }


async def record_openclaw_approved_review_async(task, match_result, *, actor: str, review_note: str = "") -> dict[str, Any]:
    """Async wrapper for the best-effort staging mapping."""
    try:
        return await asyncio.to_thread(
            record_openclaw_approved_review,
            task,
            match_result,
            actor=actor,
            review_note=review_note,
        )
    except Exception as e:
        logger.warning(f"openclaw approved review -> staging mapping failed: {e}")
        return {
            "audit_error_id": None,
            "promotion_id": None,
            "promotion_ids": [],
            "queued_rule": False,
            "queued_method": False,
            "queued_experience": False,
            "error": str(e),
        }
