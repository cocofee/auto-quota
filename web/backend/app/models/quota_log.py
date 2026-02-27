"""
额度变动日志模型

记录每一次额度变动（注册赠送、任务扣减、购买充值、管理员调整），
方便用户查看使用记录和管理员审计。
"""

import uuid
from datetime import datetime

from sqlalchemy import String, Integer, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class QuotaLog(Base):
    """额度变动日志表"""
    __tablename__ = "quota_logs"

    # 自增主键（日志表用自增ID即可，不需要UUID）
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # 所属用户
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), index=True
    )

    # 变动类型:
    #   register_gift  — 注册赠送
    #   task_deduct    — 任务扣减
    #   purchase       — 购买充值
    #   admin_adjust   — 管理员调整
    change_type: Mapped[str] = mapped_column(String(30))

    # 变动数量（正数=增加，负数=扣减）
    amount: Mapped[int] = mapped_column(Integer)

    # 变动后余额（方便展示，不需要额外查询）
    balance_after: Mapped[int] = mapped_column(Integer)

    # 关联ID（任务ID 或 订单ID，用于追溯）
    ref_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # 备注说明（如"任务xxx匹配完成，扣减20条"）
    note: Mapped[str] = mapped_column(String(255), default="")

    # 时间戳
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
