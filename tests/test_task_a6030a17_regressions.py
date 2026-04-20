from src.explicit_equipment_family_pickers import _pick_explicit_equipment_family_candidate
from src.explicit_pipe_family_pickers import _pick_explicit_pipe_run_candidate
from src.explicit_terminal_family_pickers import (
    _pick_explicit_lamp_family_candidate,
    _pick_explicit_outlet_family_candidate,
    _pick_explicit_sanitary_family_candidate,
)
from src.match_pipeline import _pick_category_safe_candidate
from src.text_parser import parser


def test_pc_plastic_conduit_stays_in_conduit_family():
    features = parser.parse_canonical(
        "配管 名称:配管 材质:PC塑料管 规格:DN20 配置形式:暗配",
        specialty="C4",
    )

    assert features["entity"] == "配管"
    assert features["family"] == "conduit_raceway"


def test_pick_explicit_pipe_run_candidate_prefers_upvc_sewage_glue_connection():
    picked = _pick_explicit_pipe_run_candidate(
        "塑料管 安装部位:室内 介质:污水 材质、规格:UPVC管De150 连接形式:粘结连接",
        [
            {"name": "给排水管道 室内塑料排水管(热熔连接) 公称外径(mm以内) 150", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "给排水管道 室内塑料排水管(螺母密封圈连接) 公称外径(mm以内) 160", "param_score": 0.85, "rerank_score": 0.85},
            {"name": "给排水管道 室内塑料排水管(粘接) 公称外径(mm以内) 160", "param_score": 0.7, "rerank_score": 0.7},
        ],
    )

    assert picked["name"] == "给排水管道 室内塑料排水管(粘接) 公称外径(mm以内) 160"


def test_pick_explicit_pipe_run_candidate_treats_glue_alias_same_as_canonical_glue():
    picked = _pick_explicit_pipe_run_candidate(
        "给水管道 PVC DN100 粘结连接",
        [
            {"name": "给水管道 PVC DN100", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "给水管道 PVC(粘接) DN100", "param_score": 0.7, "rerank_score": 0.7},
        ],
    )

    assert picked["name"] == "给水管道 PVC(粘接) DN100"


def test_pick_explicit_sanitary_family_candidate_prefers_sensor_basin_over_plain_basin():
    picked = _pick_explicit_sanitary_family_candidate(
        "洗脸盆 名称:立柱洗脸盆 附件名称、数量:感应水龙头",
        [
            {"name": "洗脸盆 立柱式 冷水", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "洗脸盆 立柱式 冷水 感应开关", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert picked["name"] == "洗脸盆 立柱式 冷水 感应开关"


def test_pick_explicit_sanitary_family_candidate_prefers_foot_flush_squat_toilet():
    picked = _pick_explicit_sanitary_family_candidate(
        "大便器 名称:液压脚踏冲洗阀蹲便器",
        [
            {"name": "蹲式大便器安装 瓷高水箱", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "蹲式大便器安装 脚踏开关", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert picked["name"] == "蹲式大便器安装 脚踏开关"


def test_pick_explicit_equipment_family_candidate_prefers_flush_tank_over_overall_tank():
    picked = _pick_explicit_equipment_family_candidate(
        "大、小便槽自动冲洗水箱 名称:自动冲洗水箱甲型 规格:100L",
        [
            {"name": "整体水箱安装 水箱总容量(m3以内) 60", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "自动冲洗水箱安装 甲型 100L", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert picked["name"] == "自动冲洗水箱安装 甲型 100L"


def test_pick_explicit_outlet_family_candidate_prefers_dark_outlet_over_floor_outlet():
    picked = _pick_explicit_outlet_family_candidate(
        "插座 名称:安全型防水二三极暗装插座 规格:250V 10A 安装方式:暗装",
        [
            {"name": "地插安装 三相插座(≤30A)", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "插座暗装 单相 单联", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert picked["name"] == "插座暗装 单相 单联"


def test_pick_explicit_outlet_family_candidate_prefers_three_phase_for_380v_bill():
    picked = _pick_explicit_outlet_family_candidate(
        "插座 名称:工业插座 规格:380V 16A 安装方式:暗装",
        [
            {"name": "插座暗装 单相 单联", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "插座暗装 三相 15A", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert picked["name"] == "插座暗装 三相 15A"


def test_pick_category_safe_candidate_prefers_exhaust_fan_over_micro_motor():
    item = {
        "name": "轴流通风机",
        "description": "名称:排风扇 型号:APB25-5-B",
    }
    candidates = [
        {"name": "微型电机", "param_score": 0.9, "rerank_score": 0.9},
        {"name": "风扇安装 排气扇", "param_score": 0.7, "rerank_score": 0.6},
    ]

    picked = _pick_category_safe_candidate(item, candidates)

    assert picked["name"] == "风扇安装 排气扇"


def test_pick_explicit_equipment_family_candidate_prefers_small_appliance_over_wiring():
    picked = _pick_explicit_equipment_family_candidate(
        "小电器 名称:电暖气 参考型号:NY22-X6022",
        [
            {"name": "低压电器装置接线 小母线安装", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "小电器安装 电暖气", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert picked["name"] == "小电器安装 电暖气"


def test_pick_explicit_lamp_family_candidate_prefers_plain_led_lamp_over_sign_lamp():
    picked = _pick_explicit_lamp_family_candidate(
        "普通灯具 名称:防水防潮LED灯 备注:吸顶安装",
        [
            {"name": "标志、诱导装饰灯具安装 吸顶式", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "吸顶灯具安装 灯罩周长(mm以内) 1400", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert picked["name"] == "吸顶灯具安装 灯罩周长(mm以内) 1400"
