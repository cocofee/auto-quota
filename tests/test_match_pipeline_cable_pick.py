from src.match_pipeline import _pick_explicit_cable_family_candidate


def test_pick_explicit_cable_family_candidate_prefers_control_cable_core_bucket():
    bill_text = "控制电缆敷设电缆6芯"
    candidates = [
        {"name": "一般电缆 电缆14芯以下", "param_score": 0.9, "rerank_score": 0.9},
        {"name": "控制电缆敷设 电缆6芯", "param_score": 0.8, "rerank_score": 0.7},
    ]

    picked = _pick_explicit_cable_family_candidate(bill_text, candidates)

    assert picked["name"] == "控制电缆敷设 电缆6芯"


def test_pick_explicit_cable_family_candidate_prefers_power_cable_core_family():
    bill_text = "铜芯电力电缆敷设35mm2(单芯)"
    candidates = [
        {"name": "铜芯电力电缆敷设 一般五芯电缆 电缆截面35mm2以下", "param_score": 0.9, "rerank_score": 0.9},
        {"name": "铜芯电力电缆敷设 一般单芯电缆 电缆截面35mm2以下", "param_score": 0.8, "rerank_score": 0.7},
    ]

    picked = _pick_explicit_cable_family_candidate(bill_text, candidates)

    assert picked["name"] == "铜芯电力电缆敷设 一般单芯电缆 电缆截面35mm2以下"


def test_pick_explicit_cable_family_candidate_prefers_terminal_head_over_laying_item():
    bill_text = "电力电缆终端头制作安装 1kV以下室内干包式铜芯电力电缆"
    candidates = [
        {"name": "软母线安装 导线截面(mm2以内) 150", "param_score": 0.9, "rerank_score": 0.9},
        {"name": "1kV以下户内干包式铜芯电力电缆终端头制作、安装 铜芯干包终端头(截面mm2以下) 16", "param_score": 0.8, "rerank_score": 0.7},
    ]

    picked = _pick_explicit_cable_family_candidate(bill_text, candidates)

    assert picked["name"] == "1kV以下户内干包式铜芯电力电缆终端头制作、安装 铜芯干包终端头(截面mm2以下) 16"


def test_pick_explicit_cable_family_candidate_prefers_total_cores_from_complex_bundle():
    bill_text = "电力电缆敷设 WDZN-BYJ 3x4+2x2.5"
    candidates = [
        {"name": "铜芯电力电缆敷设 一般四芯电缆 电缆截面4mm2以下", "param_score": 0.9, "rerank_score": 0.9},
        {"name": "铜芯电力电缆敷设 一般五芯电缆 电缆截面4mm2以下", "param_score": 0.8, "rerank_score": 0.7},
    ]

    picked = _pick_explicit_cable_family_candidate(bill_text, candidates)

    assert picked["name"] == "铜芯电力电缆敷设 一般五芯电缆 电缆截面4mm2以下"
