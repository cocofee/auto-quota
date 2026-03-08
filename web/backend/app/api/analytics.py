"""
准确率分析 API（管理员专属）

路由挂载在 /api/admin/analytics 前缀下:
    GET  /api/admin/analytics/overview            — 总体统计
    GET  /api/admin/analytics/trends              — 按日期趋势（最近30天）
    GET  /api/admin/analytics/by-province         — 按省份统计
    GET  /api/admin/analytics/by-specialty        — 按专业统计
    GET  /api/admin/analytics/benchmark-history   — Benchmark跑分历史
    POST /api/admin/analytics/run-benchmark       — 触发跑分（Celery异步）
    GET  /api/admin/analytics/benchmark-status/{task_id} — 查询跑分进度
"""

import json
from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, func, cast, Date
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.models.task import Task
from app.models.result import MatchResult
from app.auth.permissions import require_admin
from app.config import PROJECT_ROOT

router = APIRouter()

# benchmark_history.json 的路径（放在 data/ 目录，因为 data/ 在 Docker 和懒猫部署中都有挂载）
_BENCHMARK_HISTORY_PATH = PROJECT_ROOT / "data" / "benchmark_history.json"


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

    # 补零天：没有任务的日子也返回 task_count=0，避免折线图断档
    row_map = {row.date: row.task_count for row in rows if row.date}
    today = date.today()
    items = []
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        items.append({
            "date": d.isoformat(),
            "task_count": row_map.get(d, 0),
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


@router.get("/benchmark-history")
async def benchmark_history(
    admin: User = Depends(require_admin),
):
    """Benchmark 跑分历史

    读取 data/benchmark_history.json 返回全部跑分记录，
    用于前端展示算法改动的好坏趋势。
    """
    if not _BENCHMARK_HISTORY_PATH.exists():
        return {"items": []}

    try:
        data = json.loads(_BENCHMARK_HISTORY_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"items": []}

    # data 是一个数组，每条记录包含 version/date/mode/note/datasets 或 json_papers+excel_datasets
    if not isinstance(data, list):
        return {"items": []}

    # 归一化：把新旧两种格式统一输出
    # 旧格式(index<172): datasets → {green_rate, red_rate, ...}
    # 新格式(index>=172): json_papers → {hit_rate, total, correct} + excel_datasets → 同旧格式
    # 统一输出 datasets 字段，json_papers 中的 hit_rate 映射为 green_rate
    normalized = []
    for record in data:
        item = {
            "version": record.get("version", ""),
            "date": record.get("date", ""),
            "mode": record.get("mode", ""),
            "note": record.get("note", ""),
            "datasets": {},
        }

        if "datasets" in record and record["datasets"]:
            # 旧格式：直接用
            item["datasets"] = record["datasets"]
        else:
            # 新格式：json_papers 转换 + excel_datasets 合并
            for name, metrics in record.get("json_papers", {}).items():
                item["datasets"][name] = {
                    "total": metrics.get("total", 0),
                    "green_rate": metrics.get("hit_rate", 0) / 100,  # hit_rate是百分比，转成0-1
                    "red_rate": 1 - metrics.get("hit_rate", 0) / 100,
                    "yellow_rate": 0,
                    "exp_hit_rate": 0,
                    "fallback_rate": 0,
                    "avg_time_sec": 0,
                }
            for name, metrics in record.get("excel_datasets", {}).items():
                item["datasets"][name] = metrics

        normalized.append(item)

    return {"items": normalized}


# ============================================================
# Benchmark 跑分触发与状态查询
# ============================================================

class BenchmarkRunRequest(BaseModel):
    """触发跑分的请求参数"""
    mode: str = "search"  # search（免费快速）或 agent（需API Key）
    note: str = ""        # 备注（说明本次改了什么）


@router.post("/run-benchmark")
async def run_benchmark(
    req: BenchmarkRunRequest,
    admin: User = Depends(require_admin),
):
    """触发 Benchmark 跑分（Celery异步执行）

    返回 Celery 任务ID，前端用它轮询进度。
    """
    if req.mode not in ("search", "agent"):
        return {"error": "mode 必须是 search 或 agent"}

    from app.tasks.benchmark_task import execute_benchmark
    task = execute_benchmark.delay(mode=req.mode, note=req.note)

    return {"task_id": task.id, "message": "跑分已启动"}


@router.get("/benchmark-status/{task_id}")
async def benchmark_status(
    task_id: str,
    admin: User = Depends(require_admin),
):
    """查询跑分任务的执行状态

    返回:
        state: PENDING / PROGRESS / SUCCESS / FAILURE
        progress: 当前进度信息（仅 PROGRESS 状态时有值）
        result: 最终结果（仅 SUCCESS 状态时有值）
    """
    from app.celery_app import celery_app
    result = celery_app.AsyncResult(task_id)

    if result.state == "PROGRESS":
        # 正在跑分中，返回进度详情
        return {
            "state": "PROGRESS",
            "progress": result.info,  # {current, total, dataset}
        }
    elif result.state == "SUCCESS":
        return {
            "state": "SUCCESS",
            "result": result.result,  # {success, message, datasets_run, ...}
        }
    elif result.state == "FAILURE":
        return {
            "state": "FAILURE",
            "error": str(result.result),
        }
    else:
        # PENDING 或其他状态
        return {"state": result.state}
