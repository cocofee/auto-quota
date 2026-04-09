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
import hashlib

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
from app.text_utils import normalize_client_filename, repair_mojibake_text
from src.bill_reader import parse_sheet_selection

router = APIRouter()


def _revoke_celery_task(celery_task_id: str | None, *, terminate: bool) -> None:
    if not celery_task_id:
        return

    try:
        from app.celery_app import celery_app

        celery_app.control.revoke(celery_task_id, terminate=terminate, signal="SIGTERM")
        logger.info(f"已发送 Celery revoke: {celery_task_id} (terminate={terminate})")
    except Exception as exc:
        logger.warning(f"Celery revoke 失败，task_id={celery_task_id}: {exc}")


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
    if sheet_norm and len(sheet_norm) > 2000:
        raise HTTPException(status_code=400, detail="sheet 长度不能超过 2000")

    llm_norm = agent_llm.strip() if isinstance(agent_llm, str) else None
    if llm_norm == "":
        llm_norm = None
    if llm_norm and len(llm_norm) > 50:
        raise HTTPException(status_code=400, detail="agent_llm 长度不能超过 50")

    return province_norm, sheet_norm, llm_norm


def _canonicalize_sheet_selection(sheet: str | None) -> str | None:
    names = parse_sheet_selection(sheet)
    if not names:
        return None
    return json.dumps(names, ensure_ascii=False, separators=(",", ":"))


def _task_signature_matches(
    task: Task,
    *,
    province: str,
    mode: str,
    sheet: str | None,
    limit_count: int | None,
    use_experience: bool,
) -> bool:
    return (
        task.province == province
        and task.mode == mode
        and (task.sheet or None) == sheet
        and task.limit_count == limit_count
        and task.use_experience == use_experience
    )


def _sha256_file(path: str) -> str | None:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(1024 * 1024):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


async def _has_recent_duplicate_task(
    db: AsyncSession,
    *,
    user_id,
    province: str,
    mode: str,
    sheet: str | None,
    limit_count: int | None,
    use_experience: bool,
    file_path: str,
) -> bool:
    from datetime import datetime, timezone, timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
    query = (
        select(Task)
        .where(
            Task.user_id == user_id,
            Task.status.in_(["pending", "running"]),
            Task.created_at >= cutoff,
        )
        .order_by(desc(Task.created_at))
    )
    candidates = (await db.execute(query)).scalars().all()
    if not candidates:
        return False

    new_hash = _sha256_file(file_path)
    if not new_hash:
        return False

    for task in candidates:
        if not _task_signature_matches(
            task,
            province=province,
            mode=mode,
            sheet=sheet,
            limit_count=limit_count,
            use_experience=use_experience,
        ):
            continue
        existing_hash = _sha256_file(task.file_path)
        if existing_hash and existing_hash == new_hash:
            return True
    return False


def _estimate_task_bill_count(file_path: str, province: str, sheet: str | None, limit_count: int | None) -> int | None:
    try:
        import main as auto_quota_main

        items = auto_quota_main._load_bill_items_for_run(
            file_path,
            sheet=sheet,
            limit=limit_count,
            province=province,
        )
        return len(items)
    except Exception as exc:
        logger.warning(f"任务创建预检未能读取清单条数，回退到基础额度校验: {exc}")
        return None


def _to_task_response(task: Task) -> TaskResponse:
    resp = TaskResponse.model_validate(task)
    resp.name = repair_mojibake_text(resp.name) or ""
    resp.original_filename = normalize_client_filename(resp.original_filename, "unknown.xlsx")
    resp.province = repair_mojibake_text(resp.province) or ""
    resp.progress_message = repair_mojibake_text(resp.progress_message) or ""
    resp.error_message = repair_mojibake_text(resp.error_message)
    resp.username = repair_mojibake_text(resp.username)
    return resp


@router.post("", response_model=TaskResponse, status_code=201)
async def create_task(
    file: UploadFile = File(description="清单 Excel 文件（.xlsx/.xls）"),
    province: str = Form(description="省份定额库名称"),
    mode: str | None = Form(default=None, description="匹配模式：search 或 agent"),
    sheet: str | None = Form(default=None, description="指定 Sheet 名称"),
    limit_count: int | None = Form(default=None, description="限制处理条数"),
    use_experience: bool = Form(default=True, description="是否使用经验库"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """创建匹配任务。"""
    if limit_count is not None and (limit_count < 1 or limit_count > 10000):
        raise HTTPException(status_code=400, detail="limit_count 必须在 1~10000 之间")

    province, sheet, _ = _normalize_create_task_inputs(province, sheet, None)
    sheet = _canonicalize_sheet_selection(sheet)
    original_filename = normalize_client_filename(file.filename, "unknown.xlsx")
    if len(original_filename) > 255:
        raise HTTPException(status_code=400, detail="original filename 长度不能超过 255")

    from app.config import MATCH_MODE

    if mode and mode.strip():
        mode = mode.strip()
        if mode not in ("search", "agent"):
            raise HTTPException(status_code=400, detail="mode 必须是 search 或 agent")
    else:
        mode = MATCH_MODE

    min_required = limit_count if limit_count else 1
    if user.quota_balance < min_required:
        detail = f"额度不足，当前剩余 {user.quota_balance} 条"
        if limit_count:
            detail += f"，本次任务至少需要 {limit_count} 条"
        raise HTTPException(status_code=402, detail=detail)

    try:
        from app.services.llm_config_service import get_llm_config

        llm_cfg = await get_llm_config(db)
        agent_llm = llm_cfg["llm_type"]
    except Exception:
        from app.config import MATCH_LLM

        agent_llm = MATCH_LLM

    task_id = uuid.uuid4()
    try:
        saved_path = await asyncio.to_thread(save_upload_file, file, task_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    task_dir = UPLOAD_DIR / str(task_id)
    if await _has_recent_duplicate_task(
        db,
        user_id=user.id,
        province=province,
        mode=mode,
        sheet=sheet,
        limit_count=limit_count,
        use_experience=use_experience,
        file_path=str(saved_path),
    ):
        if task_dir.exists():
            shutil.rmtree(task_dir, ignore_errors=True)
        raise HTTPException(
            status_code=409,
            detail=(
                f"文件“{original_filename}”已有相同参数的进行中任务，"
                "请等待当前任务完成后再提交"
            ),
        )

    estimated_count = await asyncio.to_thread(
        _estimate_task_bill_count,
        str(saved_path),
        province,
        sheet,
        limit_count,
    )
    if estimated_count is not None and estimated_count > user.quota_balance:
        if task_dir.exists():
            shutil.rmtree(task_dir, ignore_errors=True)
        raise HTTPException(
            status_code=402,
            detail=(
                f"额度不足，当前剩余 {user.quota_balance} 条，"
                f"预估本次任务需要 {estimated_count} 条"
            ),
        )

    from pathlib import Path

    task = Task(
        id=task_id,
        user_id=user.id,
        name=Path(original_filename).stem,
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
    await db.commit()

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
        task.celery_task_id = celery_result.id
        await db.commit()
    except Exception as e:
        task.status = "failed"
        task.error_message = f"任务入队失败: {e}"
        await db.commit()
        if task_dir.exists():
            shutil.rmtree(task_dir, ignore_errors=True)
        logger.error(f"Celery任务入队失败: {e}")

    logger.info(f"创建任务 {task_id}: {task.name} (mode={mode}, province={province})")
    return _to_task_response(task)


@router.get("", response_model=TaskListResponse)
async def list_tasks(
    page: int = 1,
    size: int = 20,
    status_filter: str | None = None,
    created_after: str | None = None,
    all_users: bool = False,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """获取任务列表（分页）

    普通用户：只返回自己的任务。
    管理员：传 all_users=true 可查看所有用户的任务。
    可选按状态筛选: pending/running/completed/failed
    可选按创建时间筛选: created_after=2026-03-01（ISO日期，只返回该日期之后创建的任务）
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

    # 按创建时间过滤（用于"本月完成"等场景）
    if created_after:
        from datetime import datetime
        try:
            after_dt = datetime.fromisoformat(created_after)
            base_filter = base_filter & (Task.created_at >= after_dt)
        except ValueError:
            pass  # 日期格式不对就忽略

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

    # 管理员视图：查询每个任务的用户昵称
    items = []
    if all_users and user.is_admin and tasks:
        user_ids = list({t.user_id for t in tasks})
        user_rows = (await db.execute(
            select(User.id, User.nickname, User.email).where(User.id.in_(user_ids))
        )).all()
        user_map = {r.id: r.nickname or r.email for r in user_rows}
        for t in tasks:
            resp = _to_task_response(t)
            resp.username = user_map.get(t.user_id, "")
            resp.username = repair_mojibake_text(resp.username)
            items.append(resp)
    else:
        items = [_to_task_response(t) for t in tasks]

    return TaskListResponse(items=items, total=total, page=page, size=size, total_bills=total_bills)


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """获取任务详情

    管理员可查看任意任务，普通用户只能查看自己的。
    """
    task = await get_user_task(task_id, user, db)
    return _to_task_response(task)


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

    if task.status == "pending":
        _revoke_celery_task(task.celery_task_id, terminate=False)

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
    _revoke_celery_task(task.celery_task_id, terminate=True)

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
            select(User.is_active, User.is_admin).where(User.id == user_id)
        )
        user_row = user_result.one_or_none()
        if user_row is None:
            raise HTTPException(status_code=401, detail="用户不存在")
        if not user_row.is_active:
            raise HTTPException(status_code=403, detail="账号已被禁用，请联系管理员")

        # 管理员可查看所有用户的任务进度，普通用户只能看自己的
        task_query = select(Task.id).where(Task.id == task_id)
        if not user_row.is_admin:
            task_query = task_query.where(Task.user_id == user_id)
        result = await session.execute(task_query)
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
