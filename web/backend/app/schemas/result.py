"""
匹配结果相关的请求/响应数据结构。
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class QuotaItem(BaseModel):
    """定额项信息。用于 quotas / corrected_quotas / openclaw_suggested_quotas。"""

    quota_id: str = Field(min_length=1, max_length=50, description="定额编号，如 C10-2-45")
    name: str = Field(min_length=1, max_length=200, description="定额名称")
    unit: str = Field(default="", description="计量单位")
    param_score: float | None = Field(default=None, description="参数匹配度(0~1)")
    rerank_score: float | None = Field(default=None, description="重排得分(0~1)")
    source: str = Field(default="", description="匹配来源")


class MatchResultResponse(BaseModel):
    """单条匹配结果。"""

    id: uuid.UUID
    index: int
    bill_code: str = ""
    bill_name: str
    bill_description: str
    bill_unit: str
    bill_quantity: float | None
    bill_unit_price: float | None = None
    bill_amount: float | None = None
    specialty: str
    sheet_name: str = ""
    section: str = ""
    quotas: list[QuotaItem] | None
    alternatives: list[dict] | None = None
    confidence: int
    confidence_score: int = 0
    review_risk: str = "low"
    light_status: str = "red"
    match_source: str
    explanation: str
    candidates_count: int
    is_measure_item: bool = False
    review_status: str
    corrected_quotas: list[QuotaItem] | None
    review_note: str
    openclaw_review_status: str = "pending"
    openclaw_suggested_quotas: list[QuotaItem] | None = None
    openclaw_review_note: str = ""
    openclaw_review_confidence: int | None = None
    openclaw_review_actor: str = ""
    openclaw_review_time: datetime | None = None
    openclaw_review_confirm_status: str = "pending"
    openclaw_review_confirmed_by: str = ""
    openclaw_review_confirm_time: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ResultListResponse(BaseModel):
    """匹配结果列表。"""

    items: list[MatchResultResponse]
    total: int
    summary: dict = Field(default_factory=dict)


class CorrectResultRequest(BaseModel):
    """正式确认或正式纠正。"""

    corrected_quotas: list[QuotaItem] | None = Field(
        default=None, description="正式纠正后的定额列表；确认时可不传"
    )
    review_note: str = Field(default="", max_length=500, description="审核备注")
    review_status: str | None = Field(
        default=None, description="直接设置正式审核状态（confirmed/corrected）"
    )


class ConfirmResultsRequest(BaseModel):
    """批量确认匹配结果。"""

    result_ids: list[uuid.UUID] = Field(
        min_length=1, max_length=500, description="要确认的结果 ID 列表（1-500 条）"
    )


class OpenClawReviewDraftRequest(BaseModel):
    """OpenClaw 审核建议草稿。只保存建议，不改正式纠正结果。"""

    openclaw_suggested_quotas: list[QuotaItem] = Field(
        min_length=1, description="OpenClaw 建议的定额列表"
    )
    openclaw_review_note: str = Field(default="", max_length=500, description="OpenClaw 审核备注")
    openclaw_review_confidence: int | None = Field(
        default=None, ge=0, le=100, description="OpenClaw 对本次建议的置信度(0-100)"
    )


class OpenClawReviewConfirmRequest(BaseModel):
    """人工二次确认 OpenClaw 审核建议。"""

    decision: str = Field(description="approve 或 reject")
    review_note: str = Field(default="", max_length=500, description="人工确认备注")
