from src.text_parser import parser


def test_parse_extracts_total_cable_cores_from_complex_bundle():
    result = parser.parse("WDZN-BYJ 3x4+2x2.5 电缆敷设")

    assert result["cable_section"] == 4
    assert result["cable_cores"] == 5


def test_parse_extracts_port_count_for_switch():
    result = parser.parse("楼层交换机 24口千兆POE交换机")

    assert result["port_count"] == 24


def test_parse_does_not_treat_router_as_circuit_count():
    result = parser.parse("3路由交换机 24口")

    assert result.get("circuits") is None
    assert result["port_count"] == 24


def test_parse_extracts_generic_item_count_bucket():
    result = parser.parse("背景音乐系统调试 分区试响 扬声器数量≤50台")

    assert result["item_count"] == 50


def test_parse_extracts_generic_item_length_bucket():
    result = parser.parse("预制钢筋混凝土管桩 桩长、数量：平均桩长8米")

    assert result["item_length"] == 8.0


def test_parse_extracts_item_length_from_stake_length_alias():
    result = parser.parse("圆木桩 粧长:4m 材质:松木桩")

    assert result["item_length"] == 4.0


def test_parse_extracts_item_length_from_wall_height():
    result = parser.parse("挡土墙 挡墙高度4m内")

    assert result["item_length"] == 4.0


def test_parse_extracts_item_length_from_excavation_depth():
    result = parser.parse("挖基坑土方 挖土深度:4米内")

    assert result["item_length"] == 4.0


def test_parse_extracts_item_length_from_manual_cart_transport():
    result = parser.parse("余方弃置 人力车运50m")

    assert result["item_length"] == 50.0


def test_parse_extracts_dn_from_pipe_material_suffix_number():
    result = parser.parse("水喷淋钢管 材质：内外涂覆EP碳钢管65 连接形式：卡压连接")

    assert result["dn"] == 65


def test_parse_extracts_dn_from_slotting_spec_bucket():
    result = parser.parse("凿(压、切割)槽 名称:剔槽 规格:20以内 类型:后砌墙")

    assert result["dn"] == 20


def test_parse_does_not_treat_area_as_item_length():
    result = parser.parse("墙面块料面层 面砖 每块面积 预拌砂浆(干混) ≤0.64m2")

    assert result.get("item_length") is None


def test_parse_extracts_cable_section_from_plain_mm2_spec():
    result = parser.parse(
        "电力电缆头 名称:1kV以下户内干包式铜芯电力电缆终端头制作、安装 规格:16mm2 材质、类型:4芯"
    )

    assert result["cable_section"] == 16.0
    assert result["cable_cores"] == 4


def test_parse_extracts_cable_section_from_prefixed_wire_model():
    result = parser.parse(
        "配线5 名称:铜芯塑料绝缘线 规格、型号:WDZAN-BYJR-2.5 材质:铜芯线 敷设方式、部位:桥架中布放或穿管敷设"
    )

    assert result["cable_section"] == 2.5


def test_parse_does_not_treat_sleeve_size_as_cable_section():
    result = parser.parse("一般填料套管 名称：一般填料套管 规格：1700*500")

    assert result.get("cable_section") is None
    assert result["dn"] == 500


def test_parse_extracts_conduit_dn_from_config_form_spec():
    result = parser.parse("配管 材质：JDG 规格：20 配置形式:暗敷")

    assert result["conduit_type"] == "JDG"
    assert result["conduit_dn"] == 20


def test_parse_extracts_dn_from_civil_defense_valve_rectangle_spec():
    result = parser.parse("插板阀 500×200 名称：插板阀 规格：500×200 工作内容：供货、安装、调试；含法兰制作安装")

    assert result["dn"] == 500


def test_parse_extracts_dn_from_refrigerant_distributor_range():
    result = parser.parse("冷媒分配器 类型:冷媒分配器 规格:6.35≤φ≤19.05 满足设计图纸及相关规范")

    assert result["dn"] == 20
