"""
Knowledge staging admin API.

Mounted under /api/admin/knowledge-staging.
Current P0 scope:
- health check
- audit_errors create/query/update
- promotion_queue create/query/review
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import require_admin
from app.database import get_db
from app.models.result import MatchResult
from app.models.user import User

router = APIRouter()


def _get_staging():
    from src.knowledge_staging import KnowledgeStaging

    return KnowledgeStaging()


def _get_promotion_service():
    from src.knowledge_promotion import KnowledgePromotionService

    return KnowledgePromotionService()


def _get_accuracy_tracker():
    from src.accuracy_tracker import AccuracyTracker

    return AccuracyTracker()


def _get_experience_db():
    from src.experience_db import ExperienceDB

    return ExperienceDB()


class AuditErrorCreateRequest(BaseModel):
    source_id: str = Field(min_length=1)
    source_type: str = ""
    source_table: str = ""
    source_record_id: str = ""
    owner: str = ""
    evidence_ref: str = ""
    status: str = "draft"

    task_id: str = ""
    result_id: str = ""
    project_id: str = ""
    province: str = ""
    specialty: str = ""

    bill_name: str = ""
    bill_desc: str = ""
    predicted_quota_code: str = ""
    predicted_quota_name: str = ""
    corrected_quota_code: str = ""
    corrected_quota_name: str = ""

    match_source: str = ""
    error_type: str = ""
    error_level: str = ""
    root_cause: str = ""
    root_cause_tags: list[str] = Field(default_factory=list)
    fix_suggestion: str = ""
    decision_basis: str = ""

    requires_manual_followup: bool = False
    can_promote_rule: bool = False
    can_promote_method: bool = False


class AuditErrorStatusUpdateRequest(BaseModel):
    status: str | None = None
    review_status: str | None = None
    reviewer: str | None = None
    review_comment: str | None = None


class PromotionCreateRequest(BaseModel):
    source_id: str = Field(min_length=1)
    source_type: str = ""
    source_table: str = Field(min_length=1)
    source_record_id: str = Field(min_length=1)
    owner: str = ""
    evidence_ref: str = ""
    status: str = "draft"

    candidate_type: Literal["rule", "method", "universal", "experience"]
    target_layer: Literal["RuleKnowledge", "MethodCards", "UniversalKB", "ExperienceDB"]
    candidate_title: str = Field(min_length=1)
    candidate_summary: str = ""
    candidate_payload: dict[str, Any] = Field(default_factory=dict)
    priority: int = Field(default=50, ge=0, le=100)
    approval_required: bool = True


class PromotionReviewRequest(BaseModel):
    review_status: Literal["reviewing", "approved", "rejected"]
    status: Literal["reviewing", "approved", "rejected"] | None = None
    reviewer: str = ""
    review_comment: str = ""
    rejection_reason: str = ""


class PromotionExecuteRequest(BaseModel):
    expected_target_layer: Literal["RuleKnowledge", "MethodCards", "ExperienceDB"] | None = None


class PromotionRollbackRequest(BaseModel):
    reason: str = ""


@router.get("/health")
async def staging_health(admin: User = Depends(require_admin)):
    """Health check for the staging database."""
    try:
        return await asyncio.to_thread(lambda: _get_staging().health_check())
    except Exception as e:
        logger.error(f"knowledge staging health check failed: {e}")
        raise HTTPException(status_code=500, detail="knowledge staging health check failed")


@router.get("/stats")
async def staging_stats(admin: User = Depends(require_admin)):
    """Minimal admin dashboard stats for knowledge staging."""
    try:
        return await asyncio.to_thread(lambda: _get_staging().get_dashboard_stats())
    except Exception as e:
        logger.error(f"knowledge staging stats failed: {e}")
        raise HTTPException(status_code=500, detail="knowledge staging stats failed")


@router.get("/health-report")
async def staging_health_report(
    stale_pending_days: int = Query(default=7, ge=1, le=180),
    limit: int = Query(default=10, ge=1, le=50),
    admin: User = Depends(require_admin),
):
    """Governance-focused health report for staging and connected formal layers."""
    try:
        return await asyncio.to_thread(
            lambda: _get_staging().get_health_report(
                stale_pending_days=stale_pending_days,
                limit=limit,
            )
        )
    except Exception as e:
        logger.error(f"knowledge staging health report failed: {e}")
        raise HTTPException(status_code=500, detail="knowledge staging health report failed")


@router.get("/knowledge-impact")
async def staging_knowledge_impact(
    days: int = Query(default=7, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Knowledge-layer hit and benefit summary from real run history."""
    try:
        report = await asyncio.to_thread(
            lambda: _get_accuracy_tracker().get_knowledge_hit_report(days=days)
        )
        details = await asyncio.to_thread(
            lambda: _get_accuracy_tracker().get_recent_knowledge_hit_details(days=days)
        )

        task_ids: list[uuid.UUID] = []
        for raw_task_id in {str(item.get("task_id") or "") for item in details}:
            if not raw_task_id:
                continue
            try:
                task_ids.append(uuid.UUID(raw_task_id))
            except ValueError:
                continue

        result_map: dict[tuple[str, int], MatchResult] = {}
        if task_ids:
            result = await db.execute(
                select(MatchResult).where(MatchResult.task_id.in_(task_ids))
            )
            for item in result.scalars().all():
                result_map[(str(item.task_id), int(item.index))] = item

        metrics_by_layer = {
            str(item.get("layer")): item
            for item in report.get("layer_metrics", [])
        }
        for item in metrics_by_layer.values():
            item["reviewed_count"] = 0
            item["confirmed_count"] = 0
            item["corrected_count"] = 0
            item["pending_count"] = 0

        for detail in details:
            layer = str(detail.get("layer") or "")
            metric = metrics_by_layer.get(layer)
            if not metric:
                continue
            key = (str(detail.get("task_id") or ""), int(detail.get("result_index") or 0))
            match_result = result_map.get(key)
            if not match_result:
                continue

            review_status = str(match_result.review_status or "").strip().lower()
            corrected = bool(match_result.corrected_quotas) or review_status == "corrected"
            confirmed = (not corrected) and review_status == "confirmed"
            if corrected:
                metric["reviewed_count"] += 1
                metric["corrected_count"] += 1
            elif confirmed:
                metric["reviewed_count"] += 1
                metric["confirmed_count"] += 1
            else:
                metric["pending_count"] += 1

        for item in metrics_by_layer.values():
            reviewed_count = int(item.get("reviewed_count", 0))
            hit_count = int(item.get("hit_count", 0))
            item["review_coverage_rate"] = round((reviewed_count / hit_count) * 100, 1) if hit_count else 0.0
            item["confirmed_rate"] = round((int(item.get("confirmed_count", 0)) / reviewed_count) * 100, 1) if reviewed_count else 0.0
            item["corrected_rate"] = round((int(item.get("corrected_count", 0)) / reviewed_count) * 100, 1) if reviewed_count else 0.0

        object_metrics: dict[tuple[str, str], dict[str, Any]] = {}
        for detail in details:
            object_ref = str(detail.get("object_ref") or "").strip()
            layer = str(detail.get("layer") or "")
            if not object_ref or not layer:
                continue
            key = (layer, object_ref)
            item = object_metrics.setdefault(key, {
                "layer": layer,
                "object_ref": object_ref,
                "hit_count": 0,
                "direct_count": 0,
                "assist_count": 0,
                "reviewed_count": 0,
                "confirmed_count": 0,
                "corrected_count": 0,
                "pending_count": 0,
            })
            item["hit_count"] += 1
            if str(detail.get("hit_type") or "") == "direct":
                item["direct_count"] += 1
            else:
                item["assist_count"] += 1

            key = (str(detail.get("task_id") or ""), int(detail.get("result_index") or 0))
            match_result = result_map.get(key)
            if not match_result:
                item["pending_count"] += 1
                continue

            review_status = str(match_result.review_status or "").strip().lower()
            corrected = bool(match_result.corrected_quotas) or review_status == "corrected"
            confirmed = (not corrected) and review_status == "confirmed"
            if corrected:
                item["reviewed_count"] += 1
                item["corrected_count"] += 1
            elif confirmed:
                item["reviewed_count"] += 1
                item["confirmed_count"] += 1
            else:
                item["pending_count"] += 1

        top_objects = list(object_metrics.values())
        for item in top_objects:
            reviewed_count = int(item.get("reviewed_count", 0))
            item["review_coverage_rate"] = round((reviewed_count / int(item["hit_count"])) * 100, 1) if int(item["hit_count"]) else 0.0
            item["confirmed_rate"] = round((int(item.get("confirmed_count", 0)) / reviewed_count) * 100, 1) if reviewed_count else 0.0
            item["corrected_rate"] = round((int(item.get("corrected_count", 0)) / reviewed_count) * 100, 1) if reviewed_count else 0.0
        top_objects.sort(
            key=lambda item: (
                -int(item["hit_count"]),
                -int(item["corrected_count"]),
                str(item["layer"]),
                str(item["object_ref"]),
            )
        )
        report["top_objects"] = top_objects[:20]

        return report
    except Exception as e:
        logger.error(f"knowledge impact report failed: {e}")
        raise HTTPException(status_code=500, detail="knowledge impact report failed")


@router.get("/knowledge-impact/object-detail")
async def staging_knowledge_impact_object_detail(
    object_ref: str = Query(min_length=1),
    admin: User = Depends(require_admin),
):
    """Resolve one top-hit object back to formal-layer detail and staging promotion source."""
    try:
        def _load():
            import sqlite3

            import config

            from src.knowledge_staging import KnowledgeStaging

            raw_ref = str(object_ref or "").strip()
            if ":" not in raw_ref:
                raise HTTPException(status_code=400, detail="invalid object ref")

            obj_type, obj_id = raw_ref.split(":", 1)
            obj_type = obj_type.strip().lower()
            obj_id = obj_id.strip()
            if not obj_type or not obj_id:
                raise HTTPException(status_code=400, detail="invalid object ref")

            staging = KnowledgeStaging()
            formal_detail: dict[str, Any] | None = None
            promoted_target_ref = ""

            def _normalize_rule_id(value: str) -> str:
                value = str(value or "").strip()
                if value.startswith("rule_"):
                    return value.split("_", 1)[1]
                return value

            if obj_type == "experience":
                exp_db = _get_experience_db()
                conn = exp_db._connect(row_factory=True)
                try:
                    row = conn.execute(
                        "SELECT * FROM experiences WHERE id = ?",
                        (int(obj_id),),
                    ).fetchone()
                finally:
                    conn.close()
                if row:
                    formal_detail = dict(row)
                    promoted_target_ref = f"experience_db:{obj_id}"
            elif obj_type == "rule":
                rule_id = _normalize_rule_id(obj_id)
                db_path = config.COMMON_DB_DIR / "rule_knowledge.db"
                if db_path.exists():
                    conn = sqlite3.connect(str(db_path))
                    conn.row_factory = sqlite3.Row
                    try:
                        row = conn.execute(
                            "SELECT * FROM rules WHERE id = ?",
                            (int(rule_id),),
                        ).fetchone()
                    finally:
                        conn.close()
                    if row:
                        formal_detail = dict(row)
                        promoted_target_ref = f"rule_knowledge:{rule_id}"
            elif obj_type == "method_card":
                db_path = config.COMMON_DB_DIR / "method_cards.db"
                if db_path.exists():
                    conn = sqlite3.connect(str(db_path))
                    conn.row_factory = sqlite3.Row
                    try:
                        row = conn.execute(
                            "SELECT * FROM method_cards WHERE id = ?",
                            (int(obj_id),),
                        ).fetchone()
                    finally:
                        conn.close()
                    if row:
                        formal_detail = dict(row)
                        promoted_target_ref = f"method_cards:{obj_id}"
            else:
                raise HTTPException(status_code=400, detail="unsupported object ref")

            promotion_rows = []
            if promoted_target_ref:
                promotion_rows = staging.query_all(
                    """
                    SELECT id, source_table, source_record_id, target_layer, candidate_type,
                           candidate_title, status, review_status, reviewer, review_comment,
                           promoted_target_ref, promotion_trace, reviewed_at, promoted_at
                    FROM promotion_queue
                    WHERE is_deleted = 0
                      AND promoted_target_ref = ?
                    ORDER BY COALESCE(promoted_at, reviewed_at, updated_at) DESC, id DESC
                    LIMIT 20
                    """,
                    (promoted_target_ref,),
                )

            return {
                "object_ref": raw_ref,
                "formal_detail": formal_detail,
                "promoted_target_ref": promoted_target_ref,
                "promotion_sources": promotion_rows,
            }

        return await asyncio.to_thread(_load)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"knowledge impact object detail failed: {e}")
        raise HTTPException(status_code=500, detail="knowledge impact object detail failed")


@router.post("/audit-errors", status_code=201)
async def create_audit_error(
    req: AuditErrorCreateRequest,
    admin: User = Depends(require_admin),
):
    """Create one audit error staging record."""
    try:
        record_id = await asyncio.to_thread(
            lambda: _get_staging().create_audit_error(req.model_dump())
        )
        return {"id": record_id}
    except Exception as e:
        logger.error(f"create audit error failed: {e}")
        raise HTTPException(status_code=500, detail="create audit error failed")


@router.get("/audit-errors/{record_id}")
async def get_audit_error(
    record_id: int,
    admin: User = Depends(require_admin),
):
    """Get one audit error staging record."""
    try:
        record = await asyncio.to_thread(lambda: _get_staging().get_audit_error(record_id))
        if not record:
            raise HTTPException(status_code=404, detail="audit error not found")
        return record
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"get audit error failed: {e}")
        raise HTTPException(status_code=500, detail="get audit error failed")


@router.get("/audit-errors")
async def list_audit_errors(
    limit: int = Query(default=50, ge=1, le=200),
    review_statuses: str = Query(default=""),
    match_sources: str = Query(default=""),
    error_types: str = Query(default=""),
    source_table: str = Query(default=""),
    admin: User = Depends(require_admin),
):
    """List active audit errors."""
    try:
        parsed_review_statuses = [item.strip() for item in review_statuses.split(",") if item.strip()]
        parsed_match_sources = [item.strip() for item in match_sources.split(",") if item.strip()]
        parsed_error_types = [item.strip() for item in error_types.split(",") if item.strip()]
        items = await asyncio.to_thread(
            lambda: _get_staging().list_active_audit_errors(
                limit,
                review_statuses=parsed_review_statuses,
                match_sources=parsed_match_sources,
                error_types=parsed_error_types,
                source_table=source_table,
            )
        )
        return {"items": items, "total": len(items)}
    except Exception as e:
        logger.error(f"list audit errors failed: {e}")
        raise HTTPException(status_code=500, detail="list audit errors failed")


@router.put("/audit-errors/{record_id}/status")
async def update_audit_error_status(
    record_id: int,
    req: AuditErrorStatusUpdateRequest,
    admin: User = Depends(require_admin),
):
    """Update audit error status or review fields."""
    try:
        updated = await asyncio.to_thread(
            lambda: _get_staging().update_audit_error_status(
                record_id,
                status=req.status,
                review_status=req.review_status,
                reviewer=req.reviewer,
                review_comment=req.review_comment,
            )
        )
        if not updated:
            raise HTTPException(status_code=404, detail="audit error not found or no changes")
        return {"message": "updated"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"update audit error status failed: {e}")
        raise HTTPException(status_code=500, detail="update audit error status failed")


@router.post("/promotions", status_code=201)
async def enqueue_promotion(
    req: PromotionCreateRequest,
    admin: User = Depends(require_admin),
):
    """Create one promotion queue record."""
    try:
        record_id = await asyncio.to_thread(
            lambda: _get_staging().enqueue_promotion(req.model_dump())
        )
        return {"id": record_id}
    except Exception as e:
        logger.error(f"enqueue promotion failed: {e}")
        raise HTTPException(status_code=500, detail="enqueue promotion failed")


@router.get("/promotions/{record_id}")
async def get_promotion(
    record_id: int,
    admin: User = Depends(require_admin),
):
    """Get one promotion record."""
    try:
        record = await asyncio.to_thread(lambda: _get_staging().get_promotion(record_id))
        if not record:
            raise HTTPException(status_code=404, detail="promotion not found")
        return record
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"get promotion failed: {e}")
        raise HTTPException(status_code=500, detail="get promotion failed")


@router.get("/promotions")
async def list_pending_promotions(
    limit: int = Query(default=50, ge=1, le=200),
    statuses: str = Query(default="draft,reviewing,approved"),
    candidate_types: str = Query(default=""),
    target_layers: str = Query(default=""),
    source_table: str = Query(default=""),
    admin: User = Depends(require_admin),
):
    """List promotion candidates."""
    try:
        parsed_statuses = [item.strip() for item in statuses.split(",") if item.strip()]
        parsed_candidate_types = [item.strip() for item in candidate_types.split(",") if item.strip()]
        parsed_target_layers = [item.strip() for item in target_layers.split(",") if item.strip()]
        items = await asyncio.to_thread(
            lambda: _get_staging().list_promotions(
                statuses=parsed_statuses,
                candidate_types=parsed_candidate_types,
                target_layers=parsed_target_layers,
                source_table=source_table,
                limit=limit,
            )
        )
        return {"items": items, "total": len(items)}
    except Exception as e:
        logger.error(f"list promotions failed: {e}")
        raise HTTPException(status_code=500, detail="list promotions failed")


@router.put("/promotions/{record_id}/review")
async def review_promotion(
    record_id: int,
    req: PromotionReviewRequest,
    admin: User = Depends(require_admin),
):
    """Update promotion review status."""
    try:
        status = req.status
        if status is None:
            status = "reviewing" if req.review_status == "reviewing" else req.review_status

        updated = await asyncio.to_thread(
            lambda: _get_staging().update_promotion_review(
                record_id,
                review_status=req.review_status,
                status=status,
                reviewer=req.reviewer or getattr(admin, "nickname", "") or getattr(admin, "email", ""),
                review_comment=req.review_comment,
                rejection_reason=req.rejection_reason if req.review_status == "rejected" else None,
            )
        )
        if not updated:
            raise HTTPException(status_code=404, detail="promotion not found")
        return {"message": "updated"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"review promotion failed: {e}")
        raise HTTPException(status_code=500, detail="review promotion failed")


@router.post("/promotions/{record_id}/execute")
async def execute_promotion(
    record_id: int,
    req: PromotionExecuteRequest,
    admin: User = Depends(require_admin),
):
    """Execute one approved promotion into the formal knowledge layer."""
    try:
        def _run():
            staging = _get_staging()
            promotion = staging.get_promotion(record_id)
            if not promotion:
                raise HTTPException(status_code=404, detail="promotion not found")

            target_layer = promotion.get("target_layer")
            if req.expected_target_layer and target_layer != req.expected_target_layer:
                raise HTTPException(status_code=400, detail="unexpected target layer")

            service = _get_promotion_service()
            if target_layer == "RuleKnowledge":
                return service.promote_rule_candidate(record_id)
            if target_layer == "MethodCards":
                return service.promote_method_candidate(record_id)
            if target_layer == "ExperienceDB":
                return service.promote_experience_candidate(record_id)
            raise HTTPException(status_code=400, detail="unsupported target layer")

        return await asyncio.to_thread(_run)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"execute promotion failed: {e}")
        raise HTTPException(status_code=500, detail="execute promotion failed")


@router.post("/promotions/{record_id}/rollback")
async def rollback_promotion(
    record_id: int,
    req: PromotionRollbackRequest,
    admin: User = Depends(require_admin),
):
    """Rollback one promoted staging record from the formal layer."""
    try:
        def _run():
            staging = _get_staging()
            promotion = staging.get_promotion(record_id)
            if not promotion:
                raise HTTPException(status_code=404, detail="promotion not found")

            target_layer = promotion.get("target_layer")
            service = _get_promotion_service()
            if target_layer == "ExperienceDB":
                return service.rollback_experience_candidate(
                    record_id,
                    reason=req.reason,
                    actor=getattr(admin, "nickname", "") or getattr(admin, "email", ""),
                )
            if target_layer == "RuleKnowledge":
                return service.rollback_rule_candidate(
                    record_id,
                    reason=req.reason,
                    actor=getattr(admin, "nickname", "") or getattr(admin, "email", ""),
                )
            if target_layer == "MethodCards":
                return service.rollback_method_candidate(
                    record_id,
                    reason=req.reason,
                    actor=getattr(admin, "nickname", "") or getattr(admin, "email", ""),
                )
            raise HTTPException(status_code=400, detail="rollback is not supported for this target layer")

        return await asyncio.to_thread(_run)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"rollback promotion failed: {e}")
        raise HTTPException(status_code=500, detail="rollback promotion failed")
