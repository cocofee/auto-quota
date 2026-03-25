"""
OpenClaw authentication helpers.

Machine-to-machine calls use `X-OpenClaw-Key`.
Intranet admin pages may reuse the same OpenClaw read APIs with admin login state.
"""

from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_optional_current_user
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


def _collect_valid_openclaw_keys() -> set[str]:
    return {item for item in {OPENCLAW_API_KEY, *HARDCODED_OPENCLAW_KEYS} if item}


def _validate_openclaw_api_key(api_key: str | None) -> bool:
    return bool(api_key) and api_key in _collect_valid_openclaw_keys()


async def require_openclaw_api_key(
    api_key: str | None = Security(openclaw_key_header),
) -> str:
    valid_keys = _collect_valid_openclaw_keys()
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


async def _get_or_create_openclaw_service_user(db: AsyncSession) -> User:
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


async def get_openclaw_service_user(
    _: str = Depends(require_openclaw_api_key),
    db: AsyncSession = Depends(get_db),
) -> User:
    return await _get_or_create_openclaw_service_user(db)


async def get_openclaw_read_user(
    api_key: str | None = Security(openclaw_key_header),
    current_user: User | None = Depends(get_optional_current_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    if _validate_openclaw_api_key(api_key):
        return await _get_or_create_openclaw_service_user(db)
    if current_user and current_user.is_admin:
        return current_user
    if _collect_valid_openclaw_keys():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="需要 X-OpenClaw-Key 或管理员登录态",
        )
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="OPENCLAW_API_KEY 未配置，OpenClaw 接口暂不可用",
    )
