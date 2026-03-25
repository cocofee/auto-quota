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
