import config
from src import hybrid_searcher as hybrid_searcher_module
from src.hybrid_searcher import HybridSearcher


class _FakeBM25Engine:
    def __init__(self):
        self.calls = []
        self.quota_books = {}

    def search(self, query, top_k=None, books=None):
        self.calls.append({"query": query, "top_k": top_k, "books": books})
        return [{
            "quota_id": "Q-1",
            "name": "candidate",
            "id": "bm25",
            "engine_top_k": top_k,
            "bm25_rank": 1,
        }]


class _FakeVectorEngine:
    def __init__(self):
        self.calls = []

    @staticmethod
    def encode_queries(queries):
        return [[0.0] for _ in queries]

    def search(self, query, top_k=None, books=None, precomputed_embedding=None):
        self.calls.append({
            "query": query,
            "top_k": top_k,
            "books": books,
            "precomputed_embedding": precomputed_embedding,
        })
        return [{
            "quota_id": "Q-1",
            "name": "candidate",
            "id": "vector",
            "engine_top_k": top_k,
            "vector_rank": 1,
        }]


def _make_searcher():
    searcher = HybridSearcher.__new__(HybridSearcher)
    searcher.province = "test"
    searcher._experience_db = None
    searcher._unified_data_layer = None
    searcher._bm25_engine = _FakeBM25Engine()
    searcher._vector_engine = _FakeVectorEngine()
    searcher._universal_kb = False
    searcher._kb_keyword_cache = {}
    searcher._KB_KEYWORD_CACHE_MAX = 256
    searcher._kb_keyword_blocked_until = 0.0
    searcher._session_cache = {}
    searcher._SESSION_CACHE_MAX = 1000
    searcher._uses_standard_books = True
    return searcher


def test_search_session_cache_key_separates_adaptive_strategy(monkeypatch):
    searcher = _make_searcher()

    monkeypatch.setattr(config, "VECTOR_ENABLED", True)
    monkeypatch.setattr(
        hybrid_searcher_module.text_parser,
        "parse_canonical",
        lambda query: {"family": "test"},
    )
    monkeypatch.setattr(
        hybrid_searcher_module,
        "build_query_route_profile",
        lambda query, canonical_features=None, context_prior=None: {"route": "test"},
    )

    searcher._resolve_rank_window = lambda **kwargs: 2 if kwargs["adaptive_strategy"] == "standard" else 5
    searcher._resolve_engine_top_k = lambda **kwargs: kwargs["rank_window"]
    searcher._get_adaptive_weights = lambda **kwargs: (0.3, 0.7, "balanced")
    searcher._build_query_variants = lambda *args, **kwargs: [{"query": args[0], "tag": "raw", "weight": 1.0}]
    searcher._rrf_fusion = lambda bm25_results, vector_results, bm25_weight, vector_weight, k: [{
        "quota_id": "Q-1",
        "name": "candidate",
        "id": "Q-1",
        "hybrid_score": float(bm25_results[0]["engine_top_k"]),
        "bm25_rank": 1,
        "vector_rank": 1,
    }]
    searcher._finalize_candidates = lambda candidates, query_text, expected_books=None: list(candidates)

    standard = searcher.search("same query", top_k=3, item={"adaptive_strategy": "standard"})
    deep = searcher.search("same query", top_k=3, item={"adaptive_strategy": "deep"})

    assert standard[0]["hybrid_score"] == 2.0
    assert deep[0]["hybrid_score"] == 5.0
    assert [call["top_k"] for call in searcher.bm25_engine.calls] == [2, 5]
    assert [call["top_k"] for call in searcher.vector_engine.calls] == [2, 5]
    assert len(searcher._session_cache) == 2


def test_search_session_cache_key_separates_effective_weights(monkeypatch):
    searcher = _make_searcher()

    monkeypatch.setattr(config, "VECTOR_ENABLED", True)
    monkeypatch.setattr(
        hybrid_searcher_module.text_parser,
        "parse_canonical",
        lambda query: {"family": "test"},
    )
    monkeypatch.setattr(
        hybrid_searcher_module,
        "build_query_route_profile",
        lambda query, canonical_features=None, context_prior=None: {"route": "test"},
    )

    searcher._resolve_rank_window = lambda **kwargs: 3
    searcher._resolve_engine_top_k = lambda **kwargs: kwargs["rank_window"]
    searcher._get_adaptive_weights = (
        lambda query, bm25_weight, vector_weight: (bm25_weight, vector_weight, "explicit")
    )
    searcher._build_query_variants = lambda *args, **kwargs: [{"query": args[0], "tag": "raw", "weight": 1.0}]
    searcher._rrf_fusion = lambda bm25_results, vector_results, bm25_weight, vector_weight, k: [{
        "quota_id": "Q-1",
        "name": "candidate",
        "id": "Q-1",
        "hybrid_score": float(bm25_weight),
        "bm25_rank": 1,
        "vector_rank": 1,
    }]
    searcher._finalize_candidates = lambda candidates, query_text, expected_books=None: list(candidates)

    light_bm25 = searcher.search("same query", top_k=3, bm25_weight=0.2, vector_weight=0.8)
    heavy_bm25 = searcher.search("same query", top_k=3, bm25_weight=0.8, vector_weight=0.2)

    assert light_bm25[0]["hybrid_score"] == 0.2
    assert heavy_bm25[0]["hybrid_score"] == 0.8
    assert len(searcher.bm25_engine.calls) == 2
    assert len(searcher.vector_engine.calls) == 2
    assert len(searcher._session_cache) == 2


def test_search_session_cache_stores_prefinalized_rrf_results(monkeypatch):
    searcher = _make_searcher()

    monkeypatch.setattr(config, "VECTOR_ENABLED", True)
    monkeypatch.setattr(
        hybrid_searcher_module.text_parser,
        "parse_canonical",
        lambda query: {"family": "test"},
    )
    monkeypatch.setattr(
        hybrid_searcher_module,
        "build_query_route_profile",
        lambda query, canonical_features=None, context_prior=None: {"route": "test"},
    )

    searcher._resolve_rank_window = lambda **kwargs: 2
    searcher._resolve_engine_top_k = lambda **kwargs: kwargs["rank_window"]
    searcher._get_adaptive_weights = lambda **kwargs: (0.5, 0.5, "balanced")
    searcher._build_query_variants = lambda *args, **kwargs: [{"query": args[0], "tag": "raw", "weight": 1.0}]
    searcher._rrf_fusion = lambda bm25_results, vector_results, bm25_weight, vector_weight, k: [
        {
            "quota_id": "Q-1",
            "name": "candidate-1",
            "id": "Q-1",
            "hybrid_score": 2.0,
            "bm25_rank": 1,
            "vector_rank": 2,
        },
        {
            "quota_id": "Q-2",
            "name": "candidate-2",
            "id": "Q-2",
            "hybrid_score": 1.0,
            "bm25_rank": 2,
            "vector_rank": 1,
        },
    ]

    finalize_inputs = []

    def _finalize(candidates, query_text, expected_books=None):
        finalize_inputs.append([candidate["quota_id"] for candidate in candidates])
        candidates.sort(key=lambda candidate: candidate["quota_id"], reverse=True)
        return candidates

    searcher._finalize_candidates = _finalize

    first = searcher.search("same query", top_k=2)
    second = searcher.search("same query", top_k=2)

    assert [candidate["quota_id"] for candidate in first] == ["Q-2", "Q-1"]
    assert [candidate["quota_id"] for candidate in second] == ["Q-2", "Q-1"]
    assert finalize_inputs == [["Q-1", "Q-2"], ["Q-1", "Q-2"]]
    assert len(searcher.bm25_engine.calls) == 1
    assert len(searcher.vector_engine.calls) == 1
    assert len(searcher._session_cache) == 1
    cached = next(iter(searcher._session_cache.values()))
    assert [candidate["quota_id"] for candidate in cached] == ["Q-1", "Q-2"]
