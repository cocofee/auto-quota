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


def test_hybrid_searcher_family_gate_prefers_pipe_support_over_bridge_support():
    searcher = HybridSearcher(province="通用")
    candidates = [
        {
            "quota_id": "PIPE-SUPPORT-BRIDGE",
            "name": "支架制作与安装 电缆桥架支撑架制作",
            "hybrid_score": 0.95,
        },
        {
            "quota_id": "PIPE-SUPPORT-PIPE",
            "name": "室内管道管道支架制作安装 一般管架",
            "hybrid_score": 0.20,
        },
    ]

    finalized = searcher._finalize_candidates(candidates, query_text="管道支架制作安装")

    assert finalized[0]["quota_id"] == "PIPE-SUPPORT-PIPE"
    wrong = next(item for item in finalized if item["quota_id"] == "PIPE-SUPPORT-BRIDGE")
    assert wrong["family_gate_hard_conflict"] is True
    assert "家族冲突" in wrong["family_gate_detail"]


def test_hybrid_searcher_family_gate_prefers_filter_over_water_meter():
    searcher = HybridSearcher(province="通用")
    candidates = [
        {
            "quota_id": "VALVE-ACCESSORY-WATER-METER",
            "name": "水表组成安装 螺翼式水表 DN50",
            "hybrid_score": 0.94,
        },
        {
            "quota_id": "VALVE-ACCESSORY-FILTER",
            "name": "Y型过滤器安装(法兰连接) 公称直径(mm以内) 50",
            "hybrid_score": 0.20,
        },
    ]

    finalized = searcher._finalize_candidates(candidates, query_text="Y型过滤器 DN50")

    assert finalized[0]["quota_id"] == "VALVE-ACCESSORY-FILTER"
    wrong = next(item for item in finalized if item["quota_id"] == "VALVE-ACCESSORY-WATER-METER")
    assert wrong["family_gate_hard_conflict"] is True
    assert "家族内实体冲突" in wrong["family_gate_detail"]


def test_hybrid_searcher_builds_family_focused_query_variants_for_bridge_support():
    searcher = HybridSearcher(province="通用")
    query_features = {
        "family": "bridge_support",
        "entity": "支吊架",
        "canonical_name": "支吊架",
        "traits": ["支撑架"],
        "numeric_params": {},
    }

    variants = searcher._build_query_variants(
        "管道支架 管架形式:桥架侧纵向抗震支吊架",
        [],
        query_features=query_features,
        route_profile={"route": "installation_spec", "spec_signal_count": 1},
    )

    tags = [item["tag"] for item in variants]
    family_queries = [item["query"] for item in variants if item["tag"].startswith("family_focus_")]
    assert "family_focus_1" in tags
    assert any("桥架支撑架" in query for query in family_queries)


def test_hybrid_searcher_expands_rank_window_for_family_spec_query():
    searcher = HybridSearcher(province="通用")
    window = searcher._resolve_rank_window(
        top_k=10,
        query_features={"family": "valve_accessory"},
        route_profile={"route": "installation_spec", "spec_signal_count": 2},
    )

    assert window >= 50
