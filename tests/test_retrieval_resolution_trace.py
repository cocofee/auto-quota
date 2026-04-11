from src import match_core
from src.match_pipeline import _build_search_result_from_candidates
from tools.classify_retriever_miss import extract_search_books


def test_cascade_search_records_main_resolved_books():
    calls = []
    classification = {
        "primary": "C10",
        "fallbacks": ["C9"],
        "search_books": ["C10", "C9"],
        "hard_book_constraints": ["C10", "C9"],
        "route_mode": "strict",
        "allow_cross_book_escape": False,
    }

    class FakeSearcher:
        aux_searchers = []
        uses_standard_books = True

        def search(self, query, top_k=None, books=None):
            del query, top_k
            calls.append(list(books) if books is not None else None)
            return []

    match_core.cascade_search(FakeSearcher(), "pipe dn50", classification, top_k=5)

    assert calls == [["C10"], ["C10", "C9"]]
    assert classification["retrieval_resolution"]["calls"] == [
        {
            "target": "main",
            "stage": "primary",
            "source_province": "",
            "requested_books": ["C10"],
            "resolved_books": ["C10"],
            "open_search": False,
            "uses_standard_books": True,
        },
        {
            "target": "main",
            "stage": "expanded",
            "source_province": "",
            "requested_books": ["C10", "C9"],
            "resolved_books": ["C10", "C9"],
            "open_search": False,
            "uses_standard_books": True,
        },
    ]


def test_cascade_search_records_aux_resolved_books_for_nonstandard_searcher():
    class FakeAux:
        province = "Aux Province"
        uses_standard_books = False

        class bm25_engine:
            quota_books = {1: "2", 2: "4"}

            @staticmethod
            def classify_to_books(_query, top_k=3):
                del top_k
                return ["4"]

        def search(self, query, top_k=None, books=None, item=None, context_prior=None):
            del query, top_k, item, context_prior
            assert books == ["4", "2"]
            return []

    class FakeSearcher:
        aux_searchers = [FakeAux()]
        uses_standard_books = True

        def search(self, query, top_k=None, books=None, item=None, context_prior=None):
            del query, top_k, books, item, context_prior
            return []

    classification = {
        "primary": "C2",
        "fallbacks": ["C8"],
        "search_books": ["C2", "C8"],
        "route_mode": "strict",
        "allow_cross_book_escape": False,
    }

    match_core.cascade_search(FakeSearcher(), "inverter", classification, top_k=5)

    aux_calls = [
        call
        for call in classification["retrieval_resolution"]["calls"]
        if call["target"] == "aux"
    ]
    assert aux_calls == [
        {
            "target": "aux",
            "stage": "aux",
            "source_province": "Aux Province",
            "requested_books": ["C2", "C8"],
            "resolved_books": ["4", "2"],
            "open_search": False,
            "uses_standard_books": False,
        }
    ]


def test_nonstandard_main_resolution_does_not_expand_outside_requested_projection():
    calls = []

    class FakeSearcher:
        aux_searchers = []
        province = "Numeric Install Province"
        uses_standard_books = False

        class bm25_engine:
            quota_books = {1: "5", 2: "10", 3: "12", 4: "13"}

            @staticmethod
            def classify_to_books(_query, top_k=3):
                del top_k
                return ["10", "13", "12"]

        def search(self, query, top_k=None, books=None, item=None, context_prior=None):
            del query, top_k, item, context_prior
            calls.append(list(books) if books is not None else None)
            if books == ["5"]:
                return [
                    {"quota_id": "5-1"},
                    {"quota_id": "5-2"},
                    {"quota_id": "5-3"},
                    {"quota_id": "5-4"},
                    {"quota_id": "5-5"},
                ]
            return []

    classification = {
        "primary": "C5",
        "search_books": ["C5", "C4", "C13", "C12"],
        "route_mode": "moderate",
        "allow_cross_book_escape": True,
    }

    match_core.cascade_search(FakeSearcher(), "smart meter", classification, top_k=5)

    assert calls == [["5"]]
    assert classification["retrieval_resolution"]["calls"][0] == {
        "target": "main",
        "stage": "primary",
        "source_province": "Numeric Install Province",
        "requested_books": ["C5"],
        "resolved_books": ["5"],
        "open_search": False,
        "uses_standard_books": False,
    }


def test_nonstandard_numeric_main_route_with_broad_a_stays_open_search(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "src.match_core.detect_db_type",
        lambda province: {"Numeric Province": "civil"}.get(province, ""),
    )

    class FakeSearcher:
        aux_searchers = []
        province = "Numeric Province"
        uses_standard_books = False

        class bm25_engine:
            quota_books = {1: "8", 2: "9", 3: "14"}

            @staticmethod
            def classify_to_books(_query, top_k=3):
                del top_k
                return ["14", "9", "8"]

        def search(self, query, top_k=None, books=None, item=None, context_prior=None):
            del query, top_k, item, context_prior
            calls.append(books)
            return []

    classification = {
        "primary": "A",
        "search_books": ["A"],
        "route_mode": "strict",
        "allow_cross_book_escape": False,
    }

    match_core.cascade_search(FakeSearcher(), "wall finish", classification, top_k=5)

    assert calls == [None]
    assert classification["retrieval_resolution"]["calls"] == [
        {
            "target": "main",
            "stage": "primary",
            "source_province": "Numeric Province",
            "requested_books": ["A"],
            "resolved_books": [],
            "open_search": True,
            "uses_standard_books": False,
        }
    ]


def test_nonstandard_prefixed_a_group_expands_without_open_search(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "src.match_core.detect_db_type",
        lambda province: {"Prefixed Province": "civil"}.get(province, ""),
    )

    class FakeSearcher:
        aux_searchers = []
        province = "Prefixed Province"
        uses_standard_books = False

        class bm25_engine:
            quota_books = {1: "A1", 2: "A2", 3: "A14"}

        def search(self, query, top_k=None, books=None, item=None, context_prior=None):
            del query, top_k, item, context_prior
            calls.append(books)
            return []

    classification = {
        "primary": "A",
        "search_books": ["A"],
        "route_mode": "strict",
        "allow_cross_book_escape": False,
    }

    match_core.cascade_search(FakeSearcher(), "civil item", classification, top_k=5)

    assert calls == [["A1", "A14", "A2"]]
    assert classification["retrieval_resolution"]["calls"][0]["resolved_books"] == ["A1", "A14", "A2"]


def test_build_search_result_trace_includes_retrieval_resolution():
    result = _build_search_result_from_candidates(
        {
            "name": "support",
            "description": "",
            "query_route": {"route": "installation_spec"},
            "classification": {
                "primary": "C4",
                "fallbacks": ["C10"],
                "candidate_books": ["C4", "C10"],
                "search_books": ["C4", "C10"],
                "hard_book_constraints": ["C4"],
                "route_mode": "strict",
                "retrieval_resolution": {
                    "calls": [
                        {
                            "target": "main",
                            "stage": "primary",
                            "requested_books": ["C4"],
                            "resolved_books": ["C4"],
                            "open_search": False,
                        }
                    ]
                },
            },
        },
        [
            {
                "quota_id": "C4-1-1",
                "name": "support install",
                "unit": "m",
                "param_match": True,
                "param_score": 0.95,
                "param_detail": "ok",
                "rerank_score": 0.88,
            }
        ],
    )

    trace_step = next(
        step for step in result["trace"]["steps"]
        if step.get("stage") == "search_select"
    )
    assert trace_step["retriever"]["search_resolution"]["calls"][0]["resolved_books"] == ["C4"]


def test_extract_search_books_uses_main_resolved_books_when_present():
    record = {
        "retriever": {
            "search_resolution": {
                "calls": [
                    {"target": "aux", "resolved_books": ["4"]},
                    {"target": "main", "resolved_books": ["03", "C10"]},
                ]
            }
        },
        "router": {
            "classification": {"search_books": ["C12"]},
            "unified_plan": {"primary_book": "C8"},
        },
    }

    books = extract_search_books(record)

    assert books == ["C3", "C10"]


def test_extract_search_books_treats_empty_main_resolution_as_open_search():
    record = {
        "retriever": {
            "search_resolution": {
                "calls": [
                    {"target": "main", "resolved_books": []},
                ]
            }
        },
        "router": {
            "classification": {"search_books": ["A"]},
            "unified_plan": {"primary_book": "C1"},
        },
    }

    books = extract_search_books(record)

    assert books == []
