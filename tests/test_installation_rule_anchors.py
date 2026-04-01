from src.param_validator import ParamValidator
from src.query_builder import build_quota_query
from src.text_parser import TextParser


parser = TextParser()


def test_text_parser_extracts_installation_anchor_fields():
    pipe = parser.parse("WDZ-YJY-3x150+2x70 电缆穿管敷设")
    tray_pipe = parser.parse("ZRC-YJV-0.6/1kV,4x35+1x16 室内穿管或桥架")
    trunking = parser.parse("BV-3x2.5 电线穿线槽")
    high_voltage = parser.parse("10kV 高压开关柜")
    low_voltage = parser.parse("低压配电柜 400V")
    bridge_cm = parser.parse("桥架 20x10")
    bridge_model = parser.parse("桥架 MR-300x150")

    assert pipe["laying_method"] == "穿管"
    assert tray_pipe["laying_method"] == "桥架/穿管"
    assert trunking["laying_method"] == "线槽"
    assert high_voltage["voltage_level"] == "高压"
    assert low_voltage["voltage_level"] == "低压"
    assert bridge_cm["bridge_wh_sum"] == 300
    assert bridge_model["bridge_wh_sum"] == 450


def test_text_parser_extracts_rule_driven_anchor_fields():
    valve = parser.parse("金属阀门 类型:蝶阀 规格、压力等级:DN100 连接形式:焊接")
    plastic_valve = parser.parse("螺纹阀门 类型:截止阀 规格、压力等级:De63 1.6MPa")
    plastic_pipe = parser.parse("塑料管 规格:De63 热熔连接")
    support = parser.parse("管道支架 材质:C型槽钢 管架形式:抗震支吊架")
    coating = parser.parse("管道刷油 除锈级别:手工除锈 油漆品种:红丹防锈漆、银粉漆")
    sanitary = parser.parse("单孔水槽 插材质:不锈钢 组装形式:成品")

    assert valve["valve_type"] == "蝶阀"
    assert plastic_valve["dn"] == 50
    assert plastic_pipe["dn"] == 63
    assert support["support_material"] == "C型槽钢"
    assert coating["surface_process"] == "手工除锈/刷油/红丹防锈漆/银粉漆"
    assert sanitary["sanitary_subtype"] == "洗涤盆"


def test_query_builder_uses_switchgear_voltage_anchor():
    name = "高压成套配电柜"
    description = "名称:10kV开关柜 规格:KYN28A-12"
    full_text = f"{name} {description}"
    params = parser.parse(full_text)

    query = build_quota_query(
        parser,
        name,
        description,
        specialty="C4",
        bill_params=params,
    )

    assert "10kV开关柜安装" in query


def test_query_builder_uses_laying_method_and_bridge_bucket():
    wire_query = build_quota_query(
        parser,
        "配线",
        "BV-3x2.5 电线穿线槽",
        specialty="C4",
    )
    bridge_query = build_quota_query(
        parser,
        "桥架",
        "规格:MR-300x150 类型:槽式",
        specialty="C4",
    )

    assert "线槽配线" in wire_query
    assert "宽+高" in bridge_query
    assert "450" in bridge_query
    assert "槽式桥架" in bridge_query


def test_query_builder_builds_sanitary_query_for_sensor_squat_toilet():
    query = build_quota_query(
        parser,
        "感应蹲便器",
        "材质:瓷质 规格、类型:感应延时 附件名称、数量:感应延时器",
        specialty="C10",
    )

    assert "蹲式大便器安装" in query
    assert "感应开关" in query


def test_param_validator_rejects_wrong_laying_method_and_voltage_level():
    validator = ParamValidator()

    cable_results = validator.validate_candidates(
        query_text="WDZ-YJY-3x150+2x70 电缆穿管敷设",
        candidates=[
            {
                "quota_id": "A",
                "name": "电缆沿桥架、线槽敷设 电缆截面(mm2以内) 185",
                "rerank_score": 0.95,
                "hybrid_score": 0.95,
            },
            {
                "quota_id": "B",
                "name": "电缆穿导管敷设 电缆截面(mm2以内) 185",
                "rerank_score": 0.10,
                "hybrid_score": 0.10,
            },
        ],
    )
    switchgear_results = validator.validate_candidates(
        query_text="10kV 高压开关柜",
        candidates=[
            {
                "quota_id": "A",
                "name": "低压成套配电柜",
                "rerank_score": 0.95,
                "hybrid_score": 0.95,
            },
            {
                "quota_id": "B",
                "name": "10kV开关柜安装",
                "rerank_score": 0.10,
                "hybrid_score": 0.10,
            },
        ],
    )

    assert cable_results[0]["quota_id"] == "B"
    assert next(item for item in cable_results if item["quota_id"] == "A")["feature_alignment_hard_conflict"] is True
    assert switchgear_results[0]["quota_id"] == "B"
    assert next(item for item in switchgear_results if item["quota_id"] == "A")["feature_alignment_hard_conflict"] is True


def test_param_validator_uses_bridge_wh_sum_upward_bucket():
    validator = ParamValidator()
    results = validator.validate_candidates(
        query_text="钢制槽式桥架 MR-300x150",
        candidates=[
            {
                "quota_id": "A",
                "name": "钢制槽式桥架安装(宽+高mm以下) 400",
                "rerank_score": 0.95,
                "hybrid_score": 0.95,
            },
            {
                "quota_id": "B",
                "name": "钢制槽式桥架安装(宽+高mm以下) 600",
                "rerank_score": 0.10,
                "hybrid_score": 0.10,
            },
        ],
    )

    assert results[0]["quota_id"] == "B"
    wrong_bucket = next(item for item in results if item["quota_id"] == "A")
    right_bucket = next(item for item in results if item["quota_id"] == "B")
    assert wrong_bucket["logic_hard_conflict"] is True or wrong_bucket["param_match"] is False
    assert right_bucket["param_score"] > wrong_bucket["param_score"]


def test_param_validator_prefers_exact_valve_type_and_support_material():
    validator = ParamValidator()

    valve_results = validator.validate_candidates(
        query_text="金属阀门 类型:蝶阀 规格、压力等级:DN100",
        candidates=[
            {
                "quota_id": "A",
                "name": "法兰阀门安装 类型:闸阀 公称直径(mm以内) 100",
                "rerank_score": 0.95,
                "hybrid_score": 0.95,
            },
            {
                "quota_id": "B",
                "name": "法兰阀门安装 类型:蝶阀 公称直径(mm以内) 100",
                "rerank_score": 0.10,
                "hybrid_score": 0.10,
            },
        ],
    )
    support_results = validator.validate_candidates(
        query_text="管道支架 材质:槽钢",
        candidates=[
            {
                "quota_id": "A",
                "name": "管道支架制作安装 材质:角钢",
                "rerank_score": 0.95,
                "hybrid_score": 0.95,
            },
            {
                "quota_id": "B",
                "name": "管道支架制作安装 材质:槽钢",
                "rerank_score": 0.10,
                "hybrid_score": 0.10,
            },
        ],
    )

    assert valve_results[0]["quota_id"] == "B"
    assert next(item for item in valve_results if item["quota_id"] == "A")["param_match"] is False
    assert support_results[0]["quota_id"] == "B"
    assert next(item for item in support_results if item["quota_id"] == "A")["param_match"] is False


def test_param_validator_uses_surface_process_as_soft_sort_signal():
    validator = ParamValidator()
    results = validator.validate_candidates(
        query_text="管道刷油 油漆品种:红丹防锈漆",
        candidates=[
            {
                "quota_id": "A",
                "name": "管道刷油 调和漆 第一遍",
                "rerank_score": 0.95,
                "hybrid_score": 0.95,
            },
            {
                "quota_id": "B",
                "name": "管道刷油 红丹防锈漆 第一遍",
                "rerank_score": 0.10,
                "hybrid_score": 0.10,
            },
        ],
    )

    assert results[0]["quota_id"] == "A"
    best = next(item for item in results if item["quota_id"] == "B")
    assert best["param_rectify_selected"] is True
    assert "feature_rectify" in best["param_rectify_selected_rules"]
    assert best["param_score"] > next(
        item for item in results if item["quota_id"] == "A"
    )["param_score"]


def test_text_parser_does_not_force_generic_toilet_subtype():
    result = parser.parse("大便器 规格、类型:普通阀冲洗")
    assert "sanitary_subtype" not in result
