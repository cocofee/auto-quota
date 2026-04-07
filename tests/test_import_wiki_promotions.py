from __future__ import annotations

from pathlib import Path

from src.knowledge_staging import KnowledgeStaging
from tools.import_wiki_promotions import run_import_wiki_promotions


def _schema_path() -> Path:
    return Path(__file__).resolve().parents[1] / "docs" / "knowledge_staging_schema_v1.sql"


class FakePromotionService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def promote_rule_candidate(self, promotion_id: int) -> dict:
        self.calls.append(("RuleKnowledge", promotion_id))
        return {"promotion_id": promotion_id, "rule_id": 1}

    def promote_method_candidate(self, promotion_id: int) -> dict:
        self.calls.append(("MethodCards", promotion_id))
        return {"promotion_id": promotion_id, "card_id": 2}

    def promote_experience_candidate(self, promotion_id: int) -> dict:
        self.calls.append(("ExperienceDB", promotion_id))
        return {"promotion_id": promotion_id, "experience_id": 3}


def test_run_import_wiki_promotions_executes_only_approved_items(tmp_path):
    staging = KnowledgeStaging(db_path=tmp_path / "knowledge_staging.db", schema_path=_schema_path())
    service = FakePromotionService()

    rule_id = staging.enqueue_promotion({
        "source_id": "task-001",
        "source_type": "audit_error",
        "source_table": "audit_errors",
        "source_record_id": "1",
        "owner": "tester",
        "status": "approved",
        "review_status": "approved",
        "candidate_type": "rule",
        "target_layer": "RuleKnowledge",
        "candidate_title": "Rule Candidate",
        "candidate_payload": {"rule_text": "rule body"},
    })
    method_id = staging.enqueue_promotion({
        "source_id": "task-002",
        "source_type": "audit_error",
        "source_table": "audit_errors",
        "source_record_id": "2",
        "owner": "tester",
        "status": "approved",
        "review_status": "approved",
        "candidate_type": "method",
        "target_layer": "MethodCards",
        "candidate_title": "Method Candidate",
        "candidate_payload": {"method_text": "method body"},
    })
    staging.enqueue_promotion({
        "source_id": "task-003",
        "source_type": "audit_error",
        "source_table": "audit_errors",
        "source_record_id": "3",
        "owner": "tester",
        "status": "draft",
        "review_status": "reviewing",
        "candidate_type": "experience",
        "target_layer": "ExperienceDB",
        "candidate_title": "Draft Candidate",
        "candidate_payload": {"summary": "draft"},
    })

    result = run_import_wiki_promotions(
        staging=staging,
        service=service,  # type: ignore[arg-type]
        limit=20,
        dry_run=False,
    )

    assert result["processed"] == 2
    assert result["executed"] == 2
    assert result["errors"] == 0
    assert ("RuleKnowledge", rule_id) in service.calls
    assert ("MethodCards", method_id) in service.calls


def test_run_import_wiki_promotions_supports_dry_run(tmp_path):
    staging = KnowledgeStaging(db_path=tmp_path / "knowledge_staging.db", schema_path=_schema_path())
    service = FakePromotionService()

    staging.enqueue_promotion({
        "source_id": "task-004",
        "source_type": "audit_error",
        "source_table": "audit_errors",
        "source_record_id": "4",
        "owner": "tester",
        "status": "approved",
        "review_status": "approved",
        "candidate_type": "experience",
        "target_layer": "ExperienceDB",
        "candidate_title": "Experience Candidate",
        "candidate_payload": {"summary": "experience"},
    })

    result = run_import_wiki_promotions(
        staging=staging,
        service=service,  # type: ignore[arg-type]
        limit=20,
        dry_run=True,
    )

    assert result["processed"] == 1
    assert result["executed"] == 0
    assert result["errors"] == 0
    assert result["items"][0]["status"] == "would_execute"
    assert service.calls == []
