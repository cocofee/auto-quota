"""
认证相关的请求/响应数据格式

Pydantic模型：定义API接收什么数据、返回什么数据。
类似于"数据合同"——前端发来的数据必须符合这个格式，否则自动报错。
"""

import uuid
from datetime import datetime
from pydantic import BaseModel, EmailStr, Field


# ============================================================
# 请求格式（前端 → 后端）
# ============================================================

class RegisterRequest(BaseModel):
    """注册请求"""
    email: EmailStr = Field(description="邮箱地址")
    password: str = Field(min_length=8, max_length=100, description="密码（至少8位）")
    nickname: str = Field(default="", max_length=100, description="昵称（可选）")


class LoginRequest(BaseModel):
    """登录请求"""
    email: EmailStr = Field(description="邮箱地址")
    password: str = Field(description="密码")


class RefreshTokenRequest(BaseModel):
    """刷新Token请求（放在请求体中，避免token暴露在URL里）"""
    refresh_token: str = Field(description="刷新Token")


class LogoutRequest(BaseModel):
    """退出登录请求（用于服务端撤销refresh token）"""
    refresh_token: str = Field(description="当前会话的刷新Token")


# ============================================================
# 响应格式（后端 → 前端）
# ============================================================

class TokenResponse(BaseModel):
    """登录成功返回的Token"""
    access_token: str = Field(description="访问Token（放在请求头Authorization中）")
    refresh_token: str = Field(description="刷新Token（access_token过期后用这个换新的）")
    token_type: str = Field(default="bearer")


class UserResponse(BaseModel):
    """用户信息（不包含密码）"""
    id: uuid.UUID
    email: str
    nickname: str
    is_active: bool
    is_admin: bool
    created_at: datetime

    model_config = {"from_attributes": True}  # 允许从SQLAlchemy模型直接转换
