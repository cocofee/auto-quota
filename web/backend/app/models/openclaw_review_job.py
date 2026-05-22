"""
OpenClaw review-job model.

One review job points to exactly one Jarvis source task.
It is a workflow object for review orchestration, not a duplicated match task.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class OpenClawReviewJob(Base):
    """OpenClaw review job bound to one Jarvis source task."""

    __tablename__ = "openclaw_review_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )

    status: Mapped[str] = mapped_column(String(20), default="ready", index=True)
    scope: Mapped[str] = mapped_column(String(50), default="need_review")
    requested_by: Mapped[str] = mapped_column(String(255), default="")
    note: Mapped[str] = mapped_column(Text, default="")

    total_results: Mapped[int] = mapped_column(Integer, default=0)
    pending_results: Mapped[int] = mapped_column(Integer, default=0)
    reviewable_results: Mapped[int] = mapped_column(Integer, default=0)
    green_count: Mapped[int] = mapped_column(Integer, default=0)
    yellow_count: Mapped[int] = mapped_column(Integer, default=0)
    red_count: Mapped[int] = mapped_column(Integer, default=0)
    reviewed_pending_count: Mapped[int] = mapped_column(Integer, default=0)

    summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
