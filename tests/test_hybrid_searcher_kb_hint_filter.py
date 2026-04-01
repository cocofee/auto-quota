from src.hybrid_searcher import HybridSearcher


def test_hybrid_searcher_filters_fee_like_kb_hints_for_concrete_object_query():
    filtered = HybridSearcher._filter_kb_hints_for_query_features(
        [
            "电气设备超高增加费 建筑层数≦12层 檐高≦40m",
            "波纹电线管敷设 内径(mm) ≤32",
        ],
        query_features={
            "family": "conduit_raceway",
            "entity": "配管",
            "system": "电气",
        },
    )

    assert filtered == ["波纹电线管敷设 内径(mm) ≤32"]


def test_hybrid_searcher_keeps_fee_like_kb_hints_without_concrete_family_anchor():
    filtered = HybridSearcher._filter_kb_hints_for_query_features(
        [
            "电气设备超高增加费 建筑层数≦12层 檐高≦40m",
            "波纹电线管敷设 内径(mm) ≤32",
        ],
        query_features={},
    )

    assert filtered == [
        "电气设备超高增加费 建筑层数≦12层 檐高≦40m",
        "波纹电线管敷设 内径(mm) ≤32",
    ]
