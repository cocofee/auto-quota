from __future__ import annotations


def test_match_agent_isolates_single_future_exception(monkeypatch):
    from src import match_engine

    class DummyRuleValidator:
        def validate_results(self, _results):
            return None

    class DummyReranker:
        def rerank(self, _query, candidates):
            return candidates

    class DummySearcher:
        def search(self, _query, top_k=None, books=None):
            return []

    class DummyValidator:
        def validate_candidates(self, _full_query, candidates, supplement_query=None):
            return candidates

    def fake_prepare_match_iteration(*, item, idx, total, results, exp_hits, rule_hits, **kwargs):
        ctx = {"name": item.get("name", ""), "desc": item.get("description", "")}
        candidates = [{"quota_id": f"Q-{idx}", "name": f"candidate-{idx}", "param_match": True, "param_score": 0.9}]
        return False, exp_hits, rule_hits, (ctx, "full query", "search query", candidates, {}, {})

    def fake_resolve_agent_mode_result(**kwargs):
        item = kwargs["item"]
        if item.get("name") == "bad-item":
            raise RuntimeError("simulated llm task failure")
        return {
            "bill_item": item,
            "quotas": [{"quota_id": "Q-GOOD", "name": "good quota", "unit": "m"}],
            "confidence": 88,
            "explanation": "ok",
            "match_source": "agent",
            "candidates_count": 1,
        }, 0, 0

    def fake_resolve_search_mode_result(item, candidates, exp_backup, rule_backup, exp_hits, rule_hits):
        return {
            "bill_item": item,
            "quotas": [{"quota_id": "Q-FALLBACK", "name": "fallback quota", "unit": "m"}],
            "confidence": 55,
            "explanation": "fallback from search",
            "match_source": "search_fallback",
            "candidates_count": len(candidates),
        }, exp_hits, rule_hits

    monkeypatch.setattr(match_engine, "_create_rule_validator_and_reranker",
                        lambda province=None: (DummyRuleValidator(), DummyReranker()))
    monkeypatch.setattr(match_engine, "_load_rule_kb", lambda province=None: None)
    monkeypatch.setattr(match_engine, "_prepare_match_iteration", fake_prepare_match_iteration)
    monkeypatch.setattr(match_engine, "_should_skip_agent_llm",
                        lambda candidates, exp_backup=None, rule_backup=None: False)
    monkeypatch.setattr(match_engine, "_resolve_agent_mode_result", fake_resolve_agent_mode_result)
    monkeypatch.setattr(match_engine, "_resolve_search_mode_result", fake_resolve_search_mode_result)
    monkeypatch.setattr(match_engine.config, "LLM_CONCURRENT", 2)
    # 禁用L6批量模式，本测试专门测试逐条LLM异常隔离
    monkeypatch.setattr(match_engine.config, "AGENT_BATCH_ENABLED", False)

    items = [{"name": "good-item", "description": ""}, {"name": "bad-item", "description": ""}]
    results = match_engine.match_agent(
        items,
        searcher=DummySearcher(),
        validator=DummyValidator(),
        experience_db=None,
        llm_type="deepseek",
        province="test",
    )

    assert len(results) == 2
    by_name = {r["bill_item"]["name"]: r for r in results}
    assert by_name["good-item"]["match_source"] == "agent"
    assert by_name["bad-item"]["match_source"] == "search_fallback"
