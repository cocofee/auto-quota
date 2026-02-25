"""
认证依赖注入

提供 get_current_user() 函数，在需要登录的API路由中注入当前用户信息。

用法:
    @router.get("/me")
    async def get_me(user: User = Depends(get_current_user)):
        return user
"""

import uuid

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.auth.utils import decode_token
from app.config import ACCESS_TOKEN_COOKIE_NAME

# HTTP Bearer 认证方案（从请求头 Authorization: Bearer <token> 中提取Token）
# auto_error=False: 允许走Cookie兜底
security = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    """从请求头中提取Token，验证后返回当前登录用户

    这个函数作为FastAPI的依赖注入使用，自动在每个请求中执行。
    如果Token无效或用户不存在，会抛出401错误。

    参数:
        credentials: 从请求头 Authorization: Bearer <token> 自动提取
        db: 数据库会话（自动注入）

    返回: User对象（当前登录的用户）

    异常: HTTPException(401) — Token无效、过期、用户不存在或已禁用
    """
    token = credentials.credentials if credentials else request.cookies.get(ACCESS_TOKEN_COOKIE_NAME)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="需要登录",
        )

    # 1. 解析Token
    payload = decode_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token无效或已过期，请重新登录",
        )

    # 2. 检查Token类型（只有access token可以访问API）
    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token类型错误，请使用访问Token",
        )

    # 3. 从Token中提取用户ID
    user_id_str = payload.get("sub")
    if not user_id_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token中缺少用户信息",
        )

    # 4. 查数据库确认用户存在
    try:
        user_id = uuid.UUID(user_id_str)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token中的用户ID格式错误",
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在",
        )

    # 5. 检查用户是否被禁用
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账号已被禁用，请联系管理员",
        )

    return user
