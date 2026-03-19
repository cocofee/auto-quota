from src.match_pipeline import (
    _pick_explicit_bridge_family_candidate,
    _pick_explicit_button_broadcast_candidate,
    _pick_explicit_distribution_box_candidate,
    _pick_explicit_support_family_candidate,
)


def test_distribution_box_ap_code_prefers_floor_candidate():
    picked = _pick_explicit_distribution_box_candidate(
        "配电箱 1AP1",
        [
            {"name": "成套配电箱安装 悬挂、嵌入式 半周长 3.0m", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "成套配电箱安装 落地式", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert "落地" in picked["name"]


def test_bridge_picker_prefers_slot_bridge_subtype():
    picked = _pick_explicit_bridge_family_candidate(
        "桥架 名称:钢制槽式桥架 规格:200×150",
        [
            {"name": "钢制托盘式桥架安装 宽+高(mm以下) 400", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "钢制槽式桥架安装 宽+高(mm以下) 400", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert "槽式" in picked["name"]


def test_button_broadcast_picker_prefers_wall_speaker():
    picked = _pick_explicit_button_broadcast_candidate(
        "紧急呼叫扬声器 安装方式:距地2.6米壁装",
        [
            {"name": "消防广播(扬声器)安装 扬声器 吸顶式(3W~5W)", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "消防广播(扬声器)安装 扬声器 壁挂式(3W~5W)", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert "壁挂" in picked["name"]


def test_button_broadcast_picker_prefers_plain_button_for_emergency_call():
    picked = _pick_explicit_button_broadcast_candidate(
        "紧急呼叫按钮",
        [
            {"name": "按钮安装 消火栓报警按钮", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "普通开关、按钮安装 按钮", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert "普通开关" in picked["name"]


def test_support_picker_prefers_fabrication_for_standard_atlas_items():
    picked = _pick_explicit_support_family_candidate(
        "管道支架 详见图集03S402-77~79",
        [
            {"name": "室内管道管道支架制作安装 一般管架", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "管道支架制作 单件重量(kg以内) 5", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert "单件重量" in picked["name"]
