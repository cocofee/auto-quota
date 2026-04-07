"""
QMD knowledge search schemas.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class QMDSearchRequest(BaseModel):
    query: str = Field(min_length=1, description="QMD search query")
    top_k: int = Field(default=5, ge=1, le=20, description="Maximum hits to return")
    category: str = Field(default="", description="QMD category filter")
    page_type: str = Field(default="", description="QMD page type filter")
    province: str = Field(default="", description="Province filter")
    specialty: str = Field(default="", description="Specialty filter")
    source_kind: str = Field(default="", description="Source kind filter")
    status: str = Field(default="", description="Status filter")


class QMDSearchHit(BaseModel):
    chunk_id: str = ""
    score: float = 0.0
    title: str = ""
    heading: str = ""
    category: str = ""
    page_type: str = ""
    path: str = ""
    province: str = ""
    specialty: str = ""
    status: str = ""
    source_kind: str = ""
    source_refs_text: str = ""
    preview: str = ""
    document: str = ""


class QMDSearchResponse(BaseModel):
    query: str
    count: int = 0
    filters: dict[str, str] = Field(default_factory=dict)
    hits: list[QMDSearchHit] = Field(default_factory=list)
