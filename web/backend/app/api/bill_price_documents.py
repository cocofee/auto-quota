"""
Priced bill document APIs.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.auth.deps import get_current_user
from app.models.user import User
from app.api.file_intake import ingest
from app.schemas.price_document import (
    PriceDocumentCreateRequest,
    PriceDocumentParseRequest,
    PriceDocumentParseResponse,
    PriceDocumentResponse,
)
from app.api.price_documents import _to_response
from src.file_intake_db import FileIntakeDB
from src.priced_bill_parser import parse_priced_bill_document
from src.price_reference_db import PriceReferenceDB

router = APIRouter()


@router.post("", response_model=PriceDocumentResponse, status_code=201)
async def create_bill_price_document(
    req: PriceDocumentCreateRequest,
    user: User = Depends(get_current_user),
):
    _ = user
    if req.document_type and req.document_type != "priced_bill_file":
        raise HTTPException(status_code=422, detail="document_type must be priced_bill_file")
    intake = FileIntakeDB().get_file(req.file_id)
    if not intake:
        raise HTTPException(status_code=404, detail="file not found")
    document_id = PriceReferenceDB().create_document_from_file(
        file_id=req.file_id,
        document_type="priced_bill_file",
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


@router.post("/{document_id}/parse", response_model=PriceDocumentParseResponse)
async def parse_bill_price_document(
    document_id: int,
    req: PriceDocumentParseRequest,
    user: User = Depends(get_current_user),
):
    _ = user
    record = PriceReferenceDB().get_document(document_id)
    if not record:
        raise HTTPException(status_code=404, detail="document not found")
    if record.get("document_type") != "priced_bill_file":
        raise HTTPException(status_code=422, detail="document is not a priced_bill_file")

    intake = FileIntakeDB().get_file(record.get("file_id") or "")
    if not intake:
        raise HTTPException(status_code=404, detail="source file not found")

    parsed = parse_priced_bill_document(
        intake["stored_path"],
        project_name=record.get("project_name") or intake.get("project_name") or "",
        specialty=record.get("specialty") or "",
    )
    price_db = PriceReferenceDB()
    payload_items: list[dict] = []
    priced_count = 0
    quota_backed_count = 0
    learning_records: list[dict] = []

    for item in parsed.items:
        row = item.to_record()
        row["project_name"] = record.get("project_name") or intake.get("project_name") or parsed.project_name
        row["project_stage"] = record.get("project_stage") or intake.get("project_stage") or "historical_import"
        row["specialty"] = item.specialty or record.get("specialty") or ""
        payload_items.append(row)
        if row.get("composite_unit_price") is not None:
            priced_count += 1
        if row.get("quota_code"):
            quota_backed_count += 1

        quota_ids = [q.code for q in item.quotas if q.code]
        quota_names = [q.name for q in item.quotas if q.name]
        if quota_ids:
            learning_records.append({
                "bill_text": item.bill_text,
                "bill_name": item.boq_name_raw,
                "bill_code": item.boq_code,
                "bill_unit": item.unit,
                "quota_ids": quota_ids,
                "quota_names": quota_names,
                "materials": row.get("materials_json") or [],
                "specialty": row["specialty"] or None,
                "project_name": row["project_name"],
                "province": record.get("region") or intake.get("province") or None,
                "confidence": 95,
                "feature_text": row.get("feature_text") or "",
                "parse_status": "parsed",
            })

    written = price_db.replace_boq_items(document_id, payload_items)
    learning_result = ingest(
        file_id=record.get("file_id") or intake.get("file_id"),
        records=learning_records,
        ingest_intent="learning",
        evidence_level="completed_project",
        business_type="priced_bill_file",
        actor=(getattr(user, "email", None) or getattr(user, "nickname", None) or str(user.id)),
        source_context={
            "project_name": record.get("project_name") or intake.get("project_name") or parsed.project_name,
            "province": record.get("region") or intake.get("province") or "",
            "specialty": record.get("specialty") or "",
            "project_stage": record.get("project_stage") or intake.get("project_stage") or "historical_import",
            "parse_status": "parsed",
            "source": "completed_project",
        },
    )
    summary = {
        "file_path": intake["stored_path"],
        "file_type": parsed.file_type,
        "bill_items": len(parsed.items),
        "priced_items": priced_count,
        "quota_backed_items": quota_backed_count,
        "written_price_reference_items": written,
        "written_learning_items": learning_result.written_learning,
        "rejected_learning_items": learning_result.skipped,
        "learning_warnings": learning_result.warnings,
        "warnings": parsed.warnings,
    }
    price_db.update_document_parse(document_id, status="parsed", parse_summary=summary)
    return PriceDocumentParseResponse(
        id=document_id,
        status="parsed",
        parse_summary=summary,
    )
