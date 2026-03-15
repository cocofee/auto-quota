"""
匹配结果相关的请求/响应数据格式
"""

import uuid
from datetime import datetime
from pydantic import BaseModel, Field


class QuotaItem(BaseModel):
    """定额项信息（quotas 和 corrected_quotas 字段的元素结构）"""
    quota_id: str = Field(min_length=1, max_length=50, description="定额编号，如 C10-2-45")
    name: str = Field(min_length=1, max_length=200, description="定额名称")
    unit: str = Field(default="", description="计量单位")
    param_score: float | None = Field(default=None, description="参数匹配度 (0~1)")
    rerank_score: float | None = Field(default=None, description="重排得分 (0~1)")
    source: str = Field(default="", description="匹配来源")


class MatchResultResponse(BaseModel):
    """单条匹配结果"""
    id: uuid.UUID
    index: int
    bill_code: str = ""
    bill_name: str
    bill_description: str
    bill_unit: str
    bill_quantity: float | None
    specialty: str
    sheet_name: str = ""
    section: str = ""
    quotas: list[QuotaItem] | None
    confidence: int
    match_source: str
    explanation: str
    candidates_count: int
    review_status: str
    corrected_quotas: list[QuotaItem] | None
    review_note: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ResultListResponse(BaseModel):
    """匹配结果列表"""
    items: list[MatchResultResponse]
    total: int
    # 统计摘要（置信度分布）
    summary: dict = Field(default_factory=dict)


class CorrectResultRequest(BaseModel):
    """纠正或确认匹配结果

    纠正：传 corrected_quotas（替换定额）
    确认：传 review_status="confirmed"（不传 corrected_quotas）
    兼容 OpenClaw 等外部工具直接调 PUT 接口确认的场景。
    """
    corrected_quotas: list[QuotaItem] | None = Field(
        default=None, description="纠正后的定额列表（纠正时必填，确认时可不填）"
    )
    review_note: str = Field(default="", max_length=500, description="审核备注")
    review_status: str | None = Field(default=None, description="直接设置状态（confirmed/corrected）")


class ConfirmResultsRequest(BaseModel):
    """批量确认匹配结果"""
    result_ids: list[uuid.UUID] = Field(
        min_length=1, max_length=500, description="要确认的结果ID列表（1-500条）"
    )
