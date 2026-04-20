# -*- coding: utf-8 -*-
from src.explicit_equipment_family_pickers import _pick_explicit_equipment_family_candidate


def test_pick_explicit_equipment_family_candidate_prefers_water_tank_family():
    picked = _pick_explicit_equipment_family_candidate(
        "生活水箱 不锈钢水箱 10m3",
        [
            {"name": "离心式泵安装 设备重量(kg以内) 300", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "生活水箱安装 水箱容量(m3以内) 10", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert picked["name"] == "生活水箱安装 水箱容量(m3以内) 10"


def test_pick_explicit_equipment_family_candidate_prefers_pump_group_family():
    picked = _pick_explicit_equipment_family_candidate(
        "主泵（中区变频加压泵组）",
        [
            {"name": "离心式泵安装 设备重量(kg以内) 80", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "变频给水设备 设备重量(t以内) 0.5", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert picked["name"] == "变频给水设备 设备重量(t以内) 0.5"


def test_pick_explicit_equipment_family_candidate_rejects_sanitary_tank_context():
    picked = _pick_explicit_equipment_family_candidate(
        "坐便器 连体水箱",
        [
            {"name": "生活水箱安装 水箱容量(m3以内) 10", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "坐式大便器安装 连体水箱", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert picked is None


def test_pick_explicit_equipment_family_candidate_does_not_treat_generic_small_appliance_as_heater():
    picked = _pick_explicit_equipment_family_candidate(
        "小电器 名称:风扇",
        [
            {"name": "小电器安装 电暖气", "param_score": 0.8, "rerank_score": 0.9},
            {"name": "风扇安装 排气扇", "param_score": 0.7, "rerank_score": 0.8},
        ],
    )

    assert picked is None
