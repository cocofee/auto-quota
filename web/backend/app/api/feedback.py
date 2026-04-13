"""
反馈上传 API

用户上传人工纠正后的 Excel，文件先落到服务器，再后台提取学习样本写入经验库。

路由:
    POST /api/tasks/{task_id}/feedback/upload
    GET  /api/admin/feedback/list
    GET  /api/admin/feedback/{task_id}/details
    POST /api/admin/feedback/import
"""

import asyncio
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.file_intake import ingest
from app.api.shared import get_user_task
from app.auth.deps import get_current_user
from app.auth.permissions import require_admin
from app.config import UPLOAD_DIR
from app.database import async_session, get_db
from app.models.task import Task
from app.models.user import User
from app.text_utils import normalize_client_filename, repair_mojibake_text

router = APIRouter()


async def _commit_feedback_upload(
    db: AsyncSession,
    task: Task,
    *,
    save_path: Path,
    stats: dict | None = None,
) -> None:
    """Persist upload marker immediately so detached learning jobs can observe it."""
    task.feedback_path = str(save_path)
    task.feedback_uploaded_at = datetime.now(timezone.utc)
    if stats is not None:
        task.feedback_stats = stats
    await db.commit()


async def _commit_feedback_stats(
    db: AsyncSession,
    task: Task,
    stats: dict,
) -> None:
    """Persist feedback learning stats or failure metadata."""
    task.feedback_stats = stats
    await db.commit()


def _can_retry_feedback_upload(task: Task) -> bool:
    return bool((task.feedback_stats or {}).get("status") == "learn_failed")


def _learn_feedback_records(
    *,
    save_path: str,
    actor: str,
    project_name: str,
    province: str,
) -> dict:
    from src.feedback_learner import FeedbackLearner

    learner = FeedbackLearner()
    payload = learner.extract_learning_records_from_corrected_excel(save_path)
    ingest_result = ingest(
        records=payload["records"],
        ingest_intent="learning",
        evidence_level="user_corrected",
        business_type="feedback_corrected_excel",
        actor=actor,
        source_context={
            "project_name": project_name,
            "province": province,
            "parse_status": "parsed",
            "source": "user_correction",
        },
    )
    return {
        "total": payload["total"],
        "learned": ingest_result.written_learning,
        "skipped": ingest_result.skipped,
        "warnings": ingest_result.warnings,
        "status": "completed",
    }


async def _process_feedback_upload(
    task_id: uuid.UUID,
    *,
    save_path: str,
    actor: str,
    project_name: str,
    province: str,
) -> None:
    try:
        stats = await asyncio.to_thread(
            _learn_feedback_records,
            save_path=save_path,
            actor=actor,
            project_name=project_name,
            province=province,
        )
    except Exception as exc:
        logger.exception(f"反馈学习失败: task_id={task_id}, error={exc}")
        async with async_session() as db:
            result = await db.execute(select(Task).where(Task.id == task_id))
            task = result.scalar_one_or_none()
            if not task:
                logger.warning(f"反馈学习失败后未找到任务: task_id={task_id}")
                return
            await _commit_feedback_stats(
                db,
                task,
                {
                    "status": "learn_failed",
                    "error": str(exc)[:500],
                },
            )
        return

    async with async_session() as db:
        result = await db.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if not task:
            logger.warning(f"反馈学习完成后未找到任务: task_id={task_id}")
            return
        await _commit_feedback_stats(db, task, stats)
        logger.info(
            f"反馈学习完成: task_id={task_id}, total={stats.get('total', 0)}, "
            f"learned={stats.get('learned', 0)}"
        )


@router.post("/tasks/{task_id}/feedback/upload")
async def upload_feedback(
    task_id: uuid.UUID,
    file: UploadFile = File(description="纠正后的 Excel 文件"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """用户上传纠正后的 Excel，后台异步学习。"""
    task = await get_user_task(task_id, user, db)

    if task.status != "completed":
        raise HTTPException(status_code=400, detail="只有已完成的任务才能上传反馈")

    if task.feedback_path and not _can_retry_feedback_upload(task):
        raise HTTPException(status_code=400, detail="该任务已上传过反馈，不能重复上传")

    if not file.filename or not file.filename.endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="请上传 .xlsx 格式的 Excel 文件")

    feedback_dir = UPLOAD_DIR / "feedback" / str(task_id)
    feedback_dir.mkdir(parents=True, exist_ok=True)
    safe_name = normalize_client_filename(file.filename, "feedback.xlsx")
    if not safe_name or safe_name in (".", ".."):
        raise HTTPException(status_code=400, detail="文件名非法")
    save_path = feedback_dir / safe_name

    resolved_path = save_path.resolve()
    resolved_dir = feedback_dir.resolve()
    if not resolved_path.is_relative_to(resolved_dir):
        raise HTTPException(status_code=400, detail="文件路径非法")

    from app.config import UPLOAD_MAX_MB

    max_size = UPLOAD_MAX_MB * 1024 * 1024
    size = 0
    try:
        with open(save_path, "wb") as output:
            while chunk := await file.read(8192):
                size += len(chunk)
                if size > max_size:
                    break
                output.write(chunk)
    except Exception:
        save_path.unlink(missing_ok=True)
        raise

    if size > max_size:
        save_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=400,
            detail=f"文件过大（超过 {UPLOAD_MAX_MB}MB），最大允许 {UPLOAD_MAX_MB}MB",
        )

    logger.info(f"反馈 Excel 已保存: {save_path} ({size} bytes)")

    await _commit_feedback_upload(
        db,
        task,
        save_path=save_path,
        stats={"status": "processing"},
    )

    asyncio.create_task(_process_feedback_upload(
        task_id,
        save_path=str(save_path),
        actor=(getattr(user, "email", None) or getattr(user, "nickname", None) or str(user.id)),
        project_name=normalize_client_filename(file.filename, "feedback.xlsx"),
        province=repair_mojibake_text(task.province) or "",
    ))

    return {
        "message": "反馈文件已上传，正在后台学习",
        "stats": {"status": "processing"},
    }


@router.get("/admin/feedback/list")
async def list_feedback(
    page: int = 1,
    size: int = 20,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """获取已上传反馈的任务列表，按上传时间倒序。"""
    _ = admin
    if page < 1:
        page = 1
    if size < 1 or size > 100:
        size = 20

    count_result = await db.execute(
        select(func.count()).select_from(Task).where(Task.feedback_path.isnot(None))
    )
    total = count_result.scalar() or 0

    offset = (page - 1) * size
    result = await db.execute(
        select(Task)
        .where(Task.feedback_path.isnot(None))
        .order_by(Task.feedback_uploaded_at.desc())
        .offset(offset)
        .limit(size)
    )
    tasks = result.scalars().all()

    items = [
        {
            "task_id": task.id,
            "task_name": repair_mojibake_text(task.name) or "",
            "original_filename": normalize_client_filename(task.original_filename, "unknown.xlsx"),
            "province": repair_mojibake_text(task.province) or "",
            "feedback_uploaded_at": task.feedback_uploaded_at,
            "feedback_stats": task.feedback_stats,
        }
        for task in tasks
    ]

    return {"items": items, "total": total, "page": page, "size": size}


@router.get("/admin/feedback/{task_id}/details")
async def feedback_details(
    task_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """查看单条反馈详情。"""
    _ = admin
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if not task.feedback_path:
        raise HTTPException(status_code=404, detail="该任务未上传反馈")

    return {
        "task_id": task.id,
        "task_name": repair_mojibake_text(task.name) or "",
        "original_filename": normalize_client_filename(task.original_filename, "unknown.xlsx"),
        "province": repair_mojibake_text(task.province) or "",
        "mode": task.mode,
        "feedback_uploaded_at": task.feedback_uploaded_at,
        "feedback_stats": task.feedback_stats,
        "task_stats": task.stats,
        "created_at": task.created_at,
        "completed_at": task.completed_at,
    }


@router.post("/admin/feedback/import")
async def import_quota_excel(
    file: UploadFile = File(description="带定额编码的清单 Excel 文件"),
    province: str = "北京市建设工程施工消耗量标准(2024)",
    admin: User = Depends(require_admin),
):
    """管理员直接导入带定额的 Excel 到经验库。"""
    if not file.filename or not file.filename.endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="请上传 .xlsx 格式的 Excel 文件")

    import_dir = UPLOAD_DIR / "imports"
    import_dir.mkdir(parents=True, exist_ok=True)
    safe_name = normalize_client_filename(file.filename, "feedback_import.xlsx")
    if not safe_name or safe_name in (".", ".."):
        raise HTTPException(status_code=400, detail="文件名非法")

    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    save_path = import_dir / f"{ts}_{safe_name}"

    resolved_path = save_path.resolve()
    resolved_dir = import_dir.resolve()
    if not resolved_path.is_relative_to(resolved_dir):
        raise HTTPException(status_code=400, detail="文件路径非法")

    from app.config import UPLOAD_MAX_MB

    max_size = UPLOAD_MAX_MB * 1024 * 1024
    size = 0
    try:
        with open(save_path, "wb") as output:
            while chunk := await file.read(8192):
                size += len(chunk)
                if size > max_size:
                    break
                output.write(chunk)
    except Exception:
        save_path.unlink(missing_ok=True)
        raise

    if size > max_size:
        save_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=400,
            detail=f"文件过大（超过 {UPLOAD_MAX_MB}MB），最大允许 {UPLOAD_MAX_MB}MB",
        )

    logger.info(f"导入 Excel 已保存: {save_path} ({size} bytes), province={province}")

    def _import() -> dict:
        from src.feedback_learner import FeedbackLearner

        learner = FeedbackLearner()
        payload = learner.extract_completed_project_records(str(save_path), project_name=safe_name)
        ingest_result = ingest(
            records=payload["records"],
            ingest_intent="learning",
            evidence_level="completed_project",
            business_type="completed_project_excel",
            actor=(getattr(admin, "email", None) or getattr(admin, "nickname", None) or str(admin.id)),
            source_context={
                "project_name": safe_name,
                "province": province,
                "parse_status": "parsed",
                "source": "completed_project",
            },
        )
        return {
            "total": payload["total"],
            "added": ingest_result.written_learning,
            "skipped": ingest_result.skipped,
            "warnings": ingest_result.warnings,
        }

    try:
        stats = await asyncio.to_thread(_import)
    except Exception as exc:
        logger.error(f"导入学习失败: {exc}")
        save_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"导入失败: {exc}") from exc

    return {"message": "导入成功", "stats": stats}
