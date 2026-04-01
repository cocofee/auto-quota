# -*- coding: utf-8 -*-

from src.param_validator import ParamValidator


def test_feature_alignment_rejects_bridge_for_conduit_query():
    validator = ParamValidator()
    results = validator.validate_candidates(
        query_text="电气配管",
        candidates=[
            {
                "quota_id": "A",
                "name": "电缆桥架安装",
                "rerank_score": 0.95,
                "hybrid_score": 0.95,
            },
            {
                "quota_id": "B",
                "name": "镀锌钢管敷设 混凝土结构暗配",
                "rerank_score": 0.20,
                "hybrid_score": 0.20,
            },
        ],
    )

    assert results[0]["quota_id"] == "B"
    bridge = next(item for item in results if item["quota_id"] == "A")
    assert bridge["param_match"] is False
    assert bridge["feature_alignment_hard_conflict"] is True
    assert bridge["feature_alignment_comparable_count"] > 0


def test_feature_alignment_prefers_wiring_family_for_generic_query():
    validator = ParamValidator()
    results = validator.validate_candidates(
        query_text="配线",
        candidates=[
            {
                "quota_id": "A",
                "name": "镀锌钢管敷设",
                "rerank_score": 0.95,
                "hybrid_score": 0.95,
            },
            {
                "quota_id": "B",
                "name": "管内穿线 导线敷设",
                "rerank_score": 0.10,
                "hybrid_score": 0.10,
            },
        ],
    )

    assert results[0]["quota_id"] == "B"
    conduit = next(item for item in results if item["quota_id"] == "A")
    cable = next(item for item in results if item["quota_id"] == "B")
    assert conduit["feature_alignment_hard_conflict"] is True
    assert cable["feature_alignment_score"] > conduit["feature_alignment_score"]


def test_feature_alignment_rejects_valve_for_pipe_query():
    validator = ParamValidator()
    results = validator.validate_candidates(
        query_text="喷淋钢管",
        candidates=[
            {
                "quota_id": "A",
                "name": "阀门安装",
                "rerank_score": 0.90,
                "hybrid_score": 0.90,
            },
            {
                "quota_id": "B",
                "name": "喷淋钢管安装",
                "rerank_score": 0.15,
                "hybrid_score": 0.15,
            },
        ],
    )

    assert results[0]["quota_id"] == "B"
    valve = next(item for item in results if item["quota_id"] == "A")
    assert valve["param_match"] is False
    assert valve["feature_alignment_hard_conflict"] is True


def test_logic_score_prefers_exact_control_cable_core_bucket():
    validator = ParamValidator()
    results = validator.validate_candidates(
        query_text="控制电缆头 规格:6芯以下",
        candidates=[
            {
                "quota_id": "A",
                "name": "塑料控制电缆头制作、安装 终端头(芯以下) 14",
                "rerank_score": 0.95,
                "hybrid_score": 0.95,
            },
            {
                "quota_id": "B",
                "name": "塑料控制电缆头制作、安装 终端头(芯以下) 6",
                "rerank_score": 0.10,
                "hybrid_score": 0.10,
            },
        ],
    )

    assert results[0]["quota_id"] == "A"
    exact_hit = next(item for item in results if item["quota_id"] == "B")
    loose_hit = next(item for item in results if item["quota_id"] == "A")
    assert exact_hit["param_rectify_selected"] is True
    assert "logic_rectify" in exact_hit["param_rectify_selected_rules"]
    assert exact_hit["logic_exact_primary_match"] is True
    assert exact_hit["logic_score"] > loose_hit["logic_score"]
    assert exact_hit["param_score"] > loose_hit["param_score"]


def test_logic_score_rejects_under_bucket_control_cable_candidate():
    validator = ParamValidator()
    results = validator.validate_candidates(
        query_text="控制电缆头 规格:6芯以下",
        candidates=[
            {
                "quota_id": "A",
                "name": "塑料控制电缆头制作、安装 终端头(芯以下) 4",
                "rerank_score": 0.95,
                "hybrid_score": 0.95,
            },
            {
                "quota_id": "B",
                "name": "塑料控制电缆头制作、安装 终端头(芯以下) 6",
                "rerank_score": 0.10,
                "hybrid_score": 0.10,
            },
        ],
    )


def test_feature_only_mode_keeps_original_order_but_emits_rectify_advisory():
    validator = ParamValidator()
    results = validator.validate_candidates(
        query_text="喷淋钢管",
        candidates=[
            {
                "quota_id": "A",
                "name": "阀门安装",
                "rerank_score": 0.90,
                "hybrid_score": 0.90,
            },
            {
                "quota_id": "B",
                "name": "喷淋钢管安装",
                "rerank_score": 0.15,
                "hybrid_score": 0.15,
            },
        ],
        reorder_candidates=False,
    )

    assert [item["quota_id"] for item in results] == ["A", "B"]
    valve = next(item for item in results if item["quota_id"] == "A")
    pipe = next(item for item in results if item["quota_id"] == "B")
    assert valve["param_match"] is False
    assert valve["feature_alignment_hard_conflict"] is True
    assert pipe["param_match"] is True


def test_logic_score_uses_bundle_total_cores_for_power_cable_family():
    validator = ParamValidator()
    results = validator.validate_candidates(
        query_text="电力电缆敷设 WDZN-BYJ 3x4+2x2.5",
        candidates=[
            {
                "quota_id": "A",
                "name": "铜芯电力电缆敷设 一般单芯电缆 电缆截面4mm2以下",
                "rerank_score": 0.95,
                "hybrid_score": 0.95,
            },
            {
                "quota_id": "B",
                "name": "铜芯电力电缆敷设 一般五芯电缆 电缆截面4mm2以下",
                "rerank_score": 0.10,
                "hybrid_score": 0.10,
            },
        ],
    )

    assert results[0]["quota_id"] == "B"
    single_core = next(item for item in results if item["quota_id"] == "A")
    five_core = next(item for item in results if item["quota_id"] == "B")
    assert five_core["logic_exact_primary_match"] is True
    assert five_core["logic_score"] > single_core["logic_score"]


def test_feature_alignment_rejects_cable_head_for_cable_laying_query():
    validator = ParamValidator()
    results = validator.validate_candidates(
        query_text="阻燃电力电缆 型号:ZRC-YJV-0.6/1kV,4x35+1x16 敷设方式、部位:室内穿管或桥架",
        candidates=[
            {
                "quota_id": "A",
                "name": "中间头制作与安装 1kV以下室内干包式铝芯电力电缆 电缆截面(mm2)≤35",
                "rerank_score": 0.95,
                "hybrid_score": 0.95,
            },
            {
                "quota_id": "B",
                "name": "室内敷设电力电缆 铜芯电力电缆敷设 电缆截面(mm2)≤35",
                "rerank_score": 0.10,
                "hybrid_score": 0.10,
            },
        ],
    )

    assert results[0]["quota_id"] == "B"
    wrong = next(item for item in results if item["quota_id"] == "A")
    assert wrong["param_match"] is False
    assert wrong["feature_alignment_hard_conflict"] is True


def test_feature_alignment_prefers_copper_cable_over_aluminum_for_yjv_query():
    validator = ParamValidator()
    results = validator.validate_candidates(
        query_text="阻燃变频电力电缆 型号、规格:ZRC-BPYJV-0.6/1kV,3x240+3x40 敷设方式、部位:室内穿管或桥架",
        candidates=[
            {
                "quota_id": "A",
                "name": "铝芯电力电缆敷设 电缆截面(mm2)≤240",
                "rerank_score": 0.95,
                "hybrid_score": 0.95,
            },
            {
                "quota_id": "B",
                "name": "铜芯电力电缆敷设 电缆截面(mm2)≤240",
                "rerank_score": 0.10,
                "hybrid_score": 0.10,
            },
        ],
    )

    assert results[0]["quota_id"] == "B"
    aluminum = next(item for item in results if item["quota_id"] == "A")
    copper = next(item for item in results if item["quota_id"] == "B")
    assert aluminum["feature_alignment_hard_conflict"] is True
    assert copper["feature_alignment_score"] > aluminum["feature_alignment_score"]


def test_logic_score_prefers_exact_switch_port_bucket():
    validator = ParamValidator()
    results = validator.validate_candidates(
        query_text="楼层交换机 24口千兆POE交换机",
        candidates=[
            {
                "quota_id": "A",
                "name": "交换机设备安装、调试 交换机 固定配置 >24口",
                "rerank_score": 0.95,
                "hybrid_score": 0.95,
            },
            {
                "quota_id": "B",
                "name": "交换机设备安装、调试 交换机 固定配置 ≤24口",
                "rerank_score": 0.10,
                "hybrid_score": 0.10,
            },
        ],
    )

    assert results[0]["quota_id"] == "B"
    over_bucket = next(item for item in results if item["quota_id"] == "A")
    exact_bucket = next(item for item in results if item["quota_id"] == "B")
    assert over_bucket["logic_hard_conflict"] is True
    assert exact_bucket["logic_exact_primary_match"] is True


def test_feature_rectify_prefers_exact_sprinkler_trait_anchor():
    validator = ParamValidator()
    results = validator.validate_candidates(
        query_text="喷头安装 直立型",
        candidates=[
            {
                "quota_id": "A",
                "name": "喷头安装 下垂型",
                "rerank_score": 0.96,
                "hybrid_score": 0.96,
            },
            {
                "quota_id": "B",
                "name": "喷头安装 直立型",
                "rerank_score": 0.10,
                "hybrid_score": 0.10,
            },
        ],
    )

    assert results[0]["quota_id"] == "B"
    exact_hit = next(item for item in results if item["quota_id"] == "B")
    wrong_hit = next(item for item in results if item["quota_id"] == "A")
    assert exact_hit["feature_alignment_exact_anchor_count"] >= 2
    assert wrong_hit["feature_alignment_hard_conflict"] is True


def test_feature_rectify_prefers_jdg_conduit_family_anchor():
    validator = ParamValidator()
    results = validator.validate_candidates(
        query_text="JDG20 暗敷",
        candidates=[
            {
                "quota_id": "A",
                "name": "镀锌钢管敷设 暗配",
                "rerank_score": 0.95,
                "hybrid_score": 0.95,
            },
            {
                "quota_id": "B",
                "name": "JDG管 配管 暗敷",
                "rerank_score": 0.12,
                "hybrid_score": 0.12,
            },
        ],
    )

    assert results[0]["quota_id"] == "B"
    best = next(item for item in results if item["quota_id"] == "B")
    assert best["feature_alignment_exact_anchor_count"] >= 3
    assert best["feature_alignment_score"] > 0.85


def test_feature_alignment_prefers_pressure_switch_over_regular_switch():
    validator = ParamValidator()
    results = validator.validate_candidates(
        query_text="压力开关安装",
        candidates=[
            {
                "quota_id": "A",
                "name": "按钮开关安装",
                "rerank_score": 0.95,
                "hybrid_score": 0.95,
            },
            {
                "quota_id": "B",
                "name": "消防压力开关安装",
                "rerank_score": 0.12,
                "hybrid_score": 0.12,
            },
        ],
    )

    assert results[0]["quota_id"] == "B"
    wrong = next(item for item in results if item["quota_id"] == "A")
    assert wrong["feature_alignment_hard_conflict"] is True


def test_feature_alignment_prefers_hvac_damper_over_generic_duct():
    validator = ParamValidator()
    results = validator.validate_candidates(
        query_text="风阀安装 电动调节阀",
        candidates=[
            {
                "quota_id": "A",
                "name": "风管制作安装",
                "rerank_score": 0.93,
                "hybrid_score": 0.93,
            },
            {
                "quota_id": "B",
                "name": "风阀安装 电动调节阀",
                "rerank_score": 0.11,
                "hybrid_score": 0.11,
            },
        ],
    )

    assert results[0]["quota_id"] == "B"
    best = next(item for item in results if item["quota_id"] == "B")
    assert best["feature_alignment_exact_anchor_count"] >= 2


def test_feature_alignment_prefers_alarm_button_over_generic_switch():
    validator = ParamValidator()
    results = validator.validate_candidates(
        query_text="手动报警按钮安装",
        candidates=[
            {
                "quota_id": "A",
                "name": "按钮开关安装",
                "rerank_score": 0.96,
                "hybrid_score": 0.96,
            },
            {
                "quota_id": "B",
                "name": "手动报警按钮安装",
                "rerank_score": 0.10,
                "hybrid_score": 0.10,
            },
        ],
    )

    assert results[0]["quota_id"] == "B"
    wrong = next(item for item in results if item["quota_id"] == "A")
    assert wrong["feature_alignment_hard_conflict"] is True


def test_feature_alignment_prefers_sleeve_over_generic_pipe():
    validator = ParamValidator()
    results = validator.validate_candidates(
        query_text="刚性防水套管制作安装",
        candidates=[
            {
                "quota_id": "A",
                "name": "钢管安装",
                "rerank_score": 0.95,
                "hybrid_score": 0.95,
            },
            {
                "quota_id": "B",
                "name": "刚性防水套管制作安装",
                "rerank_score": 0.11,
                "hybrid_score": 0.11,
            },
        ],
    )

    assert results[0]["quota_id"] == "B"
    wrong = next(item for item in results if item["quota_id"] == "A")
    assert wrong["feature_alignment_hard_conflict"] is True


def test_feature_alignment_prefers_gate_valve_over_check_valve():
    validator = ParamValidator()
    results = validator.validate_candidates(
        query_text="闸阀安装 DN100",
        candidates=[
            {
                "quota_id": "A",
                "name": "止回阀安装 DN100",
                "rerank_score": 0.96,
                "hybrid_score": 0.96,
            },
            {
                "quota_id": "B",
                "name": "闸阀安装 DN100",
                "rerank_score": 0.10,
                "hybrid_score": 0.10,
            },
        ],
    )

    assert results[0]["quota_id"] == "B"
    wrong = next(item for item in results if item["quota_id"] == "A")
    assert wrong["feature_alignment_hard_conflict"] is True


def test_feature_alignment_prefers_smoke_detector_over_heat_detector():
    validator = ParamValidator()
    results = validator.validate_candidates(
        query_text="感烟探测器安装",
        candidates=[
            {
                "quota_id": "A",
                "name": "感温探测器安装",
                "rerank_score": 0.95,
                "hybrid_score": 0.95,
            },
            {
                "quota_id": "B",
                "name": "感烟探测器安装",
                "rerank_score": 0.10,
                "hybrid_score": 0.10,
            },
        ],
    )

    assert results[0]["quota_id"] == "B"
    wrong = next(item for item in results if item["quota_id"] == "A")
    assert wrong["feature_alignment_hard_conflict"] is True


def test_feature_alignment_prefers_single_phase_five_hole_outlet():
    validator = ParamValidator()
    results = validator.validate_candidates(
        query_text="单相五孔插座安装",
        candidates=[
            {
                "quota_id": "A",
                "name": "三相三孔插座安装",
                "rerank_score": 0.96,
                "hybrid_score": 0.96,
            },
            {
                "quota_id": "B",
                "name": "单相五孔插座安装",
                "rerank_score": 0.10,
                "hybrid_score": 0.10,
            },
        ],
    )

    assert results[0]["quota_id"] == "B"
    wrong = next(item for item in results if item["quota_id"] == "A")
    assert wrong["feature_alignment_hard_conflict"] is True


def test_feature_alignment_prefers_toilet_over_squatting_pan():
    validator = ParamValidator()
    results = validator.validate_candidates(
        query_text="坐便器安装",
        candidates=[
            {
                "quota_id": "A",
                "name": "蹲便器安装",
                "rerank_score": 0.95,
                "hybrid_score": 0.95,
            },
            {
                "quota_id": "B",
                "name": "坐便器安装",
                "rerank_score": 0.12,
                "hybrid_score": 0.12,
            },
        ],
    )

    assert results[0]["quota_id"] == "B"
    wrong = next(item for item in results if item["quota_id"] == "A")
    assert wrong["feature_alignment_hard_conflict"] is True


def test_feature_alignment_prefers_filter_over_generic_valve():
    validator = ParamValidator()
    results = validator.validate_candidates(
        query_text="Y型过滤器 DN50",
        candidates=[
            {
                "quota_id": "A",
                "name": "焊接法兰阀安装 公称直径(mm以内) 50",
                "rerank_score": 0.96,
                "hybrid_score": 0.96,
            },
            {
                "quota_id": "B",
                "name": "Y型过滤器安装(法兰连接) 公称直径(mm以内) 50",
                "rerank_score": 0.10,
                "hybrid_score": 0.10,
            },
        ],
    )

    assert results[0]["quota_id"] == "A"
    right = next(item for item in results if item["quota_id"] == "B")
    wrong = next(item for item in results if item["quota_id"] == "A")
    assert right["param_rectify_selected"] is True
    assert "feature_rectify" in right["param_rectify_selected_rules"]
    assert right["feature_alignment_score"] >= wrong["feature_alignment_score"]


def test_feature_alignment_prefers_soft_joint_over_valve():
    validator = ParamValidator()
    results = validator.validate_candidates(
        query_text="可曲挠橡胶接头 DN100",
        candidates=[
            {
                "quota_id": "A",
                "name": "焊接法兰阀安装 公称直径(mm以内) 100",
                "rerank_score": 0.95,
                "hybrid_score": 0.95,
            },
            {
                "quota_id": "B",
                "name": "可曲挠橡胶接头安装 公称直径(mm以内) 100",
                "rerank_score": 0.10,
                "hybrid_score": 0.10,
            },
        ],
    )

    assert results[0]["quota_id"] == "A"
    right = next(item for item in results if item["quota_id"] == "B")
    wrong = next(item for item in results if item["quota_id"] == "A")
    assert right["param_rectify_selected"] is True
    assert "feature_rectify" in right["param_rectify_selected_rules"]
    assert right["feature_alignment_score"] >= wrong["feature_alignment_score"]


def test_feature_alignment_prefers_slot_bridge_over_tray_bridge():
    validator = ParamValidator()
    results = validator.validate_candidates(
        query_text="钢制槽式桥架",
        candidates=[
            {
                "quota_id": "A",
                "name": "钢制托盘式桥架安装(宽+高mm以下) 1000",
                "rerank_score": 0.96,
                "hybrid_score": 0.96,
            },
            {
                "quota_id": "B",
                "name": "钢制槽式桥架安装(宽+高mm以下) 400",
                "rerank_score": 0.12,
                "hybrid_score": 0.12,
            },
        ],
    )

    assert results[0]["quota_id"] == "B"
    wrong = next(item for item in results if item["quota_id"] == "A")
    assert wrong["feature_alignment_hard_conflict"] is True


def test_feature_alignment_prefers_wall_speaker_for_emergency_call():
    validator = ParamValidator()
    results = validator.validate_candidates(
        query_text="紧急呼叫扬声器",
        candidates=[
            {
                "quota_id": "A",
                "name": "消防广播(扬声器)安装 扬声器 吸顶式()",
                "rerank_score": 0.96,
                "hybrid_score": 0.96,
            },
            {
                "quota_id": "B",
                "name": "消防广播(扬声器)安装 扬声器 壁挂式()",
                "rerank_score": 0.08,
                "hybrid_score": 0.08,
            },
        ],
    )

    assert results[0]["quota_id"] == "B"
    ceiling = next(item for item in results if item["quota_id"] == "A")
    wall = next(item for item in results if item["quota_id"] == "B")
    assert wall["feature_alignment_score"] > ceiling["feature_alignment_score"]


def test_logic_score_prefers_named_kg_weight_bucket():
    validator = ParamValidator()
    results = validator.validate_candidates(
        query_text="暖风机 重量(kg以内) 160",
        candidates=[
            {
                "quota_id": "A",
                "name": "暖风机安装 重量(kg以内) 2000",
                "rerank_score": 0.96,
                "hybrid_score": 0.96,
            },
            {
                "quota_id": "B",
                "name": "暖风机安装 重量(kg以内) 160",
                "rerank_score": 0.10,
                "hybrid_score": 0.10,
            },
        ],
    )

    assert results[0]["quota_id"] == "A"
    exact_hit = next(item for item in results if item["quota_id"] == "B")
    large_bucket = next(item for item in results if item["quota_id"] == "A")
    assert exact_hit["param_rectify_selected"] is True
    assert "tier_rectify" in exact_hit["param_rectify_selected_rules"]
    assert exact_hit["param_score"] > large_bucket["param_score"]


def test_logic_score_prefers_named_item_count_bucket():
    validator = ParamValidator()
    results = validator.validate_candidates(
        query_text="背景音乐系统调试 分区试响 扬声器数量≤50台",
        candidates=[
            {
                "quota_id": "A",
                "name": "分区试响 扬声器数量≤100台",
                "rerank_score": 0.60,
                "hybrid_score": 0.60,
            },
            {
                "quota_id": "B",
                "name": "分区试响 扬声器数量≤50台",
                "rerank_score": 0.55,
                "hybrid_score": 0.55,
            },
        ],
    )

    assert results[0]["quota_id"] == "B"
    exact_hit = next(item for item in results if item["quota_id"] == "B")
    large_bucket = next(item for item in results if item["quota_id"] == "A")
    assert exact_hit["param_score"] > large_bucket["param_score"]
    assert exact_hit["logic_detail"] != large_bucket["logic_detail"]


def test_feature_alignment_rejects_bridge_support_for_pipe_support_query():
    validator = ParamValidator()
    results = validator.validate_candidates(
        query_text="管道支架制作安装",
        candidates=[
            {
                "quota_id": "A",
                "name": "电缆桥架支撑架制作",
                "rerank_score": 0.96,
                "hybrid_score": 0.96,
            },
            {
                "quota_id": "B",
                "name": "室内管道管道支架制作安装 一般管架",
                "rerank_score": 0.10,
                "hybrid_score": 0.10,
            },
        ],
    )

    assert results[0]["quota_id"] == "B"
    wrong = next(item for item in results if item["quota_id"] == "A")
    assert wrong["feature_alignment_hard_conflict"] is True
    assert "家族冲突" in wrong["feature_alignment_detail"]


def test_feature_alignment_rejects_mismatched_valve_accessory_entity():
    validator = ParamValidator()
    results = validator.validate_candidates(
        query_text="Y型过滤器 DN50",
        candidates=[
            {
                "quota_id": "A",
                "name": "水表组成安装 螺翼式水表 DN50",
                "rerank_score": 0.96,
                "hybrid_score": 0.96,
            },
            {
                "quota_id": "B",
                "name": "Y型过滤器安装(法兰连接) 公称直径(mm以内) 50",
                "rerank_score": 0.10,
                "hybrid_score": 0.10,
            },
        ],
    )

    assert results[0]["quota_id"] == "B"
    wrong = next(item for item in results if item["quota_id"] == "A")
    assert wrong["feature_alignment_hard_conflict"] is True
    assert "家族内实体冲突" in wrong["feature_alignment_detail"]
