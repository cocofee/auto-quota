"""
匹配任务模型

记录每次匹配任务的配置参数、状态、统计信息。
一个任务对应一个上传的Excel文件和一次匹配执行。
"""

import uuid
from datetime import datetime

from sqlalchemy import String, Integer, Boolean, DateTime, ForeignKey, JSON, CheckConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Task(Base):
    """匹配任务表"""
    __tablename__ = "tasks"

    # 主键
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # 所属用户（外键关联 users 表）
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), index=True
    )

    # ============================================================
    # 任务配置（用户提交时的参数）
    # ============================================================

    # 任务名称（默认从文件名提取，用户可修改）
    name: Mapped[str] = mapped_column(String(255), default="")

    # 上传的Excel文件路径（服务器上的存储路径）
    file_path: Mapped[str] = mapped_column(String(500))

    # 原始文件名（用户上传时的文件名，显示用）
    original_filename: Mapped[str] = mapped_column(String(255))

    # 匹配模式: "search"=纯搜索(免费), "agent"=Agent模式(需API Key)
    mode: Mapped[str] = mapped_column(String(20), default="search")

    # 省份定额库名称（如 "北京市建设工程施工消耗量标准(2024)"）
    province: Mapped[str] = mapped_column(String(255))

    # 指定Sheet名称（None=全部Sheet）
    sheet: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # 限制处理条数（None=全部）
    limit_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # 是否使用经验库
    use_experience: Mapped[bool] = mapped_column(Boolean, default=True)

    # Agent模式使用的大模型（如 "deepseek", "claude"）
    agent_llm: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # ============================================================
    # 任务状态
    # ============================================================

    # 状态: pending=排队中, running=匹配中, completed=已完成,
    #       failed=失败, cancelled=已取消
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)

    # 匹配进度（0~100的百分比，running状态时实时更新）
    progress: Mapped[int] = mapped_column(Integer, default=0)

    # 当前正在处理的清单项名称（进度显示用）
    progress_message: Mapped[str] = mapped_column(String(255), default="")

    # 失败时的错误信息
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    # Celery任务ID（用于取消任务）
    celery_task_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # ============================================================
    # 匹配结果统计（完成后填入）
    # ============================================================

    # 统计信息（JSON格式，包含 total/matched/high_conf/mid_conf/low_conf 等）
    stats: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # 输出Excel文件路径
    output_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # JSON结果文件路径（前端读取详细结果用）
    json_output_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # ============================================================
    # 反馈上传（用户纠正后的Excel）
    # ============================================================

    # 用户上传的纠正Excel文件路径
    feedback_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # 反馈上传时间
    feedback_uploaded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # 反馈学习统计（JSON: {"total": 清单总数, "learned": 学习条数}）
    feedback_stats: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # ============================================================
    # 时间戳
    # ============================================================

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # 开始匹配的时间
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 匹配完成的时间
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # 表级约束：mode 只允许 search 或 agent
    __table_args__ = (
        CheckConstraint("mode IN ('search', 'agent')", name="ck_task_mode"),
    )
