from src.match_pipeline import (
    _pick_explicit_bridge_family_candidate,
    _pick_explicit_distribution_box_candidate,
    _pick_explicit_fire_device_candidate,
    _pick_explicit_motor_family_candidate,
    _pick_explicit_network_device_candidate,
    _pick_explicit_sanitary_family_candidate,
    _pick_explicit_support_family_candidate,
    _pick_explicit_valve_family_candidate,
    _pick_explicit_ventilation_family_candidate,
)


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


def test_pick_explicit_bridge_family_candidate_prefers_bridge_over_cable_laying():
    picked = _pick_explicit_bridge_family_candidate(
        "桥架 名称:钢制桥架 规格:300×100",
        [
            {"name": "双绞线缆 桥架内布放 ≤4对", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "钢制槽式桥架安装 宽+高(mm以下) 400", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert picked["name"] == "钢制槽式桥架安装 宽+高(mm以下) 400"


def test_pick_explicit_support_family_candidate_prefers_bridge_support():
    picked = _pick_explicit_support_family_candidate(
        "管道支架 管架形式:桥架侧纵向抗震支吊架",
        [
            {"name": "室内管道管道支架制作安装 一般管架", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "支架制作与安装 电缆桥架支撑架制作", "param_score": 0.6, "rerank_score": 0.5},
        ],
    )

    assert picked["name"] == "支架制作与安装 电缆桥架支撑架制作"


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


def test_pick_explicit_network_device_candidate_prefers_exact_large_port_bucket():
    picked = _pick_explicit_network_device_candidate(
        "核心交换机 名称:交换机 功能:48口万兆交换机",
        [
            {"name": "交换机设备安装、调试 交换机 固定配置 ≤24口", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "交换机设备安装、调试 交换机 固定配置 48口", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert picked["name"] == "交换机设备安装、调试 交换机 固定配置 48口"
