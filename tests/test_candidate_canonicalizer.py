from src.candidate_canonicalizer import (
    attach_candidate_canonical_features,
    build_candidate_canonical_features,
)
from src.hybrid_searcher import HybridSearcher


def test_build_candidate_canonical_features_normalizes_install_candidate():
    features = build_candidate_canonical_features({
        "quota_id": "C9-1-1",
        "name": "喷淋管 DN100 丝扣连接",
        "material": "喷淋管",
        "connection": "丝扣连接",
        "dn": 100,
    })

    assert features["system"] == "消防"
    assert features["material"] == "喷淋钢管"
    assert features["connection"] == "螺纹连接"
    assert features["numeric_params"]["dn"] == 100


def test_attach_candidate_canonical_features_sets_default_field():
    candidates = [
        {"quota_id": "C4-1-1", "name": "电力电缆敷设 WDZN-BYJ 3x4+2x2.5"},
    ]

    attach_candidate_canonical_features(candidates)

    assert candidates[0]["canonical_features"]["entity"] == "电缆"
    assert candidates[0]["canonical_features"]["numeric_params"]["cable_cores"] == 5


def test_hybrid_searcher_finalize_candidates_attaches_canonical_features():
    searcher = HybridSearcher(province="通用")
    candidates = [{"quota_id": "C4-11-1", "name": "电缆桥架支架制作安装"}]

    finalized = searcher._finalize_candidates(candidates)

    assert finalized[0]["canonical_features"]["system"] == "电气"
    assert finalized[0]["canonical_features"]["entity"] == "桥架"
