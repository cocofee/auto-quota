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
    searcher._KB_KEYWORD_CACHE_TTL_SEC = 300.0
    searcher._kb_keyword_blocked_until = 0.0
    searcher._session_cache = {}
    searcher._SESSION_CACHE_MAX = 1000
    searcher._SESSION_CACHE_TTL_SEC = 900.0
    searcher._uses_standard_books = True
    return searcher


def test_materialize_quota_candidate_uses_session_cache(monkeypatch):
    searcher = _make_searcher()
    calls = []

    def fake_search_by_id(quota_id, province=None, conn=None):
        calls.append((quota_id, province, conn))
        return quota_id, f"name {quota_id}", "m"

    monkeypatch.setattr(hybrid_searcher_module, "search_by_id", fake_search_by_id)
    monkeypatch.setattr(searcher, "_quota_lookup_connection", lambda: "conn")
    monkeypatch.setattr(
        hybrid_searcher_module.text_parser,
        "parse_canonical",
        lambda text, specialty="": {"canonical_name": text, "specialty": specialty},
    )

    first = searcher._materialize_quota_candidate("C4-1-1")
    first["name"] = "mutated"
    second = searcher._materialize_quota_candidate("C4-1-1")

    assert calls == [("C4-1-1", "test", "conn")]
    assert second["name"] == "name C4-1-1"
    assert second["candidate_canonical_features"]["specialty"] == "C4"


def test_materialize_quota_candidate_does_not_cache_missing_without_fallback(monkeypatch):
    searcher = _make_searcher()
    calls = []

    def fake_search_by_id(quota_id, province=None, conn=None):
        calls.append((quota_id, province, conn))
        return None

    monkeypatch.setattr(hybrid_searcher_module, "search_by_id", fake_search_by_id)
    monkeypatch.setattr(searcher, "_quota_lookup_connection", lambda: "conn")
    monkeypatch.setattr(
        hybrid_searcher_module.text_parser,
        "parse_canonical",
        lambda text, specialty="": {"canonical_name": text, "specialty": specialty},
    )

    missing = searcher._materialize_quota_candidate("C4-1-404")
    fallback = searcher._materialize_quota_candidate("C4-1-404", fallback_name="fallback name")

    assert missing is None
    assert fallback["name"] == "fallback name"
    assert calls == [("C4-1-404", "test", "conn"), ("C4-1-404", "test", "conn")]


def test_unified_prior_does_not_auto_create_experience_db_without_injection(monkeypatch):
    from src import unified_data_layer as unified_data_layer_module

    searcher = _make_searcher()
    monkeypatch.setattr(config, "SEARCH_UNIFIED_DATA_PRIOR_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "SEARCH_UNIFIED_DATA_PRIOR_REQUIRES_EXPERIENCE", True, raising=False)
    init_experience_dbs = []

    class _FakeUnifiedDataLayer:
        def __init__(self, province=None, experience_db=None):
            self.province = province
            self.experience_db = experience_db
            self.sources = []
            init_experience_dbs.append(experience_db)

        def search(self, payload, sources=None, **kwargs):
            self.sources.append(list(sources or []))
            return {"grouped": {"experience": [], "universal_kb": [], "quota": []}}

    monkeypatch.setattr(unified_data_layer_module, "UnifiedDataLayer", _FakeUnifiedDataLayer)

    assert searcher._experience_db is None
    assert searcher._collect_unified_data_prior_candidates(query_text="test query") == []
    assert isinstance(init_experience_dbs[0], hybrid_searcher_module._NoExperienceDB)
    assert searcher._unified_data_layer.sources == [["universal_kb", "quota"]]


def test_unified_prior_omits_experience_source_without_experience_db(monkeypatch):
    searcher = _make_searcher()
    monkeypatch.setattr(config, "SEARCH_UNIFIED_DATA_PRIOR_REQUIRES_EXPERIENCE", False, raising=False)

    class _FakeUnifiedDataLayer:
        def __init__(self):
            self.sources = []

        def search(self, payload, sources=None, **kwargs):
            self.sources.append(list(sources or []))
            return {"grouped": {"experience": [], "universal_kb": [], "quota": []}}

    unified = _FakeUnifiedDataLayer()
    searcher._unified_data_layer = unified

    assert searcher._collect_unified_data_prior_candidates(query_text="test query") == []
    assert unified.sources == [["universal_kb", "quota"]]


def _patch_basic_search_flow(monkeypatch, searcher):
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
    searcher._finalize_candidates = lambda candidates, query_text, expected_books=None: list(candidates)


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
    cached = next(iter(searcher._session_cache.values()))["value"]
    assert [candidate["quota_id"] for candidate in cached] == ["Q-1", "Q-2"]


def test_search_session_cache_stores_no_result_terminal_state(monkeypatch):
    searcher = _make_searcher()
    _patch_basic_search_flow(monkeypatch, searcher)

    def _empty_bm25(query, top_k=None, books=None):
        searcher.bm25_engine.calls.append({"query": query, "top_k": top_k, "books": books})
        return []

    def _empty_vector(query, top_k=None, books=None, precomputed_embedding=None):
        searcher.vector_engine.calls.append({
            "query": query,
            "top_k": top_k,
            "books": books,
            "precomputed_embedding": precomputed_embedding,
        })
        return []

    searcher.bm25_engine.search = _empty_bm25
    searcher.vector_engine.search = _empty_vector

    first = searcher.search("no result query", top_k=2)
    second = searcher.search("no result query", top_k=2)

    assert first == []
    assert second == []
    assert len(searcher.bm25_engine.calls) == 1
    assert len(searcher.vector_engine.calls) == 1
    assert next(iter(searcher._session_cache.values()))["value"] == []


def test_search_session_cache_stores_single_engine_results(monkeypatch):
    searcher = _make_searcher()
    _patch_basic_search_flow(monkeypatch, searcher)

    def _empty_vector(query, top_k=None, books=None, precomputed_embedding=None):
        searcher.vector_engine.calls.append({
            "query": query,
            "top_k": top_k,
            "books": books,
            "precomputed_embedding": precomputed_embedding,
        })
        return []

    searcher.vector_engine.search = _empty_vector

    first = searcher.search("bm25 only query", top_k=2)
    second = searcher.search("bm25 only query", top_k=2)

    assert [candidate["quota_id"] for candidate in first] == ["Q-1"]
    assert [candidate["quota_id"] for candidate in second] == ["Q-1"]
    assert len(searcher.bm25_engine.calls) == 1
    assert len(searcher.vector_engine.calls) == 1
    assert len(searcher._session_cache) == 1


def test_search_session_cache_key_separates_effective_vector_budget(monkeypatch):
    searcher = _make_searcher()
    _patch_basic_search_flow(monkeypatch, searcher)
    monkeypatch.setattr(config, "HYBRID_STANDARD_VECTOR_SKIP_SPECIALTIES", ("C5",), raising=False)

    searcher.search("same query", top_k=2, item={"adaptive_strategy": "standard", "specialty": "C5"})
    searcher.search("same query", top_k=2, item={"adaptive_strategy": "standard", "specialty": "C4"})

    assert len(searcher.bm25_engine.calls) == 2
    assert len(searcher.vector_engine.calls) == 1
    assert len(searcher._session_cache) == 2


def test_standard_vector_skip_uses_books_when_specialty_missing(monkeypatch):
    searcher = _make_searcher()
    _patch_basic_search_flow(monkeypatch, searcher)
    monkeypatch.setattr(config, "HYBRID_STANDARD_VECTOR_SKIP_SPECIALTIES", ("C5",), raising=False)

    searcher.search("weak current query", top_k=2, books=["5"], item={"adaptive_strategy": "standard"})
    searcher.search("weak current query c5", top_k=2, books=["C05"], item={"adaptive_strategy": "standard"})

    assert len(searcher.bm25_engine.calls) == 2
    assert len(searcher.vector_engine.calls) == 0


def test_search_session_cache_expires_by_ttl(monkeypatch):
    searcher = _make_searcher()
    searcher._SESSION_CACHE_TTL_SEC = 5.0

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

    current_time = {"value": 100.0}
    monkeypatch.setattr(HybridSearcher, "_cache_now", staticmethod(lambda: current_time["value"]))

    searcher._resolve_rank_window = lambda **kwargs: 2
    searcher._resolve_engine_top_k = lambda **kwargs: kwargs["rank_window"]
    searcher._get_adaptive_weights = lambda **kwargs: (0.5, 0.5, "balanced")
    searcher._build_query_variants = lambda *args, **kwargs: [{"query": args[0], "tag": "raw", "weight": 1.0}]
    searcher._rrf_fusion = lambda bm25_results, vector_results, bm25_weight, vector_weight, k: [{
        "quota_id": "Q-1",
        "name": "candidate",
        "id": "Q-1",
        "hybrid_score": float(len(searcher.bm25_engine.calls)),
        "bm25_rank": 1,
        "vector_rank": 1,
    }]
    searcher._finalize_candidates = lambda candidates, query_text, expected_books=None: list(candidates)

    first = searcher.search("same query", top_k=2)
    current_time["value"] += 3.0
    second = searcher.search("same query", top_k=2)
    current_time["value"] += 6.0
    third = searcher.search("same query", top_k=2)

    assert first[0]["hybrid_score"] == 1.0
    assert second[0]["hybrid_score"] == 1.0
    assert third[0]["hybrid_score"] == 2.0
    assert len(searcher.bm25_engine.calls) == 2
    assert len(searcher.vector_engine.calls) == 2


def test_kb_keyword_cache_expires_by_ttl(monkeypatch):
    searcher = _make_searcher()
    searcher._KB_KEYWORD_CACHE_TTL_SEC = 5.0
    searcher._store_session_search_results = lambda cache_key, results: list(results or [])

    monkeypatch.setattr(config, "VECTOR_ENABLED", False)
    monkeypatch.setattr(config, "HYBRID_STANDARD_KB_HINTS_ENABLED", True, raising=False)
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

    current_time = {"value": 200.0}
    monkeypatch.setattr(HybridSearcher, "_cache_now", staticmethod(lambda: current_time["value"]))

    class _FakeUniversalKB:
        def __init__(self):
            self.calls = []

        def get_search_keywords(self, query):
            self.calls.append(query)
            return [f"hint-{len(self.calls)}"]

    searcher._universal_kb = _FakeUniversalKB()
    searcher._resolve_rank_window = lambda **kwargs: 1
    searcher._resolve_engine_top_k = lambda **kwargs: 1
    searcher._get_adaptive_weights = lambda **kwargs: (0.5, 0.5, "balanced")
    searcher._build_query_variants = lambda query, kb_hints, **kwargs: [{"query": " ".join([query] + list(kb_hints)), "tag": "raw", "weight": 1.0}]
    searcher._rrf_fusion = lambda bm25_results, vector_results, bm25_weight, vector_weight, k: []
    searcher._finalize_candidates = lambda candidates, query_text, expected_books=None: list(candidates)

    searcher.search("same query", top_k=1, item={"adaptive_strategy": "standard"})
    current_time["value"] += 3.0
    searcher.search("same query", top_k=1, item={"adaptive_strategy": "standard"})
    current_time["value"] += 6.0
    searcher.search("same query", top_k=1, item={"adaptive_strategy": "standard"})

    assert searcher.universal_kb.calls == ["same query", "same query"]


def test_store_cache_value_uses_cache_lock():
    searcher = _make_searcher()

    class _TrackedLock:
        def __init__(self):
            self.entered = 0

        def __enter__(self):
            self.entered += 1
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    tracked_lock = _TrackedLock()
    searcher._cache_lock = tracked_lock

    searcher._store_cache_value(
        searcher._session_cache,
        "k1",
        [{"quota_id": "Q-1"}],
        ttl_sec=30.0,
        max_size=10,
    )

    assert tracked_lock.entered >= 1
    assert "k1" in searcher._session_cache
