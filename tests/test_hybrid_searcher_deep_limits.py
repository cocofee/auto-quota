from src.hybrid_searcher import HybridSearcher


def test_build_query_variants_caps_deep_variant_count(monkeypatch):
    monkeypatch.setattr("config.HYBRID_QUERY_VARIANTS", 4, raising=False)
    monkeypatch.setattr("config.HYBRID_DEEP_QUERY_VARIANTS", 3, raising=False)
    searcher = HybridSearcher.__new__(HybridSearcher)

    variants = searcher._build_query_variants(
        "pipe dn200 grooved connection",
        ["pipe install"],
        query_features={"family": "pipe_run", "numeric_params": {"dn": 200}},
        route_profile={"route": "installation_spec", "spec_signal_count": 2},
        primary_query_profile={
            "primary_text": "pipe dn200",
            "primary_subject": "pipe",
            "quota_aliases": ["pipe install", "grooved pipe install"],
        },
        adaptive_strategy="deep",
    )

    assert len(variants) == 3


def test_resolve_rank_window_caps_deep_installation_queries(monkeypatch):
    monkeypatch.setattr("config.HYBRID_DEEP_RANK_WINDOW_CAP", 72, raising=False)
    searcher = HybridSearcher.__new__(HybridSearcher)
    searcher._FAMILY_WINDOW_FAMILIES = {"cable_family"}

    window = searcher._resolve_rank_window(
        top_k=37,
        query_features={"family": "cable_family"},
        route_profile={"route": "installation_spec", "spec_signal_count": 2},
        adaptive_strategy="deep",
    )

    assert window == 72
