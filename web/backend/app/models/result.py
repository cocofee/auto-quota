"""
匹配结果模型

存储每条清单项的匹配结果。一个Task有多个MatchResult（一对多）。
这张表用于Web界面展示、在线审核、纠正等操作。
"""

import uuid
from datetime import datetime

from sqlalchemy import String, Integer, Float, DateTime, ForeignKey, JSON, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MatchResult(Base):
    """匹配结果表（每条清单项一行）

    JSON 字段结构说明:
    - quotas / corrected_quotas: list[dict]，每个元素:
      {"quota_id": str, "name": str, "unit": str, "param_score": float, ...}
    - trace: dict，结构:
      {"path": list, "final_source": str, "final_confidence": int}
    """
    __tablename__ = "match_results"

    # 主键
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # 所属任务（外键关联 tasks 表）
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )

    # 在任务中的序号（从0开始，用于排序和定位）
    index: Mapped[int] = mapped_column(Integer)

    # ============================================================
    # 清单项信息（从原始Excel读取）
    # ============================================================

    # 清单项编码（如 "031001007001"，12位编码）
    bill_code: Mapped[str] = mapped_column(String(100), default="")

    # 清单项名称（如 "给水管道安装"）
    bill_name: Mapped[str] = mapped_column(String(500))

    # 清单项描述/特征（如 "镀锌钢管DN25 沟槽连接"）
    bill_description: Mapped[str] = mapped_column(Text, default="")

    # 单位（如 "m", "个", "组"，脏数据可能很长所以放宽到50）
    bill_unit: Mapped[str] = mapped_column(String(50), default="")

    # 工程量
    bill_quantity: Mapped[float | None] = mapped_column(Float, nullable=True)

    # 综合单价（从清单Excel读取，可能为空）
    bill_unit_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    # 金额（工程量×综合单价，从清单Excel读取，可能为空）
    bill_amount: Mapped[float | None] = mapped_column(Float, nullable=True)

    # 所属专业册号（如 "C10"）
    specialty: Mapped[str] = mapped_column(String(20), default="")

    # 所属Sheet页名称（如 "给排水"、"电气"，用于前端分组显示）
    sheet_name: Mapped[str] = mapped_column(String(100), default="")

    # 所属分部工程名称（如 "给水工程"、"强电系统"，用于前端分部标题行）
    section: Mapped[str] = mapped_column(String(200), default="")

    # ============================================================
    # 匹配结果
    # ============================================================

    # 匹配到的定额列表（JSON数组，每个元素包含 quota_id/name/unit/param_score 等）
    # 示例: [{"quota_id": "C10-2-45", "name": "镀锌钢管安装DN25", "unit": "m", ...}]
    quotas: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)

    # 置信度（0~100的整数，越高越可靠）
    confidence: Mapped[int] = mapped_column(Integer, default=0)

    # 匹配来源: "search"=纯搜索, "agent"=Agent, "experience"=经验库直通
    match_source: Mapped[str] = mapped_column(String(50), default="")

    # 匹配说明（给用户看的中文解释）
    explanation: Mapped[str] = mapped_column(Text, default="")

    # 候选定额数量（搜索到多少个候选）
    candidates_count: Mapped[int] = mapped_column(Integer, default=0)

    # 备选定额列表（JSON数组，top-N候选，供OpenClaw纠正时直接选用）
    alternatives: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)

    # 是否为措施项（脚手架/增加费等非实体项，不需要套定额）
    is_measure_item: Mapped[bool] = mapped_column(default=False)

    # 匹配追踪信息（JSON，记录匹配路径和最终来源，调试用）
    trace: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # ============================================================
    # 审核状态（用户在Web界面操作后更新）
    # ============================================================

    # 审核状态: "pending"=待审核, "confirmed"=已确认, "corrected"=已纠正
    review_status: Mapped[str] = mapped_column(String(20), default="pending")

    # 纠正后的定额（用户手动修改时填入，JSON格式同 quotas 字段）
    corrected_quotas: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)

    # 审核备注
    review_note: Mapped[str] = mapped_column(Text, default="")

    # ============================================================
    # 时间戳
    # ============================================================

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
