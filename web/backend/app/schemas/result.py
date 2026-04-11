"""Schemas for match result APIs."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class QuotaItem(BaseModel):
    """Quota info used by quotas/corrected_quotas/openclaw_suggested_quotas."""

    quota_id: str = Field(min_length=1, max_length=50, description="quota code, for example C10-2-45")
    name: str = Field(min_length=1, max_length=200, description="quota name")
    unit: str = Field(default="", description="measurement unit")
    param_score: float | None = Field(default=None, description="parameter match score, 0-1")
    rerank_score: float | None = Field(default=None, description="rerank score, 0-1")
    source: str = Field(default="", description="match source")


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
    """One match result."""

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
    """Match result list."""

    items: list[MatchResultResponse]
    total: int
    summary: dict = Field(default_factory=dict)


class CorrectResultRequest(BaseModel):
    """Formal confirmation or correction."""

    corrected_quotas: list[QuotaItem] | None = Field(
        default=None,
        description="final corrected quota list",
    )
    review_note: str = Field(default="", max_length=500, description="review note")
    review_status: str | None = Field(
        default=None,
        description="optional explicit formal review status, for example confirmed/corrected",
    )


class ConfirmResultsRequest(BaseModel):
    """Batch confirm match results."""

    result_ids: list[uuid.UUID] = Field(
        min_length=1,
        max_length=500,
        description="result ids to confirm, 1-500",
    )


class OpenClawReviewDraftRequest(BaseModel):
    """OpenClaw review draft. Saves draft only, does not apply formal correction."""

    openclaw_suggested_quotas: list[QuotaItem] | None = Field(
        default=None,
        description="OpenClaw suggested quota list; optional for agree when current top1 is kept",
    )
    openclaw_review_note: str = Field(default="", max_length=500, description="OpenClaw review note")
    openclaw_review_confidence: int | None = Field(
        default=None,
        ge=0,
        le=100,
        description="OpenClaw confidence for this draft, 0-100",
    )
    openclaw_decision_type: OpenClawDecisionType | None = Field(
        default=None,
        description=(
            "structured decision type: agree / override_within_candidates / "
            "retry_search_then_select / candidate_pool_insufficient / abstain"
        ),
    )
    openclaw_error_stage: OpenClawErrorStage | None = Field(
        default=None,
        description="where OpenClaw thinks the main error happened",
    )
    openclaw_error_type: OpenClawErrorType | None = Field(
        default=None,
        description="what kind of error OpenClaw thinks it is",
    )
    openclaw_retry_query: str = Field(
        default="",
        max_length=500,
        description="suggested retry query; saved as draft only and not executed automatically",
    )
    openclaw_reason_codes: list[str] | None = Field(
        default=None,
        description="structured reason code list for analytics and learning loop",
    )
    openclaw_review_payload: dict | None = Field(
        default=None,
        description="full structured OpenClaw review payload",
    )


class OpenClawReviewConfirmRequest(BaseModel):
    """Human confirmation for one OpenClaw review draft."""

    decision: str = Field(description="approve or reject")
    review_note: str = Field(default="", max_length=500, description="human confirmation note")
    human_feedback_payload: dict | None = Field(
        default=None,
        description=(
            "structured human feedback payload. Recommended protocol: "
            "lobster_review_feedback.v1 with fields source, adopt_openclaw, "
            "final_quota/final_quotas, manual_reason_codes, manual_note, "
            "promotion_decision. See docs/lobster_review_feedback_protocol.md"
        ),
    )
