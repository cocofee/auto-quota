from pathlib import Path
import time

import config
from src.knowledge_staging import KnowledgeStaging


def _schema_path() -> Path:
    return Path(__file__).resolve().parents[1] / "docs" / "knowledge_staging_schema_v1.sql"


def test_knowledge_staging_health_and_core_flow(tmp_path):
    db_path = tmp_path / "knowledge_staging.db"
    staging = KnowledgeStaging(db_path=db_path, schema_path=_schema_path())

    health = staging.health_check()
    assert health["ok"] is True
    assert health["schema_version"] == "1"

    audit_id = staging.create_audit_error({
        "source_id": "task-001",
        "source_type": "manual_review",
        "source_table": "match_results",
        "source_record_id": "row-01",
        "owner": "tester",
        "status": "active",
        "task_id": "task-001",
        "result_id": "result-001",
        "province": "北京2024",
        "specialty": "C10",
        "bill_name": "镀锌钢管安装",
        "match_source": "search",
        "error_type": "wrong_rank",
        "error_level": "high",
        "root_cause_tags": ["排序偏差", "规则缺失"],
        "can_promote_rule": 1,
    })
    audit = staging.get_audit_error(audit_id)
    assert audit is not None
    assert audit["match_source"] == "search"
    assert audit["root_cause_tags"] == ["排序偏差", "规则缺失"]

    promotion_id = staging.enqueue_promotion({
        "source_id": "task-001",
        "source_type": "audit_error",
        "source_table": "audit_errors",
        "source_record_id": str(audit_id),
        "owner": "tester",
        "candidate_type": "rule",
        "target_layer": "RuleKnowledge",
        "candidate_title": "镀锌钢管排序约束",
        "candidate_payload": {
            "province": "北京2024",
            "specialty": "C10",
            "rule_text": "测试规则",
            "keywords": ["镀锌钢管"],
        },
        "priority": 10,
    })
    pending = staging.list_pending_promotions()
    assert any(item["id"] == promotion_id for item in pending)

    assert staging.update_promotion_review(
        promotion_id,
        review_status="approved",
        status="approved",
        reviewer="admin",
        review_comment="ok",
    )
    assert staging.mark_promotion_promoted(
        promotion_id,
        promoted_target_id="rk-001",
        promoted_target_ref="rule_knowledge:1",
        target_version=1,
        promotion_trace=f"audit_errors:{audit_id} -> RuleKnowledge:1",
    )

    promotion = staging.get_promotion(promotion_id)
    assert promotion is not None
    assert promotion["status"] == "promoted"
    assert promotion["review_status"] == "promoted"
    assert promotion["candidate_payload"]["rule_text"] == "测试规则"


def test_knowledge_staging_list_filters(tmp_path):
    db_path = tmp_path / "knowledge_staging.db"
    staging = KnowledgeStaging(db_path=db_path, schema_path=_schema_path())

    audit_rule_id = staging.create_audit_error({
        "source_id": "task-a",
        "source_type": "manual_review",
        "source_table": "match_results",
        "source_record_id": "row-a",
        "owner": "tester",
        "status": "active",
        "review_status": "approved",
        "task_id": "task-a",
        "result_id": "result-a",
        "match_source": "search",
        "error_type": "wrong_rank",
        "error_level": "high",
        "can_promote_rule": 1,
        "can_promote_method": 1,
    })
    audit_exp_id = staging.create_audit_error({
        "source_id": "task-b",
        "source_type": "manual_review",
        "source_table": "match_results",
        "source_record_id": "row-b",
        "owner": "tester",
        "status": "active",
        "review_status": "promoted",
        "task_id": "task-b",
        "result_id": "result-b",
        "match_source": "experience",
        "error_type": "polluted_experience",
        "error_level": "high",
    })

    rule_promotion_id = staging.enqueue_promotion({
        "source_id": "task-a",
        "source_type": "audit_error",
        "source_table": "audit_errors",
        "source_record_id": f"{audit_rule_id}:rule",
        "owner": "tester",
        "status": "approved",
        "review_status": "approved",
        "candidate_type": "rule",
        "target_layer": "RuleKnowledge",
        "candidate_title": "rule candidate",
        "candidate_payload": {"province": "北京2024", "rule_text": "测试规则"},
    })
    method_promotion_id = staging.enqueue_promotion({
        "source_id": "task-a",
        "source_type": "audit_error",
        "source_table": "audit_errors",
        "source_record_id": f"{audit_rule_id}:method",
        "owner": "tester",
        "status": "draft",
        "review_status": "unreviewed",
        "candidate_type": "method",
        "target_layer": "MethodCards",
        "candidate_title": "method candidate",
        "candidate_payload": {"category": "测试方法", "method_text": "测试方法正文"},
    })
    staging.enqueue_promotion({
        "source_id": "task-b",
        "source_type": "audit_error",
        "source_table": "audit_errors",
        "source_record_id": f"{audit_exp_id}:experience",
        "owner": "tester",
        "status": "promoted",
        "review_status": "promoted",
        "candidate_type": "experience",
        "target_layer": "ExperienceDB",
        "candidate_title": "experience candidate",
        "candidate_payload": {"province": "北京2024", "bill_text": "测试清单", "quota_ids": ["4-01-001"]},
    })

    audits = staging.list_active_audit_errors(
        review_statuses=["approved"],
        match_sources=["search"],
        error_types=["wrong_rank"],
    )
    assert [item["id"] for item in audits] == [audit_rule_id]

    promotions = staging.list_promotions(
        statuses=["draft", "approved"],
        candidate_types=["method"],
        target_layers=["MethodCards"],
        source_table="audit_errors",
    )
    assert [item["id"] for item in promotions] == [method_promotion_id]

    approved_rules = staging.list_promotions(
        statuses=["approved"],
        candidate_types=["rule"],
        target_layers=["RuleKnowledge"],
    )
    assert [item["id"] for item in approved_rules] == [rule_promotion_id]


def test_knowledge_staging_dashboard_stats(tmp_path):
    db_path = tmp_path / "knowledge_staging.db"
    staging = KnowledgeStaging(db_path=db_path, schema_path=_schema_path())
    now = time.time()
    yesterday = now - 86400

    staging.create_audit_error({
        "source_id": "task-1",
        "source_type": "manual_review",
        "source_table": "match_results",
        "source_record_id": "row-1",
        "owner": "tester",
        "status": "active",
        "review_status": "approved",
        "created_at": yesterday,
        "match_source": "search",
        "error_type": "wrong_rank",
        "error_level": "high",
    })
    staging.create_audit_error({
        "source_id": "task-2",
        "source_type": "manual_review",
        "source_table": "match_results",
        "source_record_id": "row-2",
        "owner": "tester",
        "status": "active",
        "review_status": "promoted",
        "created_at": now,
        "match_source": "rule",
        "error_type": "wrong_rule",
        "error_level": "high",
    })

    staging.enqueue_promotion({
        "source_id": "task-1",
        "source_type": "audit_error",
        "source_table": "audit_errors",
        "source_record_id": "1:rule",
        "owner": "tester",
        "status": "approved",
        "review_status": "approved",
        "created_at": yesterday,
        "reviewed_at": yesterday,
        "candidate_type": "rule",
        "target_layer": "RuleKnowledge",
        "candidate_title": "rule candidate",
        "candidate_payload": {"province": "北京2024", "rule_text": "测试规则"},
    })
    staging.enqueue_promotion({
        "source_id": "task-2",
        "source_type": "audit_error",
        "source_table": "audit_errors",
        "source_record_id": "2:method",
        "owner": "tester",
        "status": "rejected",
        "review_status": "rejected",
        "created_at": now,
        "reviewed_at": now,
        "candidate_type": "method",
        "target_layer": "MethodCards",
        "candidate_title": "method candidate",
        "candidate_payload": {"category": "测试方法", "method_text": "测试方法正文"},
        "rejection_reason": "证据不足",
    })

    staging.enqueue_promotion({
        "source_id": "task-3",
        "source_type": "audit_error",
        "source_table": "audit_errors",
        "source_record_id": "3:experience",
        "owner": "tester",
        "status": "rolled_back",
        "review_status": "rolled_back",
        "created_at": now,
        "reviewed_at": now,
        "candidate_type": "experience",
        "target_layer": "ExperienceDB",
        "candidate_title": "experience candidate",
        "candidate_payload": {"province": "鍖椾含2024", "bill_text": "娴嬭瘯娓呭崟", "quota_ids": ["4-01-001"]},
    })

    stats = staging.get_dashboard_stats()
    assert stats["audit_total"] == 2
    assert stats["promotion_total"] == 3
    assert stats["promotion_status_counts"]["approved"] == 1
    assert stats["promotion_status_counts"]["rejected"] == 1
    assert stats["promotion_status_counts"]["rolled_back"] == 1
    assert stats["promotion_target_counts"]["RuleKnowledge"] == 1
    assert stats["promotion_target_counts"]["MethodCards"] == 1
    assert stats["promotion_target_counts"]["ExperienceDB"] == 1
    target_metrics = {item["bucket"]: item for item in stats["promotion_target_metrics"]}
    candidate_metrics = {item["bucket"]: item for item in stats["promotion_candidate_metrics"]}
    rejection_by_target = {item["bucket"]: item for item in stats["rejection_reason_by_target"]}
    rejection_by_candidate = {item["bucket"]: item for item in stats["rejection_reason_by_candidate"]}
    assert target_metrics["RuleKnowledge"]["approved_total"] == 1
    assert target_metrics["RuleKnowledge"]["approval_rate"] == 100.0
    assert target_metrics["MethodCards"]["rejected_total"] == 1
    assert target_metrics["MethodCards"]["rejection_rate"] == 100.0
    assert target_metrics["ExperienceDB"]["rolled_back"] == 1
    assert candidate_metrics["rule"]["approved_total"] == 1
    assert candidate_metrics["method"]["rejected_total"] == 1
    assert candidate_metrics["experience"]["rolled_back"] == 1
    assert rejection_by_target["MethodCards"]["rejected_total"] == 1
    assert rejection_by_target["MethodCards"]["top_reasons"][0]["count"] == 1
    assert rejection_by_candidate["method"]["top_reasons"][0]["count"] == 1
    assert stats["audit_review_counts"]["approved"] == 1
    assert stats["audit_review_counts"]["promoted"] == 1
    assert stats["audit_match_source_counts"]["search"] == 1
    assert stats["audit_error_type_counts"]["wrong_rule"] == 1
    assert stats["promotion_reviewed_total"] == 2
    assert stats["promotion_approved_total"] == 1
    assert stats["promotion_rejected_total"] == 1
    assert stats["promotion_approval_rate"] == 50.0
    assert stats["promotion_rejection_rate"] == 50.0
    assert stats["promotion_execution_rate"] == 0.0
    assert len(stats["recent_activity"]) == 7
    assert sum(item["audit_created"] for item in stats["recent_activity"]) == 2
    assert sum(item["promotion_created"] for item in stats["recent_activity"]) == 3
    assert sum(item["promotion_reviewed"] for item in stats["recent_activity"]) == 2
    assert sum(item["promotion_promoted"] for item in stats["recent_activity"]) == 0
    assert stats["top_rejection_reasons"][0]["reason"] == "证据不足"
    assert stats["top_rejection_reasons"][0]["count"] == 1


def test_knowledge_staging_health_report(tmp_path, monkeypatch):
    db_path = tmp_path / "knowledge_staging.db"
    common_dir = tmp_path / "common"
    monkeypatch.setattr(config, "COMMON_DB_DIR", common_dir)
    monkeypatch.setattr(config, "DB_DIR", tmp_path / "db")

    from src.experience_db import ExperienceDB
    from src.method_cards import MethodCards
    from src.rule_knowledge import RuleKnowledge

    monkeypatch.setattr(RuleKnowledge, "_update_vector_index", lambda self: None)
    monkeypatch.setattr(RuleKnowledge, "_remove_from_vector_index", lambda self, rule_id: None)

    staging = KnowledgeStaging(db_path=db_path, schema_path=_schema_path())
    now = time.time()
    stale_time = now - (10 * 86400)

    staging.enqueue_promotion({
        "source_id": "dup-1",
        "source_type": "audit_error",
        "source_table": "audit_errors",
        "source_record_id": "ae-1",
        "owner": "tester",
        "status": "draft",
        "review_status": "unreviewed",
        "created_at": stale_time,
        "candidate_type": "rule",
        "target_layer": "RuleKnowledge",
        "candidate_title": "duplicate rule candidate",
        "candidate_payload": {"province": "Beijing2024", "rule_text": "same rule"},
    })
    staging.enqueue_promotion({
        "source_id": "dup-2",
        "source_type": "audit_error",
        "source_table": "audit_errors",
        "source_record_id": "ae-2",
        "owner": "tester",
        "status": "reviewing",
        "review_status": "reviewing",
        "created_at": stale_time,
        "candidate_type": "rule",
        "target_layer": "RuleKnowledge",
        "candidate_title": "duplicate rule candidate",
        "candidate_payload": {"province": "Beijing2024", "rule_text": "same rule"},
    })
    staging.enqueue_promotion({
        "source_id": "conflict-1",
        "source_type": "audit_error",
        "source_table": "audit_errors",
        "source_record_id": "ae-conflict",
        "owner": "tester",
        "status": "approved",
        "review_status": "approved",
        "candidate_type": "rule",
        "target_layer": "RuleKnowledge",
        "candidate_title": "rule conflict candidate",
        "candidate_payload": {"province": "Beijing2024", "rule_text": "rule conflict"},
    })
    staging.enqueue_promotion({
        "source_id": "conflict-2",
        "source_type": "audit_error",
        "source_table": "audit_errors",
        "source_record_id": "ae-conflict",
        "owner": "tester",
        "status": "approved",
        "review_status": "approved",
        "candidate_type": "method",
        "target_layer": "MethodCards",
        "candidate_title": "method conflict candidate",
        "candidate_payload": {"category": "test", "method_text": "method conflict"},
    })
    staging.enqueue_promotion({
        "source_id": "rb-1",
        "source_type": "audit_error",
        "source_table": "audit_errors",
        "source_record_id": "ae-rb",
        "owner": "tester",
        "status": "rolled_back",
        "review_status": "rolled_back",
        "reviewed_at": now,
        "candidate_type": "experience",
        "target_layer": "ExperienceDB",
        "candidate_title": "rolled back exp",
        "promoted_target_ref": "experience_db:99",
        "review_comment": "Rollback: test",
        "candidate_payload": {"province": "Beijing2024", "bill_text": "test", "quota_ids": ["4-01-001"]},
    })

    rule_kb = RuleKnowledge(province="Beijing2024")
    rule_write = rule_kb.add_rule_text(
        content="health report rule",
        province="Beijing2024",
        specialty="C10",
        chapter="test",
    )
    assert rule_kb.soft_disable_rule(int(rule_write["rule_id"]), reason="obsolete", actor="admin") is True

    method_cards = MethodCards()
    method_write = method_cards.add_method_text(
        category="health report method",
        specialty="C10",
        method_text="health report method text",
        source_province="Beijing2024",
    )
    assert method_cards.soft_disable_card(int(method_write["card_id"]), reason="obsolete", actor="admin") is True

    exp_db = ExperienceDB(province="Beijing2024")
    exp_write = exp_db.add_experience_text(
        province="Beijing2024",
        bill_text="health report bill",
        bill_name="health report bill",
        quota_ids=["4-01-001"],
        quota_names=["test quota"],
    )
    exp_db.demote_to_candidate(int(exp_write["experience_id"]), reason="candidate for review")
    conn = exp_db._connect()
    try:
        conn.execute("UPDATE experiences SET disputed = 1 WHERE id = ?", (int(exp_write["experience_id"]),))
        conn.commit()
    finally:
        conn.close()

    report = staging.get_health_report(stale_pending_days=7, limit=10)
    assert report["summary"]["duplicate_candidate_groups"] == 1
    assert report["summary"]["stale_pending_promotions"] == 2
    assert report["summary"]["rolled_back_promotions"] == 1
    assert report["summary"]["source_conflict_groups"] == 1
    assert report["summary"]["inactive_rules"] == 1
    assert report["summary"]["inactive_method_cards"] == 1
    assert report["summary"]["experience_candidate_count"] == 1
    assert report["summary"]["experience_disputed_count"] == 1
    assert report["duplicate_candidate_groups"][0]["duplicate_count"] == 2
    assert report["stale_pending_promotions"][0]["age_days"] >= 10
    assert report["recent_rollbacks"][0]["target_layer"] == "ExperienceDB"
