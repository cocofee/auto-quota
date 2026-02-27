"""
管理员额度管理 API

提供用户额度列表、额度调整、订单列表等管理功能。

路由挂载在 /api/admin/billing 前缀下:
    GET    /api/admin/billing/users     — 用户额度列表（分页+搜索）
    POST   /api/admin/billing/adjust    — 调整用户额度
    GET    /api/admin/billing/orders    — 订单列表（分页+状态筛选）
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func, desc, or_
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.database import get_db
from app.models.user import User
from app.models.order import Order
from app.models.quota_log import QuotaLog
from app.auth.deps import get_current_user
from app.auth.permissions import require_admin
from app.schemas.quota import (
    AdminAdjustRequest,
    AdminAdjustResponse,
    AdminUserQuotaItem,
    AdminUserQuotaListResponse,
    OrderResponse,
    OrderListResponse,
)
from app.services.quota_service import admin_adjust_quota

router = APIRouter()


@router.get("/users", response_model=AdminUserQuotaListResponse)
async def list_user_quotas(
    page: int = 1,
    size: int = 20,
    search: str = "",
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """用户额度列表（管理员查看所有用户的额度情况）"""
    if page < 1:
        page = 1
    if size < 1 or size > 100:
        size = 20

    # 构建查询条件（支持按邮箱或昵称搜索）
    filters = []
    if search:
        search_pattern = f"%{search}%"
        filters.append(
            or_(User.email.ilike(search_pattern), User.nickname.ilike(search_pattern))
        )

    # 查总数
    count_query = select(func.count()).select_from(User)
    if filters:
        count_query = count_query.where(*filters)
    total = (await db.execute(count_query)).scalar_one()

    # 分页查询用户
    query = (
        select(User)
        .order_by(desc(User.created_at))
        .offset((page - 1) * size)
        .limit(size)
    )
    if filters:
        query = query.where(*filters)
    users = (await db.execute(query)).scalars().all()

    # 批量查询使用量和购买量（用两条聚合SQL代替N+1循环查询）
    user_ids = [u.id for u in users]
    if user_ids:
        # 已使用总量（一次查所有用户）
        used_result = await db.execute(
            select(QuotaLog.user_id, func.coalesce(func.sum(QuotaLog.amount), 0))
            .where(QuotaLog.user_id.in_(user_ids), QuotaLog.change_type == "task_deduct")
            .group_by(QuotaLog.user_id)
        )
        used_map = {row[0]: abs(int(row[1])) for row in used_result.all()}

        # 已购买总量（一次查所有用户）
        purchased_result = await db.execute(
            select(QuotaLog.user_id, func.coalesce(func.sum(QuotaLog.amount), 0))
            .where(QuotaLog.user_id.in_(user_ids), QuotaLog.change_type == "purchase")
            .group_by(QuotaLog.user_id)
        )
        purchased_map = {row[0]: int(row[1]) for row in purchased_result.all()}
    else:
        used_map = {}
        purchased_map = {}

    items = []
    for user in users:
        items.append(AdminUserQuotaItem(
            user_id=user.id,
            email=user.email,
            nickname=user.nickname,
            quota_balance=user.quota_balance,
            total_used=used_map.get(user.id, 0),
            total_purchased=purchased_map.get(user.id, 0),
        ))

    return AdminUserQuotaListResponse(items=items, total=total, page=page, size=size)


@router.post("/adjust", response_model=AdminAdjustResponse)
async def adjust_user_quota(
    req: AdminAdjustRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """管理员调整用户额度

    正数增加额度，负数扣减额度。必须填写调整原因。
    """
    if req.amount == 0:
        raise HTTPException(status_code=400, detail="调整数量不能为0")

    try:
        new_balance = await admin_adjust_quota(
            db=db,
            user_id=req.user_id,
            amount=req.amount,
            note=req.note,
            admin_id=admin.id,
        )
        await db.commit()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    action = "增加" if req.amount > 0 else "扣减"
    return AdminAdjustResponse(
        message=f"已{action} {abs(req.amount)} 条额度",
        new_balance=new_balance,
    )


@router.get("/orders", response_model=OrderListResponse)
async def list_orders(
    page: int = 1,
    size: int = 20,
    status_filter: str = "",
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """订单列表（管理员查看所有订单）"""
    if page < 1:
        page = 1
    if size < 1 or size > 100:
        size = 20

    # 构建过滤条件
    filters = []
    if status_filter:
        filters.append(Order.status == status_filter)

    # 查总数
    count_query = select(func.count()).select_from(Order)
    if filters:
        count_query = count_query.where(*filters)
    total = (await db.execute(count_query)).scalar_one()

    # 查总金额（仅已支付的）
    amount_query = select(
        func.coalesce(func.sum(Order.amount), 0)
    ).select_from(Order).where(Order.status == "paid")
    total_amount = float((await db.execute(amount_query)).scalar_one())

    # 分页查询
    query = (
        select(Order)
        .order_by(desc(Order.created_at))
        .offset((page - 1) * size)
        .limit(size)
    )
    if filters:
        query = query.where(*filters)
    orders = (await db.execute(query)).scalars().all()

    return OrderListResponse(
        items=[OrderResponse.model_validate(o) for o in orders],
        total=total,
        page=page,
        size=size,
        total_amount=total_amount,
    )
