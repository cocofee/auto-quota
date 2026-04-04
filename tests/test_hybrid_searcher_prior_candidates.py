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
