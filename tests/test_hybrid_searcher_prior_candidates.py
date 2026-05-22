from src.hybrid_searcher import HybridSearcher


class _FakeExperienceDB:
    def _find_exact_match(self, variant, province, authority_only=True, exclude_sources=None):
        del province, authority_only, exclude_sources
        if variant == "normalized exact":
            return {
                "id": 11,
                "quota_ids": '["Q-EXP-1"]',
                "quota_names": '["Exact Experience Quota"]',
                "confidence": 96,
                "layer": "authority",
            }
        return None

    def find_experience(self, bill_text, province=None, limit=20, online_only=False):
        del province, limit, online_only
        if bill_text == "Exact Bill Name":
            return [
                {
                    "id": 12,
                    "bill_name": "Exact Bill Name",
                    "quota_ids": '["Q-EXP-2"]',
                    "quota_names": '["Bill Name Experience Quota"]',
                    "confidence": 92,
                    "layer": "authority",
                }
            ]
        return []

    def search_experience(self, *args, **kwargs):
        del args, kwargs
        return []


class _FakeUniversalKB:
    def _find_exact(self, variant):
        if variant == "Exact KB Bill":
            return {
                "bill_pattern": "Exact KB Bill",
                "quota_patterns": '["KB Quota Pattern"]',
                "confidence": 88,
                "layer": "authority",
            }
        return None

    def search_hints(self, *args, **kwargs):
        del args, kwargs
        return []


class _FakeBM25:
    def search(self, pattern, top_k=2, books=None):
        del top_k, books
        if pattern == "KB Quota Pattern":
            return [{"quota_id": "Q-KB-1", "name": "KB Quota", "unit": "m"}]
        return []


class _FakeUnifiedDataLayer:
    def search(self, query, sources=None, strategy="auto", top_k=10, authority_only=True):
        del query, sources, strategy, top_k, authority_only
        return {
            "grouped": {
                "experience": [
                    {
                        "raw": {
                            "id": 21,
                            "quota_ids": ["Q-U-EXP"],
                            "quota_names": ["Unified Experience Quota"],
                            "confidence": 92,
                            "layer": "authority",
                            "gate": "green",
                            "total_score": 0.91,
                            "similarity": 0.89,
                            "match_type": "similar",
                        }
                    }
                ],
                "universal_kb": [
                    {
                        "raw": {
                            "bill_pattern": "KB Bill",
                            "quota_patterns": ["KB Quota Pattern"],
                            "similarity": 0.88,
                            "confidence": 86,
                        }
                    }
                ],
                "quota": [
                    {
                        "raw": {
                            "quota_id": "Q-U-QUOTA",
                            "name": "Unified Quota Candidate",
                            "unit": "m",
                        },
                        "score": 0.77,
                    }
                ],
            }
        }


def test_collect_prior_candidates_uses_experience_exact_variants(monkeypatch):
    monkeypatch.setattr("src.hybrid_searcher.search_by_id", lambda quota_id, province=None: (quota_id, "Resolved " + quota_id, "m"))

    searcher = HybridSearcher.__new__(HybridSearcher)
    searcher.province = "TestProvince"
    searcher._experience_db = _FakeExperienceDB()
    searcher._universal_kb = False
    searcher._bm25_engine = _FakeBM25()

    priors = searcher.collect_prior_candidates(
        "search query",
        full_query="full query",
        item={
            "name": "Exact Bill Name",
            "description": "desc",
            "canonical_query": {"normalized_query": "normalized exact"},
        },
        top_k=4,
    )

    quota_ids = [row["quota_id"] for row in priors]
    assert "Q-EXP-1" in quota_ids
    assert "Q-EXP-2" in quota_ids
    assert any(row["match_source"] == "experience_injected_exact" for row in priors)


def test_collect_prior_candidates_uses_universal_kb_exact_variants():
    searcher = HybridSearcher.__new__(HybridSearcher)
    searcher.province = "TestProvince"
    searcher._experience_db = None
    searcher._universal_kb = _FakeUniversalKB()
    searcher._bm25_engine = _FakeBM25()

    priors = searcher.collect_prior_candidates(
        "search query",
        full_query="full query",
        item={
            "name": "Exact KB Bill",
            "description": "",
            "canonical_query": {},
        },
        top_k=4,
    )

    assert [row["quota_id"] for row in priors] == ["Q-KB-1"]
    assert priors[0]["match_source"] == "kb_injected_exact"
    assert priors[0]["knowledge_prior_sources"] == ["universal_kb"]


def test_collect_prior_candidates_uses_quota_alias_exact_matches():
    class _AliasBM25:
        def search(self, pattern, top_k=2, books=None):
            del top_k, books
            if pattern == "光伏逆变器安装 功率≤1000kW":
                return [
                    {"quota_id": "03-4-5-54", "name": "光伏逆变器安装 功率≤250kW", "unit": "台"},
                    {"quota_id": "03-4-5-56", "name": "光伏逆变器安装 功率≤1000kW", "unit": "台"},
                ]
            return []

    searcher = HybridSearcher.__new__(HybridSearcher)
    searcher.province = "TestProvince"
    searcher._experience_db = None
    searcher._universal_kb = False
    searcher._bm25_engine = _AliasBM25()

    priors = searcher.collect_prior_candidates(
        "组串式逆变器 150KW",
        full_query="组串式逆变器 150KW",
        item={
            "canonical_query": {
                "primary_query_profile": {
                    "primary_subject": "组串式逆变器",
                    "quota_aliases": ["光伏逆变器安装 功率≤1000kW"],
                }
            }
        },
        top_k=4,
    )

    matched = next(row for row in priors if row["quota_id"] == "03-4-5-56")
    assert matched["match_source"] == "quota_alias_exact"
    assert matched["knowledge_prior_sources"] == ["quota_alias"]


def test_collect_prior_candidates_uses_same_book_quota_name_fallback(monkeypatch):
    def fake_query_rows(self, *, terms, books, limit):
        del self, limit
        assert terms[:2] == ["混凝土", "井"]
        assert books == ["C6"]
        return [
            {
                "quota_id": "6-311",
                "name": "混凝土检查井 井深2m以内",
                "unit": "座",
                "book": "C6",
                "search_text": "混凝土 检查井 井",
            },
            {
                "quota_id": "6-276",
                "name": "砖砌检查井",
                "unit": "座",
                "book": "C6",
                "search_text": "砖砌 检查井",
            },
        ]

    monkeypatch.setattr(
        HybridSearcher,
        "_query_quota_name_fallback_rows",
        fake_query_rows,
    )

    searcher = HybridSearcher.__new__(HybridSearcher)
    searcher.province = "TestProvince"
    searcher._experience_db = None
    searcher._universal_kb = False
    searcher._bm25_engine = _FakeBM25()
    searcher._unified_data_layer = False
    searcher._uses_standard_books = True

    priors = searcher.collect_prior_candidates(
        "混凝土井",
        full_query="混凝土井",
        books=["C6"],
        item={"name": "混凝土井", "canonical_query": {"search_query": "混凝土井"}},
        top_k=4,
    )

    matched = next(row for row in priors if row["quota_id"] == "6-311")
    assert matched["match_source"] == "quota_name_fallback"
    assert matched["knowledge_prior_sources"] == ["quota_name_fallback"]


def test_collect_prior_candidates_normalizes_nonstandard_prior_books(monkeypatch):
    class _NonstandardBM25(_FakeBM25):
        quota_books = {1: "6"}

        def ensure_index(self):
            return None

    def fake_query_rows(self, *, terms, books, limit):
        del self, terms, limit
        assert books == ["6"]
        return [
            {
                "quota_id": "6-311",
                "name": "混凝土检查井",
                "unit": "座",
                "book": "6",
                "search_text": "混凝土 检查井 井",
            }
        ]

    monkeypatch.setattr(
        HybridSearcher,
        "_query_quota_name_fallback_rows",
        fake_query_rows,
    )

    searcher = HybridSearcher.__new__(HybridSearcher)
    searcher.province = "TestProvince"
    searcher._experience_db = None
    searcher._universal_kb = False
    searcher._bm25_engine = _NonstandardBM25()
    searcher._unified_data_layer = False
    searcher._uses_standard_books = False

    priors = searcher.collect_prior_candidates(
        "混凝土井",
        full_query="混凝土井",
        books=["C6"],
        item={"name": "混凝土井", "canonical_query": {"search_query": "混凝土井"}},
        top_k=4,
    )

    assert [row["quota_id"] for row in priors] == ["6-311"]


def test_collect_prior_candidates_adds_same_book_quota_id_neighbors(monkeypatch):
    class _NeighborBM25(_FakeBM25):
        quota_books = {1: "9"}

        def search(self, pattern, top_k=2, books=None):
            del pattern, top_k
            assert books == ["9"]
            return [{"quota_id": "9-91", "name": "seed", "unit": "", "book": "9"}]

    monkeypatch.setattr(
        HybridSearcher,
        "_query_quota_name_fallback_rows",
        lambda self, *, terms, books, limit: [],
    )
    monkeypatch.setattr(
        "src.hybrid_searcher.search_by_id",
        lambda quota_id, province=None: (
            (quota_id, "neighbor " + quota_id, "m")
            if quota_id == "9-92"
            else None
        ),
    )

    searcher = HybridSearcher.__new__(HybridSearcher)
    searcher.province = "TestProvince"
    searcher._experience_db = None
    searcher._universal_kb = False
    searcher._bm25_engine = _NeighborBM25()
    searcher._unified_data_layer = False
    searcher._uses_standard_books = False

    priors = searcher.collect_prior_candidates(
        "防水",
        full_query="防水",
        books=["C9"],
        item={"name": "防水", "canonical_query": {"search_query": "防水"}},
        top_k=4,
    )

    matched = next(row for row in priors if row["quota_id"] == "9-92")
    assert matched["match_source"] == "quota_id_neighbor"
    assert matched["quota_id_neighbor_seed"] == "9-91"


def test_quota_name_fallback_requires_requested_books(monkeypatch):
    called = False

    def fake_query_rows(self, *, terms, books, limit):
        del self, terms, books, limit
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(
        HybridSearcher,
        "_query_quota_name_fallback_rows",
        fake_query_rows,
    )

    searcher = HybridSearcher.__new__(HybridSearcher)
    searcher.province = "TestProvince"

    rows = searcher._collect_quota_name_fallback_prior_candidates(
        query_text="混凝土井",
        full_query="混凝土井",
        item={"name": "混凝土井"},
        books=None,
        top_k=4,
    )

    assert rows == []
    assert called is False


def test_build_prior_query_variants_include_primary_query_profile():
    variants = HybridSearcher._build_prior_query_variants(
        "search query",
        full_query="full query",
        item={
            "name": "通用项目",
            "description": "含套管制作及安装",
            "canonical_query": {
                "search_query": "search query",
                "primary_query_profile": {
                    "primary_text": "钢塑复合管 DN50 螺纹连接",
                    "primary_subject": "钢塑复合管",
                    "decisive_terms": ["钢塑复合管", "DN50", "螺纹连接"],
                },
            },
        },
    )

    assert "钢塑复合管 DN50 螺纹连接" in variants
    assert "钢塑复合管" in variants
    assert any("DN50" in variant and "螺纹连接" in variant for variant in variants)


def test_build_query_variants_include_primary_query_profile():
    searcher = HybridSearcher.__new__(HybridSearcher)
    variants = searcher._build_query_variants(
        "堵洞 穿墙 穿楼板 桥架",
        [],
        query_features={},
        route_profile={},
        primary_query_profile={
            "primary_text": "强电桥架 600mm×200mm",
            "primary_subject": "强电桥架",
            "decisive_terms": ["强电桥架", "600mm×200mm"],
        },
    )

    variant_queries = [row["query"] for row in variants]
    assert "强电桥架 600mm×200mm" in variant_queries
    assert "强电桥架" in variant_queries
def test_collect_prior_candidates_uses_unified_data_layer_when_available(monkeypatch):
    monkeypatch.setattr(
        "src.hybrid_searcher.search_by_id",
        lambda quota_id, province=None: (quota_id, "Resolved " + quota_id, "m"),
    )

    searcher = HybridSearcher.__new__(HybridSearcher)
    searcher.province = "TestProvince"
    searcher._experience_db = None
    searcher._universal_kb = False
    searcher._unified_data_layer = _FakeUnifiedDataLayer()
    searcher._bm25_engine = _FakeBM25()

    priors = searcher.collect_prior_candidates(
        "search query",
        full_query="full query",
        item={},
        top_k=4,
    )

    quota_ids = [row["quota_id"] for row in priors]
    assert "Q-U-EXP" in quota_ids
    assert "Q-KB-1" in quota_ids
    assert "Q-U-QUOTA" in quota_ids

    quota_prior = next(row for row in priors if row["quota_id"] == "Q-U-QUOTA")
    assert quota_prior["match_source"] == "quota_unified"
    assert quota_prior["knowledge_prior_sources"] == ["quota"]


def test_collect_prior_candidates_caps_long_alias_exact_for_standard_c5_lightweight(monkeypatch):
    searcher = HybridSearcher.__new__(HybridSearcher)
    searcher.province = "TestProvince"
    searcher._experience_db = None
    searcher._universal_kb = False
    searcher._unified_data_layer = object()
    searcher._bm25_engine = _FakeBM25()
    searcher._uses_standard_books = True

    monkeypatch.setattr("config.SEARCH_PRIOR_CANDIDATES_LIGHTWEIGHT", True, raising=False)
    monkeypatch.setattr("config.HYBRID_STANDARD_VECTOR_SKIP_SPECIALTIES", ("C5",), raising=False)
    monkeypatch.setattr(searcher, "_collect_quota_name_fallback_prior_candidates", lambda **kwargs: [])
    monkeypatch.setattr(searcher, "_collect_quota_id_neighbor_prior_candidates", lambda **kwargs: [])
    monkeypatch.setattr(searcher, "_collect_experience_exact_prior_candidates", lambda **kwargs: [])
    monkeypatch.setattr(searcher, "_collect_universal_kb_exact_prior_candidates", lambda **kwargs: [])
    monkeypatch.setattr(searcher, "_collect_experience_prior_candidates", lambda **kwargs: [])
    monkeypatch.setattr(searcher, "_collect_universal_kb_prior_candidates", lambda **kwargs: [])
    alias_kwargs = []
    called = {"unified": 0}

    def fake_alias(**kwargs):
        alias_kwargs.append(kwargs)
        return []

    def fake_unified(**kwargs):
        del kwargs
        called["unified"] += 1
        return [{"quota_id": "Q-U", "name": "Unified", "knowledge_prior_score": 0.9}]

    monkeypatch.setattr(searcher, "_collect_quota_alias_exact_prior_candidates", fake_alias)
    monkeypatch.setattr(searcher, "_collect_unified_data_prior_candidates", fake_unified)

    standard_c5 = searcher.collect_prior_candidates(
        "weak current",
        books=["5"],
        item={},
        adaptive_strategy="standard",
    )
    deep_c5 = searcher.collect_prior_candidates(
        "weak current",
        books=["5"],
        item={},
        adaptive_strategy="deep",
    )

    assert standard_c5[0]["quota_id"] == "Q-U"
    assert deep_c5[0]["quota_id"] == "Q-U"
    assert called["unified"] == 2
    assert alias_kwargs[0]["max_aliases"] == 2
    assert alias_kwargs[0]["max_compact_len"] == 32
    assert alias_kwargs[1]["max_aliases"] is None
    assert alias_kwargs[1]["max_compact_len"] is None
    assert searcher._last_prior_collect_trace["lightweight_standard_c5"] is False


def test_collect_prior_candidates_skips_non_authority_bill_name_exact_fallback(monkeypatch):
    class _VerifiedOnlyExperienceDB:
        def _find_exact_match(self, variant, province, authority_only=True, exclude_sources=None):
            del variant, province, authority_only, exclude_sources
            return None

        def find_experience(self, bill_text, province=None, limit=20, online_only=False):
            del province, limit, online_only
            if bill_text == "Exact Bill Name":
                return [
                    {
                        "id": 31,
                        "bill_name": "Exact Bill Name",
                        "quota_ids": '["Q-EXP-V"]',
                        "quota_names": '["Verified Experience Quota"]',
                        "confidence": 95,
                        "layer": "verified",
                    }
                ]
            return []

        def search_experience(self, *args, **kwargs):
            del args, kwargs
            return []

    monkeypatch.setattr(
        "src.hybrid_searcher.search_by_id",
        lambda quota_id, province=None: (quota_id, "Resolved " + quota_id, "m"),
    )

    searcher = HybridSearcher.__new__(HybridSearcher)
    searcher.province = "TestProvince"
    searcher._experience_db = _VerifiedOnlyExperienceDB()
    searcher._universal_kb = False
    searcher._bm25_engine = _FakeBM25()

    priors = searcher.collect_prior_candidates(
        "search query",
        full_query="full query",
        item={
            "name": "Exact Bill Name",
            "description": "desc",
            "canonical_query": {},
        },
        top_k=4,
    )

    assert all(row["match_source"] != "experience_injected_exact" for row in priors)
