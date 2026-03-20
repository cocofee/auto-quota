from src.match_pipeline import _build_item_context


def test_build_item_context_keeps_plugin_aliases_out_of_validation_query(monkeypatch):
    monkeypatch.setattr(
        "src.match_pipeline.resolve_plugin_hints",
        lambda **kwargs: {
            "matched_terms": ["\u914d\u7535\u7bb1"],
            "synonym_aliases": ["\u6210\u5957\u914d\u7535\u7bb1\u5b89\u88c5"],
            "preferred_specialties": ["C4"],
            "preferred_books": ["C4"],
        },
    )

    context = _build_item_context({
        "name": "\u914d\u7535\u7bb1",
        "description": "1AP1",
        "specialty": "C4",
        "section": "\u7535\u6c14\u5de5\u7a0b",
        "context_prior": {},
        "canonical_features": {
            "canonical_name": "\u914d\u7535\u7bb1",
            "entity": "\u914d\u7535\u7bb1",
        },
    })

    assert "\u6210\u5957\u914d\u7535\u7bb1\u5b89\u88c5" in context["search_query"]
    assert "\u6210\u5957\u914d\u7535\u7bb1\u5b89\u88c5" not in context["full_query"]
    assert context["canonical_query"]["search_query"] == context["search_query"]
    assert context["canonical_query"]["validation_query"] == context["full_query"]
    assert "\u6210\u5957\u914d\u7535\u7bb1\u5b89\u88c5" not in context["canonical_query"]["validation_query"]
    assert context["plugin_hints"]["matched_terms"] == ["\u914d\u7535\u7bb1"]
    assert "C4" in context["context_prior"]["context_hints"]
