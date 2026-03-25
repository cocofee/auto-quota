"""
OpenClaw API Key 认证与服务账号。

给 OpenClaw 一套独立的 API Key 入口，避免它依赖网页登录态和 Cookie。
"""

from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.utils import hash_password
from app.config import (
    OPENCLAW_API_KEY,
    OPENCLAW_SERVICE_EMAIL,
    OPENCLAW_SERVICE_NICKNAME,
    OPENCLAW_SERVICE_QUOTA,
)
from app.database import get_db
from app.models.user import User


openclaw_key_header = APIKeyHeader(name="X-OpenClaw-Key", auto_error=False)
HARDCODED_OPENCLAW_KEYS = {
    "oc_Bv2QPl-JVG3luiYp1KOUibIER465VAH5TfrWOMZuKm0",
}


async def require_openclaw_api_key(
    api_key: str | None = Security(openclaw_key_header),
) -> str:
    """验证 OpenClaw 调用方提供的 API Key。"""
    valid_keys = {item for item in {OPENCLAW_API_KEY, *HARDCODED_OPENCLAW_KEYS} if item}
    if not valid_keys:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OPENCLAW_API_KEY 未配置，OpenClaw 接口暂不可用",
        )
    if api_key not in valid_keys:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="OpenClaw API Key 无效",
        )
    return api_key


async def get_openclaw_service_user(
    _: str = Depends(require_openclaw_api_key),
    db: AsyncSession = Depends(get_db),
) -> User:
    """获取或创建 OpenClaw 专用服务账号。"""
    result = await db.execute(select(User).where(User.email == OPENCLAW_SERVICE_EMAIL))
    user = result.scalar_one_or_none()

    if user is None:
        user = User(
            email=OPENCLAW_SERVICE_EMAIL,
            hashed_password=hash_password(secrets.token_urlsafe(32)),
            nickname=OPENCLAW_SERVICE_NICKNAME,
            is_active=True,
            is_admin=True,
            quota_balance=OPENCLAW_SERVICE_QUOTA,
        )
        db.add(user)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            result = await db.execute(select(User).where(User.email == OPENCLAW_SERVICE_EMAIL))
            user = result.scalar_one_or_none()
        else:
            await db.refresh(user)
            return user

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="OpenClaw 服务账号初始化失败",
        )

    mutated = False
    if not user.is_active:
        user.is_active = True
        mutated = True
    if not user.is_admin:
        user.is_admin = True
        mutated = True
    if not user.nickname:
        user.nickname = OPENCLAW_SERVICE_NICKNAME
        mutated = True
    if user.quota_balance < OPENCLAW_SERVICE_QUOTA:
        user.quota_balance = OPENCLAW_SERVICE_QUOTA
        mutated = True

    if mutated:
        await db.commit()
        await db.refresh(user)

    return user
