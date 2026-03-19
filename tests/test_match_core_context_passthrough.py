# -*- coding: utf-8 -*-

from src import match_core


def test_prepare_candidates_passes_context_into_validator(monkeypatch):
    captured = {}

    class FakeSearcher:
        pass

    class FakeReranker:
        def rerank(self, query, candidates):
            return candidates

    class FakeValidator:
        def validate_candidates(self, query_text, candidates, supplement_query=None,
                                bill_params=None, search_books=None,
                                canonical_features=None, context_prior=None):
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
            {"quota_id": "C4-1-1", "name": "电缆桥架支架制作安装", "hybrid_score": 0.8}
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
