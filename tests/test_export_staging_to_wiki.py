from __future__ import annotations

import json
from pathlib import Path

from src.knowledge_staging import KnowledgeStaging
from tools import export_staging_to_wiki as exporter


def _schema_path() -> Path:
    return Path(__file__).resolve().parents[1] / "docs" / "knowledge_staging_schema_v1.sql"


def test_export_staging_to_wiki_generates_markdown_and_manifest(tmp_path):
    db_path = tmp_path / "knowledge_staging.db"
    output_dir = tmp_path / "knowledge_wiki"
    staging = KnowledgeStaging(db_path=db_path, schema_path=_schema_path())

    audit_id = staging.create_audit_error({
        "source_id": "task-001",
        "source_type": "manual_review",
        "source_table": "match_results",
        "source_record_id": "row-01",
        "owner": "tester",
        "status": "active",
        "review_status": "approved",
        "task_id": "task-001",
        "result_id": "result-001",
        "province": "北京市建设工程施工消耗量标准(2024)",
        "specialty": "C4",
        "bill_name": "配管（SC20）",
        "bill_desc": "配管SC20，暗敷,从配电箱至灯位",
        "predicted_quota_code": "C4-4-37",
        "corrected_quota_code": "C4-11-35",
        "match_source": "search",
        "error_type": "wrong_rank",
        "error_level": "high",
        "decision_basis": "人工二次确认：接受 OpenClaw 建议定额。",
        "fix_suggestion": "改判为焊接钢管暗配",
        "root_cause_tags": ["search", "ranking"],
        "can_promote_rule": 1,
        "can_promote_method": 1,
    })

    staging.enqueue_promotion({
        "source_id": "task-001",
        "source_type": "audit_error",
        "source_table": "audit_errors",
        "source_record_id": str(audit_id),
        "owner": "tester",
        "candidate_type": "rule",
        "target_layer": "RuleKnowledge",
        "candidate_title": "配管（SC20） 纠正规则候选",
        "candidate_summary": "人工确认通过。",
        "candidate_payload": {
            "province": "北京市建设工程施工消耗量标准(2024)",
            "specialty": "C4",
            "rule_text": "当清单为配管SC20时优先考虑焊接钢管暗配。",
            "keywords": ["配管（SC20）", "C4-11-35"],
            "chapter": "OpenClaw审核回流",
        },
    })
    staging.enqueue_promotion({
        "source_id": "task-001",
        "source_type": "audit_error",
        "source_table": "audit_errors",
        "source_record_id": str(audit_id),
        "owner": "tester",
        "candidate_type": "method",
        "target_layer": "MethodCards",
        "candidate_title": "配管（SC20） 审核方法候选",
        "candidate_payload": {
            "province": "北京市建设工程施工消耗量标准(2024)",
            "specialty": "C4",
            "category": "配管（SC20）",
            "method_text": "先看配管材质，再看敷设方式。",
            "keywords": ["配管（SC20）"],
            "pattern_keys": ["C4"],
            "sample_count": 1,
            "confirm_rate": 1.0,
        },
    })
    staging.enqueue_promotion({
        "source_id": "task-001",
        "source_type": "audit_error",
        "source_table": "audit_errors",
        "source_record_id": str(audit_id),
        "owner": "tester",
        "candidate_type": "experience",
        "target_layer": "ExperienceDB",
        "candidate_title": "配管（SC20） 历史案例候选",
        "candidate_payload": {
            "province": "北京市建设工程施工消耗量标准(2024)",
            "specialty": "C4",
            "bill_name": "配管（SC20）",
            "bill_desc": "配管SC20，暗敷,从配电箱至灯位",
            "bill_text": "配管（SC20） 配管SC20，暗敷,从配电箱至灯位",
            "bill_unit": "m",
            "final_quota_code": "C4-11-35",
            "final_quota_name": "焊接钢管砖、混凝土结构暗配 公称直径(mm以内) 20",
            "summary": "历史案例确认正确。",
        },
    })

    original_staging = exporter.KnowledgeStaging
    exporter.KnowledgeStaging = lambda: staging
    try:
        manifest = exporter.export_staging_to_wiki(output_dir=output_dir)
    finally:
        exporter.KnowledgeStaging = original_staging

    manifest_path = output_dir / ".generated_manifest.json"
    assert manifest_path.exists()
    manifest_json = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest_json["counts"]["reviews"] == 1
    assert manifest_json["counts"]["rules"] == 1
    assert manifest_json["counts"]["methods"] == 1
    assert manifest_json["counts"]["cases"] == 1
    assert manifest["counts"]["daily"] == 1

    review_files = list((output_dir / "reviews").glob("review-*.md"))
    rule_files = list((output_dir / "rules").glob("rule-*.md"))
    method_files = list((output_dir / "methods").glob("method-*.md"))
    case_files = list((output_dir / "cases").glob("case-*.md"))
    daily_files = list((output_dir / "daily").glob("daily-*.md"))

    assert len(review_files) == 1
    assert len(rule_files) == 1
    assert len(method_files) == 1
    assert len(case_files) == 1
    assert len(daily_files) == 1

    review_text = review_files[0].read_text(encoding="utf-8")
    assert 'type: "review"' in review_text
    assert 'source_kind: "staging"' in review_text
    assert "配管（SC20） 审核沉淀" in review_text
    assert "[[case-" in review_text

    rule_text = rule_files[0].read_text(encoding="utf-8")
    assert "## 规则正文" in rule_text
    assert "当清单为配管SC20时优先考虑焊接钢管暗配。" in rule_text
