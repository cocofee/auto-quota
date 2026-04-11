from types import SimpleNamespace

from src.match_core import (
    cascade_search,
    _collect_all_prior_candidates,
    _filter_candidates_to_effective_guard_scope,
    _filter_candidates_to_route_scope,
    _should_search_target_for_books,
)


def test_should_search_target_for_books_limits_broad_a_to_civil_db(monkeypatch):
    monkeypatch.setattr(
        "src.match_core.detect_db_type",
        lambda province: {
            "main-install": "install",
            "aux-civil": "civil",
        }.get(province, ""),
    )
    install_target = SimpleNamespace(province="main-install")
    civil_target = SimpleNamespace(province="aux-civil")

    assert _should_search_target_for_books(civil_target, ["A"]) is True
    assert _should_search_target_for_books(install_target, ["A"]) is False


def test_should_search_target_for_books_keeps_standard_books_unfiltered(monkeypatch):
    monkeypatch.setattr("src.match_core.detect_db_type", lambda province: "install")
    install_target = SimpleNamespace(province="main-install")
    civil_target = SimpleNamespace(province="aux-civil")

    assert _should_search_target_for_books(install_target, ["C10"]) is True
    assert _should_search_target_for_books(civil_target, ["C10"]) is True


def test_collect_all_prior_candidates_respects_broad_route_scope(monkeypatch):
    monkeypatch.setattr(
        "src.match_core.detect_db_type",
        lambda province: {
            "main-install": "install",
            "aux-civil": "civil",
        }.get(province, ""),
    )
    calls = []

    class Searcher:
        def __init__(self, province: str):
            self.province = province
            self.aux_searchers = []

        def collect_prior_candidates(
            self, query, *, full_query="", books=None, item=None, exact_only=False
        ):
            calls.append(
                {
                    "province": self.province,
                    "books": list(books or []),
                    "exact_only": exact_only,
                }
            )
            return [
                {
                    "quota_id": f"{self.province}-Q",
                    "name": f"{self.province}-Candidate",
                    "knowledge_prior_sources": ["experience"],
                    "knowledge_prior_score": 1.1,
                    "match_source": "experience_injected_exact",
                }
            ]

    main = Searcher("main-install")
    civil = Searcher("aux-civil")
    main.aux_searchers = [civil]

    rows = _collect_all_prior_candidates(
        main,
        search_query="stone gate",
        full_query="stone gate",
        classification={"search_books": ["A"], "candidate_books": ["A"], "primary": "A"},
        item={"name": "stone gate"},
    )

    assert len(rows) == 1
    assert rows[0]["quota_id"] == "aux-civil-Q"
    assert calls == [
        {
            "province": "aux-civil",
            "books": ["A"],
            "exact_only": True,
        }
    ]


def test_filter_candidates_to_route_scope_keeps_only_allowed_books_when_strict():
    candidates = [
        {"quota_id": "C10-1-1", "name": "right book"},
        {"quota_id": "C8-8-2", "name": "wrong book"},
        {"quota_id": "M9-1", "name": "wrong aux book"},
    ]

    filtered, meta = _filter_candidates_to_route_scope(
        candidates,
        {
            "search_books": ["C10"],
            "hard_search_books": ["C10"],
            "allow_cross_book_escape": False,
            "route_mode": "strict",
        },
    )

    assert [candidate["quota_id"] for candidate in filtered] == ["C10-1-1"]
    assert meta["applied"] is True
    assert meta["allowed_books"] == ["C10"]
    assert meta["dropped_quota_ids"] == ["C8-8-2", "M9-1"]


def test_filter_candidates_to_route_scope_skips_when_cross_book_escape_allowed():
    candidates = [
        {"quota_id": "C10-1-1", "name": "right book"},
        {"quota_id": "C8-8-2", "name": "wrong book"},
    ]

    filtered, meta = _filter_candidates_to_route_scope(
        candidates,
        {
            "search_books": ["C10"],
            "allow_cross_book_escape": True,
            "route_mode": "moderate",
        },
    )

    assert [candidate["quota_id"] for candidate in filtered] == ["C10-1-1", "C8-8-2"]
    assert meta["applied"] is False


def test_filter_candidates_to_route_scope_uses_resolved_main_books_from_retriever():
    candidates = [
        {"quota_id": "03-1-1-1", "name": "allowed"},
        {"quota_id": "01-1-1-1", "name": "filtered"},
    ]

    filtered, meta = _filter_candidates_to_route_scope(
        candidates,
        {
            "search_books": ["A"],
            "allow_cross_book_escape": False,
            "route_mode": "strict",
            "retrieval_resolution": {
                "calls": [
                    {"target": "main", "resolved_books": ["03"]},
                ]
            },
        },
    )

    assert [candidate["quota_id"] for candidate in filtered] == ["03-1-1-1"]
    assert meta["allowed_books"] == ["C3"]


def test_filter_candidates_to_route_scope_uses_candidate_book_for_numeric_quota_ids():
    candidates = [
        {"quota_id": "50106095", "book": "05", "name": "stone sculpture install"},
        {"quota_id": "50106096", "book": "03", "name": "flange valve install"},
    ]

    filtered, meta = _filter_candidates_to_route_scope(
        candidates,
        {
            "search_books": ["C10"],
            "hard_search_books": ["C10"],
            "allow_cross_book_escape": False,
            "route_mode": "strict",
        },
    )

    assert [candidate["quota_id"] for candidate in filtered] == ["50106096"]
    assert meta["applied"] is True
    assert meta["dropped_quota_ids"] == ["50106095"]


def test_effective_guard_scope_drops_candidates_outside_router_and_resolved_main():
    candidates = [
        {"quota_id": "C10-1-1", "name": "keep router scope"},
        {"quota_id": "C5-1-1", "name": "keep resolved main"},
        {"quota_id": "C13-1-1", "name": "drop advisory only"},
        {"quota_id": "C3-1-1", "name": "drop leaked"},
    ]

    filtered, meta = _filter_candidates_to_effective_guard_scope(
        candidates,
        {
            "search_books": ["C10", "C13"],
            "allow_cross_book_escape": True,
            "route_mode": "moderate",
            "retrieval_resolution": {
                "calls": [
                    {"target": "main", "stage": "primary", "resolved_books": ["C10", "C5"]},
                ]
            },
        },
    )

    assert [candidate["quota_id"] for candidate in filtered] == ["C10-1-1", "C5-1-1"]
    assert meta["applied"] is True
    assert meta["allowed_books"] == ["C10", "C5"]
    assert meta["dropped_quota_ids"] == ["C13-1-1", "C3-1-1"]


def test_effective_guard_scope_skips_when_main_used_open_search():
    candidates = [
        {"quota_id": "C10-1-1", "name": "keep"},
        {"quota_id": "C3-1-1", "name": "also keep"},
    ]

    filtered, meta = _filter_candidates_to_effective_guard_scope(
        candidates,
        {
            "search_books": ["C10"],
            "allow_cross_book_escape": True,
            "route_mode": "moderate",
            "retrieval_resolution": {
                "calls": [
                    {"target": "main", "stage": "escape", "resolved_books": []},
                ]
            },
        },
    )

    assert [candidate["quota_id"] for candidate in filtered] == ["C10-1-1", "C3-1-1"]
    assert meta["applied"] is False


def test_cascade_search_skips_unresolved_non_broad_requested_books_without_open_search():
    calls = []

    class FakeSearcher:
        aux_searchers = []
        province = "Numeric Install Province"
        uses_standard_books = False

        class bm25_engine:
            quota_books = {}

            @staticmethod
            def classify_to_books(_query, top_k=3):
                del top_k
                return []

        def search(self, query, top_k=None, books=None, item=None, context_prior=None):
            del query, top_k, books, item, context_prior
            calls.append("called")
            return []

    classification = {
        "primary": "C10",
        "search_books": ["C10"],
        "route_mode": "strict",
        "allow_cross_book_escape": False,
    }

    cascade_search(FakeSearcher(), "flow switch", classification, top_k=5)

    assert calls == []
    assert classification["retrieval_resolution"]["calls"] == [
        {
            "target": "main",
            "stage": "primary",
            "source_province": "Numeric Install Province",
            "requested_books": ["C10"],
            "resolved_books": [],
            "open_search": False,
            "uses_standard_books": False,
        }
    ]
