from src.match_pipeline import (
    _pick_category_safe_candidate,
    _pick_explicit_button_broadcast_candidate,
    _pick_explicit_bridge_family_candidate,
    _pick_explicit_cable_family_candidate,
    _pick_explicit_distribution_box_candidate,
    _pick_explicit_fire_device_candidate,
    _pick_explicit_lamp_family_candidate,
    _pick_explicit_motor_family_candidate,
    _pick_explicit_network_device_candidate,
    _pick_explicit_sanitary_family_candidate,
    _pick_explicit_support_family_candidate,
    _pick_explicit_valve_family_candidate,
    _pick_explicit_ventilation_family_candidate,
    _pick_explicit_wiring_family_candidate,
)


def test_pick_explicit_sanitary_family_candidate_prefers_wall_sensor_urinal():
    picked = _pick_explicit_sanitary_family_candidate(
        "感应式小便器 壁挂式",
        [
            {"name": "立式小便器安装 自动冲洗 一联", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "壁挂式小便器安装 感应开关 埋入式", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert picked["name"] == "壁挂式小便器安装 感应开关 埋入式"


def test_pick_explicit_lamp_family_candidate_prefers_lamp_band():
    picked = _pick_explicit_lamp_family_candidate(
        "LED线形灯 嵌入式安装",
        [
            {"name": "吸顶灯具安装 灯罩周长(mm) ≤800", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "荧光艺术装饰灯具安装 天棚荧光灯带", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert picked["name"] == "荧光艺术装饰灯具安装 天棚荧光灯带"


def test_pick_explicit_distribution_box_candidate_prefers_floor_mode():
    picked = _pick_explicit_distribution_box_candidate(
        "配电柜 安装方式:落地式 基础槽钢",
        [
            {"name": "成套配电箱安装 悬挂、嵌入式 半周长 2.5m", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "成套配电箱安装 落地式", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert picked["name"] == "成套配电箱安装 落地式"


def test_pick_explicit_distribution_box_candidate_defaults_box_to_wall_mode():
    picked = _pick_explicit_distribution_box_candidate(
        "配电箱 1-1ALE1、1-3~8ALE1",
        [
            {"name": "成套配电箱安装 落地式", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "成套配电箱安装 悬挂式 半周长(m以内) 1.5", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert picked["name"] == "成套配电箱安装 悬挂式 半周长(m以内) 1.5"


def test_pick_explicit_distribution_box_candidate_does_not_force_ap_code_to_floor():
    picked = _pick_explicit_distribution_box_candidate(
        "配电箱 1AP1",
        [
            {"name": "成套配电箱安装 落地式", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "成套配电箱安装 悬挂式 半周长(m以内) 1.5", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert picked["name"] == "成套配电箱安装 悬挂式 半周长(m以内) 1.5"


def test_pick_explicit_distribution_box_candidate_prefers_control_box_wall_mode():
    picked = _pick_explicit_distribution_box_candidate(
        "控制箱 AC-B1-WS1~4 安装方式:明装",
        [
            {"name": "控制箱安装 落地式", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "控制箱安装 墙上", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert picked["name"] == "控制箱安装 墙上"


def test_pick_explicit_ventilation_family_candidate_prefers_louver_outlet_over_window():
    picked = _pick_explicit_ventilation_family_candidate(
        "碳钢风口、散流器、百叶窗 名称:单层防雨百叶",
        [
            {"name": "风口安装 钢百叶窗 框内面积(m2以内) 0.5", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "风口安装 百叶风口 周长(mm以内) 1280", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert picked["name"] == "风口安装 百叶风口 周长(mm以内) 1280"


def test_pick_explicit_ventilation_family_candidate_prefers_ceiling_exhaust_fan():
    picked = _pick_explicit_ventilation_family_candidate(
        "天花板管道式换气扇",
        [
            {"name": "风扇安装 排气扇", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "风扇安装 天花式排气扇", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert picked["name"] == "风扇安装 天花式排气扇"


def test_pick_explicit_ventilation_family_candidate_prefers_closer_warm_fan_weight():
    picked = _pick_explicit_ventilation_family_candidate(
        "暖风机安装 重量(kg以内) 160",
        [
            {"name": "暖风机安装 重量(kg以内) 2000", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "暖风机安装 重量(kg以内) 160", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert picked["name"] == "暖风机安装 重量(kg以内) 160"


def test_pick_explicit_ventilation_family_candidate_prefers_flexible_joint_over_valve():
    picked = _pick_explicit_ventilation_family_candidate(
        "柔性软风管 -超高",
        [
            {"name": "柔性软风管阀门安装 直径(mm以内) 250", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "柔性接口及伸缩节 有法兰", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert picked["name"] == "柔性接口及伸缩节 有法兰"


def test_pick_explicit_ventilation_family_candidate_prefers_duct_check_valve_with_size_band():
    picked = _pick_explicit_ventilation_family_candidate(
        "碳钢阀门 名称:风管止回阀 规格:800×320",
        [
            {"name": "柔性软风管阀门安装 直径(mm以内) 500", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "碳钢 调节阀安装 圆、方形风管止回阀 周长(mm以内) 3200", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert picked["name"] == "碳钢 调节阀安装 圆、方形风管止回阀 周长(mm以内) 3200"


def test_pick_explicit_ventilation_family_candidate_prefers_manual_multi_leaf_damper():
    picked = _pick_explicit_ventilation_family_candidate(
        "碳钢阀门 名称:手动对开多叶调节阀 规格:500×250",
        [
            {"name": "中压阀门 调节阀门 公称直径32mm以内", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "碳钢 调节阀安装 手动对开多叶调节阀 周长(mm以内) 1600", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert picked["name"] == "碳钢 调节阀安装 手动对开多叶调节阀 周长(mm以内) 1600"


def test_pick_explicit_ventilation_family_candidate_does_not_hijack_plumbing_check_valve():
    picked = _pick_explicit_ventilation_family_candidate(
        "焊接法兰阀门 名称:止回阀 规格:DN80",
        [
            {"name": "碳钢 调节阀安装 圆、方形风管止回阀 周长(mm以内) 3200", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "法兰阀门安装 公称直径(mm以内) 80", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert picked is None


def test_pick_explicit_bridge_family_candidate_prefers_bridge_over_cable_laying():
    picked = _pick_explicit_bridge_family_candidate(
        "桥架 名称:钢制桥架 规格:300×100",
        [
            {"name": "双绞线缆 桥架内布放 ≤4对", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "钢制槽式桥架安装 宽+高(mm以下) 400", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert picked["name"] == "钢制槽式桥架安装 宽+高(mm以下) 400"


def test_pick_explicit_bridge_family_candidate_uses_bridge_wh_sum_not_half_perimeter():
    picked = _pick_explicit_bridge_family_candidate(
        "桥架 名称:钢制槽式桥架 规格:200×100",
        [
            {"name": "钢制槽式桥架安装 宽+高(mm以下) 200", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "钢制槽式桥架安装 宽+高(mm以下) 300", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert picked["name"] == "钢制槽式桥架安装 宽+高(mm以下) 300"


def test_pick_explicit_support_family_candidate_prefers_bridge_support():
    picked = _pick_explicit_support_family_candidate(
        "管道支架 管架形式:桥架侧纵向抗震支吊架",
        [
            {"name": "室内管道管道支架制作安装 一般管架", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "支架制作与安装 电缆桥架支撑架制作", "param_score": 0.6, "rerank_score": 0.5},
        ],
    )

    assert picked["name"] == "支架制作与安装 电缆桥架支撑架制作"


def test_pick_explicit_support_family_candidate_prefers_weight_bucket():
    picked = _pick_explicit_support_family_candidate(
        "管道支架 详见图集03S402-77~79 单件重量5kg",
        [
            {"name": "室内管道管道支架制作安装 一般管架", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "管道支架制作 单件重量(kg以内) 5", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert picked["name"] == "管道支架制作 单件重量(kg以内) 5"


def test_pick_explicit_motor_family_candidate_prefers_check_wiring():
    picked = _pick_explicit_motor_family_candidate(
        "低压交流异步电动机 名称:电动机检查接线 规格型号:13KW",
        [
            {"name": "交流异步电动机负载调试 低压笼型 刀开关控制", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "交流异步电动机检查接线 功率(kw) ≤20", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert picked["name"] == "交流异步电动机检查接线 功率(kw) ≤20"


def test_pick_explicit_motor_family_candidate_prefers_closer_power_bucket():
    picked = _pick_explicit_motor_family_candidate(
        "低压交流异步电动机 名称:电动机检查接线 规格型号:18KW",
        [
            {"name": "交流异步电动机检查接线 功率(kw) ≤13", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "交流异步电动机检查接线 功率(kw) ≤20", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert picked["name"] == "交流异步电动机检查接线 功率(kw) ≤20"


def test_pick_explicit_sanitary_family_candidate_prefers_sensor_and_tank_type():
    picked = _pick_explicit_sanitary_family_candidate(
        "坐便器 材质:瓷质 规格、类型:连体水箱 含感应开关",
        [
            {"name": "坐式大便器安装 隐蔽水箱", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "坐式大便器安装 连体水箱 感应开关", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert picked["name"] == "坐式大便器安装 连体水箱 感应开关"


def test_pick_explicit_sanitary_family_candidate_prefers_faucet_over_sensor_controller():
    picked = _pick_explicit_sanitary_family_candidate(
        "感应式水龙头 公称直径:DN15",
        [
            {"name": "入侵探测器 感应式控制器(不含线) 振动电缆控制器", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "水龙头安装 公称直径(mm) 15", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert picked["name"] == "水龙头安装 公称直径(mm) 15"


def test_pick_explicit_valve_family_candidate_prefers_valve_over_flange_install():
    picked = _pick_explicit_valve_family_candidate(
        "螺纹法兰阀门 类型:软密封闸阀 规格:DN100",
        [
            {"name": "螺纹法兰安装 公称直径(mm以内) 100", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "法兰阀门安装 公称直径(mm以内) 100", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert picked["name"] == "法兰阀门安装 公称直径(mm以内) 100"


def test_pick_explicit_valve_family_candidate_prefers_carbon_steel_valve_family():
    picked = _pick_explicit_valve_family_candidate(
        "碳钢阀门 规格:DN100",
        [
            {"name": "碳钢 调节阀安装 风管防火阀 周长(mm以内) 3200", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "法兰阀门安装 公称直径(mm以内) 100 碳钢", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert picked["name"] == "法兰阀门安装 公称直径(mm以内) 100 碳钢"


def test_pick_explicit_valve_family_candidate_prefers_exact_dn_bucket():
    picked = _pick_explicit_valve_family_candidate(
        "螺纹阀门 类型:截止阀 规格:DN32",
        [
            {"name": "螺纹阀门安装 公称直径(mm以内) 40", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "螺纹阀门安装 公称直径(mm以内) 32", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert picked["name"] == "螺纹阀门安装 公称直径(mm以内) 32"


def test_pick_explicit_valve_family_candidate_prefers_ppr_plastic_valve_over_plastic_pipe():
    picked = _pick_explicit_valve_family_candidate(
        "塑料阀门 类型:止回阀 规格:DN32",
        [
            {"name": "塑料给水管(热熔连接) 公称外径(mm以内) 32", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "塑料阀门安装 公称直径(mm以内) 32", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert picked["name"] == "塑料阀门安装 公称直径(mm以内) 32"


def test_pick_explicit_wiring_family_candidate_prefers_pipe_wiring_family():
    picked = _pick_explicit_wiring_family_candidate(
        "配线 配线形式:管内穿线 型号:WDZN-BYJ-3x4+2x2.5",
        [
            {"name": "线槽配线 导线截面(mm2以内) 4", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "管内穿线 穿多芯软导线 二芯 单芯导线截面(mm2以内) 4", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert picked["name"] == "管内穿线 穿多芯软导线 二芯 单芯导线截面(mm2以内) 4"


def test_pick_explicit_cable_family_candidate_prefers_matching_laying_and_model():
    picked = _pick_explicit_cable_family_candidate(
        "阻燃变频电力电缆 型号、规格:ZRC-BPYJV-0.6/1kV,3x240+3x40 敷设方式、部位:室内穿管或桥架",
        [
            {"name": "室内敷设电力电缆 铜芯电力电缆敷设 电缆截面(mm2) 240", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "电缆穿导管敷设 铜芯电力电缆 BPYJV 电缆截面(mm2) 240", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert picked["name"] == "电缆穿导管敷设 铜芯电力电缆 BPYJV 电缆截面(mm2) 240"


def test_pick_explicit_cable_family_candidate_prefers_middle_head_and_aluminum_conductor():
    picked = _pick_explicit_cable_family_candidate(
        "中间头制作与安装 1kV以下室内干包式铝芯电力电缆 电缆截面(mm2)≤240",
        [
            {"name": "终端头制作与安装 1kV以下室内干包式铜芯电力电缆 电缆截面(mm2)≤240", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "中间头制作与安装 1kV以下室内干包式铝芯电力电缆 电缆截面(mm2)≤240", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert picked["name"] == "中间头制作与安装 1kV以下室内干包式铝芯电力电缆 电缆截面(mm2)≤240"


def test_pick_explicit_fire_device_candidate_prefers_hydrant_device_over_pipe():
    picked = _pick_explicit_fire_device_candidate(
        "室内消火栓 暗装 带自救卷盘 DN65",
        [
            {"name": "消火栓钢管 镀锌钢管(螺纹连接) 公称直径(mm以内)65", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "室内消火栓 暗装(带自救卷盘) 公称直径(mm以内)单栓65", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert picked["name"] == "室内消火栓 暗装(带自救卷盘) 公称直径(mm以内)单栓65"


def test_pick_explicit_network_device_candidate_prefers_small_port_bucket():
    picked = _pick_explicit_network_device_candidate(
        "楼层24口交换机 名称:交换机 功能:24口千兆POE交换机",
        [
            {"name": "交换机设备安装、调试 交换机 固定配置 >24口", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "交换机设备安装、调试 交换机 固定配置 ≤24口", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert picked["name"] == "交换机设备安装、调试 交换机 固定配置 ≤24口"


def test_pick_explicit_support_family_candidate_prefers_aseismic_side_single_pipe():
    picked = _pick_explicit_support_family_candidate(
        "抗震支架 名称:水管侧向抗震支架 管道数量:单管",
        [
            {"name": "成品抗震支架安装 单管纵向支架", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "成品抗震支架安装 单管侧向支架", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert picked["name"] == "成品抗震支架安装 单管侧向支架"


def test_pick_explicit_support_family_candidate_prefers_pipe_like_fallback_for_plumbing_aseismic():
    picked = _pick_explicit_support_family_candidate(
        "给排水双向抗震支架 1.规格:TL-DN100mm",
        [
            {"name": "仪表支架制作安装 仪表支吊架安装 双杆吊架安装", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "吊托支架制作、安装", "param_score": 0.7, "rerank_score": 0.7},
            {"name": "电缆桥架支撑架制作", "param_score": 0.6, "rerank_score": 0.6},
        ],
    )

    assert picked["name"] == "吊托支架制作、安装"


def test_pick_category_safe_candidate_prefers_pipe_insulation_over_equipment():
    item = {
        "name": "管道绝热",
        "description": "绝热厚度:30mm",
    }
    candidates = [
        {"name": "立式设备绝热 绝热层厚度(mm以内) 30", "param_score": 0.9, "rerank_score": 0.9},
        {"name": "管道绝热 绝热层厚度(mm以内) 30", "param_score": 0.7, "rerank_score": 0.6},
    ]

    picked = _pick_category_safe_candidate(item, candidates)

    assert picked["name"] == "管道绝热 绝热层厚度(mm以内) 30"


def test_pick_category_safe_candidate_prefers_pipe_check_valve_over_duct_check_valve():
    item = {
        "name": "焊接法兰阀门",
        "description": "类型:止回阀 规格:DN100",
    }
    candidates = [
        {"name": "碳钢 调节阀安装 圆、方形风管止回阀 周长(mm以内) 3200", "param_score": 0.9, "rerank_score": 0.9},
        {"name": "焊接法兰阀门安装 公称直径(mm以内) 100", "param_score": 0.7, "rerank_score": 0.6},
    ]

    picked = _pick_category_safe_candidate(item, candidates)

    assert picked["name"] == "焊接法兰阀门安装 公称直径(mm以内) 100"


def test_pick_category_safe_candidate_prefers_pipe_like_support_fallback_over_instrument_support():
    item = {
        "name": "给排水双向抗震支架",
        "description": "1.规格:TL-DN100mm",
        "specialty": "C10",
    }
    candidates = [
        {"name": "仪表支架制作安装 仪表支吊架安装 双杆吊架安装", "param_score": 0.9, "rerank_score": 0.9},
        {"name": "吊托支架制作、安装", "param_score": 0.7, "rerank_score": 0.7},
        {"name": "电缆桥架支撑架制作", "param_score": 0.6, "rerank_score": 0.6},
    ]

    picked = _pick_category_safe_candidate(item, candidates)

    assert picked["name"] == "吊托支架制作、安装"


def test_pick_category_safe_candidate_abstains_when_explicit_support_only_has_unrelated_entities():
    item = {
        "name": "通风空调侧向抗震支架",
        "description": "1.规格:T-DN800mm",
        "specialty": "C7",
    }
    candidates = [
        {"name": "圆伞形风帽安装 ≤10kg", "param_score": 0.9, "rerank_score": 0.9},
        {"name": "锥形风帽安装 ≤25kg", "param_score": 0.8, "rerank_score": 0.8},
    ]

    picked = _pick_category_safe_candidate(item, candidates)

    assert picked is None


def test_pick_explicit_valve_family_candidate_prefers_plastic_valve_over_plastic_pipe():
    picked = _pick_explicit_valve_family_candidate(
        "PPR塑料阀门 规格:DN32",
        [
            {"name": "室外塑料给水管(热熔连接) 公称直径(mm以内) 32", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "螺纹阀门安装 公称直径(mm以内) 32", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert picked["name"] == "螺纹阀门安装 公称直径(mm以内) 32"


def test_pick_explicit_network_device_candidate_prefers_exact_large_port_bucket():
    picked = _pick_explicit_network_device_candidate(
        "核心交换机 名称:交换机 功能:48口万兆交换机",
        [
            {"name": "交换机设备安装、调试 交换机 固定配置 ≤24口", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "交换机设备安装、调试 交换机 固定配置 48口", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert picked["name"] == "交换机设备安装、调试 交换机 固定配置 48口"


def test_pick_explicit_distribution_box_candidate_rejects_junction_box_and_box_wiring():
    picked = _pick_explicit_distribution_box_candidate(
        "\u914d\u7535\u7bb1 \u5b89\u88c5\u65b9\u5f0f:\u660e\u88c5 \u89c4\u683c:600*900*220 8\u56de\u8def",
        [
            {
                "name": "\u63a5\u7ebf\u7bb1\u660e\u88c5 \u534a\u5468\u957f(mm\u4ee5\u5185) 1500",
                "param_score": 0.99,
                "rerank_score": 0.99,
            },
            {
                "name": "\u76d8\u3001\u67dc\u3001\u7bb1\u3001\u677f\u914d\u7ebf \u5bfc\u7ebf\u622a\u9762(mm2\u4ee5\u5185) 25",
                "param_score": 0.98,
                "rerank_score": 0.98,
            },
            {
                "name": "\u6210\u5957\u914d\u7535\u7bb1\u5b89\u88c5 \u60ac\u6302\u3001\u5d4c\u5165\u5f0f \u534a\u5468\u957f1.5m \u89c4\u683c(\u56de\u8def\u4ee5\u5185) 8",
                "param_score": 0.65,
                "rerank_score": 0.55,
            },
        ],
    )

    assert picked["name"] == "\u6210\u5957\u914d\u7535\u7bb1\u5b89\u88c5 \u60ac\u6302\u3001\u5d4c\u5165\u5f0f \u534a\u5468\u957f1.5m \u89c4\u683c(\u56de\u8def\u4ee5\u5185) 8"
