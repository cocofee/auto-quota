import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

from src.knowledge_staging import KnowledgeStaging


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "web" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))

from app.services import openclaw_staging as staging_service  # noqa: E402


def _schema_path() -> Path:
    return Path(__file__).resolve().parents[1] / "docs" / "knowledge_staging_schema_v1.sql"


def _make_task():
    return SimpleNamespace(
        id=uuid.uuid4(),
        province="Beijing2024",
    )


def _make_match_result(*, match_source: str = "search", note: str = "human confirmed correction", corrected_quota_id: str = "C10-9-9"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        specialty="C10",
        bill_name="water pipe installation",
        bill_description="indoor PPR water pipe",
        match_source=match_source,
        quotas=[{"quota_id": "C10-1-1", "name": "original quota", "unit": "m"}],
        corrected_quotas=[{"quota_id": corrected_quota_id, "name": "corrected quota", "unit": "m"}],
        openclaw_suggested_quotas=[{"quota_id": corrected_quota_id, "name": "corrected quota", "unit": "m"}],
        openclaw_review_note=note,
        openclaw_review_payload={
            "jarvis_absorbable_report": {
                "decision": {
                    "reason_codes": ["candidate_pool_better", "wrong_param"],
                },
                "judgment": {
                    "basis_summary": "candidate pool already contains a better correction",
                },
                "learning_record": {
                    "final_quota_code": corrected_quota_id,
                    "final_quota_name": "corrected quota",
                },
                "promotion_hints": {
                    "rule": {
                        "judgment_basis": "key parameters match after correction",
                        "core_knowledge_points": ["param:matched"],
                    },
                },
            }
        },
        review_note="second review approved",
    )


def test_record_openclaw_approved_review_writes_audit_error_and_promotion(tmp_path, monkeypatch):
    staging = KnowledgeStaging(db_path=tmp_path / "knowledge_staging.db", schema_path=_schema_path())
    monkeypatch.setattr(staging_service, "KnowledgeStaging", lambda: staging)

    result = staging_service.record_openclaw_approved_review(
        _make_task(),
        _make_match_result(match_source="search"),
        actor="admin",
        review_note="admin confirmed",
    )

    assert result["audit_error_id"] is not None
    assert result["queued_rule"] is True
    assert result["queued_method"] is True
    assert result["queued_experience"] is True
    assert result["promotion_id"] is not None
    assert len(result["promotion_ids"]) == 3

    audit = staging.get_audit_error(result["audit_error_id"])
    assert audit is not None
    assert audit["source_type"] == "openclaw_review_confirm"
    assert audit["match_source"] == "search"
    assert audit["error_type"] == "wrong_rank"
    assert audit["review_status"] == "approved"
    assert audit["corrected_quota_code"] == "C10-9-9"
    assert audit["can_promote_rule"] == 1
    assert audit["can_promote_method"] == 1
    assert "candidate_pool_better" in audit["root_cause_tags"]
    assert audit["decision_basis"] == "candidate pool already contains a better correction"

    promotions = [staging.get_promotion(item_id) for item_id in result["promotion_ids"]]
    by_layer = {item["target_layer"]: item for item in promotions if item}
    assert by_layer["RuleKnowledge"]["candidate_type"] == "rule"
    assert "rule_text" in by_layer["RuleKnowledge"]["candidate_payload"]
    assert by_layer["RuleKnowledge"]["candidate_payload"]["judgment_basis"] == "key parameters match after correction"
    assert by_layer["MethodCards"]["candidate_type"] == "method"
    assert "method_text" in by_layer["MethodCards"]["candidate_payload"]
    assert by_layer["ExperienceDB"]["candidate_type"] == "experience"
    assert by_layer["ExperienceDB"]["candidate_payload"]["final_quota_code"] == "C10-9-9"


def test_record_openclaw_approved_review_skips_rule_queue_for_experience(tmp_path, monkeypatch):
    staging = KnowledgeStaging(db_path=tmp_path / "knowledge_staging.db", schema_path=_schema_path())
    monkeypatch.setattr(staging_service, "KnowledgeStaging", lambda: staging)

    result = staging_service.record_openclaw_approved_review(
        _make_task(),
        _make_match_result(match_source="experience", note="experience pollution fixed"),
        actor="admin",
        review_note="admin confirmed",
    )

    assert result["audit_error_id"] is not None
    assert result["queued_rule"] is False
    assert result["queued_method"] is False
    assert result["queued_experience"] is False
    assert result["promotion_id"] is None
    assert result["promotion_ids"] == []

    audit = staging.get_audit_error(result["audit_error_id"])
    assert audit is not None
    assert audit["error_type"] == "polluted_experience"
    assert audit["can_promote_rule"] == 0
    assert audit["can_promote_method"] == 0
