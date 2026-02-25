"""
准确率分析 API（管理员专属）

路由挂载在 /api/admin/analytics 前缀下:
    GET  /api/admin/analytics/overview      — 总体统计
    GET  /api/admin/analytics/trends        — 按日期趋势（最近30天）
    GET  /api/admin/analytics/by-province   — 按省份统计
    GET  /api/admin/analytics/by-specialty  — 按专业统计
"""

from fastapi import APIRouter, Depends
from sqlalchemy import select, func, cast, Date
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.models.task import Task
from app.models.result import MatchResult
from app.auth.permissions import require_admin

router = APIRouter()


@router.get("/overview")
async def analytics_overview(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """总体统计概览"""
    # 总任务数
    total_tasks = (await db.execute(
        select(func.count()).select_from(Task)
    )).scalar() or 0

    # 已完成任务数
    completed_tasks = (await db.execute(
        select(func.count()).select_from(Task).where(Task.status == "completed")
    )).scalar() or 0

    # 总匹配条数
    total_results = (await db.execute(
        select(func.count()).select_from(MatchResult)
    )).scalar() or 0

    # 高置信度条数（>=85）
    high_conf = (await db.execute(
        select(func.count()).select_from(MatchResult).where(MatchResult.confidence >= 85)
    )).scalar() or 0

    # 中置信度条数（70-84）
    mid_conf = (await db.execute(
        select(func.count()).select_from(MatchResult).where(
            MatchResult.confidence >= 70, MatchResult.confidence < 85
        )
    )).scalar() or 0

    # 低置信度条数（<70）
    low_conf = (await db.execute(
        select(func.count()).select_from(MatchResult).where(MatchResult.confidence < 70)
    )).scalar() or 0

    # 平均置信度
    avg_conf = (await db.execute(
        select(func.avg(MatchResult.confidence))
    )).scalar()

    # 已确认结果数
    confirmed = (await db.execute(
        select(func.count()).select_from(MatchResult).where(
            MatchResult.review_status == "confirmed"
        )
    )).scalar() or 0

    # 总用户数
    total_users = (await db.execute(
        select(func.count()).select_from(User)
    )).scalar() or 0

    return {
        "total_tasks": total_tasks,
        "completed_tasks": completed_tasks,
        "total_results": total_results,
        "high_confidence": high_conf,
        "mid_confidence": mid_conf,
        "low_confidence": low_conf,
        "avg_confidence": round(avg_conf, 1) if avg_conf else 0,
        "confirmed_results": confirmed,
        "total_users": total_users,
    }


@router.get("/trends")
async def analytics_trends(
    days: int = 30,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """按日期的任务趋势（最近N天）

    返回每天的任务数和平均置信度。
    """
    if days < 1 or days > 365:
        days = 30

    # 按日期分组查询已完成任务
    query = (
        select(
            cast(Task.completed_at, Date).label("date"),
            func.count().label("task_count"),
        )
        .where(Task.status == "completed", Task.completed_at.isnot(None))
        .group_by(cast(Task.completed_at, Date))
        .order_by(cast(Task.completed_at, Date).desc())
        .limit(days)
    )
    rows = (await db.execute(query)).all()

    items = []
    for row in reversed(rows):  # 按时间正序
        items.append({
            "date": row.date.isoformat() if row.date else "",
            "task_count": row.task_count,
        })

    return {"items": items, "days": days}


@router.get("/by-province")
async def analytics_by_province(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """按省份统计"""
    query = (
        select(
            Task.province,
            func.count().label("task_count"),
        )
        .where(Task.status == "completed")
        .group_by(Task.province)
        .order_by(func.count().desc())
    )
    rows = (await db.execute(query)).all()

    items = []
    for row in rows:
        items.append({
            "province": row.province,
            "task_count": row.task_count,
        })

    return {"items": items}


@router.get("/by-specialty")
async def analytics_by_specialty(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """按专业统计"""
    query = (
        select(
            MatchResult.specialty,
            func.count().label("count"),
            func.avg(MatchResult.confidence).label("avg_confidence"),
        )
        .where(MatchResult.specialty.isnot(None), MatchResult.specialty != "")
        .group_by(MatchResult.specialty)
        .order_by(func.count().desc())
    )
    rows = (await db.execute(query)).all()

    items = []
    for row in rows:
        items.append({
            "specialty": row.specialty or "未分类",
            "count": row.count,
            "avg_confidence": round(row.avg_confidence, 1) if row.avg_confidence else 0,
        })

    return {"items": items}
