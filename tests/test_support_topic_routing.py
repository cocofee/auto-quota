from src.match_pipeline import _pick_explicit_support_family_candidate
from src.query_builder import build_quota_query
from src.text_parser import TextParser


parser = TextParser()


def test_support_rerank_skips_surface_process_candidate_even_when_bill_mentions_process():
    picked = _pick_explicit_support_family_candidate(
        "管道支架 制作安装 除锈后刷防锈漆二道",
        [
            {"name": "管道支架刷油 防锈漆 一遍", "param_score": 0.95, "rerank_score": 0.95},
            {"name": "管道支架制作 单件重量(kg以内) 5", "param_score": 0.70, "rerank_score": 0.70},
        ],
    )

    assert picked["name"] == "管道支架制作 单件重量(kg以内) 5"


def test_support_rerank_prefers_equipment_support_for_explicit_equipment_support_item():
    picked = _pick_explicit_support_family_candidate(
        "设备支架 设备支架制作安装 名称:风机支架制作安装 材质:型钢",
        [
            {"name": "管道支架制作 单件重量(kg以内) 5", "param_score": 0.85, "rerank_score": 0.85},
            {"name": "设备支架制作 单件重量(kg以内) 50", "param_score": 0.70, "rerank_score": 0.70},
        ],
    )

    assert picked["name"] == "设备支架制作 单件重量(kg以内) 50"


def test_support_rerank_prefers_aseismic_support_over_plain_support_family():
    picked = _pick_explicit_support_family_candidate(
        "抗震支架 管道支架 侧向支撑 DN100",
        [
            {"name": "管道支架制作 单件重量(kg以内) 5", "param_score": 0.90, "rerank_score": 0.90},
            {"name": "多管水管侧向抗震支架SG-DN80+DN100×5+DN150×2-T", "param_score": 0.75, "rerank_score": 0.75},
        ],
    )

    assert "抗震支架" in picked["name"]


def test_support_rerank_penalizes_bridge_support_for_plain_pipe_support_item():
    picked = _pick_explicit_support_family_candidate(
        "管道支架 制作安装",
        [
            {"name": "电缆桥架支架制作安装", "param_score": 0.95, "rerank_score": 0.95},
            {"name": "管道支架制作 单件重量(kg以内) 5", "param_score": 0.70, "rerank_score": 0.70},
        ],
    )

    assert picked["name"] == "管道支架制作 单件重量(kg以内) 5"


def test_build_quota_query_prefers_equipment_support_family_for_explicit_support_item():
    query = build_quota_query(
        parser,
        "设备支架",
        "设备支架制作安装 名称:风机支架制作安装 材质:型钢",
    )

    assert "设备支架制作安装" in query
    assert "除锈" not in query


def test_build_quota_query_prefers_aseismic_support_family_for_explicit_support_item():
    query = build_quota_query(
        parser,
        "管道支架",
        "名称:多管水管侧向抗震支架SG-DN80+DN100×5+DN150×2-T",
    )

    assert "抗震支架" in query


def test_build_quota_query_prefers_bridge_support_family_for_explicit_support_item():
    query = build_quota_query(
        parser,
        "桥架支架",
        "桥架支架制作安装 其他:含支架除锈、刷油",
    )

    assert "电缆桥架支撑架" in query
    assert "除锈" not in query


def test_build_quota_query_keeps_bridge_hint_for_aseismic_support_item():
    query = build_quota_query(
        parser,
        "管道支架",
        "名称:桥架侧纵向抗震支吊架CMQ-400-TL",
    )

    assert "抗震支架" in query
    assert "桥架" in query
    assert "纵向" in query


def test_build_quota_query_does_not_hijack_device_item_with_support_note():
    query = build_quota_query(
        parser,
        "静压箱",
        "名称:消声静压箱 规格:1000*1000*1000 支架形式、材质:含支吊架制作安装",
    )

    assert "设备支架制作安装" not in query
