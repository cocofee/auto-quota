"""
反馈上传 API

用户上传纠正后的Excel → 系统自动学习 → 存入经验库。

路由:
    POST /api/tasks/{task_id}/feedback/upload  — 用户上传纠正Excel（挂在 /api 前缀下）
    GET  /api/admin/feedback/list              — 管理员查看反馈列表（挂在 /api 前缀下）
    GET  /api/admin/feedback/{task_id}/details  — 管理员查看反馈详情
"""

import asyncio
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from loguru import logger
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.task import Task
from app.models.user import User
from app.auth.deps import get_current_user
from app.auth.permissions import require_admin
from app.config import UPLOAD_DIR
from app.api.shared import get_user_task

router = APIRouter()


# ============================================================
# 端点1：用户上传纠正Excel
# ============================================================

@router.post("/tasks/{task_id}/feedback/upload")
async def upload_feedback(
    task_id: uuid.UUID,
    file: UploadFile = File(description="纠正后的Excel文件"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """用户上传纠正后的Excel

    工作流程：
    1. 验证任务状态（必须已完成）
    2. 检查是否重复上传
    3. 保存文件到 output/uploads/feedback/{task_id}/
    4. 调用 FeedbackLearner.learn_from_corrected_excel() 自动学习
    5. 更新 Task 的反馈字段
    """
    task = await get_user_task(task_id, user, db)

    # 验证：任务必须已完成
    if task.status != "completed":
        raise HTTPException(status_code=400, detail="只有已完成的任务才能上传反馈")

    # 验证：不能重复上传
    if task.feedback_path:
        raise HTTPException(status_code=400, detail="该任务已上传过反馈，不能重复上传")

    # 验证：文件格式
    if not file.filename or not file.filename.endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="请上传 .xlsx 格式的Excel文件")

    # 保存上传文件
    feedback_dir = UPLOAD_DIR / "feedback" / str(task_id)
    feedback_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(file.filename).name  # 去掉目录部分
    # 额外安全校验：文件名不能为空或特殊值
    if not safe_name or safe_name in (".", ".."):
        raise HTTPException(status_code=400, detail="文件名非法")
    save_path = feedback_dir / safe_name

    # resolve 后校验路径确实在目标目录内（防止符号链接等穿越攻击）
    resolved_path = save_path.resolve()
    resolved_dir = feedback_dir.resolve()
    if not resolved_path.is_relative_to(resolved_dir):
        raise HTTPException(status_code=400, detail="文件路径非法")

    # 流式写入 + 分块大小检查（避免一次性读入内存导致 OOM）
    from app.config import UPLOAD_MAX_MB
    max_size = UPLOAD_MAX_MB * 1024 * 1024
    size = 0
    try:
        with open(save_path, "wb") as f:
            while chunk := await file.read(8192):
                size += len(chunk)
                if size > max_size:
                    break
                f.write(chunk)
    except Exception:
        save_path.unlink(missing_ok=True)
        raise

    if size > max_size:
        save_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=400,
            detail=f"文件过大（超过{UPLOAD_MAX_MB}MB），最大允许{UPLOAD_MAX_MB}MB"
        )

    logger.info(f"反馈Excel已保存: {save_path}（{size} bytes）")

    # 调用核心学习函数（同步操作，放线程池避免阻塞）
    def _learn():
        from src.feedback_learner import FeedbackLearner
        fl = FeedbackLearner()
        return fl.learn_from_corrected_excel(str(save_path))

    try:
        stats = await asyncio.to_thread(_learn)
    except Exception as e:
        logger.error(f"反馈学习失败: {e}")
        # 学习失败时清理临时文件
        try:
            save_path.unlink()
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="反馈学习失败，请联系管理员")

    # 更新 Task 记录
    task.feedback_path = str(save_path)
    task.feedback_uploaded_at = datetime.now(timezone.utc)
    task.feedback_stats = stats
    await db.flush()

    return {"message": "反馈上传成功", "stats": stats}


# ============================================================
# 端点2：管理员查看反馈列表
# ============================================================

@router.get("/admin/feedback/list")
async def list_feedback(
    page: int = 1,
    size: int = 20,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """获取已上传反馈的任务列表

    按反馈上传时间倒序排列，支持分页。
    """
    if page < 1:
        page = 1
    if size < 1 or size > 100:
        size = 20

    # 查询总数
    count_result = await db.execute(
        select(func.count()).select_from(Task).where(Task.feedback_path.isnot(None))
    )
    total = count_result.scalar() or 0

    # 分页查询
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
            "task_id": t.id,
            "task_name": t.name,
            "original_filename": t.original_filename,
            "province": t.province,
            "feedback_uploaded_at": t.feedback_uploaded_at,
            "feedback_stats": t.feedback_stats,
        }
        for t in tasks
    ]

    return {"items": items, "total": total, "page": page, "size": size}


# ============================================================
# 端点3：管理员查看反馈详情
# ============================================================

@router.get("/admin/feedback/{task_id}/details")
async def feedback_details(
    task_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """查看单条反馈的详细信息

    包含任务基本信息、反馈学习统计等。
    """
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    if not task.feedback_path:
        raise HTTPException(status_code=404, detail="该任务未上传反馈")

    return {
        "task_id": task.id,
        "task_name": task.name,
        "original_filename": task.original_filename,
        "province": task.province,
        "mode": task.mode,
        "feedback_uploaded_at": task.feedback_uploaded_at,
        "feedback_stats": task.feedback_stats,
        "task_stats": task.stats,
        "created_at": task.created_at,
        "completed_at": task.completed_at,
    }


# ============================================================
# 端点4：管理员导入带定额清单（不依赖已有任务）
# ============================================================

@router.post("/admin/feedback/import")
async def import_quota_excel(
    file: UploadFile = File(description="带定额编号的清单Excel文件"),
    province: str = "北京市建设工程施工消耗量标准(2024)",
    admin: User = Depends(require_admin),
):
    """管理员直接导入带定额的Excel到经验库

    不需要先创建匹配任务。直接上传一个"清单+定额"的Excel，
    系统自动提取清单→定额对应关系，写入经验库候选层。

    参数:
        file: Excel文件（.xlsx格式，包含清单行和定额行）
        province: 省份名称（用于绑定经验库的省份）
    """
    # 验证文件格式
    if not file.filename or not file.filename.endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="请上传 .xlsx 格式的Excel文件")

    # 保存上传文件到临时目录
    import_dir = UPLOAD_DIR / "imports"
    import_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(file.filename).name
    if not safe_name or safe_name in (".", ".."):
        raise HTTPException(status_code=400, detail="文件名非法")

    # 用时间戳避免文件名冲突
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    save_path = import_dir / f"{ts}_{safe_name}"

    # resolve 后校验路径安全
    resolved_path = save_path.resolve()
    resolved_dir = import_dir.resolve()
    if not resolved_path.is_relative_to(resolved_dir):
        raise HTTPException(status_code=400, detail="文件路径非法")

    # 流式写入 + 大小检查
    from app.config import UPLOAD_MAX_MB
    max_size = UPLOAD_MAX_MB * 1024 * 1024
    size = 0
    try:
        with open(save_path, "wb") as f:
            while chunk := await file.read(8192):
                size += len(chunk)
                if size > max_size:
                    break
                f.write(chunk)
    except Exception:
        save_path.unlink(missing_ok=True)
        raise

    if size > max_size:
        save_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=400,
            detail=f"文件过大（超过{UPLOAD_MAX_MB}MB），最大允许{UPLOAD_MAX_MB}MB"
        )

    logger.info(f"导入Excel已保存: {save_path}（{size} bytes）, 省份={province}")

    # 调用 FeedbackLearner.import_completed_project() 导入经验库
    def _import():
        from src.feedback_learner import FeedbackLearner
        fl = FeedbackLearner()
        return fl.import_completed_project(str(save_path), project_name=safe_name)

    try:
        stats = await asyncio.to_thread(_import)
    except Exception as e:
        logger.error(f"导入学习失败: {e}")
        save_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"导入失败: {e}")

    return {"message": "导入成功", "stats": stats}
