from src.query_builder import build_quota_query
from src.text_parser import TextParser


parser = TextParser()


def test_build_quota_query_adds_electrical_tube_variant_for_jdg_material_conduit():
    query = build_quota_query(
        parser,
        "配管 -超高",
        "材质:JDG 规格:20 配置形式:暗敷",
    )

    assert query == "套接紧定式钢导管JDG 镀锌电线管 敷设 砖混凝土结构暗配 公称直径 20"


def test_build_quota_query_normalizes_kjg_typo_to_kbg_variant_for_fujian_conduit():
    query = build_quota_query(
        parser,
        "配管",
        "材质:KJG管//规格:DN40//名称:电气配管//配置形式:砖、混凝土结构暗配",
    )

    assert query == "套接紧定式钢导管KBG 镀锌电线管 敷设 砖混凝土结构暗配 公称直径 40"


def test_build_quota_query_prefers_metal_hose_family_for_explicit_conduit_name():
    query = build_quota_query(
        parser,
        "配管",
        "名称:金属软管 规格:D25",
    )

    assert query == "金属软管敷设 公称直径 25"


def test_build_quota_query_builds_wire_query_with_pipe_laying():
    query = build_quota_query(
        parser,
        "配线",
        "配线形式:管内穿线 型号:WDZN-BYJ-3x4+2x2.5",
    )

    assert "管内穿线" in query
    assert "导线截面 4" in query


def test_build_quota_query_builds_power_cable_query_with_dual_laying():
    query = build_quota_query(
        parser,
        "电力电缆",
        "型号、规格:ZRC-BPYJV-0.6/1kV,3x240+3x40 敷设方式、部位:室内穿管或桥架",
    )

    assert "电力电缆" in query
    assert "桥架" in query
    assert "穿管" in query
    assert "240" in query


def test_build_quota_query_normalizes_bridge_cm_size():
    query = build_quota_query(
        parser,
        "桥架",
        "名称:钢制槽式桥架 规格:20x10",
    )

    assert "槽式桥架 安装" in query
    assert "宽+高 300" in query
