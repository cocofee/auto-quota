from __future__ import annotations

import threading


def test_match_agent_isolates_single_future_exception(monkeypatch):
    from src import match_engine
    captured = {}

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
        ctx = {
            "name": item.get("name", ""),
            "desc": item.get("description", ""),
            "canonical_query": {
                "raw_query": item.get("name", ""),
                "validation_query": f"canonical validation {idx}",
                "search_query": f"canonical search {idx}",
            },
        }
        candidates = [{"quota_id": f"Q-{idx}", "name": f"candidate-{idx}", "param_match": True, "param_score": 0.9}]
        return False, exp_hits, rule_hits, (ctx, "full query", "search query", candidates, {}, {})

    def fake_resolve_agent_mode_result(**kwargs):
        item = kwargs["item"]
        captured[item.get("name")] = kwargs.get("canonical_query")
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
    assert captured["good-item"]["search_query"] == "canonical search 1"
    assert captured["bad-item"]["validation_query"] == "canonical validation 2"


def test_match_agent_retry_prefers_registered_deterministic_strategy(monkeypatch):
    from src import agent_matcher, match_engine

    captured = {"validate_calls": [], "resolve_calls": []}

    class DummyAgentMatcher:
        def __init__(self, *args, **kwargs):
            pass

        def is_circuit_open(self):
            return False

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
    assert captured["resolve_calls"][0]["full_query"] == "canonical validation 1"
    assert captured["resolve_calls"][0]["search_query"] == "canonical search 1"
    assert captured["resolve_calls"][1]["canonical_query"]["validation_query"] == "canonical validation 1"
    assert captured["resolve_calls"][1]["canonical_query"]["search_query"] == "canonical validation 1"
    assert results[0]["retry_trace"]["strategy"] == "canonical_validation"
    assert results[0]["retry_trace"]["retry_search_query"] == "canonical validation 1"


def test_match_agent_retry_can_fallback_to_llm_strategy(monkeypatch):
    from src import agent_matcher, match_engine

    captured = {"validate_calls": [], "resolve_calls": []}

    class DummyAgentMatcher:
        def __init__(self, *args, **kwargs):
            pass

        def is_circuit_open(self):
            return False

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
                "validation_query": f"canonical search {idx}",
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
    assert captured["validate_calls"] == [("canonical search 1", "retry canonical search")]
    assert captured["resolve_calls"][1]["canonical_query"]["search_query"] == "retry canonical search"
    assert results[0]["retry_trace"]["strategy"] == "llm_suggested_search"


def test_match_agent_batches_retry_after_initial_llm_phase(monkeypatch):
    from src import agent_matcher, match_engine

    state = {
        "initial_calls": 0,
        "search_calls_before_initial_complete": 0,
    }
    state_lock = threading.Lock()
    all_initial_started = threading.Event()

    class DummyAgentMatcher:
        def __init__(self, *args, **kwargs):
            pass

        def is_circuit_open(self):
            return False

    class DummyRuleValidator:
        def validate_results(self, _results):
            return None

    class DummyReranker:
        def rerank(self, query, candidates):
            return [{**candidate, "rerank_query": query} for candidate in candidates]

    class DummySearcher:
        def search(self, query, top_k=None, books=None):
            with state_lock:
                if state["initial_calls"] < 2:
                    state["search_calls_before_initial_complete"] += 1
            return [{
                "quota_id": f"Q-RETRY-{query}",
                "name": "retry candidate",
                "param_match": True,
                "param_score": 0.95,
                "param_tier": 2,
            }]

    class DummyValidator:
        def validate_candidates(self, full_query, candidates, supplement_query=None):
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
        candidates = [{
            "quota_id": f"Q-{idx}",
            "name": f"candidate-{idx}",
            "param_match": True,
            "param_score": 0.9,
            "param_tier": 2,
        }]
        return False, exp_hits, rule_hits, (ctx, "legacy full query", "legacy search query", candidates, {}, {})

    def fake_resolve_agent_mode_result(**kwargs):
        search_query = kwargs.get("search_query", "")
        with state_lock:
            if search_query.startswith("canonical search"):
                state["initial_calls"] += 1
                if state["initial_calls"] == 2:
                    all_initial_started.set()
        if search_query.startswith("canonical search"):
            all_initial_started.wait(timeout=1.0)
            return {
                "bill_item": kwargs["item"],
                "quotas": [{"quota_id": "Q-LOW", "name": "low quota", "unit": "m"}],
                "confidence": 40,
                "explanation": "needs retry",
                "match_source": "agent",
                "candidates_count": len(kwargs["candidates"]),
                "suggested_search": f"retry {search_query}",
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
    monkeypatch.setattr(match_engine.config, "LLM_CONCURRENT", 2)
    monkeypatch.setattr(match_engine.config, "HYBRID_TOP_K", 5)
    monkeypatch.setattr(match_engine.config, "AGENT_STAGE1_PARALLEL_ENABLED", False)
    monkeypatch.setattr(match_engine.config, "AGENT_RETRY_CONCURRENT", 2)

    results = match_engine.match_agent(
        [{"name": "retry-1", "description": ""}, {"name": "retry-2", "description": ""}],
        searcher=DummySearcher(),
        validator=DummyValidator(),
        experience_db=None,
        llm_type="deepseek",
        province="test",
    )

    assert state["search_calls_before_initial_complete"] == 0
    assert [result["match_source"] for result in results] == ["agent_retry", "agent_retry"]


def test_match_agent_stage1_parallel_uses_thread_local_validator(monkeypatch):
    from types import SimpleNamespace

    from src import match_engine

    state = {
        "validate_calls": 0,
        "validator_ids": set(),
    }
    state_lock = threading.Lock()
    all_validators_started = threading.Event()

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
            with state_lock:
                state["validate_calls"] += 1
                state["validator_ids"].add(id(self))
                if state["validate_calls"] == 2:
                    all_validators_started.set()
            all_validators_started.wait(timeout=1.0)
            return candidates

    def fake_prepare_match_iteration(*, item, idx, total, results, exp_hits, rule_hits, validator, **kwargs):
        candidates = [{
            "quota_id": f"Q-{idx}",
            "name": f"candidate-{idx}",
            "param_match": True,
            "param_score": 0.95,
            "param_tier": 2,
        }]
        validated = validator.validate_candidates(
            f"canonical validation {idx}",
            candidates,
            supplement_query=f"canonical search {idx}",
        )
        ctx = {
            "name": item.get("name", ""),
            "desc": item.get("description", ""),
            "query_route": None,
            "canonical_query": {
                "raw_query": item.get("name", ""),
                "validation_query": f"canonical validation {idx}",
                "search_query": f"canonical search {idx}",
            },
        }
        return False, exp_hits, rule_hits, (ctx, "legacy full query", "legacy search query", validated, {}, {})

    def fake_resolve_search_mode_result(item, candidates, exp_backup, rule_backup, exp_hits, rule_hits):
        top = candidates[0]
        return {
            "bill_item": item,
            "quotas": [{"quota_id": top["quota_id"], "name": top["name"], "unit": "m"}],
            "confidence": 90,
            "explanation": top["name"],
            "match_source": "search_fastpath",
            "candidates_count": len(candidates),
        }, exp_hits, rule_hits

    monkeypatch.setattr(match_engine, "_create_rule_validator_and_reranker",
                        lambda province=None: (DummyRuleValidator(), DummyReranker()))
    monkeypatch.setattr(match_engine, "_load_rule_kb", lambda province=None: None)
    monkeypatch.setattr(match_engine, "_prepare_match_iteration", fake_prepare_match_iteration)
    monkeypatch.setattr(match_engine, "_resolve_search_mode_result", fake_resolve_search_mode_result)
    monkeypatch.setattr(match_engine, "get_fastpath_decision",
                        lambda *args, **kwargs: SimpleNamespace(can_fastpath=True))
    monkeypatch.setattr(match_engine, "_should_audit_fastpath", lambda decision: False)
    monkeypatch.setattr(match_engine.config, "AGENT_STAGE1_PARALLEL_ENABLED", True)
    monkeypatch.setattr(match_engine.config, "AGENT_STAGE1_BATCH_SIZE", 2)
    monkeypatch.setattr(match_engine.config, "AGENT_STAGE1_CONCURRENT", 2)
    monkeypatch.setattr(match_engine.config, "AGENT_PREPARE_BATCH_SIZE", 2, raising=False)
    monkeypatch.setattr(match_engine.config, "AGENT_PREPARE_CONCURRENT", 2, raising=False)
    monkeypatch.setattr(match_engine.config, "LLM_CONCURRENT", 1)

    results = match_engine.match_agent(
        [{"name": "item-1", "description": ""}, {"name": "item-2", "description": ""}],
        searcher=DummySearcher(),
        validator=DummyValidator(),
        experience_db=None,
        llm_type="deepseek",
        province="test",
    )

    assert len(results) == 2
    assert state["validate_calls"] == 2
    assert len(state["validator_ids"]) == 2


def test_match_agent_preserves_same_batch_consistency_hint_for_later_items(monkeypatch):
    from types import SimpleNamespace

    from src import match_engine

    prepare_calls = []

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
        hints = tuple(item.get("_context_hints", []) or [])
        prepare_calls.append((idx, hints))
        candidate = {
            "quota_id": "Q-FIRST" if idx == 1 else ("Q-HINT" if hints else "Q-NOHINT"),
            "name": "风管安装" if idx == 1 or hints else "普通安装",
            "param_match": True,
            "param_score": 0.95,
            "param_tier": 2,
        }
        ctx = {
            "name": item.get("name", ""),
            "desc": item.get("description", ""),
            "query_route": None,
            "canonical_query": {
                "raw_query": item.get("name", ""),
                "validation_query": f"canonical validation {idx}",
                "search_query": f"canonical search {idx}",
            },
        }
        return False, exp_hits, rule_hits, (ctx, "legacy full query", "legacy search query", [candidate], {}, {})

    def fake_resolve_search_mode_result(item, candidates, exp_backup, rule_backup, exp_hits, rule_hits):
        top = candidates[0]
        return {
            "bill_item": item,
            "quotas": [{"quota_id": top["quota_id"], "name": top["name"], "unit": "m"}],
            "confidence": 90,
            "explanation": top["name"],
            "match_source": "search_fastpath",
            "candidates_count": len(candidates),
        }, exp_hits, rule_hits

    monkeypatch.setattr(match_engine, "_create_rule_validator_and_reranker",
                        lambda province=None: (DummyRuleValidator(), DummyReranker()))
    monkeypatch.setattr(match_engine, "_load_rule_kb", lambda province=None: None)
    monkeypatch.setattr(match_engine, "_prepare_match_iteration", fake_prepare_match_iteration)
    monkeypatch.setattr(match_engine, "_resolve_search_mode_result", fake_resolve_search_mode_result)
    monkeypatch.setattr(match_engine, "get_fastpath_decision",
                        lambda *args, **kwargs: SimpleNamespace(can_fastpath=True))
    monkeypatch.setattr(match_engine, "_should_audit_fastpath", lambda decision: False)
    monkeypatch.setattr(match_engine, "lookup_consistency_hint",
                        lambda province, item_name, specialty: None)
    monkeypatch.setattr(match_engine, "remember_consistency_hint",
                        lambda **kwargs: None)
    monkeypatch.setattr(match_engine.config, "AGENT_STAGE1_BATCH_SIZE", 10)
    monkeypatch.setattr(match_engine.config, "AGENT_STAGE1_CONCURRENT", 1)
    monkeypatch.setattr(match_engine.config, "AGENT_STAGE1_PARALLEL_ENABLED", False)

    results = match_engine.match_agent(
        [
            {"name": "阀门", "description": "", "specialty": "给排水"},
            {"name": "阀门", "description": "", "specialty": "给排水", "_is_ambiguous_short": True},
        ],
        searcher=DummySearcher(),
        validator=DummyValidator(),
        experience_db=None,
        llm_type="deepseek",
        province="test",
    )

    assert results[1]["quotas"][0]["quota_id"] == "Q-HINT"
    assert prepare_calls == [
        (1, ()),
        (2, ()),
        (2, ("风管安装",)),
    ]
