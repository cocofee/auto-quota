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
from app.api import results as results_api
from app.api import tasks as tasks_api
from app.api.shared import get_user_task
from app.auth.openclaw import get_openclaw_read_user, get_openclaw_service_user
from app.auth.permissions import require_admin
from app.config import OPENCLAW_API_KEY, OPENCLAW_SERVICE_EMAIL, OPENCLAW_SERVICE_NICKNAME
from app.database import get_db
from app.models.result import MatchResult
from app.models.user import User
from app.schemas.result import (
    ConfirmResultsRequest,
    CorrectResultRequest,
    MatchResultResponse,
    OpenClawReviewConfirmRequest,
    OpenClawReviewDraftRequest,
    ResultListResponse,
)
from app.schemas.task import TaskListResponse, TaskResponse
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
    _ensure_openclaw_reviewable(_openclaw_policy_bucket(match_result))

    match_result.openclaw_review_status = "reviewed"
    match_result.openclaw_suggested_quotas = [item.model_dump() for item in req.openclaw_suggested_quotas]
    match_result.openclaw_review_note = req.openclaw_review_note or ""
    match_result.openclaw_review_confidence = req.openclaw_review_confidence
    match_result.openclaw_review_actor = _service_actor(service_user)
    match_result.openclaw_review_time = _utcnow()
    match_result.openclaw_review_confirm_status = "pending"
    match_result.openclaw_review_confirmed_by = ""
    match_result.openclaw_review_confirm_time = None

    await db.flush()
    return results_api._to_result_response(match_result)


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
        manifest_paths=["lzc-manifest.yml", "deploy/lazycat/lzc-manifest.yml"],
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


@router.get("/provinces")
async def list_provinces(reader: User = Depends(get_openclaw_read_user)):
    return await quota_search_api.list_search_provinces(user=reader)


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
    return _build_result_list_response(result.scalars().all())


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
