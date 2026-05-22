from src.hybrid_searcher import HybridSearcher
from src.match_core import cascade_search


def test_build_query_variants_caps_deep_variant_count(monkeypatch):
    monkeypatch.setattr("config.HYBRID_QUERY_VARIANTS", 4, raising=False)
    monkeypatch.setattr("config.HYBRID_DEEP_QUERY_VARIANTS", 3, raising=False)
    searcher = HybridSearcher.__new__(HybridSearcher)

    variants = searcher._build_query_variants(
        "pipe dn200 grooved connection",
        ["pipe install"],
        query_features={"family": "pipe_run", "numeric_params": {"dn": 200}},
        route_profile={"route": "installation_spec", "spec_signal_count": 2},
        primary_query_profile={
            "primary_text": "pipe dn200",
            "primary_subject": "pipe",
            "quota_aliases": ["pipe install", "grooved pipe install"],
        },
        adaptive_strategy="deep",
    )

    assert len(variants) == 3


def test_resolve_rank_window_caps_deep_installation_queries(monkeypatch):
    monkeypatch.setattr("config.HYBRID_DEEP_RANK_WINDOW_CAP", 72, raising=False)
    searcher = HybridSearcher.__new__(HybridSearcher)
    searcher._FAMILY_WINDOW_FAMILIES = {"cable_family"}

    window = searcher._resolve_rank_window(
        top_k=37,
        query_features={"family": "cable_family"},
        route_profile={"route": "installation_spec", "spec_signal_count": 2},
        adaptive_strategy="deep",
    )

    assert window == 72


def test_standard_search_budget_uses_lower_variant_and_window_caps(monkeypatch):
    monkeypatch.setattr("config.HYBRID_QUERY_VARIANTS", 4, raising=False)
    monkeypatch.setattr("config.HYBRID_STANDARD_QUERY_VARIANTS", 2, raising=False)
    monkeypatch.setattr("config.HYBRID_STANDARD_RANK_WINDOW_CAP", 24, raising=False)
    searcher = HybridSearcher.__new__(HybridSearcher)
    searcher._FAMILY_WINDOW_FAMILIES = {"cable_family"}

    variants = searcher._build_query_variants(
        "cable tray install 100x50",
        ["cable tray"],
        query_features={"family": "cable_family", "numeric_params": {}},
        route_profile={"route": "installation_spec", "spec_signal_count": 2},
        primary_query_profile={"primary_text": "cable tray", "primary_subject": "tray"},
        adaptive_strategy="standard",
    )
    window = searcher._resolve_rank_window(
        top_k=25,
        query_features={"family": "cable_family"},
        route_profile={"route": "installation_spec", "spec_signal_count": 2},
        adaptive_strategy="standard",
    )

    assert len(variants) == 2
    assert window == 24


class _CascadeBudgetSearcher:
    province = "test"
    uses_standard_books = True
    aux_searchers = []

    def __init__(self):
        self.calls = []

    def search(self, query, top_k=None, books=None, item=None, context_prior=None):
        self.calls.append({"query": query, "top_k": top_k, "books": books})
        prefix = "open" if books is None else "-".join(books)
        count = 12 if books and len(books) > 1 else 6
        return [
            {
                "quota_id": f"{prefix}-{idx}",
                "name": f"{prefix} candidate {idx}",
                "hybrid_score": 0.01,
            }
            for idx in range(count)
        ]


def test_deep_cascade_defers_escape_when_expanded_pool_is_saturated(monkeypatch):
    monkeypatch.setattr("src.match_core.CASCADE_MIN_CANDIDATES", 5)
    searcher = _CascadeBudgetSearcher()
    classification = {
        "primary": "C4",
        "fallbacks": ["C5"],
        "search_books": ["C4", "C5"],
        "route_mode": "open",
        "allow_cross_book_escape": True,
    }

    results = cascade_search(
        searcher,
        "fixture query",
        classification,
        top_k=10,
        adaptive_strategy="deep",
    )

    assert [call["books"] for call in searcher.calls] == [["C4"], ["C4", "C5"]]
    assert all(call["books"] is not None for call in searcher.calls)
    assert len(results) >= 10
    resolution = classification["retrieval_resolution"]
    assert any(
        call["stage"] == "escape_budget_deferred"
        for call in resolution["calls"]
    )
    assert resolution["escape_budget_reason"] == "deep_escape_candidate_pool_saturated"
