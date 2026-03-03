"""
批量处理 API（管理员专属）

路由挂载在 /api/admin/batch 前缀下:
    GET  /api/admin/batch/status       — 获取批量处理状态概览
    GET  /api/admin/batch/files        — 文件列表（分页+筛选）
    POST /api/admin/batch/scan         — 启动扫描（Celery异步）
    POST /api/admin/batch/run          — 启动批量匹配（Celery异步）
    POST /api/admin/batch/retry/{file_path} — 重跑单个文件
    GET  /api/admin/batch/task-status/{task_id} — 查询异步任务进度

通过 asyncio.to_thread() 调用 batch_scanner 的 SQLite 同步操作，
避免阻塞 FastAPI 的异步事件循环。
"""

import asyncio
import sqlite3
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from loguru import logger

from app.models.user import User
from app.auth.permissions import require_admin
from app.config import PROJECT_ROOT

router = APIRouter()

# batch.db 路径
_BATCH_DB_PATH = PROJECT_ROOT / "output" / "batch" / "batch.db"


def _get_batch_db() -> sqlite3.Connection:
    """获取 batch.db 连接（只读模式，用于API查询）"""
    if not _BATCH_DB_PATH.exists():
        raise FileNotFoundError("batch.db 不存在，请先运行 batch_scanner.py 扫描文件")
    conn = sqlite3.connect(str(_BATCH_DB_PATH), timeout=10)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


# ============================================================
# 状态概览
# ============================================================

@router.get("/status")
async def batch_status(
    admin: User = Depends(require_admin),
):
    """批量处理状态概览

    返回各状态文件数、格式分布、省份分布等统计信息。
    """
    def _query():
        try:
            conn = _get_batch_db()
        except FileNotFoundError:
            # 还没有扫描过，返回空数据
            return {
                "total": 0,
                "by_status": {},
                "by_format": {},
                "by_province": {},
                "by_specialty": {},
            }
        try:
            # 各状态数量
            rows = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM file_registry GROUP BY status"
            ).fetchall()
            by_status = {r["status"]: r["cnt"] for r in rows}

            # 各格式数量
            rows = conn.execute(
                "SELECT format, COUNT(*) as cnt FROM file_registry "
                "WHERE format IS NOT NULL GROUP BY format"
            ).fetchall()
            by_format = {r["format"]: r["cnt"] for r in rows}

            # 各省份数量（只看有省份标签的）
            rows = conn.execute(
                "SELECT province, COUNT(*) as cnt FROM file_registry "
                "WHERE province IS NOT NULL AND province != '' "
                "GROUP BY province ORDER BY cnt DESC"
            ).fetchall()
            by_province = {r["province"]: r["cnt"] for r in rows}

            # 各专业数量
            rows = conn.execute(
                "SELECT specialty, COUNT(*) as cnt FROM file_registry "
                "WHERE specialty IS NOT NULL AND specialty != '' "
                "GROUP BY specialty ORDER BY cnt DESC"
            ).fetchall()
            by_specialty = {r["specialty"]: r["cnt"] for r in rows}

            total = conn.execute("SELECT COUNT(*) FROM file_registry").fetchone()[0]

            return {
                "total": total,
                "by_status": by_status,
                "by_format": by_format,
                "by_province": by_province,
                "by_specialty": by_specialty,
            }
        finally:
            conn.close()

    result = await asyncio.to_thread(_query)
    return result


# ============================================================
# 文件列表（分页+筛选）
# ============================================================

@router.get("/files")
async def batch_files(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(50, ge=1, le=200, description="每页条数"),
    status: Optional[str] = Query(None, description="状态筛选"),
    format: Optional[str] = Query(None, description="格式筛选"),
    province: Optional[str] = Query(None, description="省份筛选"),
    specialty: Optional[str] = Query(None, description="专业筛选"),
    keyword: Optional[str] = Query(None, description="文件名关键词"),
    admin: User = Depends(require_admin),
):
    """文件列表（带分页和筛选）"""
    def _query():
        try:
            conn = _get_batch_db()
        except FileNotFoundError:
            return {"items": [], "total": 0, "page": page, "page_size": page_size}
        try:
            # 构建WHERE条件
            conditions = []
            params = []

            if status:
                conditions.append("status = ?")
                params.append(status)
            if format:
                conditions.append("format = ?")
                params.append(format)
            if province:
                conditions.append("province = ?")
                params.append(province)
            if specialty:
                conditions.append("specialty = ?")
                params.append(specialty)
            if keyword:
                conditions.append("file_name LIKE ?")
                params.append(f"%{keyword}%")

            where_clause = " AND ".join(conditions) if conditions else "1=1"

            # 查询总数
            total = conn.execute(
                f"SELECT COUNT(*) FROM file_registry WHERE {where_clause}",
                params,
            ).fetchone()[0]

            # 分页查询
            offset = (page - 1) * page_size
            rows = conn.execute(
                f"""SELECT file_path, file_name, file_size, province, specialty,
                           format, status, skip_reason, error_msg, estimated_items,
                           scan_time, match_time, algo_version
                    FROM file_registry
                    WHERE {where_clause}
                    ORDER BY updated_at DESC
                    LIMIT ? OFFSET ?""",
                params + [page_size, offset],
            ).fetchall()

            items = [dict(r) for r in rows]

            return {
                "items": items,
                "total": total,
                "page": page,
                "page_size": page_size,
            }
        finally:
            conn.close()

    result = await asyncio.to_thread(_query)
    return result


# ============================================================
# 启动扫描
# ============================================================

class ScanRequest(BaseModel):
    """扫描请求参数"""
    directory: str = "F:/jarvis"  # 默认扫描目录
    specialty: Optional[str] = None  # 只扫某专业
    rescan: bool = False  # 是否重新分类


@router.post("/scan")
async def start_scan(
    req: ScanRequest,
    admin: User = Depends(require_admin),
):
    """启动文件扫描（Celery异步执行）"""
    from app.tasks.batch_task import execute_scan
    task = execute_scan.delay(
        directory=req.directory,
        specialty=req.specialty,
        rescan=req.rescan,
    )
    return {"task_id": task.id, "message": "扫描已启动"}


# ============================================================
# 启动批量匹配
# ============================================================

class RunRequest(BaseModel):
    """批量匹配请求参数"""
    format: Optional[str] = None    # 只跑某格式
    province: Optional[str] = None  # 只跑某省
    specialty: Optional[str] = None  # 只跑某专业
    limit: Optional[int] = None     # 最多跑几个文件


@router.post("/run")
async def start_run(
    req: RunRequest,
    admin: User = Depends(require_admin),
):
    """启动批量匹配（Celery异步执行）"""
    from app.tasks.batch_task import execute_batch_run
    task = execute_batch_run.delay(
        format_filter=req.format,
        province=req.province,
        specialty=req.specialty,
        limit=req.limit,
    )
    return {"task_id": task.id, "message": "批量匹配已启动"}


# ============================================================
# 重跑单个文件
# ============================================================

class RetryRequest(BaseModel):
    """重跑请求"""
    file_path: str


@router.post("/retry")
async def retry_file(
    req: RetryRequest,
    admin: User = Depends(require_admin),
):
    """重跑单个文件（将状态重置为scanned）"""
    def _reset():
        try:
            conn = _get_batch_db()
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="batch.db不存在")
        try:
            # 只重置 matched/error 状态的文件
            cursor = conn.execute(
                """UPDATE file_registry
                   SET status = 'scanned', match_time = NULL, error_msg = NULL,
                       updated_at = datetime('now', 'localtime')
                   WHERE file_path = ? AND status IN ('matched', 'error')""",
                (req.file_path,),
            )
            conn.commit()
            if cursor.rowcount == 0:
                return {"success": False, "message": "文件不存在或状态不可重置"}
            return {"success": True, "message": "已重置为待匹配状态"}
        finally:
            conn.close()

    result = await asyncio.to_thread(_reset)
    if isinstance(result, dict) and not result.get("success"):
        raise HTTPException(status_code=404, detail=result["message"])
    return result


# ============================================================
# 异步任务状态查询
# ============================================================

@router.get("/task-status/{task_id}")
async def batch_task_status(
    task_id: str,
    admin: User = Depends(require_admin),
):
    """查询异步任务（扫描/匹配）的执行状态"""
    from app.celery_app import celery_app
    result = celery_app.AsyncResult(task_id)

    if result.state == "PROGRESS":
        return {"state": "PROGRESS", "progress": result.info}
    elif result.state == "SUCCESS":
        return {"state": "SUCCESS", "result": result.result}
    elif result.state == "FAILURE":
        return {"state": "FAILURE", "error": str(result.result)}
    else:
        return {"state": result.state}
