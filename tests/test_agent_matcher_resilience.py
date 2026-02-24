from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
import time

def _make_matcher():
    from src.agent_matcher import AgentMatcher

    matcher = AgentMatcher.__new__(AgentMatcher)
    matcher.province = "test-province"
    matcher.llm_type = "deepseek"
    matcher._client = None
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

    assert result["quotas"] == []
    assert result["confidence"] == 0
    assert result["no_match_reason"] == "降级候选缺少有效定额编号"
    assert result["match_source"] == "agent_fallback"


def test_fallback_result_keeps_valid_quota_id():
    matcher = _make_matcher()
    bill_item = {"name": "test item"}
    candidates = [{
        "quota_id": "C1-1",
        "name": "测试定额",
        "unit": "m",
        "param_match": True,
        "param_score": 0.8,
    }]

    result = matcher._fallback_result(bill_item, candidates, "test error")

    assert result["quotas"]
    assert result["quotas"][0]["quota_id"] == "C1-1"
    assert result["match_source"] == "agent_fallback"


def test_client_lazy_init_is_threadsafe():
    matcher = _make_matcher()
    created = []
    create_count = 0
    count_lock = threading.Lock()

    def _create_client():
        nonlocal create_count
        time.sleep(0.01)
        with count_lock:
            create_count += 1
            idx = create_count
        obj = {"client": idx}
        created.append(obj)
        return obj

    matcher._create_client = _create_client

    def _read_client(_):
        return matcher.client

    with ThreadPoolExecutor(max_workers=16) as pool:
        clients = list(pool.map(_read_client, range(40)))

    assert create_count == 1
    assert len(created) == 1
    assert all(c is created[0] for c in clients)
