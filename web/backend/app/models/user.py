"""
用户模型

存储注册用户的基本信息。SaaS场景下每个用户有独立的数据空间。
"""

import uuid
from datetime import datetime

from sqlalchemy import String, Boolean, DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class User(Base):
    """用户表"""
    __tablename__ = "users"

    # 主键：UUID（比自增ID更适合分布式系统，也更安全——不暴露用户数量）
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # 邮箱（唯一，用于登录，不允许为空）
    email: Mapped[str] = mapped_column(
        String(255), unique=True, index=True, nullable=False
    )

    # 密码哈希（不存明文密码，存经过bcrypt处理的哈希值，不允许为空）
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)

    # 用户昵称（显示用）
    nickname: Mapped[str] = mapped_column(String(100), default="")

    # 是否激活（预留：邮箱验证、管理员封禁）
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # 是否管理员
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)

    # 上次登录时间（安全审计用）
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # 时间戳
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
