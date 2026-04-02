"""
OpenClaw review-job request/response schemas.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


OpenClawReviewJobStatus = Literal["ready", "running", "completed", "failed"]
OpenClawReviewJobScope = Literal["need_review", "all_pending", "yellow_red_pending"]


class OpenClawReviewJobCreateRequest(BaseModel):
    source_task_id: uuid.UUID = Field(description="Jarvis 主任务 ID")
    scope: OpenClawReviewJobScope = Field(
        default="need_review",
        description="审核范围；当前仅做作业绑定和范围标识，不执行自动复判",
    )
    note: str = Field(default="", max_length=500, description="创建审核作业的说明")


class OpenClawReviewJobResponse(BaseModel):
    id: uuid.UUID
    source_task_id: uuid.UUID
    status: OpenClawReviewJobStatus
    scope: OpenClawReviewJobScope
    requested_by: str = ""
    note: str = ""

    total_results: int = 0
    pending_results: int = 0
    reviewable_results: int = 0
    green_count: int = 0
    yellow_count: int = 0
    red_count: int = 0
    reviewed_pending_count: int = 0

    summary: dict | None = None
    error_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None

    model_config = {"from_attributes": True}
