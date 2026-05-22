from src.match_core import (
    _collect_all_prior_candidates,
    _merge_with_aux,
    cascade_search,
)


def test_merge_with_aux_keeps_main_and_aux_results_without_name_error():
    result = _merge_with_aux(
        [{"quota_id": "M-1", "hybrid_score": 0.4}],
        [
            {"quota_id": "A-1", "hybrid_score": 0.8, "_source_province": "aux-a"},
            {"quota_id": "A-1", "hybrid_score": 0.7, "_source_province": "aux-a"},
            {"quota_id": "A-1", "hybrid_score": 0.9, "_source_province": "aux-b"},
        ],
        top_k=10,
    )

    assert [row["quota_id"] for row in result] == ["A-1", "A-1", "M-1"]
    assert [row["hybrid_score"] for row in result] == [0.9, 0.8, 0.4]


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


def test_cascade_search_stops_after_expanded_stage_without_escape(monkeypatch):
    monkeypatch.setattr("config.HYBRID_DEFER_AUX_SEARCH", True, raising=False)

    class Searcher:
        def __init__(self):
            self.province = "main-install"
            self.aux_searchers = []
            self.uses_standard_books = True
            self.calls = []

        def search(self, query, top_k=None, books=None, item=None, context_prior=None):
            del query, top_k, item, context_prior
            normalized_books = list(books) if books is not None else None
            self.calls.append(normalized_books)
            if normalized_books == ["C10"]:
                return [
                    {"quota_id": "C10-1-1", "hybrid_score": 0.82},
                    {"quota_id": "C10-1-2", "hybrid_score": 0.81},
                ]
            if normalized_books == ["C10", "C9"]:
                return [
                    {"quota_id": "C10-1-1", "hybrid_score": 0.95},
                    {"quota_id": "C10-1-2", "hybrid_score": 0.90},
                    {"quota_id": "C10-1-3", "hybrid_score": 0.80},
                    {"quota_id": "C10-1-4", "hybrid_score": 0.79},
                    {"quota_id": "C10-1-5", "hybrid_score": 0.78},
                ]
            raise AssertionError(f"unexpected escape search: {normalized_books}")

    searcher = Searcher()
    classification = {
        "primary": "C10",
        "search_books": ["C10", "C9"],
        "candidate_books": ["C10", "C9"],
        "fallbacks": ["C9"],
        "allow_cross_book_escape": True,
        "route_mode": "moderate",
    }

    results = cascade_search(searcher, "test query", classification, top_k=3)

    assert [row["quota_id"] for row in results] == [
        "C10-1-1",
        "C10-1-2",
        "C10-1-3",
        "C10-1-4",
        "C10-1-5",
    ]
    assert searcher.calls == [["C10"], ["C10", "C9"]]
    assert [call["stage"] for call in classification["retrieval_resolution"]["calls"]] == [
        "primary",
        "expanded",
    ]


def test_cascade_search_skips_aux_when_standard_main_pool_is_saturated(monkeypatch):
    monkeypatch.setattr("config.HYBRID_DEFER_AUX_SEARCH", True, raising=False)

    class Searcher:
        def __init__(self, province: str, *, is_aux: bool = False):
            self.province = province
            self.aux_searchers = []
            self.uses_standard_books = True
            self.is_aux = is_aux
            self.calls = []

        def search(self, query, top_k=None, books=None, item=None, context_prior=None):
            del query, item, context_prior
            normalized_books = list(books) if books is not None else None
            self.calls.append(normalized_books)
            if self.is_aux:
                raise AssertionError("aux search should be deferred")
            if normalized_books == ["C4"]:
                return [
                    {"quota_id": f"C4-12-{idx}", "name": f"main-{idx}", "hybrid_score": 0.20}
                    for idx in range(1, 3)
                ]
            if normalized_books == ["C4", "C5"]:
                return [
                    {"quota_id": f"C4-13-{idx}", "name": f"expanded-{idx}", "hybrid_score": 0.19}
                    for idx in range(1, 6)
                ]
            return []

    main = Searcher("main-install")
    aux = Searcher("aux-install", is_aux=True)
    main.aux_searchers = [aux]
    classification = {
        "primary": "C4",
        "search_books": ["C4", "C5"],
        "candidate_books": ["C4", "C5"],
        "fallbacks": ["C5"],
        "allow_cross_book_escape": False,
        "route_mode": "moderate",
    }

    results = cascade_search(main, "low score but saturated main pool", classification, top_k=4)

    assert len(results) == 4
    assert main.calls == [["C4"], ["C4", "C5"]]
    assert aux.calls == []
    assert [call["stage"] for call in classification["retrieval_resolution"]["calls"]] == [
        "primary",
        "expanded",
        "aux_budget_deferred",
    ]
    assert (
        classification["retrieval_resolution"]["aux_budget_reason"]
        == "standard_main_pool_saturated"
    )


def test_cascade_search_skips_aux_when_deep_main_pool_reaches_budget(monkeypatch):
    monkeypatch.setattr("config.HYBRID_DEFER_AUX_SEARCH", True, raising=False)

    class Searcher:
        def __init__(self, province: str, *, is_aux: bool = False):
            self.province = province
            self.aux_searchers = []
            self.uses_standard_books = True
            self.is_aux = is_aux
            self.calls = []

        def search(self, query, top_k=None, books=None, item=None, context_prior=None):
            del query, top_k, item, context_prior
            normalized_books = list(books) if books is not None else None
            self.calls.append(normalized_books)
            if self.is_aux:
                raise AssertionError("deep aux search should be budget-deferred")
            if normalized_books == ["C4"]:
                return [
                    {"quota_id": f"C4-12-{idx}", "name": f"main-{idx}", "hybrid_score": 0.20}
                    for idx in range(1, 5)
                ]
            if normalized_books == ["C4", "C5"]:
                return [
                    {"quota_id": f"C4-13-{idx}", "name": f"expanded-{idx}", "hybrid_score": 0.19}
                    for idx in range(1, 8)
                ]
            return []

    main = Searcher("main-install")
    aux = Searcher("aux-install", is_aux=True)
    main.aux_searchers = [aux]
    classification = {
        "primary": "C4",
        "search_books": ["C4", "C5"],
        "candidate_books": ["C4", "C5"],
        "fallbacks": ["C5"],
        "allow_cross_book_escape": False,
        "route_mode": "moderate",
    }

    results = cascade_search(
        main,
        "low score but deep pool is saturated",
        classification,
        top_k=4,
        adaptive_strategy="deep",
    )

    assert len(results) == 4
    assert main.calls == [["C4"], ["C4", "C5"]]
    assert aux.calls == []
    assert [call["stage"] for call in classification["retrieval_resolution"]["calls"]] == [
        "primary",
        "expanded",
        "aux_budget_deferred",
    ]
    assert (
        classification["retrieval_resolution"]["aux_budget_reason"]
        == "deep_main_pool_saturated"
    )


def test_cascade_search_keeps_aux_when_standard_main_pool_is_insufficient(monkeypatch):
    monkeypatch.setattr("config.HYBRID_DEFER_AUX_SEARCH", True, raising=False)

    class Searcher:
        def __init__(self, province: str, results: list[dict]):
            self.province = province
            self._results = list(results)
            self.aux_searchers = []
            self.uses_standard_books = True
            self.calls = []

        def search(self, query, top_k=None, books=None, item=None, context_prior=None):
            del query, top_k, item, context_prior
            self.calls.append(list(books) if books is not None else None)
            return list(self._results)

    main = Searcher(
        "main-install",
        [
            {"quota_id": "C4-12-1", "name": "main-1", "hybrid_score": 0.20},
            {"quota_id": "C4-12-2", "name": "main-2", "hybrid_score": 0.19},
        ],
    )
    aux = Searcher(
        "aux-install",
        [{"quota_id": "C4-99-1", "name": "aux-1", "hybrid_score": 0.90}],
    )
    main.aux_searchers = [aux]
    classification = {
        "primary": "C4",
        "search_books": ["C4"],
        "candidate_books": ["C4"],
        "fallbacks": [],
        "allow_cross_book_escape": False,
        "route_mode": "moderate",
    }

    results = cascade_search(main, "insufficient main pool", classification, top_k=4)

    assert [row["quota_id"] for row in results] == ["C4-99-1", "C4-12-1", "C4-12-2"]
    assert aux.calls == [["C4"]]
    assert [call["stage"] for call in classification["retrieval_resolution"]["calls"]] == [
        "primary",
        "aux",
    ]


def test_collect_prior_candidates_skips_aux_when_standard_main_pool_is_sufficient():
    calls = []

    class Searcher:
        def __init__(self, province: str):
            self.province = province
            self.aux_searchers = []
            self.uses_standard_books = True

        def collect_prior_candidates(self, query, **kwargs):
            calls.append((self.province, query, kwargs.get("exact_only")))
            return [{"quota_id": f"{self.province}-1", "hybrid_score": 0.9}]

    main = Searcher("main")
    aux = Searcher("aux")
    main.aux_searchers = [aux]

    priors = _collect_all_prior_candidates(
        main,
        search_query="query",
        full_query="query",
        classification={"search_books": ["C4"], "primary": "C4"},
        item={},
        existing_candidates=[
            {"quota_id": f"C4-12-{idx}", "hybrid_score": 0.5}
            for idx in range(1, 6)
        ],
        adaptive_strategy="standard",
    )

    assert [row["quota_id"] for row in priors] == ["main-1"]
    assert calls == [("main", "query", False)]
