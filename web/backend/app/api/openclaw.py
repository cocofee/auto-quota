"""
OpenClaw bridge API.

This router exposes a stable subset of auto-quota APIs for OpenClaw, plus the
"review draft + human second confirmation" workflow.
"""

from __future__ import annotations

import asyncio
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
from app.text_utils import repair_mojibake_data, repair_quota_name_loss
from pydantic import BaseModel, Field

router = APIRouter()
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


class OpenClawPromotionCardCreateResponse(BaseModel):
    id: int
    source_table: str
    source_record_id: str
    target_layer: str
    status: str
    review_status: str


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
        if item.openclaw_review_status == "reviewed"
        and item.openclaw_review_confirm_status == "pending"
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


def _is_reviewed_pending_confirm(result_or_row) -> bool:
    review_status = str(getattr(result_or_row, "openclaw_review_status", "") or "").strip().lower()
    confirm_status = str(
        getattr(result_or_row, "openclaw_review_confirm_status", "") or ""
    ).strip().lower()
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
    return str(getattr(result_or_row, "openclaw_review_status", "") or "").strip().lower() in {
        "",
        "pending",
    }


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
    for key in ("source", "param_score", "rerank_score"):
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
    for key in ("source", "param_score", "rerank_score"):
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


def _build_auto_review_note(
    *,
    decision_type: str,
    bucket: str,
    issue_types: list[str],
    has_current_quota: bool,
    has_candidate_pool: bool,
) -> str:
    if decision_type == "override_within_candidates" and not has_current_quota:
        return "Jarvis 未稳定产出 top1，OpenClaw 从候选池提升一个候选供人工复核。"
    if decision_type == "override_within_candidates":
        return "OpenClaw 根据终检/候选证据建议在现有候选池内改判。"
    if decision_type == "candidate_pool_insufficient":
        return "当前候选池不足以形成可执行建议，需补充召回或人工处理。"
    if bucket == "green":
        return "Jarvis 结果处于高置信区间，OpenClaw 未发现足够强的改判证据。"
    if issue_types:
        return "OpenClaw 保留 Jarvis 当前 top1，但记录了需要人工关注的诊断信号。"
    if has_candidate_pool:
        return "OpenClaw 复核后暂不改判，保留 Jarvis 当前选择并附带候选池上下文。"
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


def _build_auto_review_draft_request(task: Task, match_result: MatchResult) -> OpenClawReviewDraftRequest:
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

    correction_quota = _find_candidate_by_ref(
        candidate_pool=candidate_pool,
        quotas=current_quotas,
        target_quota_id=str(final_review_correction.get("quota_id") or "").strip(),
        target_name=str(final_review_correction.get("quota_name") or "").strip(),
    )
    if correction_quota:
        decision_type = "override_within_candidates"
        suggested_quotas = [correction_quota]
    elif _should_retry_search_then_select(
        issue_types=issue_types,
        current_quotas=current_quotas,
        candidate_pool=candidate_pool,
    ):
        decision_type = "retry_search_then_select"
        suggested_quotas = []
    elif not current_quotas and candidate_pool:
        decision_type = "override_within_candidates"
        suggested_quotas = [_candidate_to_quota(candidate_pool[0])]
    elif current_quotas:
        decision_type = "agree"
        suggested_quotas = list(current_quotas)
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
    )
    note = _build_auto_review_note(
        decision_type=decision_type,
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
        .where(
            MatchResult.task_id == task_id,
            MatchResult.openclaw_review_status == "reviewed",
            MatchResult.openclaw_review_confirm_status == "pending",
        )
        .order_by(MatchResult.index)
    )
    return _build_result_list_response(result.scalars().all())


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
            openclaw_review_status=str(getattr(match_result, "openclaw_review_status", "") or "pending"),
            reviewable=reviewable,
            note="result is not eligible for a fresh auto-review draft",
        )

    draft_req = _build_auto_review_draft_request(task, match_result)
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
                draft_req = _build_auto_review_draft_request(task, match_result)
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

    if decision == "reject":
        match_result.openclaw_review_status = "rejected"
        match_result.openclaw_review_confirm_status = "rejected"
        match_result.openclaw_review_confirmed_by = actor
        match_result.openclaw_review_confirm_time = now
        match_result.human_feedback_payload = req.human_feedback_payload
        if req.review_note.strip():
            match_result.openclaw_review_note = _merge_review_notes(
                match_result.openclaw_review_note,
                f"人工驳回: {req.review_note.strip()}",
            )
        await db.flush()
        return results_api._to_result_response(match_result)

    final_review_note = _merge_review_notes(
        match_result.openclaw_review_note,
        f"人工二次确认: {req.review_note.strip()}" if req.review_note.strip() else "",
    )
    await results_api.correct_result(
        task_id=task_id,
        result_id=result_id,
        req=CorrectResultRequest(
            corrected_quotas=match_result.openclaw_suggested_quotas,
            review_note=final_review_note,
        ),
        db=db,
        user=user,
    )

    match_result.openclaw_review_status = "applied"
    match_result.openclaw_review_confirm_status = "approved"
    match_result.openclaw_review_confirmed_by = actor
    match_result.openclaw_review_confirm_time = now
    match_result.human_feedback_payload = req.human_feedback_payload
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
