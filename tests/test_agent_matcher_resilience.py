from __future__ import annotations


def _make_matcher():
    from src.agent_matcher import AgentMatcher

    matcher = AgentMatcher.__new__(AgentMatcher)
    matcher.province = "test-province"
    matcher.llm_type = "deepseek"
    matcher._llm_consecutive_fails = 0
    matcher._llm_circuit_open = False
    matcher._llm_circuit_open_time = 0.0
    return matcher


def test_build_agent_prompt_handles_missing_candidate_fields():
    matcher = _make_matcher()
    bill_item = {"name": "test item", "description": "desc", "unit": "m"}
    candidates = [
        {"param_score": "bad"},
        {"quota_id": "Q-1"},
        {"name": "name-only"},
    ]

    prompt = matcher._build_agent_prompt(bill_item, candidates)

    assert "[UNKNOWN]" in prompt
    assert "未命名候选" in prompt


def test_parse_response_skips_invalid_main_candidate_without_keyerror():
    matcher = _make_matcher()
    bill_item = {"name": "test item"}
    candidates = [{"name": "missing-id"}]

    response = """{"main_quota_index": 1, "confidence": 88, "explanation": "ok"}"""
    result = matcher._parse_response(response, bill_item, candidates)

    assert result["quotas"] == []
    assert result["confidence"] == 0
    assert result["match_source"] == "agent"


def test_fallback_result_handles_invalid_candidate_without_keyerror():
    matcher = _make_matcher()
    bill_item = {"name": "test item"}
    candidates = [{"param_match": True, "param_score": "bad"}]

    result = matcher._fallback_result(bill_item, candidates, "test error")

    assert result["quotas"]
    assert result["quotas"][0]["quota_id"] == "UNKNOWN"
    assert result["quotas"][0]["name"] == "未命名候选"
    assert result["match_source"] == "agent_fallback"
