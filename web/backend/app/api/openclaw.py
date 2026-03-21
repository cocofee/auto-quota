"""
OpenClaw 专用桥接 API。

这组接口只做一件事：给 OpenClaw 暴露一套更稳定、无需网页登录态的工具接口。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.shared import get_user_task
from app.api import quota_search as quota_search_api
from app.api import results as results_api
from app.api import tasks as tasks_api
from app.auth.openclaw import get_openclaw_service_user
from app.database import get_db
from app.models.result import MatchResult
from app.models.user import User
from app.schemas.result import (
    ConfirmResultsRequest,
    CorrectResultRequest,
    MatchResultResponse,
    ResultListResponse,
)
from app.schemas.task import TaskListResponse, TaskResponse

router = APIRouter()
GREEN_THRESHOLD = results_api._GREEN_THRESHOLD
YELLOW_THRESHOLD = results_api._YELLOW_THRESHOLD


def _openclaw_policy_bucket(result_or_confidence) -> str:
    return results_api._resolve_light_status(result_or_confidence)


async def _get_match_result(
    *,
    task_id: uuid.UUID,
    result_id: uuid.UUID,
    db: AsyncSession,
    service_user: User,
) -> tuple[object, MatchResult]:
    task = await get_user_task(task_id, service_user, db)
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
    ids = []
    for row in result.all():
        if row.review_status in {"confirmed", "corrected"}:
            continue
        if _openclaw_policy_bucket(row) == "green":
            ids.append(row.id)
    return ids


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
        version="1.0.0",
        description=(
            "给 OpenClaw 使用的精简接口。"
            "所有业务接口统一使用请求头 X-OpenClaw-Key 做认证。"
        ),
        routes=routes,
    )
    schema["servers"] = [{"url": str(request.base_url).rstrip("/")}]
    return schema


@router.get("/openapi.json", include_in_schema=False)
async def openclaw_openapi(request: Request):
    """返回 OpenClaw 专用精简 OpenAPI 文档。"""
    return JSONResponse(_build_openclaw_openapi(request))


@router.get("/health")
async def health(service_user: User = Depends(get_openclaw_service_user)):
    """OpenClaw 接口健康检查。"""
    return {
        "status": "ok",
        "service": "auto-quota-openclaw",
        "actor": service_user.email,
        "openapi_url": "/api/openclaw/openapi.json",
    }


@router.get("/provinces")
async def list_provinces(service_user: User = Depends(get_openclaw_service_user)):
    """获取可用定额库列表。"""
    return await quota_search_api.list_search_provinces(user=service_user)


@router.get("/quota-search")
async def search_quotas(
    keyword: str = Query(description="搜索关键词"),
    province: str = Query(description="省份定额库名称"),
    book: str | None = Query(default=None, description="大册编号"),
    chapter: str | None = Query(default=None, description="章节"),
    limit: int = Query(default=20, ge=1, le=100, description="最大返回条数"),
    service_user: User = Depends(get_openclaw_service_user),
):
    """按关键词搜索定额。"""
    return await quota_search_api.search_quotas(
        keyword=keyword,
        province=province,
        book=book,
        chapter=chapter,
        limit=limit,
        user=service_user,
    )


@router.get("/quota-search/by-id")
async def get_quota_by_id(
    quota_id: str = Query(description="定额编号"),
    province: str = Query(description="省份定额库名称"),
    service_user: User = Depends(get_openclaw_service_user),
):
    """按定额编号精确查询。"""
    return await quota_search_api.get_quota_by_id(
        quota_id=quota_id,
        province=province,
        user=service_user,
    )


@router.get("/quota-search/smart")
async def smart_search(
    name: str = Query(description="清单名称原文"),
    province: str = Query(description="省份定额库名称"),
    description: str = Query(default="", description="补充描述"),
    specialty: str = Query(default="", description="专业册号"),
    limit: int = Query(default=10, ge=1, le=50, description="最大返回条数"),
    service_user: User = Depends(get_openclaw_service_user),
):
    """按清单原文做智能搜索。"""
    return await quota_search_api.smart_search(
        name=name,
        province=province,
        description=description,
        specialty=specialty,
        limit=limit,
        user=service_user,
    )


@router.post("/tasks", response_model=TaskResponse, status_code=201)
async def create_task(
    file: UploadFile = File(description="清单 Excel 文件"),
    province: str = Form(description="省份定额库名称"),
    mode: str | None = Form(default=None, description="匹配模式：search 或 agent"),
    sheet: str | None = Form(default=None, description="指定 Sheet"),
    limit_count: int | None = Form(default=None, description="限制处理条数"),
    use_experience: bool = Form(default=True, description="是否使用经验库"),
    db: AsyncSession = Depends(get_db),
    service_user: User = Depends(get_openclaw_service_user),
):
    """创建匹配任务。"""
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
    service_user: User = Depends(get_openclaw_service_user),
):
    """查询 OpenClaw 服务账号下的任务列表。"""
    return await tasks_api.list_tasks(
        page=page,
        size=size,
        status_filter=status_filter,
        created_after=created_after,
        all_users=False,
        db=db,
        user=service_user,
    )


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    service_user: User = Depends(get_openclaw_service_user),
):
    """查询任务状态。"""
    return await tasks_api.get_task(
        task_id=task_id,
        db=db,
        user=service_user,
    )


@router.get("/tasks/{task_id}/results", response_model=ResultListResponse)
async def list_results(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    service_user: User = Depends(get_openclaw_service_user),
):
    """查询任务结果列表。"""
    return await results_api.list_results(
        task_id=task_id,
        db=db,
        user=service_user,
    )


@router.get("/tasks/{task_id}/results/{result_id}", response_model=MatchResultResponse)
async def get_result(
    task_id: uuid.UUID,
    result_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    service_user: User = Depends(get_openclaw_service_user),
):
    """查询单条结果详情。"""
    return await results_api.get_result(
        task_id=task_id,
        result_id=result_id,
        db=db,
        user=service_user,
    )


@router.put("/tasks/{task_id}/results/{result_id}", response_model=MatchResultResponse)
async def correct_result(
    task_id: uuid.UUID,
    result_id: uuid.UUID,
    req: CorrectResultRequest,
    db: AsyncSession = Depends(get_db),
    service_user: User = Depends(get_openclaw_service_user),
):
    """按 OpenClaw 审核策略确认或纠正单条结果。"""
    task, match_result = await _get_match_result(
        task_id=task_id,
        result_id=result_id,
        db=db,
        service_user=service_user,
    )
    bucket = _openclaw_policy_bucket(match_result)

    if not req.corrected_quotas:
        if bucket != "green":
            raise HTTPException(
                status_code=409,
                detail=(
                    f"当前结果为{bucket}档，OpenClaw 仅允许自动确认绿灯(>={GREEN_THRESHOLD})结果。"
                ),
            )
        return await results_api.correct_result(
            task_id=task_id,
            result_id=result_id,
            req=req,
            db=db,
            user=service_user,
        )

    if bucket == "red":
        raise HTTPException(
            status_code=409,
            detail=(
                f"当前结果为红灯(<{YELLOW_THRESHOLD})，OpenClaw 只允许诊断，不允许提交修正。"
            ),
        )
    if bucket == "green":
        raise HTTPException(
            status_code=409,
            detail=(
                f"当前结果为绿灯(>={GREEN_THRESHOLD})，应直接确认，不应提交修正。"
            ),
        )

    corrected_quotas = [q.model_dump() for q in req.corrected_quotas]
    match_result.corrected_quotas = corrected_quotas
    match_result.review_status = "corrected"
    match_result.review_note = req.review_note or "OpenClaw yellow correction"
    await db.flush()
    return match_result


@router.post("/tasks/{task_id}/results/auto-confirm-green")
async def auto_confirm_green(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    service_user: User = Depends(get_openclaw_service_user),
):
    """自动确认当前任务里全部待确认绿灯结果。"""
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
    """按 OpenClaw 审核策略批量确认结果，只确认绿灯。"""
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
    """导出当前匹配结果 Excel。"""
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
    """导出最终版 Excel。"""
    return await results_api.export_final(
        task_id=task_id,
        materials=materials,
        db=db,
        user=service_user,
    )
