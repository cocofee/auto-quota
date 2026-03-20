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
            {
                "quota_id": "C4-1-1",
                "name": "\u7535\u7f06\u6865\u67b6\u652f\u67b6\u5236\u4f5c\u5b89\u88c5",
                "hybrid_score": 0.8,
            }
        ],
    )

    match_core._prepare_candidates(
        FakeSearcher(),
        FakeReranker(),
        FakeValidator(),
        "\u652f\u67b6 \u6865\u67b6",
        "\u652f\u67b6",
        {"primary": "C4", "fallbacks": [], "search_books": ["C4"]},
        bill_params={},
        canonical_features={"entity": "\u652f\u67b6", "system": "\u7535\u6c14"},
        context_prior={"specialty": "C4", "context_hints": ["\u6865\u67b6"]},
    )

    assert captured["query_text"] == "\u652f\u67b6"
    assert captured["supplement_query"] == "\u652f\u67b6 \u6865\u67b6"
    assert captured["bill_params"] == {}
    assert captured["canonical_features"]["system"] == "\u7535\u6c14"
    assert captured["context_prior"]["specialty"] == "C4"


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
            "canonical_features": {"entity": "\u914d\u7ebf"},
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
