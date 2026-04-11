from src.match_core import cascade_search


def test_cascade_search_defers_aux_when_main_results_are_good(monkeypatch):
    monkeypatch.setattr("config.HYBRID_DEFER_AUX_SEARCH", True, raising=False)

    class Searcher:
        def __init__(self, province: str, results: list[dict]):
            self.province = province
            self._results = list(results)
            self.aux_searchers = []
            self.uses_standard_books = True

        def search(self, query, top_k=None, books=None, item=None, context_prior=None):
            del query, top_k, books, item, context_prior
            return list(self._results)

    main = Searcher(
        "main-install",
        [
            {"quota_id": "C10-1-1", "name": "main-1", "hybrid_score": 0.95},
            {"quota_id": "C10-1-2", "name": "main-2", "hybrid_score": 0.90},
            {"quota_id": "C10-1-3", "name": "main-3", "hybrid_score": 0.80},
            {"quota_id": "C10-1-4", "name": "main-4", "hybrid_score": 0.79},
            {"quota_id": "C10-1-5", "name": "main-5", "hybrid_score": 0.78},
        ],
    )
    aux = Searcher(
        "aux-install",
        [
            {"quota_id": "C10-9-9", "name": "aux-1", "hybrid_score": 0.99},
        ],
    )
    main.aux_searchers = [aux]

    calls = []

    def _search_with_optional_context(searcher, *args, **kwargs):
        calls.append(searcher.province)
        return searcher.search(*args, **kwargs)

    monkeypatch.setattr("src.match_core._search_with_optional_context", _search_with_optional_context)

    results = cascade_search(
        main,
        "test query",
        {
            "primary": "C10",
            "search_books": ["C10"],
            "candidate_books": ["C10"],
            "fallbacks": [],
            "allow_cross_book_escape": True,
            "route_mode": "moderate",
        },
        top_k=3,
    )

    assert [row["quota_id"] for row in results] == [
        "C10-1-1",
        "C10-1-2",
        "C10-1-3",
        "C10-1-4",
        "C10-1-5",
    ]
    assert calls == ["main-install"]
