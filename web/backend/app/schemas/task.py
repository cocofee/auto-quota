"""
任务相关的请求/响应数据格式
"""

import uuid
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field


class MatchMode(str, Enum):
    """匹配模式枚举"""
    SEARCH = "search"  # 纯搜索（免费）
    AGENT = "agent"    # Agent模式（需API Key）


class TaskCreateRequest(BaseModel):
    """创建任务请求（文件通过multipart上传，这里只定义参数部分）"""
    mode: MatchMode = Field(default=MatchMode.SEARCH, description="匹配模式")
    province: str = Field(min_length=1, max_length=255, description="省份定额库名称")
    sheet: str | None = Field(default=None, max_length=100, description="指定Sheet名称（空=全部）")
    limit_count: int | None = Field(default=None, ge=1, le=10000, description="限制处理条数（1-10000）")
    use_experience: bool = Field(default=True, description="是否使用经验库")
    agent_llm: str | None = Field(default=None, max_length=50, description="Agent模式的大模型")


class TaskResponse(BaseModel):
    """任务信息"""
    id: uuid.UUID
    name: str
    original_filename: str
    mode: str
    province: str
    sheet: str | None
    limit_count: int | None
    use_experience: bool
    agent_llm: str | None
    status: str
    progress: int
    progress_current: int
    progress_message: str
    error_message: str | None
    stats: dict | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    # 用户信息（管理员查看所有任务时显示）
    username: str | None = None
    # 反馈上传相关字段（用户纠正Excel上传后填入）
    feedback_path: str | None = None
    feedback_uploaded_at: datetime | None = None
    feedback_stats: dict | None = None

    model_config = {"from_attributes": True}


class TaskListResponse(BaseModel):
    """任务列表（分页）"""
    items: list[TaskResponse]
    total: int         # 任务总条数
    total_bills: int   # 所有任务的清单条数合计
    page: int          # 当前页码
    size: int          # 每页条数
