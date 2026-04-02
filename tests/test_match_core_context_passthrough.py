# -*- coding: utf-8 -*-

from src import match_core
from src.performance_monitor import PerformanceMonitor


def test_prepare_candidates_passes_context_into_validator(monkeypatch):
    captured = {}

    class FakeSearcher:
        def collect_prior_candidates(self, *args, **kwargs):
            return []

    class FakeReranker:
        def rerank(self, query, candidates):
            return candidates

    class FakeValidator:
        def validate_candidates(
            self,
            query_text,
            candidates,
            supplement_query=None,
            bill_params=None,
            search_books=None,
            canonical_features=None,
            context_prior=None,
        ):
            captured["query_text"] = query_text
            captured["supplement_query"] = supplement_query
            captured["bill_params"] = bill_params
            captured["canonical_features"] = canonical_features
            captured["context_prior"] = context_prior
            return candidates

    monkeypatch.setattr(
        match_core,
        "cascade_search",
        lambda searcher, query, classification: [
            {
                "quota_id": "C4-1-1",
                "name": "电缆桥架支架制作安装",
                "hybrid_score": 0.8,
            }
        ],
    )

    match_core._prepare_candidates(
        FakeSearcher(),
        FakeReranker(),
        FakeValidator(),
        "支架 桥架",
        "支架",
        {"primary": "C4", "fallbacks": [], "search_books": ["C4"]},
        bill_params={},
        canonical_features={"entity": "支架", "system": "电气"},
        context_prior={"specialty": "C4", "context_hints": ["桥架"]},
    )

    assert captured["query_text"] == "支架"
    assert captured["supplement_query"] == "支架 桥架"
    assert captured["bill_params"] == {}
    assert captured["canonical_features"]["system"] == "电气"
    assert captured["context_prior"]["specialty"] == "C4"


def test_prepare_candidates_disables_validator_reordering_on_main_path(monkeypatch):
    captured = {}

    class FakeSearcher:
        def collect_prior_candidates(self, *args, **kwargs):
            return []

    class FakeReranker:
        def rerank(self, query, candidates):
            del query
            return candidates

    class FakeValidator:
        def validate_candidates(
            self,
            query_text,
            candidates,
            supplement_query=None,
            bill_params=None,
            search_books=None,
            canonical_features=None,
            context_prior=None,
            reorder_candidates=True,
        ):
            captured["query_text"] = query_text
            captured["reorder_candidates"] = reorder_candidates
            return candidates

    monkeypatch.setattr(
        match_core,
        "cascade_search",
        lambda searcher, query, classification: [
            {
                "quota_id": "C4-1-1",
                "name": "电缆桥架支架制作安装",
                "hybrid_score": 0.8,
            }
        ],
    )

    match_core._prepare_candidates(
        FakeSearcher(),
        FakeReranker(),
        FakeValidator(),
        "支架 桥架",
        "支架",
        {"primary": "C4", "fallbacks": [], "search_books": ["C4"]},
    )

    assert captured["query_text"] == "支架"
    assert captured["reorder_candidates"] is False


def test_prepare_candidates_passes_item_context_into_cascade_search(monkeypatch):
    captured = {}

    class FakeSearcher:
        def collect_prior_candidates(self, *args, **kwargs):
            return []

    class FakeReranker:
        def rerank(self, query, candidates, route_profile=None):
            del query, route_profile
            return candidates

    class FakeValidator:
        def validate_candidates(self, query_text, candidates, **kwargs):
            del query_text, kwargs
            return candidates

    def _fake_cascade(searcher, query, classification, top_k=None, item=None, context_prior=None):
        del searcher, top_k
        captured["query"] = query
        captured["classification"] = classification
        captured["item"] = item
        captured["context_prior"] = context_prior
        return [
            {
                "quota_id": "C10-1-1",
                "name": "钢塑复合管安装",
                "hybrid_score": 0.8,
            }
        ]

    monkeypatch.setattr(match_core, "cascade_search", _fake_cascade)

    item = {
        "name": "钢塑复合管",
        "description": "DN50 螺纹连接 含套管制作及安装",
        "canonical_query": {
            "primary_query_profile": {
                "primary_subject": "钢塑复合管",
                "decisive_terms": ["钢塑复合管", "DN50", "螺纹连接"],
            }
        },
    }

    match_core._prepare_candidates(
        FakeSearcher(),
        FakeReranker(),
        FakeValidator(),
        "钢塑复合管 DN50",
        "钢塑复合管",
        {"primary": "C10", "fallbacks": [], "search_books": ["C10"]},
        canonical_features={"entity": "钢塑复合管"},
        context_prior={"primary_subject": "钢塑复合管", "decisive_terms": ["钢塑复合管", "DN50"]},
        item=item,
    )

    assert captured["query"] == "钢塑复合管 DN50"
    assert captured["item"]["canonical_query"]["primary_query_profile"]["primary_subject"] == "钢塑复合管"
    assert captured["context_prior"]["primary_subject"] == "钢塑复合管"


def test_prepare_candidates_retains_knowledge_prior_candidates_after_rerank_truncation(monkeypatch):
    class FakeSearcher:
        def collect_prior_candidates(self, *args, **kwargs):
            return [
                {
                    "quota_id": "Q-PRIOR-1",
                    "name": "Exact Experience Quota",
                    "unit": "m",
                    "match_source": "experience_injected_exact",
                    "knowledge_prior_sources": ["experience"],
                    "knowledge_prior_score": 1.1,
                }
            ]

    class FakeReranker:
        def rerank(self, query, candidates, route_profile=None):
            del query, route_profile
            return [candidate for candidate in candidates if candidate.get("quota_id") != "Q-PRIOR-1"][:2]

    class FakeValidator:
        def validate_candidates(self, query_text, candidates, **kwargs):
            del query_text, kwargs
            return candidates

    monkeypatch.setattr(
        match_core,
        "cascade_search",
        lambda searcher, query, classification: [
            {"quota_id": "Q-SEARCH-1", "name": "Search Quota 1", "hybrid_score": 0.92},
            {"quota_id": "Q-SEARCH-2", "name": "Search Quota 2", "hybrid_score": 0.88},
            {"quota_id": "Q-SEARCH-3", "name": "Search Quota 3", "hybrid_score": 0.84},
        ],
    )

    candidates = match_core._prepare_candidates(
        FakeSearcher(),
        FakeReranker(),
        FakeValidator(),
        "桥架",
        "桥架",
        {"primary": "C4", "fallbacks": [], "search_books": ["C4"]},
    )

    retained = next(candidate for candidate in candidates if candidate["quota_id"] == "Q-PRIOR-1")
    assert retained["match_source"] == "experience_injected_exact"
    assert retained["knowledge_prior_sources"] == ["experience"]


def test_prepare_candidates_collects_prior_candidates_from_aux_searchers(monkeypatch):
    captured_books = []

    class FakeAux:
        province = "上海市安装工程预算定额(2016)"
        uses_standard_books = False

        class bm25_engine:
            quota_books = {1: "2", 2: "4"}

            @staticmethod
            def classify_to_books(_query, top_k=3):
                del top_k
                return ["4"]

        def collect_prior_candidates(self, *args, **kwargs):
            captured_books.append(kwargs.get("books"))
            return [
                {
                    "quota_id": "03-4-5-56",
                    "name": "光伏逆变器安装 功率≤1000kW",
                    "unit": "台",
                    "match_source": "kb_injected_exact",
                    "knowledge_prior_sources": ["universal_kb"],
                    "knowledge_prior_score": 1.0,
                }
            ]

    class FakeSearcher:
        aux_searchers = [FakeAux()]

        def collect_prior_candidates(self, *args, **kwargs):
            del args, kwargs
            return []

    class FakeReranker:
        def rerank(self, query, candidates, route_profile=None):
            del query, route_profile
            return candidates

    class FakeValidator:
        def validate_candidates(self, query_text, candidates, **kwargs):
            del query_text, kwargs
            return candidates

    monkeypatch.setattr(match_core, "cascade_search", lambda *args, **kwargs: [])

    candidates = match_core._prepare_candidates(
        FakeSearcher(),
        FakeReranker(),
        FakeValidator(),
        "组串式逆变器 150KW 光伏场区",
        "组串式逆变器 150KW 光伏场区",
        {"primary": "C2", "fallbacks": ["C8"], "search_books": ["C2", "C8"]},
    )

    assert captured_books == [["4", "2"]]
    retained = next(candidate for candidate in candidates if candidate["quota_id"] == "03-4-5-56")
    assert retained["_source_province"] == "上海市安装工程预算定额(2016)"
    assert retained["knowledge_prior_sources"] == ["universal_kb"]


def test_prepare_candidates_records_performance_stages(monkeypatch):
    class FakeSearcher:
        def collect_prior_candidates(self, *args, **kwargs):
            return []

    class FakeReranker:
        def rerank(self, query, candidates, route_profile=None):
            del query, route_profile
            return candidates

    class FakeValidator:
        def validate_candidates(self, query_text, candidates, **kwargs):
            del query_text, kwargs
            return candidates

    monkeypatch.setattr(
        match_core,
        "cascade_search",
        lambda searcher, query, classification: [
            {
                "quota_id": "C4-1-1",
                "name": "鐢电紗妗ユ灦鏀灦鍒朵綔瀹夎",
                "hybrid_score": 0.8,
            }
        ],
    )

    monitor = PerformanceMonitor()
    match_core._prepare_candidates(
        FakeSearcher(),
        FakeReranker(),
        FakeValidator(),
        "鏀灦 妗ユ灦",
        "鏀灦",
        {"primary": "C4", "fallbacks": [], "search_books": ["C4"]},
        performance_monitor=monitor,
    )

    assert "混合搜索" in monitor.stages
    assert "候选打分" in monitor.stages


def test_prepare_candidates_from_prepared_prefers_canonical_query(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        match_core,
        "_prepare_candidates",
        lambda searcher, reranker, validator, search_query, full_query, classification, **kwargs: (
            captured.update({
                "search_query": search_query,
                "full_query": full_query,
                "classification": classification,
                "kwargs": kwargs,
            }) or []
        ),
    )

    prepared = {
        "ctx": {
            "full_query": "legacy validation",
            "search_query": "legacy search",
            "canonical_query": {
                "validation_query": "canonical validation",
                "search_query": "canonical search",
            },
            "item": {},
            "canonical_features": {"entity": "配线"},
            "context_prior": {"specialty": "C4"},
        },
        "classification": {"primary": "C4", "fallbacks": [], "search_books": ["C4"]},
        "exp_backup": None,
        "rule_backup": None,
    }

    bundle = match_core._prepare_candidates_from_prepared(
        prepared,
        searcher=object(),
        reranker=object(),
        validator=object(),
    )

    assert captured["full_query"] == "canonical validation"
    assert captured["search_query"] == "canonical search"
    assert captured["classification"]["primary"] == "C4"
    assert bundle[1] == "canonical validation"
    assert bundle[2] == "canonical search"


def test_prepare_candidates_from_prepared_attaches_supplemental_quotas(monkeypatch):
    monkeypatch.setattr(
        match_core,
        "_prepare_candidates",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        match_core,
        "_build_support_surface_process_quotas",
        lambda item, searcher, reranker, classification: [
            {"quota_id": "C12-1-1", "name": "手工除锈 一般钢结构 轻锈"}
        ],
    )

    item = {
        "name": "管道支架",
        "description": "除锈后刷防锈漆二道，再刷灰色调和漆二道",
        "params": {"support_scope": "管道支架"},
    }
    prepared = {
        "ctx": {
            "full_query": "legacy validation",
            "search_query": "legacy search",
            "canonical_query": {
                "validation_query": "canonical validation",
                "search_query": "canonical search",
            },
            "item": item,
            "canonical_features": {"family": "pipe_support"},
            "context_prior": {"specialty": "C10"},
        },
        "classification": {"primary": "C10", "fallbacks": ["C12"], "search_books": ["C10", "C12"]},
        "exp_backup": None,
        "rule_backup": None,
    }

    match_core._prepare_candidates_from_prepared(
        prepared,
        searcher=object(),
        reranker=object(),
        validator=object(),
    )

    assert item["_supplemental_quotas"][0]["quota_id"] == "C12-1-1"


def test_cascade_search_strict_route_does_not_escape_to_full_library():
    calls = []

    class FakeSearcher:
        aux_searchers = []
        uses_standard_books = True

        def search(self, query, top_k=None, books=None):
            calls.append(list(books) if books is not None else None)
            return []

    match_core.cascade_search(
        FakeSearcher(),
        "普通PVC排水管 DN50",
        {
            "primary": "C10",
            "fallbacks": ["C9"],
            "search_books": ["C10", "C9"],
            "hard_book_constraints": ["C10", "C9"],
            "route_mode": "strict",
            "allow_cross_book_escape": False,
        },
        top_k=5,
    )

    assert calls == [["C10"], ["C10", "C9"]]
