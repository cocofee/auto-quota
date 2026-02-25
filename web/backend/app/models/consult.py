"""
咨询提交模型

用户上传截图 → AI 解析 → 用户确认提交 → 管理员审核 → 存入经验库。
每条记录对应一次截图上传 + AI 解析结果。
"""

import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, ForeignKey, JSON, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ConsultSubmission(Base):
    """咨询提交表

    记录用户通过截图提交的清单→定额对应关系。
    管理员审核通过后存入经验库权威层。
    """
    __tablename__ = "consult_submissions"

    # 主键
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # 提交用户
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), index=True
    )

    # 上传的截图路径
    image_path: Mapped[str] = mapped_column(String(500))

    # AI 解析结果（JSON 数组）
    # 格式: [{"bill_name": "给水管道DN25", "quota_id": "C10-6-30", "quota_name": "给水塑料管道安装 DN25", "unit": "m"}, ...]
    parsed_items: Mapped[list] = mapped_column(JSON, default=list)

    # 用户编辑后的最终提交数据（和 parsed_items 格式相同，用户可能修改了部分内容）
    submitted_items: Mapped[list] = mapped_column(JSON, default=list)

    # 省份
    province: Mapped[str] = mapped_column(String(255))

    # 审核状态: pending=待审核, approved=已通过, rejected=已拒绝
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)

    # 管理员审核信息
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 时间戳
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
