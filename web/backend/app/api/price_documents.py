"""
Price document APIs.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth.deps import get_current_user
from app.models.user import User
from app.schemas.price_document import (
    PriceDocumentCreateRequest,
    PriceDocumentListResponse,
    PriceDocumentParseRequest,
    PriceDocumentParseResponse,
    PriceDocumentResponse,
)
from src.file_intake_db import FileIntakeDB
from src.price_reference_db import PriceReferenceDB

router = APIRouter()


def _utc_from_ts(value) -> datetime:
    return datetime.fromtimestamp(float(value or 0), tz=timezone.utc)


def _normalize_parse_summary(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            return json.loads(value)
        except Exception:
            return value
    return {}


def _to_response(record: dict) -> PriceDocumentResponse:
    return PriceDocumentResponse(
        id=int(record["id"]),
        file_id=record.get("file_id") or "",
        document_type=record.get("document_type") or "",
        project_name=record.get("project_name") or "",
        project_stage=record.get("project_stage") or "",
        specialty=record.get("specialty") or "",
        region=record.get("region") or "",
        source_file_name=record.get("source_file_name") or "",
        status=record.get("status") or "",
        parse_summary=_normalize_parse_summary(record.get("parse_summary")),
        created_at=_utc_from_ts(record.get("created_at")),
        updated_at=_utc_from_ts(record.get("updated_at")),
    )


@router.post("", response_model=PriceDocumentResponse, status_code=201)
async def create_price_document(
    req: PriceDocumentCreateRequest,
    user: User = Depends(get_current_user),
):
    _ = user
    intake = FileIntakeDB().get_file(req.file_id)
    if not intake:
        raise HTTPException(status_code=404, detail="file not found")
    document_id = PriceReferenceDB().create_document_from_file(
        file_id=req.file_id,
        document_type=req.document_type,
        project_name=req.project_name or intake.get("project_name") or "",
        project_stage=req.project_stage or intake.get("project_stage") or "",
        specialty=req.specialty or "",
        region=req.province or intake.get("province") or "",
        source_file_name=intake.get("filename") or "",
        source_file_path=intake.get("stored_path") or "",
        status="created",
    )
    record = PriceReferenceDB().get_document(document_id)
    if not record:
        raise HTTPException(status_code=500, detail="document create failed")
    return _to_response(record)


@router.get("", response_model=PriceDocumentListResponse)
async def list_price_documents(
    document_type: str = Query(default="", description="文档类型"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
):
    _ = user
    payload = PriceReferenceDB().list_documents(
        document_type=document_type,
        page=page,
        size=size,
    )
    return PriceDocumentListResponse(
        items=[_to_response(item) for item in payload["items"]],
        total=payload["total"],
        page=payload["page"],
        size=payload["size"],
    )


@router.get("/{document_id}", response_model=PriceDocumentResponse)
async def get_price_document(
    document_id: int,
    user: User = Depends(get_current_user),
):
    _ = user
    record = PriceReferenceDB().get_document(document_id)
    if not record:
        raise HTTPException(status_code=404, detail="document not found")
    return _to_response(record)


@router.post("/{document_id}/parse", response_model=PriceDocumentParseResponse)
async def parse_price_document(
    document_id: int,
    req: PriceDocumentParseRequest,
    user: User = Depends(get_current_user),
):
    _ = user
    record = PriceReferenceDB().get_document(document_id)
    if not record:
        raise HTTPException(status_code=404, detail="document not found")
    summary = _normalize_parse_summary(record.get("parse_summary"))
    if not summary or req.force:
        summary = {"warnings": ["parser not wired yet"], "document_type": record.get("document_type")}
    return PriceDocumentParseResponse(
        id=document_id,
        status="parsed",
        parse_summary=summary,
    )
