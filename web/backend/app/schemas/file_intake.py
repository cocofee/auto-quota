"""
File intake request/response schemas.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class FileIntakeResponse(BaseModel):
    file_id: str
    filename: str
    status: str
    file_type: str = ""
    source_hint: str = ""
    province: str = ""
    project_name: str = ""
    project_stage: str = ""
    current_stage: str = ""
    next_action: str = ""
    receipt_summary: dict = Field(default_factory=dict)
    failure_type: str = ""
    failure_stage: str = ""
    needs_manual_review: bool = False
    manual_review_reason: str = ""
    classify_result: dict = Field(default_factory=dict)
    parse_summary: dict = Field(default_factory=dict)
    route_result: dict = Field(default_factory=dict)
    error_message: str = ""
    created_at: datetime
    updated_at: datetime


class FileClassifyRequest(BaseModel):
    force: bool = Field(default=False)


class FileClassifyResponse(BaseModel):
    file_id: str
    status: str
    file_type: str
    confidence: float = 0.0
    signals: list[str] = Field(default_factory=list)


class FileParseRequest(BaseModel):
    force: bool = Field(default=False)
    parser_profile: str = Field(default="", max_length=100)
    target_mode: str = Field(default="auto", max_length=50)


class FileParseResponse(BaseModel):
    file_id: str
    status: str
    file_type: str
    parse_summary: dict = Field(default_factory=dict)


class FileRouteRequest(BaseModel):
    route_targets: list[str] = Field(default_factory=list)
    auto_create_task: bool = False


class FileRouteResponse(BaseModel):
    file_id: str
    status: str
    targets: list[dict] = Field(default_factory=list)


class FileManualReviewConfirmRequest(BaseModel):
    file_type: str = Field(default="", max_length=100)
    continue_from: str = Field(default="parse", max_length=20)
    route_targets: list[str] = Field(default_factory=list)
    auto_create_task: bool = False


class FileManualReviewConfirmResponse(BaseModel):
    file_id: str
    status: str
    current_stage: str = ""
    next_action: str = ""
    file_type: str = ""
    message: str = ""
