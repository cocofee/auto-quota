"""
Price document schemas.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class PriceDocumentCreateRequest(BaseModel):
    file_id: str
    document_type: str
    project_name: str = ""
    project_stage: str = ""
    province: str = ""
    specialty: str = ""


class PriceDocumentResponse(BaseModel):
    id: int
    file_id: str = ""
    document_type: str
    project_name: str = ""
    project_stage: str = ""
    specialty: str = ""
    region: str = ""
    source_file_name: str = ""
    status: str = ""
    parse_summary: str | dict = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class PriceDocumentListResponse(BaseModel):
    items: list[PriceDocumentResponse]
    total: int
    page: int
    size: int


class PriceDocumentParseRequest(BaseModel):
    force: bool = False


class PriceDocumentParseResponse(BaseModel):
    id: int
    status: str
    parse_summary: dict = Field(default_factory=dict)
