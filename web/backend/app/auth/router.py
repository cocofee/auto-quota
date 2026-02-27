"""
认证 API 路由

提供用户注册、登录、Token刷新、获取当前用户信息等接口。
"""

from datetime import datetime, timedelta, timezone
import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.models.refresh_token import RefreshToken

from app.schemas.auth import (
    RegisterRequest, LoginRequest, RefreshTokenRequest, LogoutRequest, TokenResponse, UserResponse,
)
from app.auth.utils import (
    hash_password, verify_password,
    create_access_token, create_refresh_token, decode_token,
)
from app.auth.deps import get_current_user
from app.config import (
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES,
    JWT_REFRESH_TOKEN_EXPIRE_MINUTES,
    ACCESS_TOKEN_COOKIE_NAME,
    REFRESH_TOKEN_COOKIE_NAME,
    COOKIE_SECURE,
    COOKIE_SAMESITE,
)

router = APIRouter()


def _set_auth_cookies(response: Response, access_token: str, refresh_token: str):
    response.set_cookie(
        key=ACCESS_TOKEN_COOKIE_NAME,
        value=access_token,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
        max_age=JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )
    response.set_cookie(
        key=REFRESH_TOKEN_COOKIE_NAME,
        value=refresh_token,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
        max_age=JWT_REFRESH_TOKEN_EXPIRE_MINUTES * 60,
        path="/api/auth",
    )


def _clear_auth_cookies(response: Response):
    response.delete_cookie(
        key=ACCESS_TOKEN_COOKIE_NAME,
        path="/",
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
    )
    response.delete_cookie(
        key=REFRESH_TOKEN_COOKIE_NAME,
        path="/api/auth",
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
    )


@router.post("/register", response_model=UserResponse, status_code=201)
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """用户注册

    用邮箱+密码注册新账号。邮箱不能重复。需要填写正确的邀请码。
    """
    # 0. 验证邀请码（防止乱注册白嫖额度）
    from app.services.invite_service import get_invite_code
    valid_code = await get_invite_code(db)
    if req.invite_code != valid_code:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="邀请码错误，请向管理员获取正确的邀请码",
        )

    # 1. 检查邮箱是否已注册
    result = await db.execute(select(User).where(User.email == req.email))
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该邮箱已注册，请直接登录",
        )

    # 2. 创建用户（密码哈希后存储，不存明文）
    user = User(
        email=req.email,
        hashed_password=hash_password(req.password),
        nickname=req.nickname or req.email.split("@")[0],  # 默认昵称取邮箱前缀
    )
    db.add(user)

    # 显式 commit 确保用户数据立即持久化
    # （如果只 flush 不 commit，客户端立即登录可能在 auto-commit 之前到达，导致 401）
    # 捕获IntegrityError处理并发注册相同邮箱的情况（两个请求同时通过了上面的检查）
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该邮箱已注册，请直接登录",
        )

    # 3. 记录注册赠送额度日志（方便用户在"使用记录"里看到）
    # 赠送日志写入失败不应阻塞注册（用户余额由 User.default=1000 保证正确）
    try:
        from app.services.quota_service import record_register_gift
        await record_register_gift(db, user.id)
        await db.commit()
    except Exception as e:
        # 日志写入失败不影响注册，只记录警告
        from loguru import logger
        logger.warning(f"注册赠送额度日志写入失败（用户余额不受影响）: {e}")
        try:
            await db.rollback()
        except Exception:
            pass

    return user


@router.post("/login", response_model=TokenResponse)
async def login(
    req: LoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """用户登录

    用邮箱+密码登录，返回访问Token和刷新Token。
    前端拿到Token后，每次请求API放在请求头:
        Authorization: Bearer <access_token>
    """
    # 1. 查找用户
    result = await db.execute(select(User).where(User.email == req.email))
    user = result.scalar_one_or_none()

    # 2. 验证密码（故意不区分"用户不存在"和"密码错误"，防止枚举攻击）
    if user is None or not verify_password(req.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="邮箱或密码错误",
        )

    # 3. 检查用户是否被禁用
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账号已被禁用，请联系管理员",
        )

    # 4. 更新最后登录时间
    user.last_login_at = datetime.now(timezone.utc)

    # 5. 生成Token（refresh token 带 jti，入库用于轮换和撤销）
    user_id = str(user.id)
    refresh_jti = uuid.uuid4().hex
    refresh_expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=JWT_REFRESH_TOKEN_EXPIRE_MINUTES
    )
    db.add(
        RefreshToken(
            user_id=user.id,
            jti=refresh_jti,
            expires_at=refresh_expires_at,
        )
    )
    await db.flush()

    access_token = create_access_token(user_id)
    refresh_token = create_refresh_token(user_id, refresh_jti)
    _set_auth_cookies(response, access_token, refresh_token)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    response: Response,
    request: Request,
    req: RefreshTokenRequest | None = Body(default=None),
    db: AsyncSession = Depends(get_db),
):
    """刷新Token

    访问Token过期后，用刷新Token换一对新的Token。
    前端检测到401错误时自动调用这个接口。
    """
    # 1. 获取并解析刷新Token（优先请求体，兜底HttpOnly Cookie）
    refresh_token_raw = None
    if req and req.refresh_token:
        refresh_token_raw = req.refresh_token
    if not refresh_token_raw:
        refresh_token_raw = request.cookies.get(REFRESH_TOKEN_COOKIE_NAME)
    if not refresh_token_raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少刷新Token，请重新登录",
        )

    payload = decode_token(refresh_token_raw)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="刷新Token无效或已过期，请重新登录",
        )

    # 2. 检查Token类型
    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token类型错误，请使用刷新Token",
        )

    # 3. 校验 refresh jti（防重放）
    refresh_jti = payload.get("jti")
    if not refresh_jti:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="刷新Token缺少jti，请重新登录",
        )

    # 4. 检查用户是否存在且未被禁用
    try:
        user_id = uuid.UUID(payload["sub"])
    except (ValueError, KeyError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token中的用户信息无效",
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在或已被禁用",
        )

    now = datetime.now(timezone.utc)
    # Lock the refresh-token row to prevent concurrent replay/rotation races.
    token_row_result = await db.execute(
        select(RefreshToken)
        .where(
            RefreshToken.user_id == user_id,
            RefreshToken.jti == refresh_jti,
            RefreshToken.revoked_at.is_(None),
            RefreshToken.expires_at > now,
        )
        .with_for_update()
    )
    token_row = token_row_result.scalar_one_or_none()
    if token_row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="刷新Token已失效，请重新登录",
        )

    # 5. 轮换refresh token：旧jti作废，新jti入库
    token_row.revoked_at = now
    new_refresh_jti = uuid.uuid4().hex
    new_refresh_expires_at = now + timedelta(minutes=JWT_REFRESH_TOKEN_EXPIRE_MINUTES)
    db.add(
        RefreshToken(
            user_id=user.id,
            jti=new_refresh_jti,
            expires_at=new_refresh_expires_at,
        )
    )
    await db.flush()

    # 6. 签发新的一对Token
    user_id_str = str(user.id)
    access_token = create_access_token(user_id_str)
    new_refresh_token = create_refresh_token(user_id_str, new_refresh_jti)
    _set_auth_cookies(response, access_token, new_refresh_token)
    return TokenResponse(
        access_token=access_token,
        refresh_token=new_refresh_token,
    )


@router.post("/logout")
async def logout(
    response: Response,
    request: Request,
    req: LogoutRequest | None = Body(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """退出登录并撤销当前 refresh token（幂等）"""
    refresh_token_raw = None
    if req and req.refresh_token:
        refresh_token_raw = req.refresh_token
    if not refresh_token_raw:
        refresh_token_raw = request.cookies.get(REFRESH_TOKEN_COOKIE_NAME)

    payload = decode_token(refresh_token_raw) if refresh_token_raw else None
    if payload and payload.get("type") == "refresh" and payload.get("sub") == str(user.id):
        refresh_jti = payload.get("jti")
        if refresh_jti:
            now = datetime.now(timezone.utc)
            result = await db.execute(
                select(RefreshToken).where(
                    RefreshToken.user_id == user.id,
                    RefreshToken.jti == refresh_jti,
                    RefreshToken.revoked_at.is_(None),
                )
            )
            token_row = result.scalar_one_or_none()
            if token_row is not None:
                token_row.revoked_at = now
                await db.flush()
    _clear_auth_cookies(response)
    return {"ok": True}


@router.get("/me", response_model=UserResponse)
async def get_me(user: User = Depends(get_current_user)):
    """获取当前登录用户信息

    需要在请求头中携带访问Token:
        Authorization: Bearer <access_token>
    """
    return user
