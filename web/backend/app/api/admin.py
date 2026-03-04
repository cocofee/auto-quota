"""
用户管理 API（管理员专属）

路由挂载在 /api/admin 前缀下:
    GET    /api/admin/users           — 用户列表（分页）
    PUT    /api/admin/users/{id}      — 修改用户（启用/禁用/设管理员）
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, func, desc, cast, Integer
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
    admin_count: int = 0    # 全局管理员数量
    active_count: int = 0   # 全局活跃用户数量


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

    # 总数 + 全局统计（管理员数、活跃数）
    count_query = select(
        func.count(),
        func.sum(func.cast(User.is_admin, Integer)),
        func.sum(func.cast(User.is_active, Integer)),
    ).select_from(User)
    row = (await db.execute(count_query)).one()
    total = row[0]
    admin_count = int(row[1] or 0)
    active_count = int(row[2] or 0)

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

    return UserListResponse(
        items=items, total=total,
        admin_count=admin_count, active_count=active_count,
    )


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


# ============================================================
# 邀请码管理
# ============================================================

@router.get("/invite-code")
async def get_invite_code(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """获取当前邀请码"""
    from app.services.invite_service import get_invite_code as _get
    code = await _get(db)
    return {"invite_code": code}


class UpdateInviteCodeRequest(BaseModel):
    invite_code: str = Field(min_length=4, max_length=50, description="新邀请码（至少4位）")


@router.put("/invite-code")
async def update_invite_code(
    body: UpdateInviteCodeRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """修改邀请码（立即生效）"""
    from app.services.invite_service import set_invite_code
    new_code = await set_invite_code(db, body.invite_code)
    return {"message": "邀请码已更新", "invite_code": new_code}


# ============================================================
# 大模型配置管理
# ============================================================

def _mask_key(raw_key: str) -> str:
    """API Key脱敏：只显示前4位和后4位"""
    if len(raw_key) > 8:
        return raw_key[:4] + "****" + raw_key[-4:]
    elif raw_key:
        return "****"
    return ""


@router.get("/llm-config")
async def get_llm_config_api(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """获取当前大模型配置（API Key只返回脱敏版本）"""
    from app.services.llm_config_service import get_llm_config
    cfg = await get_llm_config(db)
    raw_key = cfg.get("api_key", "")
    return {
        "llm_type": cfg["llm_type"],
        "api_key_masked": _mask_key(raw_key),
        "has_api_key": bool(raw_key),
        "base_url": cfg["base_url"],
        "model": cfg["model"],
    }


class UpdateLlmConfigRequest(BaseModel):
    llm_type: str = Field(description="模型类型: qwen/claude/deepseek/kimi/openai")
    api_key: str = Field(default="", description="API密钥（留空则保持不变）")
    base_url: str = Field(default="", description="API地址（留空用默认值）")
    model: str = Field(default="", description="模型名称（留空用默认值）")


@router.put("/llm-config")
async def update_llm_config_api(
    body: UpdateLlmConfigRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """修改大模型配置（下次任务立即生效，无需重启）"""
    from app.services.llm_config_service import set_llm_config, get_llm_config, VALID_LLM_TYPES
    if body.llm_type not in VALID_LLM_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的模型类型: {body.llm_type}，可选: {', '.join(VALID_LLM_TYPES)}",
        )

    # 如果API Key为空，保持数据库中已有的Key不变
    api_key = body.api_key
    if not api_key:
        existing = await get_llm_config(db)
        api_key = existing.get("api_key", "")

    try:
        await set_llm_config(db, body.llm_type, api_key, body.base_url, body.model)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"message": f"已切换为 {body.llm_type}，下次任务生效"}


# ============================================================
# 验证模型配置管理
# ============================================================

@router.get("/verify-config")
async def get_verify_config_api(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """获取验证模型配置"""
    from app.services.llm_config_service import get_verify_config
    cfg = await get_verify_config(db)
    raw_key = cfg.get("api_key", "")
    return {
        "llm_type": cfg["llm_type"],
        "api_key_masked": _mask_key(raw_key),
        "has_api_key": bool(raw_key),
        "base_url": cfg["base_url"],
        "model": cfg["model"],
    }


@router.put("/verify-config")
async def update_verify_config_api(
    body: UpdateLlmConfigRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """修改验证模型配置（下次任务立即生效）

    llm_type 传空字符串表示"跟匹配模型走同一个"。
    """
    from app.services.llm_config_service import set_verify_config, get_verify_config, VALID_LLM_TYPES
    if body.llm_type and body.llm_type not in VALID_LLM_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的模型类型: {body.llm_type}，可选: {', '.join(VALID_LLM_TYPES)}",
        )

    # API Key为空时保持已有的
    api_key = body.api_key
    if not api_key:
        existing = await get_verify_config(db)
        api_key = existing.get("api_key", "")

    try:
        await set_verify_config(db, body.llm_type, api_key, body.base_url, body.model)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    label = body.llm_type or "跟匹配模型"
    return {"message": f"验证模型已设为 {label}，下次任务生效"}
