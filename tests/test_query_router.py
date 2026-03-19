from src.query_router import build_query_route_profile


def test_installation_spec_route_detects_complex_cable_query():
    profile = build_query_route_profile(
        "WDZN-BYJ 3x4+2x2.5 配线",
        item={"section": "电气工程", "specialty": "C4"},
    )

    assert profile["route"] == "installation_spec"
    assert profile["reason"] == "spec_heavy_installation"
    assert profile["has_complex_install_spec"] is True


def test_semantic_description_route_detects_pure_description():
    profile = build_query_route_profile(
        "成套配电箱安装，包含基础制作与整体调试",
        item={"section": "电气工程"},
    )

    assert profile["route"] == "semantic_description"
    assert profile["reason"] == "semantic_heavy"


def test_material_route_detects_material_like_query():
    profile = build_query_route_profile("主材 WDZN-YJY 3x2.5 品牌询价")

    assert profile["route"] == "material"
    assert profile["is_material_query"] is True
