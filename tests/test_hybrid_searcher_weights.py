from src.hybrid_searcher import HybridSearcher


def test_installation_spec_query_biases_toward_bm25():
    searcher = HybridSearcher.__new__(HybridSearcher)
    searcher._get_feedback_bias = lambda: 0.0

    bm25_weight, vector_weight, reason = HybridSearcher._get_adaptive_weights(
        searcher,
        "WDZN-BYJ 3x4+2x2.5 配线",
        0.3,
        0.7,
    )

    assert bm25_weight > vector_weight
    assert bm25_weight > 0.5
    assert reason == "spec_heavy_installation"


def test_semantic_query_biases_toward_vector():
    searcher = HybridSearcher.__new__(HybridSearcher)
    searcher._get_feedback_bias = lambda: 0.0

    bm25_weight, vector_weight, reason = HybridSearcher._get_adaptive_weights(
        searcher,
        "成套配电箱安装，包含基础制作与整体调试",
        0.3,
        0.7,
    )

    assert vector_weight > bm25_weight
    assert reason == "semantic_heavy"


def test_is_spec_heavy_text_keeps_regex_fallback_when_router_signal_misses(monkeypatch):
    monkeypatch.setattr(
        HybridSearcher,
        "_count_spec_signals",
        staticmethod(lambda text: 0),
    )

    assert HybridSearcher._is_spec_heavy_text("WDZN-BYJ 3x4+2x2.5 配线")
