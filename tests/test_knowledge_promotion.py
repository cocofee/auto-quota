from pathlib import Path

from src.knowledge_promotion import KnowledgePromotionService
from src.knowledge_staging import KnowledgeStaging


def _schema_path() -> Path:
    return Path(__file__).resolve().parents[1] / "docs" / "knowledge_staging_schema_v1.sql"


def test_promote_rule_candidate_updates_staging_and_trace(tmp_path, monkeypatch):
    db_path = tmp_path / "knowledge_staging.db"
    staging = KnowledgeStaging(db_path=db_path, schema_path=_schema_path())

    audit_id = staging.create_audit_error({
        "source_id": "task-002",
        "source_type": "manual_review",
        "source_table": "match_results",
        "source_record_id": "row-02",
        "owner": "tester",
        "status": "active",
        "task_id": "task-002",
        "result_id": "result-002",
        "bill_name": "测试清单",
        "match_source": "rule",
        "error_type": "wrong_rule",
        "error_level": "high",
    })
    promotion_id = staging.enqueue_promotion({
        "source_id": "task-002",
        "source_type": "audit_error",
        "source_table": "audit_errors",
        "source_record_id": str(audit_id),
        "owner": "tester",
        "status": "approved",
        "review_status": "approved",
        "candidate_type": "rule",
        "target_layer": "RuleKnowledge",
        "candidate_title": "测试规则候选",
        "candidate_payload": {
            "province": "北京2024",
            "specialty": "C10",
            "rule_text": "测试规则正文",
            "chapter": "测试章节",
        },
    })

    calls = {}

    class FakeRuleKnowledge:
        def __init__(self, province=None):
            calls["province"] = province

        def add_rule_text(self, **kwargs):
            calls["kwargs"] = kwargs
            return {
                "added": True,
                "skipped": False,
                "rule_id": 321,
                "content_hash": "fakehash",
            }

    monkeypatch.setattr("src.knowledge_promotion.RuleKnowledge", FakeRuleKnowledge)

    service = KnowledgePromotionService(staging=staging)
    result = service.promote_rule_candidate(promotion_id)

    assert result["rule_id"] == 321
    assert result["added"] is True
    assert calls["province"] == "北京2024"
    assert calls["kwargs"]["content"] == "测试规则正文"

    promotion = staging.get_promotion(promotion_id)
    assert promotion["status"] == "promoted"
    assert promotion["review_status"] == "promoted"
    assert promotion["promoted_target_id"] == "321"
    assert "RuleKnowledge:321" in promotion["promotion_trace"]

    audit = staging.get_audit_error(audit_id)
    assert audit["review_status"] == "promoted"
    assert "RuleKnowledge:321" in audit["review_comment"]


def test_promote_method_candidate_updates_staging_and_trace(tmp_path, monkeypatch):
    db_path = tmp_path / "knowledge_staging.db"
    staging = KnowledgeStaging(db_path=db_path, schema_path=_schema_path())

    audit_id = staging.create_audit_error({
        "source_id": "task-003",
        "source_type": "manual_review",
        "source_table": "match_results",
        "source_record_id": "row-03",
        "owner": "tester",
        "status": "active",
        "task_id": "task-003",
        "result_id": "result-003",
        "bill_name": "测试清单",
        "match_source": "manual",
        "error_type": "review_corrected",
        "error_level": "high",
    })
    promotion_id = staging.enqueue_promotion({
        "source_id": "task-003",
        "source_type": "audit_error",
        "source_table": "audit_errors",
        "source_record_id": str(audit_id),
        "owner": "tester",
        "status": "approved",
        "review_status": "approved",
        "candidate_type": "method",
        "target_layer": "MethodCards",
        "candidate_title": "给水管审核方法",
        "candidate_payload": {
            "province": "北京2024",
            "specialty": "C10",
            "category": "给水管审核方法",
            "method_text": "先核对清单关键词，再校验定额口径。",
            "keywords": ["给水管", "定额"],
            "pattern_keys": ["给水管_审核"],
            "common_errors": "不要把排水误判为给水",
            "sample_count": 2,
            "confirm_rate": 1.0,
        },
    })

    calls = {}

    class FakeMethodCards:
        def add_method_text(self, **kwargs):
            calls["kwargs"] = kwargs
            return {
                "card_id": 654,
                "added": True,
                "skipped": False,
                "content_hash": "fakehash",
            }

    monkeypatch.setattr("src.knowledge_promotion.MethodCards", FakeMethodCards)

    service = KnowledgePromotionService(staging=staging)
    result = service.promote_method_candidate(promotion_id)

    assert result["card_id"] == 654
    assert result["added"] is True
    assert calls["kwargs"]["category"] == "给水管审核方法"
    assert calls["kwargs"]["method_text"] == "先核对清单关键词，再校验定额口径。"
    assert calls["kwargs"]["source_province"] == "北京2024"

    promotion = staging.get_promotion(promotion_id)
    assert promotion["status"] == "promoted"
    assert promotion["review_status"] == "promoted"
    assert promotion["promoted_target_id"] == "654"
    assert "MethodCards:654" in promotion["promotion_trace"]

    audit = staging.get_audit_error(audit_id)
    assert audit["review_status"] == "promoted"
    assert "MethodCards:654" in audit["review_comment"]


def test_promote_method_candidate_from_openclaw_card_payload(tmp_path, monkeypatch):
    db_path = tmp_path / "knowledge_staging.db"
    staging = KnowledgeStaging(db_path=db_path, schema_path=_schema_path())

    promotion_id = staging.enqueue_promotion({
        "source_id": "card-001",
        "source_type": "openclaw_manual_card",
        "source_table": "openclaw_manual_cards",
        "source_record_id": "card-001",
        "owner": "openclaw@system.local",
        "status": "approved",
        "review_status": "approved",
        "candidate_type": "method",
        "target_layer": "MethodCards",
        "candidate_title": "桥架审核方法",
        "candidate_summary": "先核专业，再核册号，再核单位。",
        "candidate_payload": {
            "original_problem": "桥架清单经常错套到电缆敷设。",
            "final_conclusion": "先核专业，再核册号，再核单位。",
            "judgment_basis": "桥架与电缆虽然都在电气专业，但册号和单位口径不同。",
            "exclusion_reasons": ["不能只看关键词“桥架”", "不能忽略单位差异"],
            "core_knowledge_points": ["专业优先", "册号约束", "单位校验"],
            "tags": ["桥架", "审核顺序"],
            "province": "北京2024",
            "specialty": "C6",
        },
    })

    calls = {}

    class FakeMethodCards:
        def add_method_text(self, **kwargs):
            calls["kwargs"] = kwargs
            return {
                "card_id": 777,
                "added": True,
                "skipped": False,
                "content_hash": "fakehash",
            }

    monkeypatch.setattr("src.knowledge_promotion.MethodCards", FakeMethodCards)

    service = KnowledgePromotionService(staging=staging)
    result = service.promote_method_candidate(promotion_id)

    assert result["card_id"] == 777
    assert calls["kwargs"]["category"] == "桥架审核方法"
    assert "先核专业，再核册号，再核单位。" in calls["kwargs"]["method_text"]
    assert "判断依据：" in calls["kwargs"]["method_text"]
    assert calls["kwargs"]["keywords"] == ["桥架", "审核顺序"]
    assert calls["kwargs"]["pattern_keys"] == ["桥架", "审核顺序"]


def test_promote_experience_candidate_updates_staging_and_trace(tmp_path, monkeypatch):
    db_path = tmp_path / "knowledge_staging.db"
    staging = KnowledgeStaging(db_path=db_path, schema_path=_schema_path())

    audit_id = staging.create_audit_error({
        "source_id": "task-004",
        "source_type": "manual_review",
        "source_table": "match_results",
        "source_record_id": "row-04",
        "owner": "tester",
        "status": "active",
        "task_id": "task-004",
        "result_id": "result-004",
        "bill_name": "测试清单",
        "match_source": "experience",
        "error_type": "historical_case_confirmed",
        "error_level": "high",
    })
    promotion_id = staging.enqueue_promotion({
        "source_id": "task-004",
        "source_type": "audit_error",
        "source_table": "audit_errors",
        "source_record_id": str(audit_id),
        "owner": "tester",
        "status": "approved",
        "review_status": "approved",
        "candidate_type": "experience",
        "target_layer": "ExperienceDB",
        "candidate_title": "测试历史案例",
        "evidence_ref": "task-004/result-004",
        "candidate_payload": {
            "province": "北京2024",
            "bill_name": "测试清单",
            "bill_desc": "给排水管道安装 DN25",
            "final_quota_code": "4-01-001",
            "final_quota_name": "管道安装",
            "specialty": "C10",
            "project_name": "测试项目",
            "summary": "历史案例审核后确认可复用",
            "confidence": 96,
        },
    })

    calls = {}

    class FakeExperienceDB:
        def __init__(self, province=None):
            calls["province"] = province

        def add_experience_text(self, **kwargs):
            calls["kwargs"] = kwargs
            return {
                "experience_id": 987,
                "added": True,
                "skipped": False,
                "content_hash": "fakehash",
            }

    monkeypatch.setattr("src.knowledge_promotion.ExperienceDB", FakeExperienceDB)

    service = KnowledgePromotionService(staging=staging)
    result = service.promote_experience_candidate(promotion_id)

    assert result["experience_id"] == 987
    assert result["added"] is True
    assert calls["province"] == "北京2024"
    assert calls["kwargs"]["bill_text"] == "测试清单 给排水管道安装 DN25"
    assert calls["kwargs"]["quota_ids"] == ["4-01-001"]
    assert calls["kwargs"]["quota_names"] == ["管道安装"]

    promotion = staging.get_promotion(promotion_id)
    assert promotion["status"] == "promoted"
    assert promotion["review_status"] == "promoted"
    assert promotion["promoted_target_id"] == "987"
    assert "ExperienceDB:987" in promotion["promotion_trace"]

    audit = staging.get_audit_error(audit_id)
    assert audit["review_status"] == "promoted"
    assert "ExperienceDB:987" in audit["review_comment"]


def test_rollback_experience_candidate_updates_staging_and_trace(tmp_path, monkeypatch):
    db_path = tmp_path / "knowledge_staging.db"
    staging = KnowledgeStaging(db_path=db_path, schema_path=_schema_path())

    audit_id = staging.create_audit_error({
        "source_id": "task-005",
        "source_type": "manual_review",
        "source_table": "match_results",
        "source_record_id": "row-05",
        "owner": "tester",
        "status": "active",
        "task_id": "task-005",
        "result_id": "result-005",
        "bill_name": "test bill",
        "match_source": "experience",
        "error_type": "polluted_experience",
        "error_level": "high",
    })
    promotion_id = staging.enqueue_promotion({
        "source_id": "task-005",
        "source_type": "audit_error",
        "source_table": "audit_errors",
        "source_record_id": str(audit_id),
        "owner": "tester",
        "status": "promoted",
        "review_status": "promoted",
        "candidate_type": "experience",
        "target_layer": "ExperienceDB",
        "candidate_title": "rollback exp",
        "review_comment": "Promoted: experience_db:888",
        "promoted_target_id": "888",
        "promoted_target_ref": "experience_db:888",
        "promotion_trace": "audit_errors:1 -> ExperienceDB:888",
        "candidate_payload": {
            "province": "Beijing2024",
            "bill_text": "test bill text",
            "quota_ids": ["4-01-001"],
        },
    })

    calls = {}

    class FakeExperienceDB:
        def __init__(self, province=None):
            calls["province"] = province

        def demote_to_candidate(self, record_id: int, reason: str = ""):
            calls["record_id"] = record_id
            calls["reason"] = reason
            return True

    monkeypatch.setattr("src.knowledge_promotion.ExperienceDB", FakeExperienceDB)

    service = KnowledgePromotionService(staging=staging)
    result = service.rollback_experience_candidate(
        promotion_id,
        reason="bad evidence",
        actor="admin",
    )

    assert result["rolled_back"] is True
    assert calls["province"] == "Beijing2024"
    assert calls["record_id"] == 888
    assert calls["reason"] == "bad evidence"

    promotion = staging.get_promotion(promotion_id)
    assert promotion["status"] == "rolled_back"
    assert promotion["review_status"] == "rolled_back"
    assert "ROLLBACK ExperienceDB:888 -> candidate" in promotion["promotion_trace"]
    assert "Rollback: bad evidence" in promotion["review_comment"]

    audit = staging.get_audit_error(audit_id)
    assert "Rollback: bad evidence" in audit["review_comment"]


def test_rollback_rule_candidate_updates_staging_and_trace(tmp_path, monkeypatch):
    db_path = tmp_path / "knowledge_staging.db"
    staging = KnowledgeStaging(db_path=db_path, schema_path=_schema_path())

    audit_id = staging.create_audit_error({
        "source_id": "task-006",
        "source_type": "manual_review",
        "source_table": "match_results",
        "source_record_id": "row-06",
        "owner": "tester",
        "status": "active",
        "task_id": "task-006",
        "result_id": "result-006",
        "bill_name": "test bill",
        "match_source": "rule",
        "error_type": "wrong_rule",
        "error_level": "high",
    })
    promotion_id = staging.enqueue_promotion({
        "source_id": "task-006",
        "source_type": "audit_error",
        "source_table": "audit_errors",
        "source_record_id": str(audit_id),
        "owner": "tester",
        "status": "promoted",
        "review_status": "promoted",
        "candidate_type": "rule",
        "target_layer": "RuleKnowledge",
        "candidate_title": "rollback rule",
        "review_comment": "Promoted: rule_knowledge:777",
        "promoted_target_id": "777",
        "promoted_target_ref": "rule_knowledge:777",
        "promotion_trace": "audit_errors:1 -> RuleKnowledge:777",
        "candidate_payload": {
            "province": "Beijing2024",
            "rule_text": "test rule text",
        },
    })

    calls = {}

    class FakeRuleKnowledge:
        def __init__(self, province=None):
            calls["province"] = province

        def soft_disable_rule(self, rule_id: int, *, reason: str = "", actor: str = ""):
            calls["rule_id"] = rule_id
            calls["reason"] = reason
            calls["actor"] = actor
            return True

    monkeypatch.setattr("src.knowledge_promotion.RuleKnowledge", FakeRuleKnowledge)

    service = KnowledgePromotionService(staging=staging)
    result = service.rollback_rule_candidate(
        promotion_id,
        reason="rule obsolete",
        actor="admin",
    )

    assert result["rolled_back"] is True
    assert calls["province"] == "Beijing2024"
    assert calls["rule_id"] == 777
    assert calls["reason"] == "rule obsolete"
    assert calls["actor"] == "admin"

    promotion = staging.get_promotion(promotion_id)
    assert promotion["status"] == "rolled_back"
    assert promotion["review_status"] == "rolled_back"
    assert "ROLLBACK RuleKnowledge:777 -> inactive" in promotion["promotion_trace"]
    assert "Rollback: rule obsolete" in promotion["review_comment"]

    audit = staging.get_audit_error(audit_id)
    assert "Rollback: rule obsolete" in audit["review_comment"]


def test_rollback_method_candidate_updates_staging_and_trace(tmp_path, monkeypatch):
    db_path = tmp_path / "knowledge_staging.db"
    staging = KnowledgeStaging(db_path=db_path, schema_path=_schema_path())

    audit_id = staging.create_audit_error({
        "source_id": "task-007",
        "source_type": "manual_review",
        "source_table": "match_results",
        "source_record_id": "row-07",
        "owner": "tester",
        "status": "active",
        "task_id": "task-007",
        "result_id": "result-007",
        "bill_name": "test bill",
        "match_source": "manual",
        "error_type": "review_corrected",
        "error_level": "high",
    })
    promotion_id = staging.enqueue_promotion({
        "source_id": "task-007",
        "source_type": "audit_error",
        "source_table": "audit_errors",
        "source_record_id": str(audit_id),
        "owner": "tester",
        "status": "promoted",
        "review_status": "promoted",
        "candidate_type": "method",
        "target_layer": "MethodCards",
        "candidate_title": "rollback method",
        "review_comment": "Promoted: method_cards:666",
        "promoted_target_id": "666",
        "promoted_target_ref": "method_cards:666",
        "promotion_trace": "audit_errors:1 -> MethodCards:666",
        "candidate_payload": {
            "province": "Beijing2024",
            "category": "test method",
            "method_text": "test method text",
        },
    })

    calls = {}

    class FakeMethodCards:
        def soft_disable_card(self, card_id: int, *, reason: str = "", actor: str = ""):
            calls["card_id"] = card_id
            calls["reason"] = reason
            calls["actor"] = actor
            return True

    monkeypatch.setattr("src.knowledge_promotion.MethodCards", FakeMethodCards)

    service = KnowledgePromotionService(staging=staging)
    result = service.rollback_method_candidate(
        promotion_id,
        reason="method outdated",
        actor="admin",
    )

    assert result["rolled_back"] is True
    assert calls["card_id"] == 666
    assert calls["reason"] == "method outdated"
    assert calls["actor"] == "admin"

    promotion = staging.get_promotion(promotion_id)
    assert promotion["status"] == "rolled_back"
    assert promotion["review_status"] == "rolled_back"
    assert "ROLLBACK MethodCards:666 -> inactive" in promotion["promotion_trace"]
    assert "Rollback: method outdated" in promotion["review_comment"]

    audit = staging.get_audit_error(audit_id)
    assert "Rollback: method outdated" in audit["review_comment"]
