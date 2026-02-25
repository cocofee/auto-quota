"""
反馈上传相关的请求/响应数据格式
"""

import uuid
from datetime import datetime
from pydantic import BaseModel, Field


class FeedbackUploadResponse(BaseModel):
    """反馈上传成功响应"""
    message: str = "反馈上传成功"
    stats: dict = Field(description="学习统计: {total: 清单总数, learned: 学习条数}")


class FeedbackListItem(BaseModel):
    """反馈列表中的单条记录"""
    task_id: uuid.UUID
    task_name: str
    original_filename: str
    province: str
    feedback_uploaded_at: datetime | None
    feedback_stats: dict | None

    model_config = {"from_attributes": True}


class FeedbackListResponse(BaseModel):
    """反馈列表分页响应"""
    items: list[FeedbackListItem]
    total: int
    page: int
    size: int


class FeedbackDetailResponse(BaseModel):
    """反馈详情"""
    task_id: uuid.UUID
    task_name: str
    original_filename: str
    province: str
    mode: str
    feedback_uploaded_at: datetime | None
    feedback_stats: dict | None
    # 任务统计（原匹配结果的统计）
    task_stats: dict | None
    created_at: datetime
    completed_at: datetime | None
