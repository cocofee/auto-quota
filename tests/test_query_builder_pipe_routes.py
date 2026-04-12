# -*- coding: utf-8 -*-
from src.query_builder import build_quota_query
from src.text_parser import TextParser


parser = TextParser()


def test_build_quota_query_prefers_cast_iron_drainage_family_for_indoor_soil_pipe():
    query = build_quota_query(
        parser,
        "铸铁管",
        "安装部位:室内 介质:污水、废水 材质、规格:机制铸铁管 Dn65 连接形式:机械接口",
    )

    assert "柔性铸铁排水管" in query
    assert "钢塑复合管" not in query


def test_build_quota_query_prefers_general_steel_sleeve_for_wall_sleeve():
    query = build_quota_query(
        parser,
        "套管",
        "名称:钢套管 部位:穿墙钢套管 规格:DN25 其他:套管制作及安装",
    )

    assert "一般钢套管制作安装" in query


def test_build_quota_query_prefers_hole_blocking_route_for_blocking_item():
    query = build_quota_query(
        parser,
        "套管",
        "名称:堵洞 规格:介质管径综合考虑",
    )

    assert "堵洞" in query
    assert "套管" not in query


def test_build_quota_query_prefers_support_family_over_surface_process_noise():
    query = build_quota_query(
        parser,
        "管道支架",
        "材质:管道支架 管架形式:按需制作 防腐油漆:除锈后刷防锈漆二道,再刷灰色调和漆二道",
    )

    assert "管道支架制作安装" in query
    assert "一般管架" in query
    assert "除锈" not in query


def test_build_quota_query_prefers_pipe_rubber_insulation_family():
    query = build_quota_query(
        parser,
        "管道绝热",
        "绝热材料品种:难燃B1级橡塑海绵 绝热厚度:20mm 管道外径:管道外径≤φ57",
    )

    assert "橡塑管壳安装(管道)" in query
    assert "直埋保温管" not in query


def test_build_quota_query_prefers_condensation_insulation_route():
    query = build_quota_query(
        parser,
        "防结露保温",
        "绝热材料品种:离心玻璃棉 保冷层厚度:30mm 部位:给水管道防结露",
        specialty="C10",
    )

    assert "管道绝热" in query
    assert "保冷" in query


def test_build_quota_query_prefers_surface_process_route_over_pipe_install():
    query = build_quota_query(
        parser,
        "废水管道标识刷调和漆（黄棕色）",
        "管道标识 色环 调和漆",
        specialty="C10",
    )

    assert "管道标识" in query
    assert "色环" in query
    assert "调和漆" in query


def test_build_quota_query_does_not_route_accessory_with_included_hole_to_sleeve():
    query = build_quota_query(
        parser,
        "给、排水附(配)件",
        "型号、规格:87型雨水斗 DN100 含预留孔洞",
        specialty="C10",
    )

    assert "雨水斗" in query
    assert "套管" not in query
    assert "堵洞" not in query


def test_build_quota_query_prefers_explicit_composite_pipe_route():
    query = build_quota_query(
        parser,
        "复合管",
        (
            "1.安装部位:室内 "
            "2.介质:给水 "
            "3.材质、规格:钢塑复合压力给水管 1.6MPA DN25 "
            "4.连接形式:电磁感应热熔"
        ),
    )

    assert "给排水管道" in query
    assert "钢塑复合管" in query
    assert "热熔连接" in query
    assert "DN25" in query
