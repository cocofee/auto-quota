"""
用户管理 API（管理员专属）

路由挂载在 /api/admin 前缀下:
    GET    /api/admin/users           — 用户列表（分页）
    PUT    /api/admin/users/{id}      — 修改用户（启用/禁用/设管理员）
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.database import get_db
from app.models.user import User
from app.models.task import Task
from app.auth.permissions import require_admin

router = APIRouter()


# ============================================================
# Pydantic 模型（请求/响应格式）
# ============================================================

class UserItem(BaseModel):
    """用户列表中的单个用户"""
    id: uuid.UUID
    email: str
    nickname: str
    is_active: bool
    is_admin: bool
    created_at: str
    last_login_at: str | None
    task_count: int  # 任务数量

    model_config = {"from_attributes": True}


class UserListResponse(BaseModel):
    """用户列表响应"""
    items: list[UserItem]
    total: int


class UserUpdateRequest(BaseModel):
    """修改用户请求"""
    is_active: bool | None = None
    is_admin: bool | None = None
    nickname: str | None = None


# ============================================================
# API 路由
# ============================================================

@router.get("/users", response_model=UserListResponse)
async def list_users(
    page: int = 1,
    size: int = 20,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """获取所有用户列表"""
    if page < 1:
        page = 1
    if size < 1 or size > 100:
        size = 20

    # 总数
    count_query = select(func.count()).select_from(User)
    total = (await db.execute(count_query)).scalar()

    # 分页查询用户 + 任务计数（子查询避免 N+1）
    task_count_sub = (
        select(func.count())
        .where(Task.user_id == User.id)
        .correlate(User)
        .scalar_subquery()
    )
    query = (
        select(User, task_count_sub.label("task_count"))
        .order_by(desc(User.created_at))
        .offset((page - 1) * size)
        .limit(size)
    )
    rows = (await db.execute(query)).all()

    items = []
    for row in rows:
        u = row[0]          # User 对象
        task_count = row[1]  # 子查询的 task_count
        items.append(UserItem(
            id=u.id,
            email=u.email,
            nickname=u.nickname or "",
            is_active=u.is_active,
            is_admin=u.is_admin,
            created_at=u.created_at.isoformat() if u.created_at else "",
            last_login_at=u.last_login_at.isoformat() if u.last_login_at else None,
            task_count=task_count or 0,
        ))

    return UserListResponse(items=items, total=total)


@router.put("/users/{user_id}")
async def update_user(
    user_id: uuid.UUID,
    body: UserUpdateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """修改用户信息（启用/禁用/设管理员/改昵称）"""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    # 不能修改自己的管理员状态（防止误操作把自己降级）
    if user.id == admin.id and body.is_admin is False:
        raise HTTPException(status_code=400, detail="不能取消自己的管理员权限")

    if body.is_active is not None:
        user.is_active = body.is_active
    if body.is_admin is not None:
        user.is_admin = body.is_admin
    if body.nickname is not None:
        user.nickname = body.nickname

    await db.flush()
    return {"message": "修改成功"}
