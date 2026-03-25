from src.candidate_arbiter import arbitrate_candidates


def _candidate(
    quota_id: str,
    *,
    name: str,
    entity: str,
    family: str = "",
    param_score: float,
    logic_score: float,
    feature_score: float,
    context_score: float,
    rerank_score: float,
    logic_exact: bool = False,
    param_tier: int = 2,
) -> dict:
    return {
        "quota_id": quota_id,
        "name": name,
        "param_match": True,
        "param_tier": param_tier,
        "param_score": param_score,
        "logic_score": logic_score,
        "feature_alignment_score": feature_score,
        "context_alignment_score": context_score,
        "rerank_score": rerank_score,
        "candidate_canonical_features": {"entity": entity, "family": family, "system": "water"},
        "logic_exact_primary_match": logic_exact,
    }


def test_arbitrate_candidates_swaps_when_structured_signal_is_clearly_better():
    item = {"query_route": {"route": "installation_spec", "spec_signal_count": 2}}
    candidates = [
        _candidate(
            "Q1",
            name="阀门 DN100",
            entity="valve",
            family="valve_body",
            param_score=0.82,
            logic_score=0.62,
            feature_score=0.70,
            context_score=0.70,
            rerank_score=0.86,
        ),
        _candidate(
            "Q2",
            name="阀门 DN100 闸阀",
            entity="valve",
            family="valve_body",
            param_score=0.88,
            logic_score=0.95,
            feature_score=0.88,
            context_score=0.86,
            rerank_score=0.80,
            logic_exact=True,
        ),
    ]

    reordered, decision = arbitrate_candidates(item, candidates, route_profile=item["query_route"])

    assert reordered[0]["quota_id"] == "Q2"
    assert decision["applied"] is True
    assert decision["reason"] == "structured_candidate_swap"


def test_arbitrate_candidates_can_override_large_search_gap_when_structured_signal_is_decisive():
    item = {"query_route": {"route": "installation_spec", "spec_signal_count": 2}}
    candidates = [
        _candidate(
            "Q1",
            name="阀门 DN100",
            entity="valve",
            family="valve_body",
            param_score=0.83,
            logic_score=0.68,
            feature_score=0.74,
            context_score=0.72,
            rerank_score=0.95,
        ),
        _candidate(
            "Q2",
            name="阀门 DN100 闸阀",
            entity="valve",
            family="valve_body",
            param_score=0.90,
            logic_score=0.96,
            feature_score=0.90,
            context_score=0.88,
            rerank_score=0.60,
            logic_exact=True,
        ),
    ]

    reordered, decision = arbitrate_candidates(item, candidates, route_profile=item["query_route"])

    assert reordered[0]["quota_id"] == "Q1"
    assert decision["applied"] is False
    assert decision["reason"] == "search_gap_too_large"


def test_arbitrate_candidates_does_not_swap_across_unrelated_family():
    item = {"query_route": {"route": "installation_spec", "spec_signal_count": 2}}
    candidates = [
        _candidate(
            "Q1",
            name="配电箱",
            entity="box",
            family="electrical_box",
            param_score=0.83,
            logic_score=0.72,
            feature_score=0.78,
            context_score=0.76,
            rerank_score=0.85,
        ),
        _candidate(
            "Q2",
            name="阀门 DN100",
            entity="valve",
            family="valve_body",
            param_score=0.92,
            logic_score=0.96,
            feature_score=0.92,
            context_score=0.90,
            rerank_score=0.82,
        ),
    ]

    reordered, decision = arbitrate_candidates(item, candidates, route_profile=item["query_route"])

    assert reordered[0]["quota_id"] == "Q1"
    assert decision["applied"] is False
    assert decision["reason"] == "no_better_structured_candidate"


def test_arbitrate_candidates_skips_when_route_lacks_enough_spec_signal():
    item = {"query_route": {"route": "installation_spec", "spec_signal_count": 1}}
    candidates = [
        _candidate(
            "Q1",
            name="阀门 DN100",
            entity="valve",
            family="valve_body",
            param_score=0.82,
            logic_score=0.62,
            feature_score=0.70,
            context_score=0.70,
            rerank_score=0.86,
        ),
        _candidate(
            "Q2",
            name="阀门 DN100 闸阀",
            entity="valve",
            family="valve_body",
            param_score=0.88,
            logic_score=0.95,
            feature_score=0.88,
            context_score=0.86,
            rerank_score=0.80,
            logic_exact=True,
        ),
    ]

    reordered, decision = arbitrate_candidates(item, candidates, route_profile=item["query_route"])

    assert reordered[0]["quota_id"] == "Q1"
    assert decision["applied"] is False
    assert decision["reason"] == "route_not_ready"


def test_arbitrate_candidates_prefers_exact_dn_band_when_search_gap_is_acceptable():
    item = {
        "name": "焊接钢管 DN100",
        "query_route": {"route": "installation_spec", "spec_signal_count": 2},
    }
    candidates = [
        _candidate(
            "Q1",
            name="焊接钢管 DN150",
            entity="pipe",
            family="pipe_support",
            param_score=0.86,
            logic_score=0.76,
            feature_score=0.84,
            context_score=0.82,
            rerank_score=0.84,
        ),
        _candidate(
            "Q2",
            name="焊接钢管 DN100",
            entity="pipe",
            family="pipe_support",
            param_score=0.88,
            logic_score=0.82,
            feature_score=0.88,
            context_score=0.84,
            rerank_score=0.79,
        ),
    ]

    reordered, decision = arbitrate_candidates(item, candidates, route_profile=item["query_route"])

    assert reordered[0]["quota_id"] == "Q2"
    assert decision["applied"] is True
    assert decision["main_param_key"] == "dn"
    assert decision["selected_band_score"] > decision["top_band_score"]


def test_arbitrate_candidates_does_not_swap_to_worse_dn_band_even_if_structured_is_higher():
    item = {
        "name": "焊接钢管 DN100",
        "query_route": {"route": "installation_spec", "spec_signal_count": 2},
    }
    candidates = [
        _candidate(
            "Q1",
            name="焊接钢管 DN100",
            entity="pipe",
            family="pipe_support",
            param_score=0.84,
            logic_score=0.76,
            feature_score=0.82,
            context_score=0.80,
            rerank_score=0.86,
        ),
        _candidate(
            "Q2",
            name="焊接钢管 DN65",
            entity="pipe",
            family="pipe_support",
            param_score=0.93,
            logic_score=0.96,
            feature_score=0.92,
            context_score=0.90,
            rerank_score=0.84,
            logic_exact=True,
        ),
    ]

    reordered, decision = arbitrate_candidates(item, candidates, route_profile=item["query_route"])

    assert reordered[0]["quota_id"] == "Q1"
    assert decision["applied"] is False
    assert decision["reason"] == "no_better_structured_candidate"


def test_arbitrate_candidates_prefers_exact_cable_core_band_for_terminal_head():
    item = {
        "name": "电力电缆头 WDZ-YJY-5x16",
        "query_route": {"route": "installation_spec", "spec_signal_count": 2},
    }
    candidates = [
        _candidate(
            "Q1",
            name="户内干包式铜芯电力电缆终端头 4×16",
            entity="cable_head",
            family="cable_family",
            param_score=0.85,
            logic_score=0.79,
            feature_score=0.84,
            context_score=0.82,
            rerank_score=0.84,
        ),
        _candidate(
            "Q2",
            name="户内干包式铜芯电力电缆终端头 5×16",
            entity="cable_head",
            family="cable_family",
            param_score=0.84,
            logic_score=0.78,
            feature_score=0.83,
            context_score=0.81,
            rerank_score=0.80,
        ),
    ]

    reordered, decision = arbitrate_candidates(item, candidates, route_profile=item["query_route"])

    assert reordered[0]["quota_id"] == "Q2"
    assert decision["applied"] is True
    assert decision["main_param_key"] == "cable_cores"


def test_arbitrate_candidates_can_use_family_when_entity_is_missing():
    item = {"query_route": {"route": "installation_spec", "spec_signal_count": 2}}
    candidates = [
        _candidate(
            "Q1",
            name="Y型过滤器 DN50",
            entity="",
            family="valve_accessory",
            param_score=0.84,
            logic_score=0.70,
            feature_score=0.76,
            context_score=0.74,
            rerank_score=0.86,
        ),
        _candidate(
            "Q2",
            name="Y型过滤器安装(法兰连接) 公称直径(mm以内) 50",
            entity="过滤器",
            family="valve_accessory",
            param_score=0.90,
            logic_score=0.96,
            feature_score=0.90,
            context_score=0.86,
            rerank_score=0.81,
            logic_exact=True,
        ),
    ]

    reordered, decision = arbitrate_candidates(item, candidates, route_profile=item["query_route"])

    assert reordered[0]["quota_id"] == "Q2"
    assert decision["applied"] is True


def test_arbitrate_candidates_does_not_relax_thresholds_via_plugin_gap():
    item = {"query_route": {"route": "installation_spec", "spec_signal_count": 2}}
    candidates = [
        {
            **_candidate(
                "Q1",
                name="成套配电箱安装 落地式",
                entity="box",
                family="electrical_box",
                param_score=0.84,
                logic_score=0.80,
                feature_score=0.82,
                context_score=0.80,
                rerank_score=0.86,
            ),
            "plugin_score": -0.10,
        },
        {
            **_candidate(
                "Q2",
                name="成套配电箱安装 悬挂、嵌入式",
                entity="box",
                family="electrical_box",
                param_score=0.85,
                logic_score=0.82,
                feature_score=0.84,
                context_score=0.82,
                rerank_score=0.82,
            ),
            "plugin_score": 0.18,
        },
    ]

    reordered, decision = arbitrate_candidates(item, candidates, route_profile=item["query_route"])

    assert reordered[0]["quota_id"] == "Q1"
    assert decision["applied"] is False
    assert decision["reason"] == "no_better_structured_candidate"


def test_arbitrate_candidates_ignores_plugin_only_margin_when_swapping():
    item = {"query_route": {"route": "installation_spec", "spec_signal_count": 2}}
    candidates = [
        {
            **_candidate(
                "Q1",
                name="成套配电箱安装 落地式",
                entity="box",
                family="electrical_box",
                param_score=0.84,
                logic_score=0.80,
                feature_score=0.82,
                context_score=0.80,
                rerank_score=0.84,
            ),
            "plugin_score": -2.0,
        },
        {
            **_candidate(
                "Q2",
                name="成套配电箱安装 悬挂、嵌入式",
                entity="box",
                family="electrical_box",
                param_score=0.84,
                logic_score=0.80,
                feature_score=0.82,
                context_score=0.80,
                rerank_score=0.84,
            ),
            "plugin_score": 6.0,
        },
    ]

    reordered, decision = arbitrate_candidates(item, candidates, route_profile=item["query_route"])

    assert reordered[0]["quota_id"] == "Q1"
    assert decision["applied"] is False
