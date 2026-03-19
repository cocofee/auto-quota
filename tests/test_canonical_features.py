from src.bill_cleaner import clean_bill_items
from src.text_parser import TextParser


def test_text_parser_extracts_complex_cable_bundle():
    parser = TextParser()
    result = parser.parse("WDZN-BYJ 3x4+2x2.5 电缆敷设")
    assert result["cable_section"] == 4
    assert result["cable_bundle"] == [
        {"cores": 3, "section": 4.0, "role": "main"},
        {"cores": 2, "section": 2.5, "role": "aux"},
    ]


def test_parse_canonical_includes_context_prior():
    parser = TextParser()
    features = parser.parse_canonical(
        "WDZN-BYJ 3x4+2x2.5 电力电缆敷设",
        specialty="C4",
        context_prior={"specialty": "C4", "context_hints": ["桥架"]},
    )
    assert features["specialty"] == "C4"
    assert features["entity"] == "电缆"
    assert features["cable_section"] == 4
    assert features["numeric_params"]["cable_cores"] == 5
    assert features["context_prior"]["context_hints"] == ["桥架"]


def test_parse_canonical_prefers_cable_entity_over_bridge_context_words():
    parser = TextParser()
    features = parser.parse_canonical(
        "阻燃变频电力电缆 型号、规格:ZRC-BPYJV-0.6/1kV,3x240+3x40 敷设方式、部位：室内穿管或桥架",
        specialty="A4",
    )

    assert features["entity"] == "电缆"
    assert features["family"] == "cable_family"
    assert features["cable_section"] == 240
    assert features["material"] == "铜芯"


def test_parse_canonical_marks_mineral_insulated_cable_material():
    parser = TextParser()
    features = parser.parse_canonical(
        "矿物绝缘电力电缆 型号:BTLY-3x185+2x95 敷设方式、部位:桥架或配管内敷设",
        specialty="A4",
    )

    assert features["entity"] == "电缆"
    assert features["family"] == "cable_family"
    assert features["material"] == "矿物绝缘电缆"


def test_parse_canonical_separates_cable_head_from_cable_body():
    parser = TextParser()
    features = parser.parse_canonical(
        "中间头制作与安装 1kV以下室内干包式铝芯电力电缆 电缆截面(mm2)≤240",
        specialty="A4",
    )

    assert features["entity"] == "电缆头"
    assert features["family"] == "cable_head_accessory"


def test_clean_bill_items_attaches_context_prior_and_canonical_features():
    items = clean_bill_items([
        {
            "name": "电力电缆敷设",
            "description": "WDZN-BYJ 3x4+2x2.5",
            "section": "电气工程",
            "sheet_name": "安装",
        }
    ])
    item = items[0]
    assert item["context_prior"]["specialty"] == item.get("specialty", "")
    assert item["canonical_features"]["cable_section"] == 4


def test_parse_canonical_normalizes_alias_material_and_connection():
    parser = TextParser()
    features = parser.parse_canonical(
        "喷淋管 DN100 丝扣连接",
        specialty="C9",
        params={"dn": 100, "material": "喷淋管", "connection": "丝扣连接"},
    )

    assert features["material"] == "喷淋钢管"
    assert features["connection"] == "螺纹连接"
    assert features["system"] == "消防"


def test_parse_canonical_detects_hydrant_entity_and_system():
    parser = TextParser()
    features = parser.parse_canonical("室内消火栓 暗装 带自救卷盘 DN65", specialty="C9")

    assert features["entity"] == "消火栓"
    assert features["system"] == "消防"
    assert "带自救卷盘" in features["traits"]


def test_parse_canonical_detects_faucet_entity_and_plumbing_system():
    parser = TextParser()
    features = parser.parse_canonical("感应式水龙头 DN15", specialty="C10")

    assert features["entity"] == "水龙头"
    assert features["system"] == "给排水"


def test_parse_canonical_keeps_conduit_surface_method_and_entity_system_fallback():
    parser = TextParser()
    features = parser.parse_canonical("JDG20 暗敷")

    assert features["entity"] == "配管"
    assert features["install_method"] == "暗敷"
    assert features["system"] == "电气"


def test_parse_canonical_infers_electrical_system_from_distribution_box():
    parser = TextParser()
    features = parser.parse_canonical("配电箱 明装 半周长500mm以内")

    assert features["entity"] == "配电箱"
    assert features["install_method"] == "明装"
    assert features["system"] == "电气"


def test_parse_canonical_extracts_conduit_family_as_material():
    parser = TextParser()
    features = parser.parse_canonical("JDG20 暗敷")

    assert features["entity"] == "配管"
    assert features["material"] == "JDG管"
    assert features["canonical_name"] == "JDG管配管"


def test_parse_canonical_extracts_metal_flexible_conduit_and_system():
    parser = TextParser()
    features = parser.parse_canonical("金属软管敷设")

    assert features["entity"] == "金属软管"
    assert features["system"] == "电气"
    assert features["canonical_name"] == "金属软管"


def test_parse_canonical_extracts_fire_traits_for_sprinkler_and_alarm_valve():
    parser = TextParser()

    sprinkler = parser.parse_canonical("喷头安装 直立型", specialty="C9")
    valve = parser.parse_canonical("报警阀组安装 湿式", specialty="C9")

    assert sprinkler["entity"] == "喷头"
    assert sprinkler["system"] == "消防"
    assert "直立型" in sprinkler["traits"]
    assert valve["entity"] == "报警阀组"
    assert valve["system"] == "消防"
    assert "湿式" in valve["traits"]


def test_parse_canonical_extracts_fire_and_electrical_components():
    parser = TextParser()

    pressure = parser.parse_canonical("压力开关安装", specialty="C9")
    junction_box = parser.parse_canonical("接线盒安装", specialty="C4")
    light = parser.parse_canonical("灯具安装 吸顶式", specialty="C4")

    assert pressure["entity"] == "压力开关"
    assert pressure["system"] == "消防"
    assert junction_box["entity"] == "接线盒"
    assert junction_box["system"] == "电气"
    assert light["entity"] == "灯具"
    assert light["system"] == "电气"
    assert "吸顶式" in light["traits"]


def test_parse_canonical_extracts_hvac_and_sleeve_components():
    parser = TextParser()

    sleeve = parser.parse_canonical("刚性防水套管制作安装", specialty="C10")
    damper = parser.parse_canonical("风阀安装 电动调节阀", specialty="C7")
    outlet = parser.parse_canonical("风口安装 散流器", specialty="C7")
    fan = parser.parse_canonical("风机安装 离心式", specialty="C7")

    assert sleeve["entity"] == "套管"
    assert sleeve["system"] == "给排水"
    assert "刚性" in sleeve["traits"]
    assert damper["entity"] == "风阀"
    assert damper["system"] == "通风空调"
    assert "电动调节" in damper["traits"]
    assert outlet["entity"] == "风口"
    assert outlet["system"] == "通风空调"
    assert fan["entity"] == "风机"
    assert fan["system"] == "通风空调"
    assert "离心式" in fan["traits"]


def test_parse_canonical_extracts_fire_alarm_and_plumbing_devices():
    parser = TextParser()

    alarm_button = parser.parse_canonical("手动报警按钮安装", specialty="C9")
    broadcast = parser.parse_canonical("消防广播安装", specialty="C9")
    phone = parser.parse_canonical("消防电话插孔安装", specialty="C9")
    toilet = parser.parse_canonical("坐便器安装", specialty="C10")
    basin = parser.parse_canonical("洗脸盆安装", specialty="C10")
    pump = parser.parse_canonical("潜水泵安装", specialty="C10")
    support = parser.parse_canonical("支吊架制作安装", specialty="C10")

    assert alarm_button["entity"] == "报警按钮"
    assert alarm_button["system"] == "消防"
    assert broadcast["entity"] == "消防广播"
    assert phone["entity"] == "消防电话插孔"
    assert toilet["entity"] == "坐便器"
    assert toilet["system"] == "给排水"
    assert basin["entity"] == "洗脸盆"
    assert pump["entity"] == "水泵"
    assert support["entity"] == "支吊架"


def test_parse_canonical_extracts_valve_lighting_outlet_and_fixture_subtypes():
    parser = TextParser()

    gate_valve = parser.parse_canonical("闸阀安装 DN100", specialty="C10")
    smoke = parser.parse_canonical("感烟探测器安装", specialty="C9")
    lamp = parser.parse_canonical("吸顶灯安装", specialty="C4")
    outlet = parser.parse_canonical("单相五孔插座安装", specialty="C4")
    squatting = parser.parse_canonical("蹲便器安装", specialty="C10")
    urinal = parser.parse_canonical("小便器安装", specialty="C10")

    assert gate_valve["entity"] == "闸阀"
    assert gate_valve["system"] == "给排水"
    assert smoke["entity"] == "探测器"
    assert "感烟" in smoke["traits"]
    assert lamp["entity"] == "吸顶灯"
    assert "吸顶灯" in lamp["traits"]
    assert outlet["entity"] == "插座"
    assert "单相" in outlet["traits"]
    assert "五孔" in outlet["traits"]
    assert squatting["entity"] == "蹲便器"
    assert urinal["entity"] == "小便器"


def test_parse_canonical_extracts_filter_soft_joint_and_sink_entities():
    parser = TextParser()

    filter_item = parser.parse_canonical("Y型过滤器安装(法兰连接) 公称直径(mm以内) 50", specialty="C10")
    soft_joint = parser.parse_canonical("可曲挠橡胶接头安装 公称直径(mm以内) 100", specialty="C10")
    sink = parser.parse_canonical("单孔水槽 插材质:不锈钢 组装形式:成品", specialty="C10")

    assert filter_item["entity"] == "过滤器"
    assert filter_item["family"] == "valve_accessory"
    assert filter_item["system"] == "给排水"
    assert soft_joint["entity"] == "软接头"
    assert soft_joint["family"] == "valve_accessory"
    assert soft_joint["system"] == "给排水"
    assert sink["entity"] == "洗涤盆"
    assert sink["family"] == "sanitary_fixture"
    assert sink["system"] == "给排水"


def test_parse_canonical_extracts_bridge_and_ventilation_subtypes():
    parser = TextParser()

    bridge = parser.parse_canonical("钢制槽式桥架安装(宽+高mm以下) 400", specialty="C4")
    speaker = parser.parse_canonical("紧急呼叫扬声器", specialty="C9")
    toilet_fan = parser.parse_canonical("卫生间通风器安装 风量(m3/h) ≤400", specialty="C7")

    assert bridge["entity"] == "桥架"
    assert bridge["family"] == "bridge_raceway"
    assert "槽式" in bridge["traits"]
    assert speaker["entity"] == "消防广播"
    assert speaker["install_method"] == "挂壁"
    assert toilet_fan["entity"] == "卫生间通风器"
    assert toilet_fan["family"] == "air_device"
    assert toilet_fan["system"] == "通风空调"
    assert "卫生间通风器" in toilet_fan["traits"]


def test_text_parser_extracts_named_kg_weight_and_distribution_box_defaults():
    parser = TextParser()

    heater = parser.parse("暖风机安装 重量(kg以内) 160")
    box = parser.parse_canonical("配电箱 1AP1", specialty="C4")
    faucet = parser.parse("感应式水龙头")

    assert heater["weight_t"] == 0.16
    assert box["install_method"] == "落地"
    assert faucet["dn"] == 15


def test_parse_canonical_distinguishes_pipe_support_and_bridge_support_family():
    parser = TextParser()
    pipe_support = parser.parse_canonical("管道支架制作安装", specialty="C10")
    bridge_support = parser.parse_canonical("电缆桥架支撑架制作", specialty="C4")

    assert pipe_support["entity"] == "支吊架"
    assert pipe_support["family"] == "pipe_support"
    assert bridge_support["family"] == "bridge_support"
