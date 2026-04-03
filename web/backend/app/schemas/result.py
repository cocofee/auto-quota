"""
匹配结果相关的请求/响应数据结构。
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class QuotaItem(BaseModel):
    """定额项信息。用于 quotas / corrected_quotas / openclaw_suggested_quotas。"""

    quota_id: str = Field(min_length=1, max_length=50, description="定额编号，如 C10-2-45")
    name: str = Field(min_length=1, max_length=200, description="定额名称")
    unit: str = Field(default="", description="计量单位")
    param_score: float | None = Field(default=None, description="参数匹配度(0~1)")
    rerank_score: float | None = Field(default=None, description="重排得分(0~1)")
    source: str = Field(default="", description="匹配来源")


OpenClawReviewStatus = Literal["pending", "reviewed", "applied", "rejected"]
OpenClawReviewConfirmStatus = Literal["pending", "approved", "rejected"]
OpenClawDecisionType = Literal[
    "agree",
    "override_within_candidates",
    "retry_search_then_select",
    "candidate_pool_insufficient",
    "abstain",
]
OpenClawErrorStage = Literal["retriever", "ranker", "arbiter", "final_validator", "unknown"]
OpenClawErrorType = Literal[
    "wrong_family",
    "wrong_param",
    "wrong_book",
    "synonym_gap",
    "low_confidence_override",
    "missing_candidate",
    "unknown",
]


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
    knowledge_evidence: dict | None = None
    knowledge_basis: dict | None = None
    knowledge_summary: dict | None = None
    trace: dict | None = None
    candidates_count: int
    is_measure_item: bool = False
    review_status: str
    corrected_quotas: list[QuotaItem] | None
    review_note: str
    openclaw_review_status: OpenClawReviewStatus = "pending"
    openclaw_suggested_quotas: list[QuotaItem] | None = None
    openclaw_review_note: str = ""
    openclaw_review_confidence: int | None = None
    openclaw_review_actor: str = ""
    openclaw_review_time: datetime | None = None
    openclaw_decision_type: OpenClawDecisionType | None = None
    openclaw_error_stage: OpenClawErrorStage | None = None
    openclaw_error_type: OpenClawErrorType | None = None
    openclaw_retry_query: str = ""
    openclaw_reason_codes: list[str] | None = None
    openclaw_review_payload: dict | None = None
    openclaw_review_confirm_status: OpenClawReviewConfirmStatus = "pending"
    openclaw_review_confirmed_by: str = ""
    openclaw_review_confirm_time: datetime | None = None
    human_feedback_payload: dict | None = None
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

    openclaw_suggested_quotas: list[QuotaItem] | None = Field(
        default=None,
        description="OpenClaw 建议的定额列表；旧版接口建议继续传，结构化 agree 场景可省略",
    )
    openclaw_review_note: str = Field(default="", max_length=500, description="OpenClaw 审核备注")
    openclaw_review_confidence: int | None = Field(
        default=None, ge=0, le=100, description="OpenClaw 对本次建议的置信度(0-100)"
    )
    openclaw_decision_type: OpenClawDecisionType | None = Field(
        default=None,
        description="结构化复判类型：agree / override_within_candidates / retry_search_then_select / candidate_pool_insufficient / abstain",
    )
    openclaw_error_stage: OpenClawErrorStage | None = Field(
        default=None,
        description="OpenClaw 判断错误主要发生的阶段",
    )
    openclaw_error_type: OpenClawErrorType | None = Field(
        default=None,
        description="OpenClaw 判断的错误类型",
    )
    openclaw_retry_query: str = Field(
        default="",
        max_length=500,
        description="OpenClaw 建议的重搜 query；只保存建议，不代表已执行",
    )
    openclaw_reason_codes: list[str] | None = Field(
        default=None,
        description="结构化原因码列表，供后续统计和回流使用",
    )
    openclaw_review_payload: dict | None = Field(
        default=None,
        description="OpenClaw 完整结构化审核 payload",
    )


class OpenClawReviewConfirmRequest(BaseModel):
    """人工二次确认 OpenClaw 审核建议。"""

    decision: str = Field(description="approve 或 reject")
    review_note: str = Field(default="", max_length=500, description="人工确认备注")
    human_feedback_payload: dict | None = Field(
        default=None,
        description="人工确认后沉淀的结构化错因/反馈信息",
    )
