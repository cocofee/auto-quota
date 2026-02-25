"""
任务管理 API

提供匹配任务的创建（上传Excel+参数）、查询、删除和SSE进度跟踪。

路由挂载在 /api/tasks 前缀下:
    POST   /api/tasks              — 创建任务
    GET    /api/tasks              — 任务列表（分页）
    GET    /api/tasks/{id}         — 任务详情
    DELETE /api/tasks/{id}         — 删除任务
    GET    /api/tasks/{id}/progress — SSE实时进度
"""

import uuid
import json
import shutil
import asyncio

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Request
from sqlalchemy import select, func, desc, true as sa_true, cast, Integer
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse
from loguru import logger

from app.database import get_db, async_session
from app.models.task import Task
from app.models.user import User
from app.auth.deps import get_current_user
from app.auth.utils import decode_token
from app.schemas.task import TaskResponse, TaskListResponse
from app.services.match_service import save_upload_file, get_task_output_dir
from app.tasks.match_task import execute_match
from app.config import UPLOAD_DIR, TASK_OUTPUT_DIR, ACCESS_TOKEN_COOKIE_NAME
from app.api.shared import get_user_task

router = APIRouter()


def _normalize_create_task_inputs(
    province: str, sheet: str | None, agent_llm: str | None
) -> tuple[str, str | None, str | None]:
    """规范化并校验创建任务的文本参数，避免DB层报错。"""
    province_norm = (province or "").strip()
    if not province_norm or province_norm.lower() == "undefined":
        raise HTTPException(status_code=400, detail="province 不能为空")
    if len(province_norm) > 255:
        raise HTTPException(status_code=400, detail="province 长度不能超过 255")

    sheet_norm = sheet.strip() if isinstance(sheet, str) else None
    if sheet_norm == "":
        sheet_norm = None
    if sheet_norm and len(sheet_norm) > 100:
        raise HTTPException(status_code=400, detail="sheet 长度不能超过 100")

    llm_norm = agent_llm.strip() if isinstance(agent_llm, str) else None
    if llm_norm == "":
        llm_norm = None
    if llm_norm and len(llm_norm) > 50:
        raise HTTPException(status_code=400, detail="agent_llm 长度不能超过 50")

    return province_norm, sheet_norm, llm_norm


@router.post("", response_model=TaskResponse, status_code=201)
async def create_task(
    file: UploadFile = File(description="清单Excel文件（.xlsx/.xls）"),
    province: str = Form(description="省份定额库名称"),
    sheet: str | None = Form(default=None, description="指定Sheet名称"),
    limit_count: int | None = Form(default=None, description="限制处理条数"),
    use_experience: bool = Form(default=True, description="是否使用经验库"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """创建匹配任务

    上传清单Excel文件 + 配置匹配参数 → 任务进入Celery队列等待执行。
    立即返回任务信息，前端可通过 /progress 端点跟踪进度。
    匹配模式和大模型由后端配置统一控制（MATCH_MODE / MATCH_LLM）。
    """
    # 1. 校验参数
    if limit_count is not None and (limit_count < 1 or limit_count > 10000):
        raise HTTPException(status_code=400, detail="limit_count 必须在 1~10000 之间")
    province, sheet, _ = _normalize_create_task_inputs(province, sheet, None)

    # 匹配模式和大模型从后端配置读取（用户不需要选择）
    from app.config import MATCH_MODE, MATCH_LLM
    mode = MATCH_MODE
    agent_llm = MATCH_LLM

    # 2. 生成任务ID并保存上传文件
    #    save_upload_file 是同步磁盘I/O，用 to_thread 避免阻塞事件循环
    task_id = uuid.uuid4()
    try:
        saved_path = await asyncio.to_thread(save_upload_file, file, task_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 3. 创建任务记录
    from pathlib import Path
    original_filename = file.filename or "unknown.xlsx"
    if len(original_filename) > 255:
        # 校验失败，清理已保存的临时文件
        try:
            Path(saved_path).unlink(missing_ok=True)
        except Exception:
            pass
        raise HTTPException(status_code=400, detail="原始文件名过长（最多255字符）")
    task = Task(
        id=task_id,
        user_id=user.id,
        name=Path(original_filename).stem,  # 任务名从文件名提取
        file_path=str(saved_path),
        original_filename=original_filename,
        mode=mode,
        province=province,
        sheet=sheet,
        limit_count=limit_count,
        use_experience=use_experience,
        agent_llm=agent_llm,
        status="pending",
        progress=0,
        progress_message="等待执行...",
    )
    db.add(task)

    # 必须先提交到数据库，确保 Celery worker 能找到这条记录
    # （如果只 flush 不 commit，快速的 worker 可能在 commit 前就查不到记录）
    await db.commit()

    # 4. 提交Celery异步任务
    try:
        celery_result = execute_match.delay(
            task_id=str(task_id),
            file_path=str(saved_path),
            params={
                "mode": mode,
                "province": province,
                "sheet": sheet,
                "limit": limit_count,
                "agent_llm": agent_llm,
                "no_experience": not use_experience,
            },
        )
        # 记录Celery任务ID（用于后续取消等操作）
        task.celery_task_id = celery_result.id
        await db.commit()  # 显式提交 celery_task_id
    except Exception as e:
        # Redis/Celery 不可用时，标记任务失败并清理上传文件
        task.status = "failed"
        task.error_message = f"任务入队失败: {e}"
        await db.commit()  # 显式提交失败状态，避免依赖 teardown 而丢失
        task_dir = UPLOAD_DIR / str(task_id)
        if task_dir.exists():
            shutil.rmtree(task_dir, ignore_errors=True)
        logger.error(f"Celery任务入队失败: {e}")

    logger.info(f"创建任务 {task_id}: {task.name} (mode={mode}, province={province})")
    return task


@router.get("", response_model=TaskListResponse)
async def list_tasks(
    page: int = 1,
    size: int = 20,
    status_filter: str | None = None,
    all_users: bool = False,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """获取任务列表（分页）

    普通用户：只返回自己的任务。
    管理员：传 all_users=true 可查看所有用户的任务。
    可选按状态筛选: pending/running/completed/failed
    """
    if page < 1:
        page = 1
    if size < 1 or size > 100:
        size = 20

    # 构建查询条件
    # 管理员传 all_users=true 时不过滤 user_id，查看全部用户任务
    if all_users and user.is_admin:
        base_filter = sa_true()  # 不过滤（SQLAlchemy 的 true() 表达式）
        if status_filter:
            base_filter = Task.status == status_filter
    else:
        base_filter = Task.user_id == user.id
        if status_filter:
            base_filter = (Task.user_id == user.id) & (Task.status == status_filter)

    # 查总数 + 清单条数合计（stats 是 JSON 字段，取其中 total 值求和）
    count_query = select(
        func.count(),
        func.coalesce(func.sum(cast(Task.stats["total"].as_string(), Integer)), 0),
    ).select_from(Task).where(base_filter)
    row = (await db.execute(count_query)).one()
    total = row[0]
    total_bills = int(row[1])

    # 分页查询
    query = (
        select(Task)
        .where(base_filter)
        .order_by(desc(Task.created_at))
        .offset((page - 1) * size)
        .limit(size)
    )
    tasks = (await db.execute(query)).scalars().all()

    return TaskListResponse(items=tasks, total=total, page=page, size=size, total_bills=total_bills)


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """获取任务详情

    管理员可查看任意任务，普通用户只能查看自己的。
    """
    return await get_user_task(task_id, user, db)


@router.delete("/{task_id}", status_code=204)
async def delete_task(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """删除任务

    同时删除关联的匹配结果（数据库外键设置了 CASCADE）。
    正在运行中的任务不允许删除。
    管理员可删除任意任务，普通用户只能删除自己的。
    """
    task = await get_user_task(task_id, user, db)

    if task.status == "running":
        raise HTTPException(status_code=409, detail="任务正在运行中，无法删除")

    # 清理上传文件和输出文件（避免磁盘泄漏）
    upload_dir = UPLOAD_DIR / str(task_id)
    output_dir = TASK_OUTPUT_DIR / str(task_id)
    for d in (upload_dir, output_dir):
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)

    await db.delete(task)
    await db.commit()
    logger.info(f"删除任务 {task_id}（已清理关联文件）")


@router.post("/{task_id}/cancel", status_code=200)
async def cancel_task(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """取消正在运行或排队中的任务

    通过 Celery revoke 终止后台任务，并将状态标记为 cancelled。
    """
    task = await get_user_task(task_id, user, db)

    if task.status not in ("pending", "running"):
        raise HTTPException(status_code=409, detail=f"任务状态为 {task.status}，无法取消")

    # 通过 Celery revoke 终止后台任务
    if task.celery_task_id:
        try:
            from app.celery_app import celery_app
            celery_app.control.revoke(task.celery_task_id, terminate=True, signal="SIGTERM")
            logger.info(f"已发送 Celery revoke: {task.celery_task_id}")
        except Exception as e:
            logger.warning(f"Celery revoke 失败（任务可能已结束）: {e}")

    # 更新任务状态
    task.status = "cancelled"
    task.progress_message = "用户取消"
    task.error_message = "任务已被用户取消"
    await db.commit()

    logger.info(f"任务 {task_id} 已取消")
    return {"message": "任务已取消"}


@router.get("/{task_id}/bill-preview")
async def get_bill_preview(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """获取任务的清单预览 + 实时匹配结果

    返回 items（清单列表）和 results（已匹配的定额结果，按序号索引）。
    """
    await get_user_task(task_id, user, db)

    output_dir = get_task_output_dir(task_id)

    # 读取清单预览
    items = []
    preview_path = output_dir / "bill_preview.json"
    if preview_path.exists():
        try:
            items = json.loads(preview_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    # 读取实时匹配结果（jsonl 格式，每行一条）
    results = {}
    live_path = output_dir / "results_live.jsonl"
    if live_path.exists():
        try:
            for line in live_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r = json.loads(line)
                    results[r["idx"]] = r  # 按序号索引
        except Exception:
            pass

    return {"items": items, "results": results}


@router.get("/{task_id}/progress")
async def task_progress(task_id: uuid.UUID, request: Request):
    """SSE 实时进度推送

    前端通过 EventSource 或 fetch 连接此端点，每2秒接收一次进度更新。
    任务完成或失败后自动关闭连接。

    注意：SSE 端点手动验证 Token（不使用 FastAPI 依赖注入，
    因为 SSE 的长连接会导致 get_db() 会话一直占用）。
    """
    # 手动验证 Token
    auth_header = request.headers.get("Authorization", "")
    token = None
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    if not token:
        token = request.cookies.get(ACCESS_TOKEN_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="需要登录")
    payload = decode_token(token)
    if not payload or payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Token无效或已过期")
    user_id_str = payload.get("sub")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="Token中缺少用户信息")
    try:
        user_id = uuid.UUID(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Token中的用户ID格式错误")

    # 验证用户存在且未禁用，再校验任务归属
    async with async_session() as session:
        user_result = await session.execute(
            select(User.is_active).where(User.id == user_id)
        )
        is_active = user_result.scalar_one_or_none()
        if is_active is None:
            raise HTTPException(status_code=401, detail="用户不存在")
        if not is_active:
            raise HTTPException(status_code=403, detail="账号已被禁用，请联系管理员")

        result = await session.execute(
            select(Task.id, Task.user_id).where(
                Task.id == task_id,
                Task.user_id == user_id,
            )
        )
        row = result.one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="任务不存在")

    async def event_generator():
        """轮询数据库，推送进度更新"""
        while True:
            try:
                async with async_session() as session:
                    result = await session.execute(
                        select(
                            Task.status, Task.progress, Task.progress_current,
                            Task.progress_message, Task.stats,
                            Task.error_message, Task.started_at,
                        ).where(Task.id == task_id)
                    )
                    row = result.one_or_none()

                if not row:
                    yield {
                        "event": "error",
                        "data": json.dumps({"message": "任务不存在"}, ensure_ascii=False),
                    }
                    break

                data = {
                    "status": row.status,
                    "progress": row.progress,
                    "current_idx": row.progress_current,
                    "message": row.progress_message,
                    "stats": row.stats,
                    "error": row.error_message,
                    "started_at": row.started_at.isoformat() if row.started_at else None,
                }
                yield {
                    "event": "progress",
                    "data": json.dumps(data, ensure_ascii=False),
                }

                # 终态：推送最后一次后停止
                if row.status in ("completed", "failed", "cancelled"):
                    break

                await asyncio.sleep(2)  # 每2秒轮询一次

            except asyncio.CancelledError:
                # 客户端断开连接
                break
            except Exception as e:
                logger.error(f"SSE进度推送异常: {e}")
                break

    return EventSourceResponse(event_generator())
