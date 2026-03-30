# -*- coding: utf-8 -*-

from src.query_builder import build_primary_query_profile, build_quota_query, discover_primary_subject
from src.text_parser import TextParser


parser = TextParser()


def test_discover_primary_subject_prefers_front_entity_over_support_tail():
    subject = discover_primary_subject(
        "冷媒管",
        "去磷无缝紫铜管Φ9.52 焊接连接 室内敷设 管道支架制作安装",
    )

    assert subject["primary_subject"] == "冷媒管"
    assert "Φ9.52" in subject["key_specs"]
    assert "支架制作安装" in "".join(subject["suppressed_terms"])


def test_discover_primary_subject_prefers_named_field_over_generic_bill_name():
    subject = discover_primary_subject(
        "碳钢阀门",
        "名称:280℃防火阀 规格:1500*320",
        {"名称": "280℃防火阀", "规格": "1500*320"},
    )

    assert subject["primary_subject"] == "280℃防火阀"
    assert "1500*320" in subject["key_specs"]


def test_build_quota_query_does_not_hijack_pipe_item_with_support_tail():
    query = build_quota_query(
        parser,
        "冷媒管",
        "去磷无缝紫铜管Φ9.52 焊接连接 室内敷设 管道支架制作安装",
    )

    assert "支架制作安装" not in query
    assert "一般管架" not in query
    assert ("铜管" in query) or ("冷媒管" in query)


def test_build_quota_query_does_not_hijack_pipe_item_with_sleeve_tail():
    query = build_quota_query(
        parser,
        "钢塑复合管",
        "DN50 螺纹连接 综合单价中含穿非混凝土构件的套管制作及安装",
    )

    assert "套管制作" not in query
    assert "钢塑复合管" in query
    assert "DN50" in query


def test_build_quota_query_generic_item_uses_named_field_and_specs():
    query = build_quota_query(
        parser,
        "其他构件",
        "名称、类型:石英石台面 规格:20mm 其他:详见图纸",
    )

    assert "20mm" in query
    assert len(query.split()) >= 2


def test_build_quota_query_pipe_route_keeps_field_subject_seed_terms():
    query = build_quota_query(
        parser,
        "塑料管",
        "连接形式:电热熔连接//安装部位:室外//介质:给水//材质、规格:PE100给水管 公称压力1.0MPa DN100//压力试验及吹、洗设计要求:按规范要求",
    )

    assert "PE100" in query
    assert "DN100" in query
    assert ("电热熔连接" in query) or ("热熔连接" in query)


def test_build_quota_query_empty_name_routes_bridge_by_discovered_subject():
    query = build_quota_query(
        parser,
        "",
        "\u5f3a\u7535\u6865\u67b6 600mm\u00d7200mm \u542b\u7a7f\u5899\u53ca\u7a7f\u697c\u677f\u9632\u706b\u5c01\u5835",
    )

    assert "\u6865\u67b6" in query
    assert "\u5b89\u88c5" in query
    assert "\u5835\u6d1e" not in query


def test_build_quota_query_empty_name_keeps_front_segment_subject_seed_terms():
    query = build_quota_query(
        parser,
        "",
        "送风口、回风口 定制成品石膏板检修口（活动板） 定制成品石膏检修口（套口） 铝合金护角边框",
    )

    assert "送风口" in query
    assert "检修口" not in query


def test_discover_primary_subject_trims_wind_outlet_auxiliary_inspection_tail():
    subject = discover_primary_subject(
        "",
        "送风口、回风口 定制成品石膏板检修口（活动板） 定制成品石膏检修口（套口） 铝合金护角边框",
    )

    assert subject["primary_subject"] == "送风口、回风口"


def test_build_quota_query_empty_name_does_not_override_strong_normalized_subject():
    query = build_quota_query(
        parser,
        "",
        "墙面喷刷涂料 PT-202白色无机防水涂料 满刮二遍环保耐水腻子磨平 1:3水泥砂浆找平层 专用界面剂 具体详见设计图纸，应符合设计图及招标文件等相关文件的要求",
    )

    assert "墙面涂料" in query
    assert "PT-202" not in query


def test_build_quota_query_does_not_misroute_glass_partition_to_pvc_conduit():
    query = build_quota_query(
        parser,
        "玻璃隔断",
        "钢化夹胶玻璃(磨砂6+6MM) PVC挡水条 包含门及加工、运输、安装、锁具、把手、五金配件等全部施工内容 具体详见设计图纸",
    )

    assert "玻璃隔断" in query
    assert "PVC阻燃塑料管敷设" not in query


def test_build_quota_query_keeps_bridge_object_route_for_cable_bridge_item():
    query = build_quota_query(
        parser,
        "电缆桥架",
        "材质、形式:不锈钢槽式桥架 断面:50*50，壁厚1.0mm 配套零部件:含连接片、紧固件、接地编织带等 (槽盒内设隔板） 其他技术要求:符合设计及施工规范要求",
    )

    assert "桥架" in query
    assert "安装" in query
    assert "室内敷设电力电缆 沿桥架" not in query


def test_discover_primary_subject_trims_numeric_spec_tail_for_device_subject():
    subject = discover_primary_subject(
        "",
        "空气加热器（冷却器）全热交换器 新风量600 配套电机功率0.55kW 其他技术要求:符合设计及施工规范要求",
    )

    assert subject["primary_subject"] == "空气加热器 全热交换器"


def test_discover_primary_subject_extracts_real_object_after_generic_component_prefix():
    subject = discover_primary_subject(
        "其他构件",
        "C20混凝土挡水坎",
    )

    assert subject["primary_subject"] == "混凝土挡水坎"


def test_discover_primary_subject_extracts_real_object_after_generic_stone_prefix():
    subject = discover_primary_subject(
        "石材零星项目",
        "ST-201灰色石材门槛石 30厚1:3干硬性水泥砂浆结合层，表面撒水泥粉 石材结晶处理",
    )

    assert "门槛石" in subject["primary_subject"]


def test_discover_primary_subject_keeps_generic_subject_when_tail_is_only_field_labels():
    subject = discover_primary_subject(
        "平面砂浆找平层",
        "找平层厚度、砂浆配合比:1:3 C20水泥砂浆找平",
    )

    assert subject["primary_subject"] == "平面砂浆找平层"


def test_build_primary_query_profile_adds_quota_alias_for_pv_inverter_subject():
    profile = build_primary_query_profile(
        "组串式逆变器",
        "规格型号:150KW 安装点离地高度:屋面支架安装 布置场地:光伏场区",
    )

    assert "光伏逆变器安装" in profile["quota_aliases"]
    assert any("150KW" in alias for alias in profile["quota_aliases"])
    assert "光伏逆变器安装 功率≤250kW" in profile["quota_aliases"]
    assert "光伏逆变器安装 功率≤1000kW" in profile["quota_aliases"]


def test_build_primary_query_profile_adds_capacity_alias_for_ups_debug_subject():
    profile = build_primary_query_profile(
        "不间断电源系统调试",
        "电源型号、规格:UPS电源 电源容量:15kVA以下",
    )

    assert any("保安电源系统调试" in alias for alias in profile["quota_aliases"])
    assert any("15kVA" in alias for alias in profile["quota_aliases"])


def test_discover_primary_subject_extracts_tail_object_from_generic_system_install_title():
    subject = discover_primary_subject(
        "抄表采集系统安装",
        "抄表采集系统安装 多表采集智能终端调试",
    )

    assert subject["primary_subject"] == "多表采集智能终端调试"


def test_build_quota_query_generic_install_title_keeps_real_tail_object():
    query = build_quota_query(
        parser,
        "信号避雷装置安装",
        "信号避雷装置安装 信号避雷器",
        specialty="C5",
    )

    assert "信号避雷器" in query
    assert "信号避雷装置安装" not in query or query.index("信号避雷器") <= query.index("信号避雷装置安装")


def test_build_quota_query_does_not_hijack_air_heater_into_pipe_route():
    query = build_quota_query(
        parser,
        "空气加热器（冷却器）",
        (
            "空气加热器（冷却器） "
            "名称：全热交换器1 "
            "规格：新风量：600CMPH；机外静压：80Pa；排风量：480CMPH；全热回收效率：制冷：≥65 "
            "制热：≥70；噪声：35dB（A）；额定电压：220V；额定功率：0.09kw "
            "附件：自带电控柜、弱电控制柜；设置过渡工况旁通功能；自带初效G4+静电杀菌段；"
            "风机效率不低于现行国家标准《通风机能效限定值及能效等级》GB 19761规定的通风机能效等级的2级 "
            "支架形式、减震措施、材质、刷油：按设计文件及相关规范要求； "
            "试压要求：机组的清洗、安装、试漏、调试等事宜应严格按照制造厂提供的《使用说明书》进行； "
            "安装形式：吊装. 7.未尽事宜详见设计图纸"
        ),
        specialty="C5",
    )

    assert "镀锌钢管敷设" not in query
    assert "公称直径 4" not in query
    assert "空气加热器" in query or "全热交换器" in query
