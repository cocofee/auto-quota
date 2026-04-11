"""
OpenClaw bridge API.

This router exposes a stable subset of auto-quota APIs for OpenClaw, plus the
"review draft + human second confirmation" workflow.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from app.api import quota_search as quota_search_api
from app.api import file_intake as file_intake_api
from app.api import reference as reference_api
from app.api import results as results_api
from app.api import tasks as tasks_api
from app.api.shared import get_user_task
from app.auth.openclaw import get_openclaw_read_user, get_openclaw_service_user
from app.auth.permissions import require_admin
from app.config import OPENCLAW_API_KEY, OPENCLAW_SERVICE_EMAIL, OPENCLAW_SERVICE_NICKNAME
from app.database import get_db
from app.models.openclaw_review_job import OpenClawReviewJob
from app.models.result import MatchResult
from app.models.task import Task
from app.models.user import User
from app.schemas.openclaw_review_job import (
    OpenClawReviewJobCreateRequest,
    OpenClawReviewJobResponse,
)
from app.schemas.result import (
    ConfirmResultsRequest,
    CorrectResultRequest,
    MatchResultResponse,
    OpenClawReviewConfirmRequest,
    OpenClawReviewDraftRequest,
    ResultListResponse,
)
from app.schemas.file_intake import (
    FileClassifyRequest,
    FileClassifyResponse,
    FileIntakeResponse,
    FileParseRequest,
    FileParseResponse,
    FileRouteRequest,
    FileRouteResponse,
)
from app.schemas.qmd import QMDSearchRequest, QMDSearchResponse
from app.schemas.reference import CompositePriceReferenceResponse, ItemPriceReferenceResponse
from app.schemas.task import TaskListResponse, TaskResponse
from app.services.openclaw_review_service import OpenClawReviewService
from app.services.qmd_service import get_default_qmd_service
from app.services.source_learning_service import SourceLearningService
from app.text_utils import repair_mojibake_data, repair_quota_name_loss
from pydantic import BaseModel, Field

router = APIRouter()
logger = logging.getLogger(__name__)
GREEN_THRESHOLD = results_api._GREEN_THRESHOLD
YELLOW_THRESHOLD = results_api._YELLOW_THRESHOLD


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _openclaw_policy_bucket(result_or_confidence) -> str:
    return results_api._resolve_light_status(result_or_confidence)


def _human_actor(user: User) -> str:
    return (getattr(user, "nickname", None) or getattr(user, "email", None) or str(user.id)).strip()


def _service_actor(user: User) -> str:
    return (getattr(user, "email", None) or getattr(user, "nickname", None) or "openclaw").strip()


def _merge_review_notes(*parts: str) -> str:
    merged = "\n".join(part.strip() for part in parts if part and part.strip())
    return merged[:500]


def _mask_key(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 10:
        return "*" * len(value)
    return f"{value[:4]}{'*' * (len(value) - 8)}{value[-4:]}"


def _normalize_openclaw_reason_codes(values: list[str] | None) -> list[str] | None:
    if not values:
        return None
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        normalized.append(item)
    return normalized or None


def _normalize_quota_items(items: Any) -> list[dict[str, Any]]:
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        quota = _normalize_quota_dict(item)
        if quota.get("quota_id") or quota.get("name"):
            normalized.append(quota)
    return normalized


def _normalize_external_feedback_payload(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    raw = repair_mojibake_data(dict(payload), preserve_newlines=True)
    final_quotas = _normalize_quota_items(raw.get("final_quotas") if raw.get("final_quotas") is not None else raw.get("final_quota"))
    adopt_raw = raw.get("adopt_openclaw")
    if isinstance(adopt_raw, bool):
        adopt_openclaw = adopt_raw
    else:
        adopt_openclaw = not bool(final_quotas)
    normalized = {
        **raw,
        "protocol_version": str(raw.get("protocol_version") or "lobster_review_feedback.v1").strip(),
        "source": str(raw.get("source") or raw.get("audit_source") or "external_audit").strip(),
        "adopt_openclaw": adopt_openclaw,
        "final_quotas": final_quotas,
        "manual_reason_codes": _normalize_feedback_reason_codes(raw),
        "manual_note": str(raw.get("manual_note") or raw.get("note") or "").strip(),
        "promotion_decision": str(
            raw.get("promotion_decision")
            or ("follow_openclaw" if adopt_openclaw else "manual_override")
        ).strip(),
    }
    normalized.pop("final_quota", None)
    return normalized


def _extract_absorbable_report(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    repaired = repair_mojibake_data(dict(payload), preserve_newlines=True)
    report = repaired.get("jarvis_absorbable_report")
    return report if isinstance(report, dict) else {}


def _normalize_feedback_reason_codes(payload: dict[str, Any]) -> list[str]:
    raw_values = (
        payload.get("manual_reason_codes")
        if payload.get("manual_reason_codes") is not None
        else payload.get("reason_codes")
    )
    if raw_values is None:
        raw_values = payload.get("error_tags")
    if isinstance(raw_values, str):
        raw_values = [raw_values]
    if not isinstance(raw_values, list):
        return []
    normalized = _normalize_openclaw_reason_codes([str(item) for item in raw_values])
    return normalized or []


def _normalize_feedback_final_quotas(
    match_result: MatchResult,
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    raw_final = payload.get("final_quotas")
    if raw_final is None:
        raw_final = payload.get("final_quota")
    if isinstance(raw_final, dict):
        raw_items = [raw_final]
    elif isinstance(raw_final, list):
        raw_items = raw_final
    else:
        raw_items = []
    normalized = [
        _normalize_quota_dict(item)
        for item in raw_items
        if isinstance(item, dict)
    ]
    repaired, _ = _repair_suggested_quotas_from_result(match_result, normalized)
    return repaired or []


def _normalize_human_feedback_payload(
    match_result: MatchResult,
    payload: dict[str, Any] | None,
    *,
    actor: str,
    decision: str,
) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None

    raw = repair_mojibake_data(dict(payload), preserve_newlines=True)
    final_quotas = _normalize_feedback_final_quotas(match_result, raw)
    adopt_raw = raw.get("adopt_openclaw")
    if isinstance(adopt_raw, bool):
        adopt_openclaw = adopt_raw
    else:
        adopt_openclaw = not bool(final_quotas)

    manual_note = str(raw.get("manual_note") or raw.get("note") or "").strip()
    normalized = {
        **raw,
        "protocol_version": str(raw.get("protocol_version") or "lobster_review_feedback.v1").strip(),
        "source": str(raw.get("source") or raw.get("audit_source") or "external_audit").strip(),
        "decision": decision,
        "reviewer": actor,
        "adopt_openclaw": adopt_openclaw,
        "final_quotas": final_quotas,
        "manual_reason_codes": _normalize_feedback_reason_codes(raw),
        "manual_note": manual_note,
        "promotion_decision": str(
            raw.get("promotion_decision")
            or ("follow_openclaw" if adopt_openclaw else "manual_override")
        ).strip(),
    }
    if "final_quota" in normalized:
        normalized.pop("final_quota", None)
    return normalized


def _merge_feedback_into_absorbable_report(
    report: dict[str, Any] | None,
    *,
    feedback: dict[str, Any] | None,
    final_quotas: list[dict[str, Any]] | None,
    actor: str,
) -> dict[str, Any]:
    base = repair_mojibake_data(dict(report or {}), preserve_newlines=True)
    if not base:
        return {}

    manual = dict(feedback or {})
    final_quota = _normalize_quota_dict(final_quotas[0]) if final_quotas else {}
    decision_block = dict(base.get("decision") or {})
    decision_block["confirmed_by"] = actor
    decision_block["manual_reason_codes"] = list(manual.get("manual_reason_codes") or [])
    decision_block["adopt_openclaw"] = bool(manual.get("adopt_openclaw", True))
    if manual.get("promotion_decision"):
        decision_block["promotion_decision"] = str(manual.get("promotion_decision") or "").strip()
    if final_quota:
        decision_block["final_quota_id"] = final_quota.get("quota_id", "")
        decision_block["final_quota_name"] = final_quota.get("name", "")
    base["decision"] = decision_block

    judgment = dict(base.get("judgment") or {})
    if manual.get("manual_note"):
        judgment["basis_summary"] = str(manual.get("manual_note") or "").strip()
    basis_points = [
        str(item).strip()
        for item in list(judgment.get("basis_points") or [])
        if str(item).strip()
    ]
    for code in list(manual.get("manual_reason_codes") or []):
        point = f"manual_reason:{code}"
        if point not in basis_points:
            basis_points.append(point)
    if manual.get("source"):
        point = f"manual_source:{str(manual.get('source') or '').strip()}"
        if point not in basis_points:
            basis_points.append(point)
    judgment["basis_points"] = basis_points
    base["judgment"] = judgment

    if final_quota:
        base["final_top1"] = final_quota
        learning = dict(base.get("learning_record") or {})
        learning["quota_ids"] = [final_quota.get("quota_id", "")] if final_quota.get("quota_id") else []
        learning["quota_names"] = [final_quota.get("name", "")] if final_quota.get("name") else []
        learning["final_quota_code"] = final_quota.get("quota_id", "")
        learning["final_quota_name"] = final_quota.get("name", "")
        learning["bill_unit"] = learning.get("bill_unit") or final_quota.get("unit", "")
        learning["unit"] = learning.get("unit") or final_quota.get("unit", "")
        if manual.get("manual_note"):
            learning["summary"] = str(manual.get("manual_note") or "").strip()[:300]
            learning["notes"] = str(manual.get("manual_note") or "").strip()[:300]
        base["learning_record"] = learning

        hints = dict(base.get("promotion_hints") or {})
        experience = dict(hints.get("experience") or {})
        experience["quota_ids"] = [final_quota.get("quota_id", "")] if final_quota.get("quota_id") else []
        experience["quota_names"] = [final_quota.get("name", "")] if final_quota.get("name") else []
        experience["final_quota_code"] = final_quota.get("quota_id", "")
        experience["final_quota_name"] = final_quota.get("name", "")
        experience["bill_unit"] = experience.get("bill_unit") or final_quota.get("unit", "")
        experience["unit"] = experience.get("unit") or final_quota.get("unit", "")
        if manual.get("manual_note"):
            experience["summary"] = str(manual.get("manual_note") or "").strip()[:300]
            experience["notes"] = str(manual.get("manual_note") or "").strip()[:300]
        hints["experience"] = experience
        base["promotion_hints"] = hints

    base["manual_review"] = manual
    return repair_mojibake_data(base, preserve_newlines=True)


def _analyze_openclaw_audit_report(
    req: OpenClawAuditReportAnalyzeRequest,
) -> OpenClawAuditReportAnalyzeResponse:
    review_payload = repair_mojibake_data(dict(req.openclaw_review_payload or {}), preserve_newlines=True)
    report = _extract_absorbable_report(review_payload)
    feedback = _normalize_external_feedback_payload(req.human_feedback_payload)

    current_quota = _normalize_quota_dict(req.current_quota or {})
    if not (current_quota.get("quota_id") or current_quota.get("name")):
        current_quota = _normalize_quota_dict((report.get("jarvis_top1") or {}) if isinstance(report, dict) else {})
    if not (current_quota.get("quota_id") or current_quota.get("name")):
        current_quota = {}

    suggested_quotas = _normalize_quota_items(req.suggested_quotas or review_payload.get("suggested_quotas"))
    final_quota = {}
    if feedback and not bool(feedback.get("adopt_openclaw", True)):
        final_quota = _normalize_quota_dict((feedback.get("final_quotas") or [{}])[0])
    elif isinstance(report.get("final_top1"), dict):
        final_quota = _normalize_quota_dict(report.get("final_top1") or {})
    elif isinstance(report.get("openclaw_top1"), dict):
        final_quota = _normalize_quota_dict(report.get("openclaw_top1") or {})
    elif suggested_quotas:
        final_quota = _normalize_quota_dict(suggested_quotas[0])

    report_decision = dict(report.get("decision") or {}) if isinstance(report, dict) else {}
    report_judgment = dict(report.get("judgment") or {}) if isinstance(report, dict) else {}
    reason_codes = _normalize_openclaw_reason_codes(
        list(feedback.get("manual_reason_codes") or [])
        if feedback
        else (
            list(report_decision.get("manual_reason_codes") or [])
            or list(report_decision.get("reason_codes") or [])
            or list(req.openclaw_reason_codes or [])
        )
    ) or []
    note = (
        str((feedback or {}).get("manual_note") or "").strip()
        or str(report_judgment.get("basis_summary") or "").strip()
        or str(req.openclaw_review_note or "").strip()
    )
    confirmed = bool(feedback) or bool(report_decision.get("confirmed_by")) or str(req.openclaw_review_status or "").strip().lower() == "applied"

    missing_fields: list[str] = []
    why: list[str] = []

    if final_quota.get("quota_id") or final_quota.get("name"):
        why.append("contains final quota outcome")
    else:
        missing_fields.append("final_quota")

    if reason_codes:
        why.append("contains structured reason codes")
    else:
        missing_fields.append("reason_codes")

    if note:
        why.append("contains final decision note")
    else:
        missing_fields.append("manual_note_or_review_note")

    if confirmed:
        why.append("has confirmed human or applied final state")
    else:
        missing_fields.append("confirmed_final_state")

    if current_quota.get("quota_id") or current_quota.get("name"):
        why.append("contains current Jarvis quota context")
    else:
        missing_fields.append("current_quota")

    if not missing_fields:
        absorbability: Literal["absorbable", "partial", "not_absorbable"] = "absorbable"
    elif "final_quota" not in missing_fields and ("reason_codes" not in missing_fields or "manual_note_or_review_note" not in missing_fields):
        absorbability = "partial"
    else:
        absorbability = "not_absorbable"

    learning_targets: list[str] = []
    final_id = str(final_quota.get("quota_id") or "").strip()
    current_id = str(current_quota.get("quota_id") or "").strip()
    if final_id or final_quota.get("name"):
        learning_targets.append("ExperienceDB")
    if (current_id and final_id and current_id != final_id) or (
        current_quota.get("name") and final_quota.get("name") and current_quota.get("name") != final_quota.get("name")
    ):
        learning_targets.append("audit_errors")
    if reason_codes and note:
        learning_targets.append("promotion_queue")
    if not learning_targets:
        learning_targets.append("manual_only")

    if "ExperienceDB" in learning_targets:
        primary_target: Literal["ExperienceDB", "audit_errors", "promotion_queue", "manual_only"] = "ExperienceDB"
    elif "audit_errors" in learning_targets:
        primary_target = "audit_errors"
    elif "promotion_queue" in learning_targets:
        primary_target = "promotion_queue"
    else:
        primary_target = "manual_only"

    return OpenClawAuditReportAnalyzeResponse(
        absorbability=absorbability,
        primary_target=primary_target,
        learning_targets=learning_targets,
        current_quota=current_quota or None,
        final_quota=final_quota or None,
        reason_codes=reason_codes,
        missing_fields=missing_fields,
        why=why,
        normalized_feedback_payload=feedback,
        normalized_absorbable_report=report or None,
    )


def _build_openclaw_review_payload(
    req: OpenClawReviewDraftRequest,
    *,
    suggested_quotas: list[dict],
) -> dict:
    explicit_payload = dict(req.openclaw_review_payload or {})
    payload = {
        **explicit_payload,
        "decision_type": str(req.openclaw_decision_type or explicit_payload.get("decision_type") or "").strip(),
        "error_stage": str(req.openclaw_error_stage or explicit_payload.get("error_stage") or "").strip(),
        "error_type": str(req.openclaw_error_type or explicit_payload.get("error_type") or "").strip(),
        "retry_query": str(req.openclaw_retry_query or explicit_payload.get("retry_query") or "").strip(),
        "reason_codes": _normalize_openclaw_reason_codes(
            req.openclaw_reason_codes
            if req.openclaw_reason_codes is not None
            else explicit_payload.get("reason_codes")
        ) or [],
        "review_confidence": req.openclaw_review_confidence,
        "note": str(req.openclaw_review_note or explicit_payload.get("note") or "").strip(),
        "suggested_quotas": suggested_quotas,
    }
    return payload


def _get_staging():
    from src.knowledge_staging import KnowledgeStaging

    return KnowledgeStaging()

def _get_source_learning_service():
    return SourceLearningService()


def _resolve_promotion_target(
    candidate_type: str,
    target_layer: str | None,
) -> str:
    expected = PROMOTION_TARGET_BY_TYPE.get(candidate_type)
    if not expected:
        raise HTTPException(status_code=422, detail="unsupported candidate_type")
    if target_layer and target_layer != expected:
        raise HTTPException(
            status_code=422,
            detail=f"candidate_type={candidate_type} 只能进入 {expected}",
        )
    return target_layer or expected


class OpenClawKeyStatusResponse(BaseModel):
    configured: bool
    masked_key: str = ""
    service_email: str
    service_nickname: str
    openapi_url: str
    public_path: str
    sync_targets: list[str]
    update_hint: str


class OpenClawKeySuggestionResponse(BaseModel):
    suggested_key: str
    env_name: str
    sync_targets: list[str]
    manifest_paths: list[str]
    rollout_steps: list[str]


class OpenClawKeySuggestionRequest(BaseModel):
    prefix: str = Field(default="oc_", max_length=24)


class OpenClawPromotionCardCreateRequest(BaseModel):
    card_id: str = Field(min_length=1, description="OpenClaw 侧独立卡片 ID")
    candidate_type: Literal["rule", "method", "universal", "experience"]
    target_layer: Literal["RuleKnowledge", "MethodCards", "UniversalKB", "ExperienceDB"] | None = None
    candidate_title: str = Field(min_length=1, description="卡片标题")
    candidate_summary: str = Field(default="", description="卡片摘要")
    candidate_payload: dict[str, Any] = Field(default_factory=dict, description="结构化卡片内容")
    evidence_ref: str = Field(default="", description="证据链接或引用")
    owner: str = Field(default="", description="来源作者")
    priority: int = Field(default=50, ge=0, le=100)
    approval_required: bool = True
    source_type: str = Field(default="openclaw_manual_card")


class OpenClawAuditReportAnalyzeRequest(BaseModel):
    openclaw_review_status: str = ""
    openclaw_decision_type: str = ""
    openclaw_review_note: str = ""
    openclaw_reason_codes: list[str] | None = None
    current_quota: dict[str, Any] | None = None
    suggested_quotas: list[dict[str, Any]] | None = None
    openclaw_review_payload: dict[str, Any] | None = None
    human_feedback_payload: dict[str, Any] | None = None


class OpenClawAuditReportAnalyzeResponse(BaseModel):
    absorbability: Literal["absorbable", "partial", "not_absorbable"]
    primary_target: Literal["ExperienceDB", "audit_errors", "promotion_queue", "manual_only"]
    learning_targets: list[str] = Field(default_factory=list)
    current_quota: dict[str, Any] | None = None
    final_quota: dict[str, Any] | None = None
    reason_codes: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    why: list[str] = Field(default_factory=list)
    normalized_feedback_payload: dict[str, Any] | None = None
    normalized_absorbable_report: dict[str, Any] | None = None


class OpenClawPromotionCardCreateResponse(BaseModel):
    id: int
    source_table: str
    source_record_id: str
    target_layer: str
    status: str
    review_status: str


class OpenClawSourcePackSummaryResponse(BaseModel):
    source_id: str
    title: str = ""
    summary: str = ""
    source_kind: str = ""
    province: str = ""
    specialty: str = ""
    created_at: str = ""
    confidence: int = 0
    full_text_path: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class OpenClawSourcePackListResponse(BaseModel):
    items: list[OpenClawSourcePackSummaryResponse] = Field(default_factory=list)
    total: int


class OpenClawSourceLearningRunRequest(BaseModel):
    dry_run: bool = False
    llm_type: str | None = Field(default=None, max_length=32)
    chunk_size: int = Field(default=1800, ge=200, le=8000)
    overlap: int = Field(default=240, ge=0, le=2000)
    max_chunks: int = Field(default=24, ge=1, le=100)


class OpenClawSourceLearningRunResponse(BaseModel):
    source_id: str
    title: str = ""
    chunks: int
    raw_candidates: int
    merged_candidates: int
    staged: int
    staged_ids: list[int] = Field(default_factory=list)
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    pack: OpenClawSourcePackSummaryResponse | None = None


PROMOTION_TARGET_BY_TYPE = {
    "rule": "RuleKnowledge",
    "method": "MethodCards",
    "universal": "UniversalKB",
    "experience": "ExperienceDB",
}
OPENCLAW_MANUAL_CARD_SOURCE_TABLE = "openclaw_manual_cards"
OPENCLAW_REVIEW_SERVICE = OpenClawReviewService()


class OpenClawAutoReviewRequest(BaseModel):
    review_job_id: uuid.UUID | None = None


class OpenClawBatchAutoReviewRequest(BaseModel):
    review_job_id: uuid.UUID | None = None
    scope: Literal["need_review", "yellow_red_pending"] | None = Field(default="yellow_red_pending")
    limit: int | None = Field(default=None, ge=1, le=1000)


class OpenClawAutoReviewResponse(BaseModel):
    result_id: uuid.UUID
    source_task_id: uuid.UUID
    review_job_id: uuid.UUID | None = None
    status: Literal["drafted", "skipped"]
    decision_type: str | None = None
    openclaw_review_status: str
    reviewable: bool
    note: str = ""


class OpenClawBatchAutoReviewResponse(BaseModel):
    review_job_id: uuid.UUID | None = None
    source_task_id: uuid.UUID
    scope: str
    total_candidates: int
    drafted_count: int
    skipped_count: int
    failed_count: int
    processed_result_ids: list[uuid.UUID] = Field(default_factory=list)


def _build_result_list_response(items: list[MatchResult]) -> ResultListResponse:
    total = len(items)
    high_conf = sum(1 for item in items if results_api._resolve_light_status(item) == "green")
    mid_conf = sum(1 for item in items if results_api._resolve_light_status(item) == "yellow")
    low_conf = sum(1 for item in items if results_api._resolve_light_status(item) == "red")
    no_match = sum(1 for item in items if not item.quotas)
    confirmed = sum(1 for item in items if item.review_status == "confirmed")
    corrected = sum(1 for item in items if item.review_status == "corrected")
    review_pending = sum(
        1
        for item in items
        if _is_reviewed_pending_confirm(item)
    )

    return ResultListResponse(
        items=[results_api._to_result_response(item) for item in items],
        total=total,
        summary={
            "total": total,
            "high_confidence": high_conf,
            "mid_confidence": mid_conf,
            "low_confidence": low_conf,
            "no_match": no_match,
            "confirmed": confirmed,
            "corrected": corrected,
            "pending": total - confirmed - corrected,
            "openclaw_review_pending": review_pending,
        },
    )


async def _get_match_result(
    *,
    task_id: uuid.UUID,
    result_id: uuid.UUID,
    db: AsyncSession,
    owner: User,
) -> tuple[object, MatchResult]:
    task = await get_user_task(task_id, owner, db)
    result = await db.execute(
        select(MatchResult).where(
            MatchResult.id == result_id,
            MatchResult.task_id == task_id,
        )
    )
    match_result = result.scalar_one_or_none()
    if not match_result:
        raise HTTPException(status_code=404, detail="结果不存在")
    return task, match_result


async def _collect_green_result_ids(
    *,
    task_id: uuid.UUID,
    db: AsyncSession,
    service_user: User,
) -> list[uuid.UUID]:
    await get_user_task(task_id, service_user, db)
    result = await db.execute(
        select(
            MatchResult.id,
            MatchResult.light_status,
            MatchResult.confidence_score,
            MatchResult.confidence,
            MatchResult.review_status,
        ).where(MatchResult.task_id == task_id)
    )
    ids: list[uuid.UUID] = []
    for row in result.all():
        if row.review_status in {"confirmed", "corrected"}:
            continue
        if _openclaw_policy_bucket(row) == "green":
            ids.append(row.id)
    return ids


def _ensure_openclaw_reviewable(bucket: str) -> None:
    if bucket == "red":
        return
    if False and bucket == "red":
        raise HTTPException(
            status_code=409,
            detail=f"当前结果为红灯(<{YELLOW_THRESHOLD})，保持现有规则不变，OpenClaw 不能提交审核建议。",
        )
    if bucket == "green":
        raise HTTPException(
            status_code=409,
            detail=f"当前结果为绿灯(>={GREEN_THRESHOLD})，保持现有规则不变，应直接走确认而不是提交审核建议。",
        )


async def _save_review_draft(
    *,
    task_id: uuid.UUID,
    result_id: uuid.UUID,
    req: OpenClawReviewDraftRequest,
    db: AsyncSession,
    service_user: User,
) -> MatchResultResponse:
    _, match_result = await _get_match_result(
        task_id=task_id,
        result_id=result_id,
        db=db,
        owner=service_user,
    )
    response = await _apply_review_draft(
        match_result=match_result,
        req=req,
        service_user=service_user,
        enforce_bucket_policy=True,
    )
    await db.flush()
    return response


def _is_pending_formal_result(result_or_row) -> bool:
    review_status = str(getattr(result_or_row, "review_status", "") or "").strip().lower()
    return review_status not in {"confirmed", "corrected"}


def _has_openclaw_review_draft(result_or_row) -> bool:
    decision_type = str(getattr(result_or_row, "openclaw_decision_type", "") or "").strip()
    if decision_type:
        return True
    suggested = getattr(result_or_row, "openclaw_suggested_quotas", None)
    if isinstance(suggested, list) and suggested:
        return True
    payload = getattr(result_or_row, "openclaw_review_payload", None)
    return isinstance(payload, dict) and bool(payload)


def _normalized_openclaw_review_status(result_or_row) -> str:
    review_status = str(getattr(result_or_row, "openclaw_review_status", "") or "").strip().lower()
    if review_status not in {"pending", "reviewed", "applied", "rejected"}:
        review_status = "pending"
    if review_status == "pending" and _has_openclaw_review_draft(result_or_row):
        return "reviewed"
    return review_status


def _normalized_openclaw_confirm_status(result_or_row) -> str:
    confirm_status = str(
        getattr(result_or_row, "openclaw_review_confirm_status", "") or ""
    ).strip().lower()
    if confirm_status not in {"pending", "approved", "rejected"}:
        confirm_status = "pending"
    return confirm_status


def _is_reviewed_pending_confirm(result_or_row) -> bool:
    review_status = _normalized_openclaw_review_status(result_or_row)
    confirm_status = _normalized_openclaw_confirm_status(result_or_row)
    return review_status == "reviewed" and confirm_status == "pending"


def _matches_review_job_scope(result_or_row, scope: str) -> bool:
    pending_formal = _is_pending_formal_result(result_or_row)
    yellow_red_pending = pending_formal and _openclaw_policy_bucket(result_or_row) in {
        "yellow",
        "red",
    }
    if scope == "yellow_red_pending":
        return yellow_red_pending
    return yellow_red_pending or _is_reviewed_pending_confirm(result_or_row)


def _review_job_scope_label(scope: str) -> str:
    if scope == "yellow_red_pending":
        return "yellow/red pending formal results"
    return "results that still need OpenClaw attention"


async def _get_review_job_source_task(
    *,
    source_task_id: uuid.UUID,
    db: AsyncSession,
) -> Task:
    result = await db.execute(select(Task).where(Task.id == source_task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="source task not found")
    if str(task.status or "").strip().lower() != "completed":
        raise HTTPException(
            status_code=409,
            detail=f"source task status must be completed, current={task.status}",
        )
    return task


async def _ensure_no_active_review_job(
    *,
    source_task_id: uuid.UUID,
    scope: str,
    db: AsyncSession,
) -> None:
    result = await db.execute(
        select(OpenClawReviewJob)
        .where(
            OpenClawReviewJob.source_task_id == source_task_id,
            OpenClawReviewJob.scope == scope,
            OpenClawReviewJob.status.in_(["ready", "running"]),
        )
        .order_by(OpenClawReviewJob.created_at.desc())
    )
    existing = result.scalars().first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"active review job already exists: {existing.id}",
        )


async def _summarize_review_job_source_task(
    *,
    task: Task,
    scope: str,
    db: AsyncSession,
) -> dict[str, Any]:
    result = await db.execute(
        select(
            MatchResult.id,
            MatchResult.light_status,
            MatchResult.confidence_score,
            MatchResult.confidence,
            MatchResult.review_status,
            MatchResult.openclaw_review_status,
            MatchResult.openclaw_review_confirm_status,
        ).where(MatchResult.task_id == task.id)
    )
    rows = result.all()
    if not rows:
        raise HTTPException(status_code=409, detail="source task has no results")

    total_results = len(rows)
    pending_results = 0
    green_count = 0
    yellow_count = 0
    red_count = 0
    reviewed_pending_count = 0
    reviewable_results = 0

    for row in rows:
        bucket = _openclaw_policy_bucket(row)
        if bucket == "green":
            green_count += 1
        elif bucket == "yellow":
            yellow_count += 1
        else:
            red_count += 1

        if _is_pending_formal_result(row):
            pending_results += 1
        if _is_reviewed_pending_confirm(row):
            reviewed_pending_count += 1
        if _matches_review_job_scope(row, scope):
            reviewable_results += 1

    return {
        "total_results": total_results,
        "pending_results": pending_results,
        "reviewable_results": reviewable_results,
        "green_count": green_count,
        "yellow_count": yellow_count,
        "red_count": red_count,
        "reviewed_pending_count": reviewed_pending_count,
        "summary": {
            "scope": scope,
            "scope_label": _review_job_scope_label(scope),
            "source_task": {
                "task_id": str(task.id),
                "name": str(task.name or "").strip(),
                "province": str(task.province or "").strip(),
                "status": str(task.status or "").strip(),
                "mode": str(task.mode or "").strip(),
                "original_filename": str(task.original_filename or "").strip(),
            },
            "counts": {
                "total_results": total_results,
                "pending_results": pending_results,
                "reviewable_results": reviewable_results,
                "green_count": green_count,
                "yellow_count": yellow_count,
                "red_count": red_count,
                "reviewed_pending_count": reviewed_pending_count,
            },
        },
    }


def _is_openclaw_auto_review_pending(result_or_row) -> bool:
    return _normalized_openclaw_review_status(result_or_row) == "pending"


def _issue_list_from_final_validation(final_validation: dict[str, Any]) -> list[dict[str, Any]]:
    issues = final_validation.get("issues") if isinstance(final_validation, dict) else None
    return [item for item in (issues or []) if isinstance(item, dict)]


def _map_issue_type_to_openclaw_error(issue_type: str) -> str:
    mapping = {
        "category_mismatch": "wrong_family",
        "anchor_conflict": "wrong_family",
        "unit_conflict": "wrong_param",
        "param_conflict": "wrong_param",
        "book_conflict": "wrong_book",
        "ambiguity_review": "low_confidence_override",
        "price_mismatch": "low_confidence_override",
        "missing_candidate": "missing_candidate",
        "synonym_gap": "synonym_gap",
    }
    return mapping.get(issue_type, "unknown")


def _pick_primary_error_type(issue_types: list[str]) -> str:
    priority = [
        "category_mismatch",
        "anchor_conflict",
        "book_conflict",
        "unit_conflict",
        "param_conflict",
        "missing_candidate",
        "synonym_gap",
        "ambiguity_review",
        "price_mismatch",
    ]
    issue_set = {str(item or "").strip() for item in issue_types if str(item or "").strip()}
    for item in priority:
        if item in issue_set:
            return _map_issue_type_to_openclaw_error(item)
    return _map_issue_type_to_openclaw_error(issue_types[0]) if issue_types else "unknown"


def _infer_openclaw_error_stage(
    *,
    final_validation: dict[str, Any],
    final_review_correction: dict[str, Any],
    reasoning_summary: dict[str, Any],
) -> str:
    if final_validation or final_review_correction:
        return "final_validator"
    if reasoning_summary.get("engaged"):
        return "arbiter"
    return "unknown"


def _candidate_to_quota(candidate: dict[str, Any]) -> dict[str, Any]:
    quota = {
        "quota_id": str(candidate.get("quota_id") or "").strip(),
        "name": str(candidate.get("name") or "").strip(),
        "unit": str(candidate.get("unit") or "").strip(),
    }
    for key in ("source", "reason", "param_score", "rerank_score"):
        value = candidate.get(key)
        if value not in (None, ""):
            quota[key] = value
    return quota


def _normalize_quota_dict(quota: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        "quota_id": str(quota.get("quota_id") or "").strip(),
        "name": str(quota.get("name") or "").strip(),
        "unit": str(quota.get("unit") or "").strip(),
    }
    for key in ("source", "reason", "param_score", "rerank_score"):
        value = quota.get(key)
        if value not in (None, ""):
            normalized[key] = value
    return normalized


def _repair_suggested_quotas_from_result(
    match_result: MatchResult,
    suggested_quotas: list[dict] | None,
) -> tuple[list[dict], bool]:
    repaired, changed = repair_quota_name_loss(
        suggested_quotas or [],
        match_result.alternatives or [],
        match_result.corrected_quotas or [],
        match_result.quotas or [],
        preserve_newlines=True,
    )
    return repaired or [], changed


def _find_candidate_by_ref(
    *,
    candidate_pool: list[dict[str, Any]],
    quotas: list[dict[str, Any]],
    target_quota_id: str,
    target_name: str,
) -> dict[str, Any] | None:
    target_quota_id = str(target_quota_id or "").strip()
    target_name = str(target_name or "").strip()
    for item in candidate_pool:
        if not isinstance(item, dict):
            continue
        if target_quota_id and str(item.get("quota_id") or "").strip() == target_quota_id:
            return _candidate_to_quota(item)
        if target_name and str(item.get("name") or "").strip() == target_name:
            return _candidate_to_quota(item)
    for item in quotas:
        if not isinstance(item, dict):
            continue
        if target_quota_id and str(item.get("quota_id") or "").strip() == target_quota_id:
            return _normalize_quota_dict(item)
        if target_name and str(item.get("name") or "").strip() == target_name:
            return _normalize_quota_dict(item)
    return None


def _reason_codes_for_auto_review(
    *,
    bucket: str,
    issue_types: list[str],
    reasoning_summary: dict[str, Any],
    final_review_correction: dict[str, Any],
    has_candidate_pool: bool,
    has_current_quota: bool,
    decision_type: str | None = None,
    jarvis_problem_detected: bool = False,
) -> list[str]:
    codes: list[str] = []
    if bucket:
        codes.append(f"light_{bucket}")
    for issue_type in issue_types[:3]:
        item = str(issue_type or "").strip()
        if item:
            codes.append(item)
    if reasoning_summary.get("engaged"):
        codes.append("reasoning_engaged")
    if final_review_correction:
        codes.append("final_review_correction")
    if not has_current_quota:
        codes.append("jarvis_missing_top1")
    if has_candidate_pool:
        codes.append("candidate_pool_available")
    if jarvis_problem_detected:
        codes.append("jarvis_problem_detected")
    if decision_type == "agree" and bucket != "green":
        codes.append("jarvis_top1_verified")
    if decision_type in {"candidate_pool_insufficient", "abstain"}:
        codes.append("needs_manual_gate")
    elif decision_type and decision_type != "agree":
        codes.append("jarvis_top1_unverified")
    deduped: list[str] = []
    seen: set[str] = set()
    for code in codes:
        if not code or code in seen:
            continue
        seen.add(code)
        deduped.append(code)
    return deduped


def _should_retry_search_then_select(
    *,
    issue_types: list[str],
    current_quotas: list[dict[str, Any]],
    candidate_pool: list[dict[str, Any]],
) -> bool:
    if not current_quotas:
        return False
    issue_set = {str(item or "").strip() for item in issue_types if str(item or "").strip()}
    if not issue_set:
        return False
    has_family_conflict = bool({"category_mismatch", "anchor_conflict"} & issue_set)
    has_unit_or_param_conflict = bool({"unit_conflict", "param_conflict", "book_conflict"} & issue_set)
    return has_family_conflict and has_unit_or_param_conflict and bool(candidate_pool)


def _normalize_review_text(*parts: Any) -> str:
    text = " ".join(str(part or "") for part in parts if str(part or "").strip())
    return text.lower().replace(" ", "")


def _contains_any_keyword(text: str, keywords: set[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def _is_non_boq_entry(task: Task, match_result: MatchResult) -> bool:
    bill_name = str(getattr(match_result, "bill_name", "") or "").strip()
    bill_desc = str(getattr(match_result, "bill_description", "") or "").strip()
    sheet_name = str(getattr(match_result, "sheet_name", "") or "").strip()
    bill_code = str(getattr(match_result, "bill_code", "") or "").strip()
    combined = _normalize_review_text(bill_name, bill_desc, sheet_name, getattr(task, "name", ""))

    non_boq_keywords = {
        "主要材料和工程设备选用表",
        "主要材料表",
        "工程设备选用表",
        "材料表",
        "设备表",
        "主材表",
        "设备选用表",
        "信息价",
        "暂估价材料表",
    }
    if any(keyword in combined for keyword in non_boq_keywords):
        return True

    bad_names = {"√", "分部分项工程1条", "其他材料费"}
    if bill_name in bad_names:
        return True

    if bill_name and len(bill_name) <= 2 and any(token in bill_name for token in {"√", "*", "-"}):
        return True

    if not bill_code and any(token in combined for token in {"表-23", "表23", "主材", "设备选用"}):
        return True

    return False


def _detect_system_conflict(*, task: Task, match_result: MatchResult, quota: dict[str, Any] | None) -> list[str]:
    if not quota:
        return []

    bill_text = _normalize_review_text(
        getattr(match_result, "bill_name", ""),
        getattr(match_result, "bill_description", ""),
    )
    if not bill_text:
        bill_text = _normalize_review_text(
        getattr(task, "name", ""),
        getattr(task, "province", ""),
        getattr(match_result, "sheet_name", ""),
        getattr(match_result, "section", ""),
        getattr(match_result, "specialty", ""),
        )
    quota_text = _normalize_review_text(
        quota.get("quota_id", ""),
        quota.get("name", ""),
        quota.get("unit", ""),
    )

    system_pairs = [
        ("water_supply", {"给水", "给排水", "ppr", "pp-r", "塑料给水管", "给水塑料管", "生活给水"}, {"采暖", "采暖管道", "采暖系统", "散热器"}),
        ("heating", {"采暖", "采暖管道", "采暖系统", "散热器"}, {"给水", "给排水", "给水管", "给水塑料管", "生活给水"}),
        ("drainage", {"排水", "雨水", "污水", "废水", "污废水"}, {"给水", "采暖", "生活给水"}),
        ("ventilation", {"通风", "风管", "风口", "百叶", "散流器", "防火阀", "风阀"}, {"给水", "排水", "采暖", "管道"}),
        ("firefighting", {"消防", "喷淋", "消火栓", "报警阀"}, {"给水", "排水", "采暖"}),
        ("electrical", {"电缆", "桥架", "配管", "配线", "灯具", "开关", "插座"}, {"给水", "排水", "采暖", "风管", "风口"}),
    ]

    conflicts: list[str] = []
    for label, bill_keywords, quota_blockers in system_pairs:
        if _contains_any_keyword(bill_text, bill_keywords) and _contains_any_keyword(quota_text, quota_blockers):
            conflicts.append(f"system_conflict:{label}")

    return conflicts


def _classify_review_object_family(text: str) -> str:
    normalized = _normalize_review_text(text)
    if not normalized:
        return ""

    family_rules = [
        ("pipe_support", {"成品管卡", "管卡", "托钩", "支吊架", "吊架", "吊卡"}),
        ("pipe_sleeve", {"防水套管", "刚性防水套管", "柔性防水套管", "穿楼板套管", "穿墙套管", "套管"}),
        ("water_meter", {"磁卡水表", "ic卡水表", "水表"}),
        ("filter", {"y型过滤器", "过滤器"}),
        ("valve", {"减压阀", "止回阀", "闸阀", "蝶阀", "球阀", "排气阀", "阀门", "阀"}),
        ("pipe_run", {"给水管", "排水管", "钢塑复合管", "复合管", "镀锌钢管", "不锈钢管", "铜管", "塑料管", "管道"}),
    ]
    for family, keywords in family_rules:
        if _contains_any_keyword(normalized, keywords):
            return family
    return ""


def _build_review_search_name(match_result: MatchResult) -> str:
    bill_name = str(getattr(match_result, "bill_name", "") or "").strip()
    bill_desc = str(getattr(match_result, "bill_description", "") or "").strip()
    bill_text = _normalize_review_text(bill_name, bill_desc)
    family = _classify_review_object_family(bill_text)

    if family == "pipe_support":
        if "成品管卡" in bill_text or "管卡" in bill_text:
            return "成品管卡安装"
    if family == "water_meter":
        return "水表安装"
    if family == "filter":
        return "过滤器安装"
    if family == "valve":
        if "减压" in bill_text:
            return "减压器组成安装"
        if "止回" in bill_text:
            return "止回阀安装"
        return "阀门安装"
    return bill_name


def _dedupe_review_strings(values: list[str] | None) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        item = str(value or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _build_audit_search_queries(
    *,
    match_result: MatchResult,
    llm_object_guard: dict[str, Any] | None = None,
) -> list[str]:
    llm_object_guard = llm_object_guard or {}
    bill_name = str(getattr(match_result, "bill_name", "") or "").strip()
    bill_desc = str(getattr(match_result, "bill_description", "") or "").strip()
    bill_text = _normalize_review_text(bill_name, bill_desc)
    family = _classify_review_object_family(bill_text)

    llm_queries = [
        str(item or "").strip()
        for item in (llm_object_guard.get("audit_queries") or [])
        if str(item or "").strip()
    ]
    search_hint = str(llm_object_guard.get("search_hint") or "").strip()
    default_query = _build_review_search_name(match_result)

    family_defaults = {
        "pipe_support": ["\u6210\u54c1\u7ba1\u5361\u5b89\u88c5", "\u7ba1\u5361\u5b89\u88c5"],
        "pipe_sleeve": ["\u5957\u7ba1\u5b89\u88c5", "\u9632\u6c34\u5957\u7ba1\u5b89\u88c5"],
        "water_meter": ["\u6c34\u8868\u5b89\u88c5"],
        "filter": ["\u8fc7\u6ee4\u5668\u5b89\u88c5", "Y\u578b\u8fc7\u6ee4\u5668\u5b89\u88c5"],
    }
    fallback_queries = list(family_defaults.get(family, []))
    if family == "valve":
        if default_query:
            fallback_queries.append(default_query)
        if bill_name:
            fallback_queries.append(bill_name)
    elif default_query and not fallback_queries:
        fallback_queries.append(default_query)

    return _dedupe_review_strings([
        *llm_queries,
        search_hint,
        default_query,
        bill_name,
        *fallback_queries,
    ])[:4]


def _review_candidate_key(candidate: dict[str, Any] | None) -> str:
    if not isinstance(candidate, dict):
        return ""
    quota_id = str(candidate.get("quota_id") or "").strip()
    if quota_id:
        return f"quota:{quota_id}"
    quota_name = str(candidate.get("name") or "").strip()
    if quota_name:
        return f"name:{quota_name}"
    return ""


def _append_unique_review_candidates(
    *,
    target: list[dict[str, Any]],
    seen: set[str],
    candidates: list[dict[str, Any]],
    limit: int,
) -> None:
    for candidate in candidates:
        normalized = _candidate_to_quota(candidate)
        key = _review_candidate_key(normalized)
        if not key or key in seen:
            continue
        seen.add(key)
        target.append(normalized)
        if len(target) >= limit:
            return


def _extract_first_json_object(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start:end + 1])
        except Exception:
            return {}
    return {}


def _call_review_llm_json(prompt: str) -> dict[str, Any]:
    llm_type = (
        os.getenv("VERIFY_LLM", "").strip().lower()
        or os.getenv("MATCH_LLM", "").strip().lower()
        or os.getenv("AGENT_LLM", "").strip().lower()
        or os.getenv("DEFAULT_LLM", "").strip().lower()
        or "qwen"
    )
    api_configs = {
        "deepseek": (os.getenv("DEEPSEEK_API_KEY", ""), os.getenv("DEEPSEEK_BASE_URL", ""), os.getenv("DEEPSEEK_MODEL", "")),
        "kimi": (os.getenv("KIMI_API_KEY", ""), os.getenv("KIMI_BASE_URL", ""), os.getenv("KIMI_MODEL", "")),
        "qwen": (os.getenv("QWEN_API_KEY", ""), os.getenv("QWEN_BASE_URL", ""), os.getenv("QWEN_MODEL", "")),
        "openai": (os.getenv("OPENAI_API_KEY", ""), os.getenv("OPENAI_BASE_URL", ""), os.getenv("OPENAI_MODEL", "")),
    }
    api_key, base_url, model = api_configs.get(llm_type, api_configs["qwen"])
    if not api_key or not base_url or not model:
        return {}

    import httpx
    from openai import OpenAI

    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        http_client=httpx.Client(timeout=20.0, trust_env=False),
    )
    request_kwargs = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": 600,
        "timeout": 20.0,
    }
    try:
        response = client.chat.completions.create(
            response_format={"type": "json_object"},
            **request_kwargs,
        )
    except Exception as exc:
        if "response_format" not in str(exc).lower():
            raise
        response = client.chat.completions.create(**request_kwargs)
    content = ((response.choices or [None])[0] or {}).message.content
    return _extract_first_json_object(content)


def _build_review_llm_prompt(
    *,
    task: Task,
    match_result: MatchResult,
    current_quota: dict[str, Any] | None,
    candidate_pool: list[dict[str, Any]],
) -> str:
    quota_text = ""
    if current_quota:
        quota_text = (
            f"- quota_id: {str(current_quota.get('quota_id') or '').strip()}\n"
            f"- 名称: {str(current_quota.get('name') or '').strip()}\n"
            f"- 单位: {str(current_quota.get('unit') or '').strip()}"
        )
    candidate_lines = []
    for idx, candidate in enumerate(candidate_pool[:5], start=1):
        candidate_lines.append(
            f"{idx}. {str(candidate.get('quota_id') or '').strip()} | "
            f"{str(candidate.get('name') or '').strip()} | "
            f"{str(candidate.get('unit') or '').strip()}"
        )
    candidates_text = "\n".join(candidate_lines) if candidate_lines else "(empty)"
    return (
        "你是工程造价二次审核器，只判断“清单对象”和“定额对象”是不是同一类安装对象。\n"
        "不要被相同系统、相同DN、相同册号误导；对象家族不同就判 false。\n"
        "典型不同对象：成品管卡/支吊架/套管/阀门/过滤器/水表/管道本体/卫生器具。\n"
        "如果当前 top1 不是同类对象，请给一个更适合补搜的短搜索词。\n\n"
        f"任务省份: {str(getattr(task, 'province', '') or '').strip()}\n"
        f"清单名称: {str(getattr(match_result, 'bill_name', '') or '').strip()}\n"
        f"清单特征: {str(getattr(match_result, 'bill_description', '') or '').strip()}\n"
        f"清单单位: {str(getattr(match_result, 'bill_unit', '') or '').strip()}\n"
        f"当前 top1:\n{quota_text or '(none)'}\n"
        f"候选池前5:\n{candidates_text}\n\n"
        "只输出 JSON，格式如下：\n"
        "{"
        "\"same_object\": true,"
        "\"bill_object\": \"\","
        "\"quota_object\": \"\","
        "\"reason\": \"\","
        "\"search_hint\": \"\","
        "\"audit_queries\": [],"
        "\"confidence\": 0"
        "}"
    )


def _normalize_review_guard_response(
    payload: dict[str, Any] | None,
    *,
    default_search_hint: str,
    default_audit_queries: list[str],
    source: str,
) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("same_object") not in {True, False}:
        return {}

    search_hint = str(payload.get("search_hint") or "").strip() or default_search_hint
    audit_queries = _dedupe_review_strings([
        *[
            str(item or "").strip()
            for item in list(payload.get("audit_queries") or [])
            if str(item or "").strip()
        ],
        search_hint,
        *default_audit_queries,
    ])[:4]
    return {
        "same_object": bool(payload.get("same_object")),
        "bill_object": str(payload.get("bill_object") or "").strip(),
        "quota_object": str(payload.get("quota_object") or "").strip(),
        "reason": str(payload.get("reason") or "").strip(),
        "search_hint": search_hint,
        "audit_queries": audit_queries,
        "confidence": int(payload.get("confidence") or 0),
        "source": source,
    }


async def _review_object_guard(
    *,
    task: Task,
    match_result: MatchResult,
    current_quotas: list[dict[str, Any]],
    candidate_pool: list[dict[str, Any]],
) -> dict[str, Any]:
    _ = task
    current_quota = current_quotas[0] if current_quotas else {}
    if not current_quota:
        return {}

    default_search_hint = _build_review_search_name(match_result)
    default_audit_queries = _build_audit_search_queries(match_result=match_result)
    bill_family = _classify_review_object_family(
        _normalize_review_text(
            getattr(match_result, "bill_name", ""),
            getattr(match_result, "bill_description", ""),
        )
    )
    quota_family = _classify_review_object_family(
        _normalize_review_text(
            current_quota.get("quota_id", ""),
            current_quota.get("name", ""),
            current_quota.get("unit", ""),
        )
    )

    if bill_family and quota_family and bill_family != quota_family:
        return _normalize_review_guard_response({
            "same_object": False,
            "bill_object": bill_family,
            "quota_object": quota_family,
            "reason": f"rule_family_mismatch:{bill_family}!={quota_family}",
            "search_hint": default_search_hint,
            "audit_queries": default_audit_queries,
            "confidence": 92,
        }, default_search_hint=default_search_hint, default_audit_queries=default_audit_queries, source="rule")

    candidate_families = {
        _classify_review_object_family(
            _normalize_review_text(
                candidate.get("quota_id", ""),
                candidate.get("name", ""),
                candidate.get("unit", ""),
            )
        )
        for candidate in candidate_pool[:5]
        if isinstance(candidate, dict)
    }
    candidate_families.discard("")
    if bill_family and candidate_families and bill_family not in candidate_families and quota_family != bill_family:
        return _normalize_review_guard_response({
            "same_object": False,
            "bill_object": bill_family,
            "quota_object": quota_family,
            "reason": f"candidate_family_missing:{bill_family}",
            "search_hint": default_search_hint,
            "audit_queries": default_audit_queries,
            "confidence": 85,
        }, default_search_hint=default_search_hint, default_audit_queries=default_audit_queries, source="rule")

    prompt = _build_review_llm_prompt(
        task=task,
        match_result=match_result,
        current_quota=current_quota,
        candidate_pool=candidate_pool,
    )
    try:
        llm_payload = await asyncio.to_thread(_call_review_llm_json, prompt)
    except Exception as exc:
        logger.warning(
            "openclaw object-guard llm unavailable for task=%s result=%s: %s",
            getattr(task, "id", ""),
            getattr(match_result, "id", ""),
            exc,
        )
        return {}

    return _normalize_review_guard_response(
        llm_payload,
        default_search_hint=default_search_hint,
        default_audit_queries=default_audit_queries,
        source="llm",
    )


def _normalized_unit(value: Any) -> str:
    return str(value or "").strip().lower().replace("米", "m").replace("平方米", "m2").replace("立方米", "m3")


def _unit_matches_match_result(*, match_result: MatchResult, quota: dict[str, Any] | None) -> bool:
    if not quota:
        return False
    bill_unit = _normalized_unit(getattr(match_result, "bill_unit", ""))
    quota_unit = _normalized_unit(quota.get("unit", ""))
    return bool(bill_unit and quota_unit and bill_unit == quota_unit)


def _specialty_matches_match_result(*, match_result: MatchResult, quota: dict[str, Any] | None) -> bool:
    if not quota:
        return False
    specialty = str(getattr(match_result, "specialty", "") or "").strip().upper()
    quota_id = str(quota.get("quota_id", "") or "").strip().upper()
    if not specialty or not quota_id:
        return False
    return quota_id.startswith(specialty) or quota_id.startswith("03-")


def _detect_candidate_risks(*, match_result: MatchResult, quota: dict[str, Any] | None) -> list[str]:
    risks: list[str] = []
    risks.extend(_detect_unit_conflict(match_result=match_result, quota=quota))
    risks.extend(_detect_specialty_conflict(match_result=match_result, quota=quota))
    return risks


def _coerce_review_score(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _detect_entity_conflict(*, match_result: MatchResult, quota: dict[str, Any] | None) -> list[str]:
    if not quota:
        return []

    bill_text = _normalize_review_text(
        getattr(match_result, "bill_name", ""),
        getattr(match_result, "bill_description", ""),
    )
    quota_text = _normalize_review_text(
        quota.get("quota_id", ""),
        quota.get("name", ""),
        quota.get("unit", ""),
    )
    bill_family = _classify_review_object_family(bill_text)
    quota_family = _classify_review_object_family(quota_text)

    if not bill_family:
        return []
    if quota_family and quota_family != bill_family:
        return [f"entity_conflict:{bill_family}"]

    if bill_family == "pipe_support":
        pipe_run_keywords = {"给水管", "排水管", "钢塑复合管", "不锈钢管", "塑料管", "铜管", "镀锌钢管", "隔油器", "水表"}
        if _contains_any_keyword(quota_text, pipe_run_keywords):
            return ["entity_conflict:pipe_support"]
    if bill_family == "water_meter" and "水表" not in quota_text:
        return ["entity_conflict:water_meter"]
    if bill_family == "filter" and "过滤器" not in quota_text:
        return ["entity_conflict:filter"]
    if bill_family == "valve":
        valve_keywords = {"阀", "阀门", "减压器", "止回阀", "闸阀", "蝶阀", "球阀", "排气阀"}
        if not _contains_any_keyword(quota_text, valve_keywords):
            return ["entity_conflict:valve"]

    return []


def _detect_unit_conflict(*, match_result: MatchResult, quota: dict[str, Any] | None) -> list[str]:
    if not quota:
        return []
    bill_unit = str(getattr(match_result, "bill_unit", "") or "").strip().lower()
    quota_unit = str(quota.get("unit", "") or "").strip().lower()
    if not bill_unit or not quota_unit:
        return []

    normalized_bill = bill_unit.replace("米", "m").replace("平方米", "m2").replace("立方米", "m3")
    normalized_quota = quota_unit.replace("米", "m").replace("平方米", "m2").replace("立方米", "m3")
    if normalized_bill == normalized_quota:
        return []

    hard_pairs = {
        ("m", "个"), ("m", "台"), ("m", "套"),
        ("个", "m"), ("台", "m"), ("套", "m"),
        ("m2", "个"), ("m2", "台"), ("m3", "个"),
    }
    if (normalized_bill, normalized_quota) in hard_pairs:
        return ["unit_conflict:hard"]
    return []


def _detect_specialty_conflict(*, match_result: MatchResult, quota: dict[str, Any] | None) -> list[str]:
    if not quota:
        return []
    specialty = str(getattr(match_result, "specialty", "") or "").strip().upper()
    quota_id = str(quota.get("quota_id", "") or "").strip().upper()
    if not specialty or not quota_id or not specialty.startswith("C"):
        return []
    if quota_id.startswith(specialty):
        return []
    if quota_id.startswith("03-"):
        return []
    return ["specialty_conflict"]


def _detect_candidate_conflicts(*, task: Task, match_result: MatchResult, quota: dict[str, Any] | None) -> list[str]:
    conflicts: list[str] = []
    conflicts.extend(_detect_system_conflict(task=task, match_result=match_result, quota=quota))
    conflicts.extend(_detect_entity_conflict(match_result=match_result, quota=quota))
    return conflicts


async def _search_better_quota_candidates(
    *,
    task: Task,
    match_result: MatchResult,
    audit_queries: list[str] | None = None,
    search_name_override: str = "",
    limit: int = 5,
) -> list[dict[str, Any]]:
    province = str(getattr(task, "province", "") or "").strip()
    if not province:
        return []

    specialty = str(getattr(match_result, "specialty", "") or "").strip()
    search_queries = _dedupe_review_strings([
        *(audit_queries or []),
        search_name_override,
        _build_review_search_name(match_result),
    ])[:4]
    merged_items: list[dict[str, Any]] = []
    seen: set[str] = set()

    async def _append_keyword_results(query: str) -> None:
        if not query or len(merged_items) >= limit:
            return
        search_books = [specialty, ""] if specialty else [""]
        for book in search_books[:2]:
            try:
                response = await quota_search_api.search_quotas(
                    keyword=query,
                    province=province,
                    book=book or None,
                    chapter=None,
                    limit=limit,
                    user=get_openclaw_read_user,
                )
            except Exception as exc:
                logger.warning(
                    "openclaw audit keyword search unavailable for task=%s result=%s province=%s query=%s: %s",
                    getattr(task, "id", ""),
                    getattr(match_result, "id", ""),
                    province,
                    query,
                    exc,
                )
                break
            items = list((response or {}).get("items") or []) if isinstance(response, dict) else []
            normalized_items: list[dict[str, Any]] = []
            for index, item in enumerate(items, start=1):
                if not isinstance(item, dict):
                    continue
                normalized_items.append({
                    "rank": index,
                    "quota_id": str(item.get("quota_id") or "").strip(),
                    "name": str(item.get("name") or "").strip(),
                    "unit": str(item.get("unit") or "").strip(),
                    "source": "audit_keyword",
                    "param_score": item.get("score"),
                    "rerank_score": item.get("score"),
                    "reason": query,
                    "book": str(item.get("book") or "").strip(),
                    "chapter": str(item.get("chapter") or "").strip(),
                })
            _append_unique_review_candidates(
                target=merged_items,
                seen=seen,
                candidates=normalized_items,
                limit=limit,
            )
            if normalized_items or not book:
                break

    for query in search_queries[:3]:
        await _append_keyword_results(query)
        if merged_items:
            return merged_items[:limit]

    for query in search_queries[:2]:
        if len(merged_items) >= limit:
            break
        try:
            response = await quota_search_api.smart_search(
                name=query,
                province=province,
                description=str(getattr(match_result, "bill_description", "") or "").strip(),
                specialty=specialty,
                limit=limit,
                user=get_openclaw_read_user,
            )
        except Exception as exc:
            logger.warning(
                "openclaw smart_search unavailable for task=%s result=%s province=%s query=%s: %s",
                getattr(task, "id", ""),
                getattr(match_result, "id", ""),
                province,
                query,
                exc,
            )
            continue
        items = list((response or {}).get("items") or []) if isinstance(response, dict) else []
        search_query = str((response or {}).get("search_query") or "").strip() if isinstance(response, dict) else ""
        normalized_items: list[dict[str, Any]] = []
        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                continue
            normalized_items.append({
                "rank": index,
                "quota_id": str(item.get("quota_id") or "").strip(),
                "name": str(item.get("name") or "").strip(),
                "unit": str(item.get("unit") or "").strip(),
                "source": "smart_search",
                "param_score": item.get("score"),
                "rerank_score": item.get("score"),
                "reason": search_query or query,
                "book": str(item.get("book") or "").strip(),
                "chapter": str(item.get("chapter") or "").strip(),
            })
        _append_unique_review_candidates(
            target=merged_items,
            seen=seen,
            candidates=normalized_items,
            limit=limit,
        )

    return merged_items[:limit]


def _pick_best_review_candidate(
    *,
    task: Task,
    match_result: MatchResult,
    current_ids: set[str],
    candidates: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[str], str]:
    rejection_reasons: list[str] = []
    ranked: list[tuple[tuple[Any, ...], dict[str, Any], str]] = []

    for index, candidate in enumerate(candidates):
        normalized = _candidate_to_quota(candidate)
        conflict_codes = _detect_candidate_conflicts(task=task, match_result=match_result, quota=normalized)
        if conflict_codes:
            rejection_reasons.extend(conflict_codes)
            continue
        candidate_id = str(normalized.get("quota_id") or "").strip()
        if candidate_id and candidate_id in current_ids:
            continue

        soft_risks = _detect_candidate_risks(match_result=match_result, quota=normalized)
        sort_key = (
            len(soft_risks),
            0 if _specialty_matches_match_result(match_result=match_result, quota=normalized) else 1,
            0 if _unit_matches_match_result(match_result=match_result, quota=normalized) else 1,
            -_coerce_review_score(normalized.get("rerank_score") or normalized.get("param_score")),
            int(normalized.get("rank") or index + 1),
        )
        ranked.append((sort_key, normalized, ",".join(soft_risks)))

    if not ranked:
        return None, rejection_reasons, ""

    ranked.sort(key=lambda item: item[0])
    best_candidate = ranked[0][1]
    source = str(best_candidate.get("source") or "candidate_pool").strip()
    reason = str(best_candidate.get("reason") or "").strip()
    return best_candidate, rejection_reasons, f"{source}:{reason}" if reason else source


async def _choose_best_safe_candidate(
    *,
    task: Task,
    match_result: MatchResult,
    current_quotas: list[dict[str, Any]],
    candidate_pool: list[dict[str, Any]],
    audit_queries: list[str] | None = None,
    search_name_override: str = "",
    allow_slow_search: bool = False,
) -> tuple[dict[str, Any] | None, list[str], str]:
    current_ids = {
        str(item.get("quota_id") or "").strip()
        for item in current_quotas
        if isinstance(item, dict) and str(item.get("quota_id") or "").strip()
    }

    pool_candidate, rejection_reasons, pool_source = _pick_best_review_candidate(
        task=task,
        match_result=match_result,
        current_ids=current_ids,
        candidates=candidate_pool,
    )
    if pool_candidate:
        return pool_candidate, rejection_reasons, pool_source

    if not allow_slow_search:
        return None, rejection_reasons, ""

    searched_candidates = await _search_better_quota_candidates(
        task=task,
        match_result=match_result,
        audit_queries=audit_queries,
        search_name_override=search_name_override,
        limit=5,
    )
    search_candidate, search_rejections, search_source = _pick_best_review_candidate(
        task=task,
        match_result=match_result,
        current_ids=current_ids,
        candidates=searched_candidates,
    )
    rejection_reasons.extend(search_rejections)
    return search_candidate, rejection_reasons, search_source


def _can_agree_with_current_top1(
    *,
    task: Task,
    match_result: MatchResult,
    current_quotas: list[dict[str, Any]],
    issue_set: set[str],
) -> bool:
    if not current_quotas:
        return False

    current_top1 = current_quotas[0]
    current_conflicts = _detect_candidate_conflicts(
        task=task,
        match_result=match_result,
        quota=current_top1,
    )
    if current_conflicts:
        return False

    if not issue_set:
        return True

    soft_issue_types = {
        "ambiguity_review",
        "price_mismatch",
    }
    return issue_set.issubset(soft_issue_types)


def _should_search_for_better_candidate(
    *,
    current_quotas: list[dict[str, Any]],
    issue_set: set[str],
    current_top1_conflicts: list[str],
    llm_object_guard: dict[str, Any],
) -> bool:
    if not current_quotas:
        return True
    if current_top1_conflicts:
        return True
    if issue_set:
        return True
    return llm_object_guard.get("same_object") is False


def _is_same_quota_candidate(
    current_quota: dict[str, Any] | None,
    candidate_quota: dict[str, Any] | None,
) -> bool:
    if not current_quota or not candidate_quota:
        return False
    current_id = str(current_quota.get("quota_id") or "").strip()
    candidate_id = str(candidate_quota.get("quota_id") or "").strip()
    if current_id and candidate_id:
        return current_id == candidate_id
    current_name = str(current_quota.get("name") or "").strip()
    candidate_name = str(candidate_quota.get("name") or "").strip()
    return bool(current_name and candidate_name and current_name == candidate_name)


def _build_auto_review_note(
    *,
    decision_type: str,
    candidate_source: str,
    bucket: str,
    issue_types: list[str],
    has_current_quota: bool,
    has_candidate_pool: bool,
) -> str:
    search_driven = candidate_source.startswith("audit_keyword:") or candidate_source.startswith("smart_search:")
    if decision_type == "override_within_candidates" and search_driven and not has_current_quota:
        return "Jarvis 未稳定产出 top1，OpenClaw 已独立审库补出建议定额，需人工复核。"
    if decision_type == "override_within_candidates" and search_driven:
        return "OpenClaw 独立审库后判定当前 top1 不可靠，建议改判并人工复核。"
    if decision_type == "override_within_candidates" and not has_current_quota:
        return "Jarvis 未稳定产出 top1，OpenClaw 从现有候选中补出一个建议项供人工复核。"
    if decision_type == "override_within_candidates":
        return "OpenClaw 根据终检和候选证据建议在现有候选中改判。"
    if decision_type == "candidate_pool_insufficient":
        return "OpenClaw 当前审库证据不足，不能安全改判，需人工复核。"
    if bucket == "green":
        return "Jarvis 结果处于高置信区间，OpenClaw 未发现足够强的改判证据。"
    if issue_types:
        return "OpenClaw 保留 Jarvis 当前 top1，但记录了需要人工关注的诊断信号。"
    if has_candidate_pool:
        return "OpenClaw 复核后暂不改判，保留 Jarvis 当前选择并附带候选上下文。"
    return "OpenClaw 复核后暂不改判。"


def _build_qmd_evidence_summary(context: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    qmd_recall = context.get("qmd_recall")
    if not isinstance(qmd_recall, dict):
        return "", {}

    raw_hits = [item for item in (qmd_recall.get("hits") or []) if isinstance(item, dict)]
    if not raw_hits:
        return "", {
            "query": str(qmd_recall.get("query") or "").strip(),
            "count": 0,
            "top_hits": [],
        }

    top_hits: list[dict[str, Any]] = []
    brief_labels: list[str] = []
    for hit in raw_hits[:3]:
        compact = {
            "title": str(hit.get("title") or hit.get("heading") or "").strip(),
            "category": str(hit.get("category") or "").strip(),
            "page_type": str(hit.get("page_type") or hit.get("type") or "").strip(),
            "path": str(hit.get("path") or "").strip(),
            "preview": str(hit.get("preview") or "").strip(),
            "score": hit.get("score"),
        }
        top_hits.append(compact)
        label = " / ".join(part for part in [compact["category"], compact["title"] or compact["path"]] if part)
        if label:
            brief_labels.append(label)

    note = ""
    if brief_labels:
        note = f"QMD证据: 命中 {len(raw_hits)} 条，优先参考 {'；'.join(brief_labels[:2])}。"

    return note, {
        "query": str(qmd_recall.get("query") or "").strip(),
        "count": int(qmd_recall.get("count") or len(raw_hits)),
        "top_hits": top_hits,
    }


# Override garbled legacy literals with clean review text.
def _build_auto_review_note(
    *,
    decision_type: str,
    candidate_source: str,
    bucket: str,
    issue_types: list[str],
    has_current_quota: bool,
    has_candidate_pool: bool,
) -> str:
    search_driven = candidate_source.startswith("audit_keyword:") or candidate_source.startswith("smart_search:")
    if decision_type == "override_within_candidates" and search_driven and not has_current_quota:
        return "Jarvis 当前没有稳定 top1，OpenClaw 已独立补出建议定额，需人工复核。"
    if decision_type == "override_within_candidates" and search_driven:
        return "OpenClaw 独立审库后判定当前 top1 不可靠，建议改判并人工复核。"
    if decision_type == "override_within_candidates" and not has_current_quota:
        return "Jarvis 当前没有稳定 top1，OpenClaw 从现有候选中补出一个建议项供人工复核。"
    if decision_type == "override_within_candidates":
        return "OpenClaw 根据终检和候选证据，建议在现有候选中改判。"
    if decision_type == "candidate_pool_insufficient":
        return "OpenClaw 当前审库证据不足，不能安全改判，需人工复核。"
    if bucket == "green":
        return "Jarvis 结果处于高置信区间，OpenClaw 未发现足够强的改判证据。"
    if issue_types:
        return "OpenClaw 保留 Jarvis 当前 top1，但记录了需要人工关注的诊断信号。"
    if has_candidate_pool:
        return "OpenClaw 复核后暂不改判，保留 Jarvis 当前选择并附带候选上下文。"
    return "OpenClaw 复核后暂不改判。"


def _build_qmd_evidence_summary(context: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    qmd_recall = context.get("qmd_recall")
    if not isinstance(qmd_recall, dict):
        return "", {}

    raw_hits = [item for item in (qmd_recall.get("hits") or []) if isinstance(item, dict)]
    if not raw_hits:
        return "", {
            "query": str(qmd_recall.get("query") or "").strip(),
            "count": 0,
            "top_hits": [],
        }

    top_hits: list[dict[str, Any]] = []
    brief_labels: list[str] = []
    for hit in raw_hits[:3]:
        compact = {
            "title": str(hit.get("title") or hit.get("heading") or "").strip(),
            "category": str(hit.get("category") or "").strip(),
            "page_type": str(hit.get("page_type") or hit.get("type") or "").strip(),
            "path": str(hit.get("path") or "").strip(),
            "preview": str(hit.get("preview") or "").strip(),
            "score": hit.get("score"),
        }
        top_hits.append(compact)
        label = " / ".join(part for part in [compact["category"], compact["title"] or compact["path"]] if part)
        if label:
            brief_labels.append(label)

    note = ""
    if brief_labels:
        note = f"QMD证据: 命中 {len(raw_hits)} 条，优先参考 {'；'.join(brief_labels[:2])}。"

    return note, {
        "query": str(qmd_recall.get("query") or "").strip(),
        "count": int(qmd_recall.get("count") or len(raw_hits)),
        "top_hits": top_hits,
    }


async def _build_auto_review_draft_request(task: Task, match_result: MatchResult) -> OpenClawReviewDraftRequest:
    if _is_non_boq_entry(task, match_result):
        note = "当前记录属于材料/设备选用表或非清单入口，不进入定额自动二审。"
        note = "当前记录属于材料/设备选用表或非清单入口，不进入定额自动二审。"
        return OpenClawReviewDraftRequest(
            decision_type="abstain",
            openclaw_review_status="reviewed",
            review_confidence=96,
            error_stage="input_routing",
            error_type="non_boq_entry",
            reason_codes=["non_boq_entry", "needs_input_routing_fix"],
            retry_query="",
            note=note,
            suggested_quotas=[],
            openclaw_review_payload={
                "decision_type": "abstain",
                "note": note,
                "evidence": {
                    "sheet_name": str(getattr(match_result, "sheet_name", "") or ""),
                    "bill_name": str(getattr(match_result, "bill_name", "") or ""),
                    "bill_description": str(getattr(match_result, "bill_description", "") or ""),
                },
            },
        )

    context = OPENCLAW_REVIEW_SERVICE.build_review_context(task, match_result)
    trace_summary = context.get("trace_summary") or {}
    final_validation = trace_summary.get("final_validation") or {}
    final_review_correction = trace_summary.get("final_review_correction") or {}
    reasoning_summary = trace_summary.get("reasoning_summary") or {}
    candidate_pool = [
        item for item in (context.get("candidate_pool") or []) if isinstance(item, dict)
    ]
    current_quotas = [
        _normalize_quota_dict(item)
        for item in list(getattr(match_result, "quotas", None) or [])
        if isinstance(item, dict)
    ]
    issue_types = [
        str(item.get("type") or "").strip()
        for item in _issue_list_from_final_validation(final_validation)
        if str(item.get("type") or "").strip()
    ]
    bucket = _openclaw_policy_bucket(match_result)
    suggested_quotas: list[dict[str, Any]] = []
    decision_type = "agree"
    issue_set = {item for item in issue_types if item}
    jarvis_problem_detected = bool(issue_set)
    llm_object_guard = await _review_object_guard(
        task=task,
        match_result=match_result,
        current_quotas=current_quotas,
        candidate_pool=candidate_pool,
    )
    review_search_hint = str(llm_object_guard.get("search_hint") or "").strip()
    audit_queries = _build_audit_search_queries(
        match_result=match_result,
        llm_object_guard=llm_object_guard,
    )
    current_top1_conflicts = (
        _detect_candidate_conflicts(task=task, match_result=match_result, quota=current_quotas[0])
        if current_quotas else []
    )
    if llm_object_guard.get("same_object") is False:
        current_top1_conflicts = list(dict.fromkeys([*current_top1_conflicts, "rule_entity_mismatch"]))

    correction_quota = _find_candidate_by_ref(
        candidate_pool=candidate_pool,
        quotas=current_quotas,
        target_quota_id=str(final_review_correction.get("quota_id") or "").strip(),
        target_name=str(final_review_correction.get("quota_name") or "").strip(),
    )
    safe_correction_quota = None
    blocked_reason_codes: list[str] = []
    if correction_quota:
        safe_conflicts = _detect_candidate_conflicts(task=task, match_result=match_result, quota=correction_quota)
        if safe_conflicts:
            blocked_reason_codes.extend(safe_conflicts)
            correction_quota = None
        else:
            safe_correction_quota = correction_quota

    pool_candidate, rejected_candidate_reasons, candidate_source = await _choose_best_safe_candidate(
        task=task,
        match_result=match_result,
        current_quotas=current_quotas,
        candidate_pool=candidate_pool,
        audit_queries=audit_queries,
        search_name_override=review_search_hint,
        allow_slow_search=False,
    )
    search_candidate = None
    should_search_for_candidate = _should_search_for_better_candidate(
        current_quotas=current_quotas,
        issue_set=issue_set,
        current_top1_conflicts=current_top1_conflicts,
        llm_object_guard=llm_object_guard,
    )
    if not pool_candidate and should_search_for_candidate:
        search_candidate, search_rejected_candidate_reasons, search_candidate_source = await _choose_best_safe_candidate(
            task=task,
            match_result=match_result,
            current_quotas=current_quotas,
            candidate_pool=candidate_pool,
            audit_queries=audit_queries,
            search_name_override=review_search_hint,
            allow_slow_search=True,
        )
        rejected_candidate_reasons.extend(search_rejected_candidate_reasons)
        if search_candidate:
            candidate_source = search_candidate_source

    safe_candidate = pool_candidate or search_candidate
    search_driven_candidate = candidate_source.startswith("audit_keyword:") or candidate_source.startswith("smart_search:")
    safe_candidate_is_current_top1 = _is_same_quota_candidate(
        current_quotas[0] if current_quotas else None,
        safe_candidate,
    )
    blocked_reason_codes.extend(rejected_candidate_reasons)
    blocked_reason_codes.extend(current_top1_conflicts)
    if llm_object_guard.get("source") == "llm":
        blocked_reason_codes.append("llm_object_guard_used")
    if llm_object_guard.get("same_object") is True:
        blocked_reason_codes.append(
            "llm_entity_verified" if llm_object_guard.get("source") == "llm" else "rule_entity_verified"
        )
    if review_search_hint and should_search_for_candidate:
        blocked_reason_codes.append(f"llm_search_hint:{review_search_hint}")
    for query in audit_queries[:2]:
        blocked_reason_codes.append(f"audit_query:{query}")

    can_agree_with_current_top1 = _can_agree_with_current_top1(
        task=task,
        match_result=match_result,
        current_quotas=current_quotas,
        issue_set=issue_set,
    )
    if safe_correction_quota:
        decision_type = "override_within_candidates"
        suggested_quotas = [safe_correction_quota]
    elif not current_quotas and safe_candidate:
        decision_type = "override_within_candidates"
        suggested_quotas = [safe_candidate]
    elif current_quotas and safe_candidate and current_top1_conflicts:
        decision_type = "override_within_candidates"
        suggested_quotas = [safe_candidate]
    elif current_quotas and safe_candidate and not safe_candidate_is_current_top1 and (
        not can_agree_with_current_top1
        or bool(issue_set)
        or search_driven_candidate
    ):
        decision_type = "override_within_candidates"
        suggested_quotas = [safe_candidate]
    elif can_agree_with_current_top1:
        decision_type = "agree"
        suggested_quotas = list(current_quotas)
    elif _should_retry_search_then_select(
        issue_types=issue_types,
        current_quotas=current_quotas,
        candidate_pool=candidate_pool,
    ):
        decision_type = "retry_search_then_select"
        suggested_quotas = []
    elif current_quotas:
        decision_type = "candidate_pool_insufficient"
        suggested_quotas = []
    else:
        decision_type = "candidate_pool_insufficient"
        suggested_quotas = []

    error_stage = _infer_openclaw_error_stage(
        final_validation=final_validation,
        final_review_correction=final_review_correction,
        reasoning_summary=reasoning_summary,
    )
    error_type = _pick_primary_error_type(issue_types)
    review_confidence = {
        "override_within_candidates": 78 if bucket == "red" else 86,
        "retry_search_then_select": 90 if bucket == "red" else 84,
        "agree": 93 if bucket == "green" else 80,
        "candidate_pool_insufficient": 55,
    }.get(decision_type, 70)
    reason_codes = _reason_codes_for_auto_review(
        bucket=bucket,
        issue_types=issue_types,
        reasoning_summary=reasoning_summary,
        final_review_correction=final_review_correction,
        has_candidate_pool=bool(candidate_pool),
        has_current_quota=bool(current_quotas),
        decision_type=decision_type,
        jarvis_problem_detected=jarvis_problem_detected,
    )
    if candidate_source:
        reason_codes = list(dict.fromkeys([*(reason_codes or []), f"candidate_source:{candidate_source}"]))
    if blocked_reason_codes:
        reason_codes = list(dict.fromkeys([*(reason_codes or []), *blocked_reason_codes]))
    note = _build_auto_review_note(
        decision_type=decision_type,
        candidate_source=candidate_source,
        bucket=bucket,
        issue_types=issue_types,
        has_current_quota=bool(current_quotas),
        has_candidate_pool=bool(candidate_pool),
    )
    qmd_note, qmd_summary = _build_qmd_evidence_summary(context)
    note = _merge_review_notes(note, qmd_note)
    retry_query = str((trace_summary.get("query_route") or {}).get("rewrite_query") or "").strip()
    if not retry_query and decision_type == "retry_search_then_select":
        retry_query = str(qmd_summary.get("query") or "").strip()
    if not retry_query and (
        candidate_source.startswith("smart_search:") or candidate_source.startswith("audit_keyword:")
    ):
        retry_query = candidate_source.split(":", 1)[1].strip()
    draft = OPENCLAW_REVIEW_SERVICE.build_structured_draft(
        task,
        match_result,
        decision_type=decision_type,
        suggested_quotas=suggested_quotas or None,
        review_confidence=review_confidence,
        error_stage=error_stage,
        error_type=error_type,
        retry_query=retry_query,
        reason_codes=reason_codes,
        note=note,
        evidence={
            "bucket": bucket,
            "issue_types": issue_types,
            "final_validation": final_validation,
            "final_review_correction": final_review_correction,
            "reasoning_summary": reasoning_summary,
            "qmd_recall": context.get("qmd_recall") if isinstance(context.get("qmd_recall"), dict) else {},
            "qmd_summary": qmd_summary,
        },
    )
    return OpenClawReviewDraftRequest(**draft)


async def _apply_review_draft(
    *,
    match_result: MatchResult,
    req: OpenClawReviewDraftRequest,
    service_user: User,
    enforce_bucket_policy: bool,
) -> MatchResultResponse:
    if enforce_bucket_policy:
        _ensure_openclaw_reviewable(_openclaw_policy_bucket(match_result))

    suggested_quotas = repair_mojibake_data(
        [item.model_dump() for item in (req.openclaw_suggested_quotas or [])],
        preserve_newlines=True,
    )
    suggested_quotas, _ = _repair_suggested_quotas_from_result(match_result, suggested_quotas)
    decision_type = str(req.openclaw_decision_type or "").strip()
    allow_empty_suggestions = decision_type in {
        "candidate_pool_insufficient",
        "retry_search_then_select",
        "abstain",
    }
    if not suggested_quotas and not allow_empty_suggestions:
        suggested_quotas = list(match_result.quotas or [])
    if not suggested_quotas and not allow_empty_suggestions:
        raise HTTPException(status_code=422, detail="OpenClaw review draft requires a suggested quota set")

    payload = repair_mojibake_data(
        _build_openclaw_review_payload(req, suggested_quotas=suggested_quotas),
        preserve_newlines=True,
    )
    payload_suggested, payload_changed = repair_quota_name_loss(
        payload.get("suggested_quotas") if isinstance(payload, dict) else None,
        suggested_quotas,
        match_result.alternatives or [],
        match_result.corrected_quotas or [],
        match_result.quotas or [],
        preserve_newlines=True,
    )
    if payload_changed and isinstance(payload, dict):
        payload = dict(payload)
        payload["suggested_quotas"] = payload_suggested

    match_result.openclaw_review_status = "reviewed"
    match_result.openclaw_suggested_quotas = suggested_quotas or None
    match_result.openclaw_review_note = str(payload.get("note") or req.openclaw_review_note or "")
    payload_confidence = payload.get("review_confidence")
    match_result.openclaw_review_confidence = (
        int(payload_confidence) if isinstance(payload_confidence, int) else req.openclaw_review_confidence
    )
    decision_type = str(payload.get("decision_type") or req.openclaw_decision_type or "").strip()
    error_stage = str(payload.get("error_stage") or req.openclaw_error_stage or "").strip()
    error_type = str(payload.get("error_type") or req.openclaw_error_type or "").strip()
    match_result.openclaw_decision_type = decision_type or None
    match_result.openclaw_error_stage = error_stage or None
    match_result.openclaw_error_type = error_type or None
    match_result.openclaw_retry_query = str(payload.get("retry_query") or req.openclaw_retry_query or "")
    match_result.openclaw_reason_codes = _normalize_openclaw_reason_codes(
        payload.get("reason_codes") if isinstance(payload.get("reason_codes"), list) else req.openclaw_reason_codes
    )
    match_result.openclaw_review_payload = payload
    match_result.openclaw_review_actor = _service_actor(service_user)
    match_result.openclaw_review_time = _utcnow()
    match_result.openclaw_review_confirm_status = "pending"
    match_result.openclaw_review_confirmed_by = ""
    match_result.openclaw_review_confirm_time = None
    return results_api._to_result_response(match_result)


async def _get_review_job_or_404(
    *,
    review_job_id: uuid.UUID,
    db: AsyncSession,
) -> OpenClawReviewJob:
    result = await db.execute(
        select(OpenClawReviewJob).where(OpenClawReviewJob.id == review_job_id)
    )
    review_job = result.scalar_one_or_none()
    if not review_job:
        raise HTTPException(status_code=404, detail="review job not found")
    return review_job


async def _resolve_auto_review_run(
    *,
    task_id: uuid.UUID,
    requested_scope: str | None,
    review_job_id: uuid.UUID | None,
    db: AsyncSession,
) -> tuple[Task, str, OpenClawReviewJob | None]:
    task = await _get_review_job_source_task(source_task_id=task_id, db=db)
    review_job = None
    scope = str(requested_scope or "yellow_red_pending").strip() or "yellow_red_pending"
    if review_job_id:
        review_job = await _get_review_job_or_404(review_job_id=review_job_id, db=db)
        if review_job.source_task_id != task_id:
            raise HTTPException(status_code=409, detail="review job does not belong to this source task")
        scope = str(review_job.scope or scope).strip() or "yellow_red_pending"
    return task, scope, review_job


async def _mark_review_job_running(review_job: OpenClawReviewJob | None, db: AsyncSession) -> None:
    if not review_job:
        return
    review_job.status = "running"
    review_job.started_at = review_job.started_at or _utcnow()
    review_job.completed_at = None
    review_job.error_message = None
    await db.flush()


async def _finalize_review_job(
    *,
    review_job: OpenClawReviewJob | None,
    task: Task,
    scope: str,
    drafted_count: int,
    skipped_count: int,
    failed_count: int,
    db: AsyncSession,
) -> None:
    if not review_job:
        return
    refreshed = await _summarize_review_job_source_task(task=task, scope=scope, db=db)
    review_job.total_results = int(refreshed["total_results"])
    review_job.pending_results = int(refreshed["pending_results"])
    review_job.reviewable_results = int(refreshed["reviewable_results"])
    review_job.green_count = int(refreshed["green_count"])
    review_job.yellow_count = int(refreshed["yellow_count"])
    review_job.red_count = int(refreshed["red_count"])
    review_job.reviewed_pending_count = int(refreshed["reviewed_pending_count"])
    summary = dict(refreshed["summary"])
    summary["last_batch"] = {
        "drafted_count": drafted_count,
        "skipped_count": skipped_count,
        "failed_count": failed_count,
        "completed_at": _utcnow().isoformat(),
    }
    review_job.summary = summary
    review_job.status = "completed" if failed_count == 0 else "failed"
    review_job.completed_at = _utcnow()
    review_job.error_message = "" if failed_count == 0 else f"failed_count={failed_count}"
    await db.flush()


def _build_openclaw_openapi(request: Request) -> dict:
    routes = []
    for route in request.app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/openclaw"):
            continue
        if path == "/api/openclaw/openapi.json":
            continue
        routes.append(route)

    schema = get_openapi(
        title="auto-quota OpenClaw API",
        version="1.1.0",
        description=(
            "OpenClaw 使用的精简桥接接口。"
            "结果正式纠正采用 review draft + human confirm 两段式机制。"
        ),
        routes=routes,
    )
    schema["servers"] = [{"url": str(request.base_url).rstrip("/")}]
    return schema


@router.get("/openapi.json", include_in_schema=False)
async def openclaw_openapi(request: Request):
    return JSONResponse(_build_openclaw_openapi(request))


@router.get("/health")
async def health(service_user: User = Depends(get_openclaw_service_user)):
    return {
        "status": "ok",
        "service": "auto-quota-openclaw",
        "actor": service_user.email,
        "openapi_url": "/api/openclaw/openapi.json",
    }


@router.post("/file-intake/upload", response_model=FileIntakeResponse, status_code=201)
async def upload_file_intake(
    file: UploadFile = File(...),
    province: str = Form(default=""),
    project_name: str = Form(default=""),
    project_stage: str = Form(default=""),
    source_hint: str = Form(default=""),
    service_user: User = Depends(get_openclaw_service_user),
):
    return await file_intake_api._save_upload(
        file=file,
        province=province,
        project_name=project_name,
        project_stage=project_stage,
        source_hint=source_hint,
        actor=service_user,
    )


@router.get("/file-intake/{file_id}", response_model=FileIntakeResponse)
async def get_file_intake(
    file_id: str,
    reader: User = Depends(get_openclaw_read_user),
):
    _ = reader
    record = file_intake_api.FileIntakeDB().get_file(file_id)
    if not record:
        raise HTTPException(status_code=404, detail="file not found")
    return file_intake_api._to_response(record)


@router.post("/file-intake/{file_id}/classify", response_model=FileClassifyResponse)
async def classify_file_intake(
    file_id: str,
    req: FileClassifyRequest,
    reader: User = Depends(get_openclaw_read_user),
):
    _ = reader
    return await file_intake_api._classify_file(file_id, req)


@router.post("/file-intake/{file_id}/parse", response_model=FileParseResponse)
async def parse_file_intake(
    file_id: str,
    req: FileParseRequest,
    reader: User = Depends(get_openclaw_read_user),
):
    _ = reader
    return await file_intake_api._parse_file(file_id, req)


@router.post("/file-intake/{file_id}/route", response_model=FileRouteResponse)
async def route_file_intake(
    file_id: str,
    req: FileRouteRequest,
    service_user: User = Depends(get_openclaw_service_user),
):
    _ = service_user
    return await file_intake_api._route_file(file_id, req)


@router.get("/admin/key-status", response_model=OpenClawKeyStatusResponse)
async def key_status(admin: User = Depends(require_admin)):
    return OpenClawKeyStatusResponse(
        configured=bool(OPENCLAW_API_KEY),
        masked_key=_mask_key(OPENCLAW_API_KEY),
        service_email=OPENCLAW_SERVICE_EMAIL,
        service_nickname=OPENCLAW_SERVICE_NICKNAME,
        openapi_url="/api/openclaw/openapi.json",
        public_path="/api/openclaw/",
        sync_targets=["backend", "celery-worker"],
        update_hint="这是运行时环境变量方案。修改 OPENCLAW_API_KEY 后，需要同步更新 backend 和 celery-worker，并重新部署或重启服务。",
    )


@router.post("/admin/key-suggestion", response_model=OpenClawKeySuggestionResponse)
async def generate_key_suggestion(
    req: OpenClawKeySuggestionRequest,
    admin: User = Depends(require_admin),
):
    prefix = (req.prefix or "oc_").strip()[:24]
    suggested_key = f"{prefix}{secrets.token_urlsafe(32)}"
    return OpenClawKeySuggestionResponse(
        suggested_key=suggested_key,
        env_name="OPENCLAW_API_KEY",
        sync_targets=["backend", "celery-worker"],
        manifest_paths=["lzc-manifest.yml"],
        rollout_steps=[
            "把新 key 填到 backend.environment.OPENCLAW_API_KEY",
            "把同一个 key 填到 celery-worker.environment.OPENCLAW_API_KEY",
            "确认 application.public_path 已放行 /api/openclaw/",
            "重新部署或重启服务后，再让 OpenClaw 用新 key 调 /api/openclaw/openapi.json",
        ],
    )


@router.post("/promotion-cards", response_model=OpenClawPromotionCardCreateResponse, status_code=201)
async def create_promotion_card(
    req: OpenClawPromotionCardCreateRequest,
    service_user: User = Depends(get_openclaw_service_user),
):
    target_layer = _resolve_promotion_target(req.candidate_type, req.target_layer)
    payload = {
        "source_id": req.card_id,
        "source_type": req.source_type or "openclaw_manual_card",
        "source_table": OPENCLAW_MANUAL_CARD_SOURCE_TABLE,
        "source_record_id": req.card_id,
        "owner": req.owner or _service_actor(service_user),
        "evidence_ref": req.evidence_ref,
        "status": "draft",
        "candidate_type": req.candidate_type,
        "target_layer": target_layer,
        "candidate_title": req.candidate_title,
        "candidate_summary": req.candidate_summary,
        "candidate_payload": req.candidate_payload,
        "priority": req.priority,
        "approval_required": req.approval_required,
    }
    try:
        record_id = await asyncio.to_thread(
            lambda: _get_staging().enqueue_promotion(payload)
        )
        record = await asyncio.to_thread(lambda: _get_staging().get_promotion(record_id))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"create promotion card failed: {e}") from e

    if not record:
        raise HTTPException(status_code=500, detail="promotion card created but record lookup failed")

    return OpenClawPromotionCardCreateResponse(
        id=int(record["id"]),
        source_table=str(record.get("source_table") or OPENCLAW_MANUAL_CARD_SOURCE_TABLE),
        source_record_id=str(record.get("source_record_id") or req.card_id),
        target_layer=str(record.get("target_layer") or target_layer),
        status=str(record.get("status") or "draft"),
        review_status=str(record.get("review_status") or "unreviewed"),
    )


@router.get("/source-packs", response_model=OpenClawSourcePackListResponse)
async def list_source_packs(
    q: str = Query(default="", description="Source-pack search query"),
    source_kind: str = Query(default="", description="Source kind filter"),
    province: str = Query(default="", description="Province filter"),
    specialty: str = Query(default="", description="Specialty filter"),
    limit: int = Query(default=20, ge=1, le=200),
    reader: User = Depends(get_openclaw_read_user),
):
    _ = reader
    try:
        payload = await asyncio.to_thread(
            lambda: _get_source_learning_service().list_source_packs(
                q=q,
                source_kind=source_kind,
                province=province,
                specialty=specialty,
                limit=limit,
            )
        )
        return OpenClawSourcePackListResponse(**payload)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"list source packs failed: {e}") from e


@router.get("/source-packs/{source_id}", response_model=OpenClawSourcePackSummaryResponse)
async def get_source_pack(
    source_id: str,
    reader: User = Depends(get_openclaw_read_user),
):
    _ = reader
    try:
        payload = await asyncio.to_thread(
            lambda: _get_source_learning_service().get_source_pack_summary(source_id)
        )
        return OpenClawSourcePackSummaryResponse(**payload)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"get source pack failed: {e}") from e


@router.post("/source-packs/{source_id}/learn", response_model=OpenClawSourceLearningRunResponse)
async def learn_source_pack(
    source_id: str,
    req: OpenClawSourceLearningRunRequest,
    service_user: User = Depends(get_openclaw_service_user),
):
    _ = service_user
    try:
        payload = await asyncio.to_thread(
            lambda: _get_source_learning_service().extract_source_pack(
                source_id,
                dry_run=req.dry_run,
                llm_type=req.llm_type,
                chunk_size=req.chunk_size,
                overlap=req.overlap,
                max_chunks=req.max_chunks,
            )
        )
        return OpenClawSourceLearningRunResponse(**payload)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"learn source pack failed: {e}") from e

@router.post("/review-jobs", response_model=OpenClawReviewJobResponse, status_code=201)
async def create_review_job(
    req: OpenClawReviewJobCreateRequest,
    db: AsyncSession = Depends(get_db),
    service_user: User = Depends(get_openclaw_service_user),
):
    task = await _get_review_job_source_task(source_task_id=req.source_task_id, db=db)
    await _ensure_no_active_review_job(
        source_task_id=req.source_task_id,
        scope=req.scope,
        db=db,
    )
    summary = await _summarize_review_job_source_task(task=task, scope=req.scope, db=db)
    if int(summary["reviewable_results"]) <= 0:
        raise HTTPException(
            status_code=409,
            detail=f"no reviewable results found for scope={req.scope}",
        )

    review_job = OpenClawReviewJob(
        source_task_id=req.source_task_id,
        status="ready",
        scope=req.scope,
        requested_by=_service_actor(service_user),
        note=(req.note or "").strip(),
        total_results=int(summary["total_results"]),
        pending_results=int(summary["pending_results"]),
        reviewable_results=int(summary["reviewable_results"]),
        green_count=int(summary["green_count"]),
        yellow_count=int(summary["yellow_count"]),
        red_count=int(summary["red_count"]),
        reviewed_pending_count=int(summary["reviewed_pending_count"]),
        summary=summary["summary"],
    )
    db.add(review_job)
    await db.commit()
    await db.refresh(review_job)
    return OpenClawReviewJobResponse.model_validate(review_job)


@router.get("/review-jobs/{review_job_id}", response_model=OpenClawReviewJobResponse)
async def get_review_job(
    review_job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    reader: User = Depends(get_openclaw_read_user),
):
    _ = reader
    result = await db.execute(
        select(OpenClawReviewJob).where(OpenClawReviewJob.id == review_job_id)
    )
    review_job = result.scalar_one_or_none()
    if not review_job:
        raise HTTPException(status_code=404, detail="review job not found")
    return OpenClawReviewJobResponse.model_validate(review_job)


@router.get("/provinces")
async def list_provinces(reader: User = Depends(get_openclaw_read_user)):
    return await quota_search_api.list_search_provinces(user=reader)


@router.get("/reference/item-price", response_model=ItemPriceReferenceResponse)
async def get_openclaw_item_price_reference(
    q: str = Query(description="设备/材料查询词"),
    specialty: str = Query(default="", description="专业"),
    brand: str = Query(default="", description="品牌"),
    model: str = Query(default="", description="型号"),
    region: str = Query(default="", description="地区"),
    top_k: int = Query(default=20, ge=1, le=100),
    reader: User = Depends(get_openclaw_read_user),
):
    _ = reader
    return reference_api._get_item_price_reference(
        q=q,
        specialty=specialty,
        brand=brand,
        model=model,
        region=region,
        top_k=top_k,
    )


@router.get("/reference/composite-price", response_model=CompositePriceReferenceResponse)
async def get_openclaw_composite_price_reference(
    q: str = Query(description="清单/综合单价查询词"),
    specialty: str = Query(default="", description="专业"),
    quota_code: str = Query(default="", description="定额编号"),
    region: str = Query(default="", description="地区"),
    top_k: int = Query(default=20, ge=1, le=100),
    reader: User = Depends(get_openclaw_read_user),
):
    _ = reader
    return reference_api._get_composite_price_reference(
        q=q,
        specialty=specialty,
        quota_code=quota_code,
        region=region,
        top_k=top_k,
    )


@router.get("/quota-search")
async def search_quotas(
    keyword: str = Query(description="搜索关键词"),
    province: str = Query(description="省份定额库名称"),
    book: str | None = Query(default=None, description="册号"),
    chapter: str | None = Query(default=None, description="章节"),
    limit: int = Query(default=20, ge=1, le=100, description="最大返回条数"),
    reader: User = Depends(get_openclaw_read_user),
):
    return await quota_search_api.search_quotas(
        keyword=keyword,
        province=province,
        book=book,
        chapter=chapter,
        limit=limit,
        user=reader,
    )


@router.get("/quota-search/by-id")
async def get_quota_by_id(
    quota_id: str = Query(description="定额编号"),
    province: str = Query(description="省份定额库名称"),
    reader: User = Depends(get_openclaw_read_user),
):
    return await quota_search_api.get_quota_by_id(
        quota_id=quota_id,
        province=province,
        user=reader,
    )


@router.get("/quota-search/smart")
async def smart_search(
    name: str = Query(description="清单名称原文"),
    province: str = Query(description="省份定额库名称"),
    description: str = Query(default="", description="补充描述"),
    specialty: str = Query(default="", description="专业册号"),
    limit: int = Query(default=10, ge=1, le=50, description="最大返回条数"),
    reader: User = Depends(get_openclaw_read_user),
):
    return await quota_search_api.smart_search(
        name=name,
        province=province,
        description=description,
        specialty=specialty,
        limit=limit,
        user=reader,
    )


@router.get("/qmd-search", response_model=QMDSearchResponse)
async def qmd_search(
    q: str = Query(description="QMD knowledge query"),
    top_k: int = Query(default=5, ge=1, le=20),
    category: str = Query(default="", description="QMD category filter"),
    page_type: str = Query(default="", description="QMD page type filter"),
    province: str = Query(default="", description="Province filter"),
    specialty: str = Query(default="", description="Specialty filter"),
    source_kind: str = Query(default="", description="Source kind filter"),
    status: str = Query(default="", description="Status filter"),
    reader: User = Depends(get_openclaw_read_user),
):
    _ = reader
    service = get_default_qmd_service()
    request = QMDSearchRequest(
        query=q,
        top_k=top_k,
        category=category,
        page_type=page_type,
        province=province,
        specialty=specialty,
        source_kind=source_kind,
        status=status,
    )
    try:
        return await asyncio.to_thread(service.search, request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/tasks", response_model=TaskResponse, status_code=201)
async def create_task(
    file: UploadFile = File(description="清单 Excel 文件"),
    province: str = Form(description="省份定额库名称"),
    mode: str | None = Form(default=None, description="匹配模式"),
    sheet: str | None = Form(default=None, description="指定 Sheet"),
    limit_count: int | None = Form(default=None, description="限制处理条数"),
    use_experience: bool = Form(default=True, description="是否使用经验库"),
    db: AsyncSession = Depends(get_db),
    service_user: User = Depends(get_openclaw_service_user),
):
    return await tasks_api.create_task(
        file=file,
        province=province,
        mode=mode,
        sheet=sheet,
        limit_count=limit_count,
        use_experience=use_experience,
        db=db,
        user=service_user,
    )


@router.get("/tasks", response_model=TaskListResponse)
async def list_tasks(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    status_filter: str | None = Query(default=None),
    created_after: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    reader: User = Depends(get_openclaw_read_user),
):
    return await tasks_api.list_tasks(
        page=page,
        size=size,
        status_filter=status_filter,
        created_after=created_after,
        all_users=True,
        db=db,
        user=reader,
    )


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    reader: User = Depends(get_openclaw_read_user),
):
    return await tasks_api.get_task(
        task_id=task_id,
        db=db,
        user=reader,
    )


@router.get("/tasks/{task_id}/results", response_model=ResultListResponse)
async def list_results(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    reader: User = Depends(get_openclaw_read_user),
):
    return await results_api.list_results(
        task_id=task_id,
        db=db,
        user=reader,
    )


@router.get("/tasks/{task_id}/review-items", response_model=ResultListResponse)
async def list_review_items(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    reader: User = Depends(get_openclaw_read_user),
):
    await get_user_task(task_id, reader, db)
    result = await db.execute(
        select(MatchResult)
        .where(MatchResult.task_id == task_id)
        .order_by(MatchResult.index)
    )
    items = [
        item
        for item in result.scalars().all()
        if _openclaw_policy_bucket(item) in {"yellow", "red"}
        or _is_reviewed_pending_confirm(item)
    ]
    return _build_result_list_response(items)


@router.get("/tasks/{task_id}/review-pending", response_model=ResultListResponse)
async def list_review_pending(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    await get_user_task(task_id, user, db)
    result = await db.execute(
        select(MatchResult)
        .where(MatchResult.task_id == task_id)
        .order_by(MatchResult.index)
    )
    items = [item for item in result.scalars().all() if _is_reviewed_pending_confirm(item)]
    return _build_result_list_response(items)


@router.get("/tasks/{task_id}/results/{result_id}", response_model=MatchResultResponse)
async def get_result(
    task_id: uuid.UUID,
    result_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    reader: User = Depends(get_openclaw_read_user),
):
    return await results_api.get_result(
        task_id=task_id,
        result_id=result_id,
        db=db,
        user=reader,
    )


@router.put("/tasks/{task_id}/results/{result_id}", response_model=MatchResultResponse, deprecated=True)
async def legacy_save_review_draft(
    task_id: uuid.UUID,
    result_id: uuid.UUID,
    req: CorrectResultRequest,
    db: AsyncSession = Depends(get_db),
    service_user: User = Depends(get_openclaw_service_user),
):
    if not req.corrected_quotas:
        raise HTTPException(
            status_code=409,
            detail="OpenClaw 直接正式操作已禁用。绿灯请走确认接口，黄灯请改用 /review-draft 保存审核建议。",
        )
    return await _save_review_draft(
        task_id=task_id,
        result_id=result_id,
        req=OpenClawReviewDraftRequest(
            openclaw_suggested_quotas=req.corrected_quotas,
            openclaw_review_note=req.review_note or "",
            openclaw_review_confidence=None,
            openclaw_decision_type="override_within_candidates" if req.corrected_quotas else None,
        ),
        db=db,
        service_user=service_user,
    )


@router.put("/tasks/{task_id}/results/{result_id}/review-draft", response_model=MatchResultResponse)
async def save_review_draft(
    task_id: uuid.UUID,
    result_id: uuid.UUID,
    req: OpenClawReviewDraftRequest,
    db: AsyncSession = Depends(get_db),
    service_user: User = Depends(get_openclaw_service_user),
):
    return await _save_review_draft(
        task_id=task_id,
        result_id=result_id,
        req=req,
        db=db,
        service_user=service_user,
    )


@router.post("/tasks/{task_id}/results/{result_id}/auto-review", response_model=OpenClawAutoReviewResponse)
async def auto_review_result(
    task_id: uuid.UUID,
    result_id: uuid.UUID,
    req: OpenClawAutoReviewRequest,
    db: AsyncSession = Depends(get_db),
    service_user: User = Depends(get_openclaw_service_user),
):
    task, scope, review_job = await _resolve_auto_review_run(
        task_id=task_id,
        requested_scope="yellow_red_pending",
        review_job_id=req.review_job_id,
        db=db,
    )
    _, match_result = await _get_match_result(
        task_id=task_id,
        result_id=result_id,
        db=db,
        owner=service_user,
    )
    reviewable = _matches_review_job_scope(match_result, scope)
    if not reviewable or not _is_openclaw_auto_review_pending(match_result):
        return OpenClawAutoReviewResponse(
            result_id=result_id,
            source_task_id=task_id,
            review_job_id=review_job.id if review_job else None,
            status="skipped",
            decision_type=str(getattr(match_result, "openclaw_decision_type", "") or "").strip() or None,
            openclaw_review_status=_normalized_openclaw_review_status(match_result),
            reviewable=reviewable,
            note="result is not eligible for a fresh auto-review draft",
        )

    draft_req = await _build_auto_review_draft_request(task, match_result)
    response = await _apply_review_draft(
        match_result=match_result,
        req=draft_req,
        service_user=service_user,
        enforce_bucket_policy=False,
    )
    await db.flush()
    return OpenClawAutoReviewResponse(
        result_id=result_id,
        source_task_id=task_id,
        review_job_id=review_job.id if review_job else None,
        status="drafted",
        decision_type=response.openclaw_decision_type,
        openclaw_review_status=response.openclaw_review_status,
        reviewable=True,
        note=response.openclaw_review_note,
    )


@router.post("/tasks/{task_id}/results/batch-auto-review", response_model=OpenClawBatchAutoReviewResponse)
async def batch_auto_review_results(
    task_id: uuid.UUID,
    req: OpenClawBatchAutoReviewRequest,
    db: AsyncSession = Depends(get_db),
    service_user: User = Depends(get_openclaw_service_user),
):
    task, scope, review_job = await _resolve_auto_review_run(
        task_id=task_id,
        requested_scope=req.scope,
        review_job_id=req.review_job_id,
        db=db,
    )
    if review_job and str(review_job.status or "").strip().lower() == "completed":
        raise HTTPException(status_code=409, detail="review job already completed")

    result = await db.execute(
        select(MatchResult)
        .where(MatchResult.task_id == task_id)
        .order_by(MatchResult.index)
    )
    candidates = [
        item
        for item in result.scalars().all()
        if _matches_review_job_scope(item, scope)
    ]
    if req.limit is not None:
        candidates = candidates[: req.limit]

    drafted_count = 0
    skipped_count = 0
    failed_count = 0
    processed_result_ids: list[uuid.UUID] = []
    await _mark_review_job_running(review_job, db)

    try:
        for match_result in candidates:
            if not _is_openclaw_auto_review_pending(match_result):
                skipped_count += 1
                continue
            try:
                draft_req = await _build_auto_review_draft_request(task, match_result)
                await _apply_review_draft(
                    match_result=match_result,
                    req=draft_req,
                    service_user=service_user,
                    enforce_bucket_policy=False,
                )
                drafted_count += 1
                processed_result_ids.append(match_result.id)
            except HTTPException:
                failed_count += 1
            except Exception:
                failed_count += 1

        await _finalize_review_job(
            review_job=review_job,
            task=task,
            scope=scope,
            drafted_count=drafted_count,
            skipped_count=skipped_count,
            failed_count=failed_count,
            db=db,
        )
        await db.commit()
    except Exception as exc:
        if review_job:
            review_job.status = "failed"
            review_job.error_message = str(exc)[:1000]
            review_job.completed_at = _utcnow()
            await db.commit()
        raise

    return OpenClawBatchAutoReviewResponse(
        review_job_id=review_job.id if review_job else None,
        source_task_id=task_id,
        scope=scope,
        total_candidates=len(candidates),
        drafted_count=drafted_count,
        skipped_count=skipped_count,
        failed_count=failed_count,
        processed_result_ids=processed_result_ids,
    )


@router.post("/report-analyze", response_model=OpenClawAuditReportAnalyzeResponse)
async def analyze_report(
    req: OpenClawAuditReportAnalyzeRequest,
    user: User = Depends(get_openclaw_read_user),
):
    return _analyze_openclaw_audit_report(req)


@router.post("/tasks/{task_id}/results/{result_id}/review-confirm", response_model=MatchResultResponse)
async def review_confirm(
    task_id: uuid.UUID,
    result_id: uuid.UUID,
    req: OpenClawReviewConfirmRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    task, match_result = await _get_match_result(
        task_id=task_id,
        result_id=result_id,
        db=db,
        owner=user,
    )

    decision = (req.decision or "").strip().lower()
    if decision not in {"approve", "reject"}:
        raise HTTPException(status_code=422, detail="decision 仅支持 approve 或 reject")
    repaired_suggested_quotas, repaired_changed = _repair_suggested_quotas_from_result(
        match_result,
        match_result.openclaw_suggested_quotas,
    )
    if repaired_changed:
        match_result.openclaw_suggested_quotas = repaired_suggested_quotas or None
        if isinstance(match_result.openclaw_review_payload, dict):
            payload = dict(match_result.openclaw_review_payload)
            payload["suggested_quotas"] = repaired_suggested_quotas
            match_result.openclaw_review_payload = payload
    if not match_result.openclaw_suggested_quotas:
        raise HTTPException(status_code=409, detail="当前结果还没有 OpenClaw 审核建议")
    if match_result.openclaw_review_status == "applied":
        raise HTTPException(status_code=409, detail="当前建议已经正式应用，无需再次确认")

    actor = _human_actor(user)
    now = _utcnow()
    normalized_feedback = _normalize_human_feedback_payload(
        match_result,
        req.human_feedback_payload,
        actor=actor,
        decision=decision,
    )

    if decision == "reject":
        match_result.openclaw_review_status = "rejected"
        match_result.openclaw_review_confirm_status = "rejected"
        match_result.openclaw_review_confirmed_by = actor
        match_result.openclaw_review_confirm_time = now
        match_result.human_feedback_payload = normalized_feedback
        if req.review_note.strip():
            match_result.openclaw_review_note = _merge_review_notes(
                match_result.openclaw_review_note,
                f"人工驳回: {req.review_note.strip()}",
            )
        await db.flush()
        return results_api._to_result_response(match_result)

    final_corrected_quotas = repaired_suggested_quotas
    if normalized_feedback and not bool(normalized_feedback.get("adopt_openclaw", True)):
        feedback_final = [
            _normalize_quota_dict(item)
            for item in list(normalized_feedback.get("final_quotas") or [])
            if isinstance(item, dict)
        ]
        if feedback_final:
            final_corrected_quotas = feedback_final

    final_review_note = _merge_review_notes(
        match_result.openclaw_review_note,
        str(normalized_feedback.get("manual_note") or "") if normalized_feedback else "",
        f"人工二次确认: {req.review_note.strip()}" if req.review_note.strip() else "",
    )
    await results_api.correct_result(
        task_id=task_id,
        result_id=result_id,
        req=CorrectResultRequest(
            corrected_quotas=final_corrected_quotas,
            review_note=final_review_note,
        ),
        db=db,
        user=user,
    )

    match_result.openclaw_review_status = "applied"
    match_result.openclaw_review_confirm_status = "approved"
    match_result.openclaw_review_confirmed_by = actor
    match_result.openclaw_review_confirm_time = now
    match_result.human_feedback_payload = normalized_feedback
    if isinstance(match_result.openclaw_review_payload, dict):
        payload = dict(match_result.openclaw_review_payload)
        payload["human_feedback_payload"] = normalized_feedback
        payload["jarvis_absorbable_report"] = _merge_feedback_into_absorbable_report(
            payload.get("jarvis_absorbable_report"),
            feedback=normalized_feedback,
            final_quotas=final_corrected_quotas,
            actor=actor,
        )
        match_result.openclaw_review_payload = payload
    if final_review_note:
        match_result.openclaw_review_note = final_review_note
    await db.flush()

    # Best-effort: map real approved OpenClaw review results into staging.
    # This must not block the formal correction flow.
    from app.services.openclaw_staging import record_openclaw_approved_review_async
    await record_openclaw_approved_review_async(
        task,
        match_result,
        actor=actor,
        review_note=req.review_note or "",
    )
    return results_api._to_result_response(match_result)


@router.post("/tasks/{task_id}/results/auto-confirm-green")
async def auto_confirm_green(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    service_user: User = Depends(get_openclaw_service_user),
):
    green_ids = await _collect_green_result_ids(
        task_id=task_id,
        db=db,
        service_user=service_user,
    )
    if not green_ids:
        return {
            "confirmed": 0,
            "skipped_corrected": 0,
            "skipped_low_confidence": 0,
            "total": 0,
        }
    return await results_api.confirm_results(
        task_id=task_id,
        req=ConfirmResultsRequest(result_ids=green_ids),
        db=db,
        user=service_user,
    )


@router.post("/tasks/{task_id}/results/confirm")
async def confirm_results(
    task_id: uuid.UUID,
    req: ConfirmResultsRequest,
    db: AsyncSession = Depends(get_db),
    service_user: User = Depends(get_openclaw_service_user),
):
    await get_user_task(task_id, service_user, db)
    result = await db.execute(
        select(
            MatchResult.id,
            MatchResult.light_status,
            MatchResult.confidence_score,
            MatchResult.confidence,
            MatchResult.review_status,
        ).where(
            MatchResult.task_id == task_id,
            MatchResult.id.in_(req.result_ids),
        )
    )
    rows = result.all()
    green_ids: list[uuid.UUID] = []
    skipped_corrected = 0
    skipped_non_green = 0

    for row in rows:
        if row.review_status == "corrected":
            skipped_corrected += 1
            continue
        if _openclaw_policy_bucket(row) != "green":
            skipped_non_green += 1
            continue
        green_ids.append(row.id)

    if not green_ids:
        return {
            "confirmed": 0,
            "skipped_corrected": skipped_corrected,
            "skipped_low_confidence": skipped_non_green,
            "total": len(rows),
        }

    payload = await results_api.confirm_results(
        task_id=task_id,
        req=ConfirmResultsRequest(result_ids=green_ids),
        db=db,
        user=service_user,
    )
    payload["skipped_corrected"] = payload.get("skipped_corrected", 0) + skipped_corrected
    payload["skipped_low_confidence"] = payload.get("skipped_low_confidence", 0) + skipped_non_green
    payload["total"] = len(rows)
    return payload


@router.get("/tasks/{task_id}/export")
async def export_results(
    task_id: uuid.UUID,
    materials: bool = False,
    db: AsyncSession = Depends(get_db),
    service_user: User = Depends(get_openclaw_service_user),
):
    return await results_api.export_results(
        task_id=task_id,
        materials=materials,
        db=db,
        user=service_user,
    )


@router.get("/tasks/{task_id}/export-final")
async def export_final(
    task_id: uuid.UUID,
    materials: bool = False,
    db: AsyncSession = Depends(get_db),
    service_user: User = Depends(get_openclaw_service_user),
):
    return await results_api.export_final(
        task_id=task_id,
        materials=materials,
        db=db,
        user=service_user,
    )
