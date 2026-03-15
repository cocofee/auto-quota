"""
匹配结果 API

提供匹配结果的查看、纠正、批量确认和Excel导出。

路由挂载在 /api 前缀下:
    GET    /api/tasks/{id}/results              — 结果列表
    GET    /api/tasks/{id}/results/{result_id}  — 单条结果详情
    PUT    /api/tasks/{id}/results/{result_id}  — 纠正结果
    POST   /api/tasks/{id}/results/confirm      — 批量确认
    GET    /api/tasks/{id}/export               — 导出Excel
"""

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.result import MatchResult
from app.models.user import User
from app.auth.deps import get_current_user
from app.schemas.result import (
    MatchResultResponse, ResultListResponse,
    CorrectResultRequest, ConfirmResultsRequest,
)
from app.api.shared import get_user_task, store_experience, store_experience_batch

router = APIRouter()

# 置信度分档阈值（必须与 config.py CONFIDENCE_GREEN/YELLOW 和前端 experience.ts 保持一致）
# 修改时三处同步：config.py:585-586 / experience.ts:12-13 / 此处
_GREEN_THRESHOLD = 85
_YELLOW_THRESHOLD = 70


@router.get("/tasks/{task_id}/results", response_model=ResultListResponse)
async def list_results(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """获取任务的匹配结果列表

    返回所有匹配结果（按序号排序），附带置信度分布统计。
    """
    await get_user_task(task_id, user, db)

    # 查询所有结果
    result = await db.execute(
        select(MatchResult)
        .where(MatchResult.task_id == task_id)
        .order_by(MatchResult.index)
    )
    items = result.scalars().all()

    # 统计置信度分布
    total = len(items)
    high_conf = sum(1 for r in items if r.confidence >= _GREEN_THRESHOLD)
    mid_conf = sum(1 for r in items if _YELLOW_THRESHOLD <= r.confidence < _GREEN_THRESHOLD)
    low_conf = sum(1 for r in items if r.confidence < _YELLOW_THRESHOLD)
    no_match = sum(1 for r in items if not r.quotas)

    summary = {
        "total": total,
        "high_confidence": high_conf,
        "mid_confidence": mid_conf,
        "low_confidence": low_conf,
        "no_match": no_match,
    }

    return ResultListResponse(items=items, total=total, summary=summary)


@router.get("/tasks/{task_id}/results/{result_id}", response_model=MatchResultResponse)
async def get_result(
    task_id: uuid.UUID,
    result_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """获取单条匹配结果详情

    包含清单信息、匹配定额、置信度、匹配说明等。
    """
    await get_user_task(task_id, user, db)

    result = await db.execute(
        select(MatchResult).where(
            MatchResult.id == result_id,
            MatchResult.task_id == task_id,
        )
    )
    match_result = result.scalar_one_or_none()
    if not match_result:
        raise HTTPException(status_code=404, detail="结果不存在")
    return match_result


@router.put("/tasks/{task_id}/results/{result_id}", response_model=MatchResultResponse)
async def correct_result(
    task_id: uuid.UUID,
    result_id: uuid.UUID,
    req: CorrectResultRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """纠正或确认匹配结果

    两种用法：
    1. 纠正：传 corrected_quotas → review_status 变为 "corrected"
    2. 确认：传 review_status="confirmed"（不传 corrected_quotas）→ 直接确认
    兼容 OpenClaw 等外部工具直接调 PUT 接口的场景。
    """
    task = await get_user_task(task_id, user, db)

    result = await db.execute(
        select(MatchResult).where(
            MatchResult.id == result_id,
            MatchResult.task_id == task_id,
        )
    )
    match_result = result.scalar_one_or_none()
    if not match_result:
        raise HTTPException(status_code=404, detail="结果不存在")

    # 场景1：只是确认（没传 corrected_quotas）
    if not req.corrected_quotas:
        match_result.review_status = req.review_status or "confirmed"
        match_result.review_note = req.review_note
        await db.flush()

        # 确认数据回流经验库权威层
        quotas_data = match_result.quotas
        if quotas_data and match_result.review_status == "confirmed":
            await store_experience(
                name=match_result.bill_name,
                desc=match_result.bill_description or "",
                quota_ids=[q["quota_id"] for q in quotas_data if q.get("quota_id")],
                quota_names=[q.get("name", "") for q in quotas_data],
                reason=f"API确认: {req.review_note or ''}",
                specialty=match_result.specialty or "",
                province=task.province,
                confirmed=True,  # 确认 → 权威层
            )
        return match_result

    # 场景2：纠正（传了 corrected_quotas）
    match_result.corrected_quotas = [q.model_dump() for q in req.corrected_quotas]
    match_result.review_status = "corrected"
    match_result.review_note = req.review_note
    await db.flush()

    # 纠正数据回流经验库候选层
    await store_experience(
        name=match_result.bill_name,
        desc=match_result.bill_description or "",
        quota_ids=[q.quota_id for q in req.corrected_quotas],
        quota_names=[q.name for q in req.corrected_quotas],
        reason=f"Web端纠正: {req.review_note or ''}",
        specialty=match_result.specialty or "",
        province=task.province,
        confirmed=False,  # 纠正 → 候选层
    )

    return match_result


@router.post("/tasks/{task_id}/results/confirm")
async def confirm_results(
    task_id: uuid.UUID,
    req: ConfirmResultsRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """批量确认匹配结果

    用户确认系统匹配正确的结果（通常是高置信度的绿色项）。
    确认后 review_status 变为 "confirmed"。
    """
    await get_user_task(task_id, user, db)

    # 批量查询要确认的结果
    result = await db.execute(
        select(MatchResult).where(
            MatchResult.task_id == task_id,
            MatchResult.id.in_(req.result_ids),
        )
    )
    results = result.scalars().all()

    updated = 0
    skipped = 0
    confirmed_records = []  # 收集需要回流经验库的记录
    for r in results:
        # 已纠正的结果不能被批量确认覆盖（保留人工纠正状态）
        if r.review_status == "corrected":
            skipped += 1
            continue
        if r.review_status != "confirmed":
            r.review_status = "confirmed"
            updated += 1
            # 收集数据用于经验库写入（优先用纠正后的定额）
            quotas_data = r.corrected_quotas or r.quotas
            if quotas_data:
                confirmed_records.append({
                    "name": r.bill_name,
                    "desc": r.bill_description or "",
                    "quota_ids": [q["quota_id"] for q in quotas_data if q.get("quota_id")],
                    "quota_names": [q.get("name", "") for q in quotas_data],
                    "specialty": r.specialty or "",
                })

    await db.flush()

    # 确认数据回流经验库（权威层，系统匹配+用户确认=双重保障）
    if confirmed_records:
        task = await get_user_task(task_id, user, db)
        await store_experience_batch(
            records=confirmed_records,
            province=task.province,
            reason="Web端确认",
            confirmed=True,  # 确认 → 权威层
        )

    return {"confirmed": updated, "skipped_corrected": skipped, "total": len(results)}


@router.get("/tasks/{task_id}/export")
async def export_results(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """导出匹配结果Excel

    下载匹配完成后生成的广联达格式Excel文件。
    """
    task = await get_user_task(task_id, user, db)

    if task.status != "completed":
        raise HTTPException(status_code=400, detail="任务尚未完成，无法导出")

    if not task.output_path or not Path(task.output_path).exists():
        raise HTTPException(status_code=404, detail="输出文件不存在")

    # 构造下载文件名（原始文件名 + _定额匹配结果）
    download_name = Path(task.original_filename).stem + "_定额匹配结果.xlsx"

    return FileResponse(
        path=task.output_path,
        filename=download_name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
