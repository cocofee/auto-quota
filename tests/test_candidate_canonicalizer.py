from src.candidate_canonicalizer import (
    attach_candidate_canonical_features,
    build_candidate_canonical_features,
)
from src.canonical_dictionary import detect_entity
from src.hybrid_searcher import HybridSearcher


def test_build_candidate_canonical_features_keeps_surge_protector_out_of_distribution_box_family():
    features = build_candidate_canonical_features({
        "quota_id": "C4-SPD-1",
        "name": "模块式电涌保护器安装 总配电箱电涌保护器",
        "specialty": "C4",
    })

    assert features["entity"] == "浪涌保护器"
    assert features["canonical_name"] == "浪涌保护器"
    assert features["family"] == "protection_device"
    assert features["system"] == "电气"


def test_detect_entity_prefers_primary_subject_for_surge_protector():
    assert detect_entity("浪涌保护器 名称:电源避雷器 安装形式:放置于抱杆机箱内") == "浪涌保护器"


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


def test_hybrid_searcher_family_gate_prefers_general_pipe_support_over_special_shape():
    searcher = HybridSearcher(province="通用")
    candidates = [
        {
            "quota_id": "PIPE-SUPPORT-WOOD",
            "name": "管道支吊架制作与安装 木垫式管架",
            "hybrid_score": 0.95,
        },
        {
            "quota_id": "PIPE-SUPPORT-GENERAL",
            "name": "室内管道管道支架制作安装 一般管架",
            "hybrid_score": 0.80,
        },
    ]

    finalized = searcher._finalize_candidates(
        candidates,
        query_text="管道支架制作安装 一般管架 支吊架 给排水 C10",
    )

    assert finalized[0]["quota_id"] == "PIPE-SUPPORT-GENERAL"
    wrong = next(item for item in finalized if item["quota_id"] == "PIPE-SUPPORT-WOOD")
    assert wrong["support_subtype_gate_score"] < 0
    assert "未声明子型:木垫式" in wrong["family_gate_detail"]


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


def test_hybrid_searcher_builds_general_pipe_support_variants_without_weight_noise():
    searcher = HybridSearcher(province="通用")
    query_features = {
        "family": "pipe_support",
        "entity": "支吊架",
        "canonical_name": "支吊架",
        "support_scope": "管道支架",
        "support_action": "制作",
        "system": "给排水",
        "traits": ["管道支架", "制作", "防锈漆/调和漆"],
        "numeric_params": {},
    }

    variants = searcher._build_query_variants(
        "管道支架制作安装 一般管架 支吊架 给排水 C10",
        ["管道支架制作(单件重量100kg以内)", "管道支架安装(单件重量100kg以内)"],
        query_features=query_features,
        route_profile={"route": "semantic_description", "spec_signal_count": 0},
    )

    family_queries = [item["query"] for item in variants if item["tag"].startswith("family_focus_")]
    assert "管道支架制作安装 一般管架" in family_queries
    assert "室内管道管道支架制作安装 一般管架" in family_queries
    assert all("单件重量" not in item["query"] for item in variants)


def test_hybrid_searcher_expands_rank_window_for_family_spec_query():
    searcher = HybridSearcher(province="通用")
    window = searcher._resolve_rank_window(
        top_k=10,
        query_features={"family": "valve_accessory"},
        route_profile={"route": "installation_spec", "spec_signal_count": 2},
    )

    assert window >= 50


def test_hybrid_searcher_filters_steel_kb_hints_for_electrical_conduit_query():
    filtered = HybridSearcher._filter_kb_hints_for_query_features(
        ["钢管敷设 砖、混凝土结构暗配 SC32", "波纹电线管敷设 内径 32"],
        query_features={
            "family": "conduit_raceway",
            "entity": "配管",
            "system": "电气",
        },
    )

    assert filtered == ["波纹电线管敷设 内径 32"]


def test_build_candidate_canonical_features_distinguishes_pipe_run_and_sleeve():
    pipe_features = build_candidate_canonical_features({
        "quota_id": "C10-1-1",
        "name": "给排水管道 室内塑料排水管 De75",
    })
    sleeve_features = build_candidate_canonical_features({
        "quota_id": "C10-2-1",
        "name": "刚性防水套管制作安装 DN75",
    })

    assert pipe_features["entity"] == "管道"
    assert pipe_features["family"] == "pipe_run"
    assert sleeve_features["entity"] == "套管"
    assert sleeve_features["family"] == "pipe_sleeve"


def test_build_candidate_canonical_features_recognizes_electrical_conduit_candidates():
    features = build_candidate_canonical_features({
        "quota_id": "C4-12-177",
        "name": "波纹电线管敷设 内径(mm) ≤32",
        "specialty": "C4",
    })

    assert features["entity"] == "配管"
    assert features["family"] == "conduit_raceway"
    assert features["system"] == "电气"
    assert features["canonical_name"] == "配管"
    assert features["cable_type"] == ""


def test_detect_entity_handles_real_pipe_and_sleeve_bill_terms():
    assert detect_entity("防紫外线PVC-U塑料管 De110【雨水管埋地】") == "管道"
    assert detect_entity("覆塑不锈钢(PP-R)压力管 DN50【冷水管】") == "管道"
    assert detect_entity("超静音HTPP三层内螺旋管 De110【污、废水管】") == "管道"
    assert detect_entity("刚性防水套管 DN100") == "套管"
