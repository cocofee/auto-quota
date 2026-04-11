from src.knowledge_promotion_rules import (
    build_openclaw_promotion_candidates,
    classify_openclaw_audit_error,
)


def test_classify_openclaw_audit_error_sets_explicit_flags():
    search_rule = classify_openclaw_audit_error("search")
    assert search_rule["error_type"] == "wrong_rank"
    assert search_rule["can_promote_rule"] is True
    assert search_rule["can_promote_method"] is True

    exp_rule = classify_openclaw_audit_error("experience")
    assert exp_rule["error_type"] == "polluted_experience"
    assert exp_rule["can_promote_rule"] is False
    assert exp_rule["can_promote_method"] is False


def test_build_openclaw_promotion_candidates_for_agent_prefers_method_and_experience():
    candidates = build_openclaw_promotion_candidates(
        task_id="task-1",
        province="北京2024",
        specialty="C10",
        bill_name="给水管道安装",
        bill_desc="室内PPR给水管",
        match_source="agent",
        original_quota={"quota_id": "C10-1-1", "name": "原定额", "unit": "m"},
        corrected_quota={"quota_id": "C10-9-9", "name": "修正定额", "unit": "m"},
        final_note="人工审核后确认应改判为修正定额",
        audit_id=10,
    )

    layers = {item["target_layer"] for item in candidates}
    assert "MethodCards" in layers
    assert "ExperienceDB" in layers
    assert "RuleKnowledge" not in layers


def test_build_openclaw_promotion_candidates_for_rule_prefers_rule_and_experience():
    candidates = build_openclaw_promotion_candidates(
        task_id="task-2",
        province="北京2024",
        specialty="C10",
        bill_name="给水管道安装",
        bill_desc="室内PPR给水管",
        match_source="rule",
        original_quota={"quota_id": "C10-1-1", "name": "原定额", "unit": "m"},
        corrected_quota={"quota_id": "C10-9-9", "name": "修正定额", "unit": "m"},
        final_note="人工审核后确认原规则命中错误，应改判为修正定额",
        audit_id=11,
    )

    layers = {item["target_layer"] for item in candidates}
    assert "RuleKnowledge" in layers
    assert "ExperienceDB" in layers
    assert "MethodCards" not in layers


def test_build_openclaw_promotion_candidates_prefers_report_hints():
    candidates = build_openclaw_promotion_candidates(
        task_id="task-3",
        province="北京2024",
        specialty="C10",
        bill_name="三联单控开关",
        bill_desc="暗装 86 型",
        match_source="search",
        original_quota={"quota_id": "C10-1-1", "name": "原定额", "unit": "个"},
        corrected_quota={"quota_id": "C10-1-2", "name": "修正定额", "unit": "个"},
        final_note="确认候选池中存在更优修正项。",
        audit_id=12,
        report={
            "promotion_hints": {
                "rule": {
                    "chapter": "Electrical",
                    "section": "switch",
                    "judgment_basis": "联数和控制方式匹配。",
                    "core_knowledge_points": ["param:gang_count", "param:control_mode"],
                },
                "method": {
                    "category": "开关审核",
                    "pattern_keys": ["三联单控开关", "安装"],
                    "common_errors": "不要把双控误判为单控",
                },
                "experience": {
                    "bill_code": "031101",
                    "summary": "确认该项目应回落到修正定额。",
                },
            }
        },
    )

    by_layer = {item["target_layer"]: item for item in candidates}
    assert by_layer["RuleKnowledge"]["candidate_payload"]["chapter"] == "Electrical"
    assert by_layer["RuleKnowledge"]["candidate_payload"]["judgment_basis"] == "联数和控制方式匹配。"
    assert by_layer["MethodCards"]["candidate_payload"]["category"] == "开关审核"
    assert by_layer["ExperienceDB"]["candidate_payload"]["bill_code"] == "031101"
