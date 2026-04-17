from __future__ import annotations


def test_match_agent_retry_keeps_deterministic_strategy_when_llm_circuit_is_open(monkeypatch):
    from src import agent_matcher, match_engine

    captured = {"validate_calls": [], "resolve_calls": []}

    class DummyAgentMatcher:
        def __init__(self, *args, **kwargs):
            pass

        def is_circuit_open(self):
            return True

    class DummyRuleValidator:
        def validate_results(self, _results):
            return None

    class DummyReranker:
        def rerank(self, query, candidates):
            return [{**candidate, "rerank_query": query} for candidate in candidates]

    class DummySearcher:
        def search(self, query, top_k=None, books=None):
            return [{
                "quota_id": "Q-RETRY",
                "name": "retry candidate",
                "param_match": True,
                "param_score": 0.92,
                "param_tier": 2,
                "rerank_score": 0.8,
                "search_query_used": query,
            }]

    class DummyValidator:
        def validate_candidates(self, full_query, candidates, supplement_query=None):
            captured["validate_calls"].append((full_query, supplement_query))
            return candidates

    def fake_prepare_match_iteration(*, item, idx, total, results, exp_hits, rule_hits, **kwargs):
        ctx = {
            "name": item.get("name", ""),
            "desc": item.get("description", ""),
            "canonical_query": {
                "raw_query": item.get("name", ""),
                "validation_query": f"canonical validation {idx}",
                "search_query": f"canonical search {idx}",
            },
        }
        candidates = [{"quota_id": f"Q-{idx}", "name": f"candidate-{idx}", "param_match": True, "param_score": 0.9, "param_tier": 2}]
        return False, exp_hits, rule_hits, (ctx, "legacy full query", "legacy search query", candidates, {}, {})

    call_counter = {"count": 0}

    def fake_resolve_agent_mode_result(**kwargs):
        call_counter["count"] += 1
        captured["resolve_calls"].append({
            "canonical_query": dict(kwargs.get("canonical_query") or {}),
            "full_query": kwargs.get("full_query"),
            "search_query": kwargs.get("search_query"),
        })
        if call_counter["count"] == 1:
            return {
                "bill_item": kwargs["item"],
                "quotas": [{"quota_id": "Q-LOW", "name": "low quota", "unit": "m"}],
                "confidence": 40,
                "explanation": "needs retry",
                "match_source": "agent",
                "candidates_count": len(kwargs["candidates"]),
                "suggested_search": "retry canonical search",
            }, 0, 0
        return {
            "bill_item": kwargs["item"],
            "quotas": [{"quota_id": "Q-HIGH", "name": "high quota", "unit": "m"}],
            "confidence": 88,
            "explanation": "retried",
            "match_source": "agent_retry",
            "candidates_count": len(kwargs["candidates"]),
        }, 0, 0

    monkeypatch.setattr(agent_matcher, "AgentMatcher", DummyAgentMatcher)
    monkeypatch.setattr(match_engine, "_create_rule_validator_and_reranker",
                        lambda province=None: (DummyRuleValidator(), DummyReranker()))
    monkeypatch.setattr(match_engine, "_load_rule_kb", lambda province=None: None)
    monkeypatch.setattr(match_engine, "_prepare_match_iteration", fake_prepare_match_iteration)
    monkeypatch.setattr(match_engine, "_should_skip_agent_llm",
                        lambda candidates, exp_backup=None, rule_backup=None, route_profile=None: False)
    monkeypatch.setattr(match_engine, "_resolve_agent_mode_result", fake_resolve_agent_mode_result)
    monkeypatch.setattr(match_engine.config, "LOW_CONFIDENCE_RETRY_THRESHOLD", 70)
    monkeypatch.setattr(match_engine.config, "LOW_CONFIDENCE_RETRY_MAX_ATTEMPTS", 1)
    monkeypatch.setattr(match_engine.config, "LLM_CONCURRENT", 1)
    monkeypatch.setattr(match_engine.config, "HYBRID_TOP_K", 5)

    results = match_engine.match_agent(
        [{"name": "retry-item", "description": ""}],
        searcher=DummySearcher(),
        validator=DummyValidator(),
        experience_db=None,
        llm_type="deepseek",
        province="test",
    )

    assert results[0]["match_source"] == "agent_retry"
    assert captured["validate_calls"] == [("canonical validation 1", "canonical validation 1")]
    assert captured["resolve_calls"][1]["canonical_query"]["search_query"] == "canonical validation 1"
    assert results[0]["retry_trace"]["strategy"] == "canonical_validation"
