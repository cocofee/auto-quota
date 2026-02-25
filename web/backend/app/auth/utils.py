"""
认证工具函数

提供密码哈希和JWT Token的生成/验证功能。
被 auth/router.py（API路由）和 auth/deps.py（依赖注入）共同使用。
"""

from datetime import datetime, timedelta, timezone
import uuid

import bcrypt
from jose import JWTError, jwt

from app.config import (
    JWT_SECRET_KEY, JWT_ALGORITHM,
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES, JWT_REFRESH_TOKEN_EXPIRE_MINUTES,
)


def hash_password(plain_password: str) -> str:
    """把明文密码转成哈希值（注册时调用）

    使用 bcrypt 算法，自带随机盐值，同一个密码每次哈希结果都不同，
    防止彩虹表攻击。
    """
    password_bytes = plain_password.encode("utf-8")
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password_bytes, salt)
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码是否正确（登录时调用）

    参数:
        plain_password: 用户输入的明文密码
        hashed_password: 数据库中存储的哈希值
    返回:
        True=密码正确, False=密码错误
    """
    password_bytes = plain_password.encode("utf-8")
    hashed_bytes = hashed_password.encode("utf-8")
    return bcrypt.checkpw(password_bytes, hashed_bytes)


def create_access_token(user_id: str) -> str:
    """生成访问Token（登录成功后返回给前端）

    前端每次请求API时，把这个Token放在请求头里:
        Authorization: Bearer <access_token>
    """
    expire = datetime.now(timezone.utc) + timedelta(minutes=JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": user_id,       # sub = subject，存用户ID
        "exp": expire,        # exp = expiration，Token过期时间
        "type": "access",     # 区分访问Token和刷新Token
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def create_refresh_token(user_id: str, jti: str | None = None) -> str:
    """生成刷新Token（访问Token过期后用这个换新的）

    刷新Token有效期更长（7天），但只能用来换新的访问Token，不能直接访问API。
    """
    expire = datetime.now(timezone.utc) + timedelta(minutes=JWT_REFRESH_TOKEN_EXPIRE_MINUTES)
    token_jti = jti or uuid.uuid4().hex
    payload = {
        "sub": user_id,
        "exp": expire,
        "type": "refresh",
        "jti": token_jti,
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict | None:
    """解析Token，返回payload（用户ID等信息）

    返回:
        成功: {"sub": "用户ID", "exp": ..., "type": "access/refresh"}
        失败: None（Token无效、过期、被篡改）
    """
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        return payload
    except JWTError:
        return None
