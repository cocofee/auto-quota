from src.hybrid_searcher import HybridSearcher


def test_rrf_fusion_multi_query_uses_best_ranked_vector_variant_as_base():
    searcher = HybridSearcher.__new__(HybridSearcher)

    bm25_runs = [
        {
            "weight": 1.0,
            "results": [
                {"id": 1, "quota_id": "BM25-ONLY", "name": "bm25 meta", "bm25_score": 0.81},
            ],
        }
    ]
    vector_runs = [
        {
            "weight": 1.0,
            "results": [
                {"id": 1, "quota_id": "V-BEST", "name": "vector best", "vector_score": 0.92},
            ],
        },
        {
            "weight": 1.0,
            "results": [
                {"id": 1, "quota_id": "V-LATE", "name": "vector late", "vector_score": 0.55},
            ],
        },
    ]

    fused = HybridSearcher._rrf_fusion_multi_query(
        searcher,
        bm25_runs=bm25_runs,
        vector_runs=vector_runs,
        bm25_weight=0.3,
        vector_weight=0.7,
    )

    assert len(fused) == 1
    assert fused[0]["quota_id"] == "V-BEST"
    assert fused[0]["name"] == "vector best"
    assert fused[0]["bm25_rank"] == 1
    assert fused[0]["vector_rank"] == 1


def test_rrf_fusion_multi_query_uses_best_ranked_bm25_variant_when_no_vector_hit():
    searcher = HybridSearcher.__new__(HybridSearcher)

    bm25_runs = [
        {
            "weight": 1.0,
            "results": [
                {"id": 2, "quota_id": "BM25-BEST", "name": "bm25 best", "bm25_score": 0.93},
            ],
        },
        {
            "weight": 1.0,
            "results": [
                {"id": 2, "quota_id": "BM25-LATE", "name": "bm25 late", "bm25_score": 0.51},
            ],
        },
    ]

    fused = HybridSearcher._rrf_fusion_multi_query(
        searcher,
        bm25_runs=bm25_runs,
        vector_runs=[],
        bm25_weight=0.3,
        vector_weight=0.7,
    )

    assert len(fused) == 1
    assert fused[0]["quota_id"] == "BM25-BEST"
    assert fused[0]["name"] == "bm25 best"
    assert fused[0]["bm25_rank"] == 1
    assert fused[0]["vector_rank"] is None


def test_rrf_fusion_keeps_same_db_id_from_different_provinces_separate():
    searcher = HybridSearcher.__new__(HybridSearcher)

    fused = HybridSearcher._rrf_fusion(
        searcher,
        bm25_results=[
            {"id": 101, "quota_id": "TJ-101", "name": "tianjin", "source_province": "天津2024", "bm25_score": 0.91},
        ],
        vector_results=[
            {"id": 101, "quota_id": "SH-101", "name": "shanghai", "source_province": "上海2016", "vector_score": 0.96},
        ],
        bm25_weight=0.3,
        vector_weight=0.7,
    )

    assert len(fused) == 2
    assert {row["quota_id"] for row in fused} == {"TJ-101", "SH-101"}


def test_rrf_fusion_multi_query_keeps_same_db_id_from_different_provinces_separate():
    searcher = HybridSearcher.__new__(HybridSearcher)

    fused = HybridSearcher._rrf_fusion_multi_query(
        searcher,
        bm25_runs=[
            {
                "weight": 1.0,
                "results": [
                    {"id": 101, "quota_id": "TJ-101", "name": "tianjin", "source_province": "天津2024", "bm25_score": 0.91},
                ],
            }
        ],
        vector_runs=[
            {
                "weight": 1.0,
                "results": [
                    {"id": 101, "quota_id": "SH-101", "name": "shanghai", "source_province": "上海2016", "vector_score": 0.96},
                ],
            }
        ],
        bm25_weight=0.3,
        vector_weight=0.7,
    )

    assert len(fused) == 2
    assert {row["quota_id"] for row in fused} == {"TJ-101", "SH-101"}
