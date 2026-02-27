"""
额度购买订单模型

记录用户通过好易支付购买额度包的每笔订单。
订单状态流转: pending(待支付) → paid(已支付) 或 expired(已过期)
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import String, Integer, Numeric, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Order(Base):
    """额度购买订单表"""
    __tablename__ = "orders"

    # 主键
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # 所属用户（外键关联 users 表）
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), index=True
    )

    # 商户订单号（唯一，发给好易支付的 out_trade_no）
    # 格式: AQ + 年月日时分秒 + 4位随机数，如 AQ20260227143059ABCD
    out_trade_no: Mapped[str] = mapped_column(
        String(64), unique=True, index=True
    )

    # 额度包信息
    package_name: Mapped[str] = mapped_column(String(100))   # 如"500条额度包"
    package_quota: Mapped[int] = mapped_column(Integer)       # 购买的额度条数
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))   # 支付金额（元，精确到分）

    # 支付方式: alipay(支付宝) / wxpay(微信)
    pay_type: Mapped[str] = mapped_column(String(20))

    # 订单状态: pending=待支付, paid=已支付, expired=已过期
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)

    # 好易支付返回的平台交易号（支付成功后由回调填入）
    trade_no: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # 时间戳
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
