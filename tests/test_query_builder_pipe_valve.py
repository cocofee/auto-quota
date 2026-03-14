# -*- coding: utf-8 -*-
"""管道阀门规范化测试用例

测试改进：
1. 特殊阀名归一化（浮球阀/电磁阀/信号蝶阀等）
2. 描述中连接方式覆盖清单名
3. 现有逻辑不退化
"""
from src.query_builder import build_quota_query
from src.text_parser import TextParser


parser = TextParser()


# === 1. 特殊阀名归一化 ===

def test_float_valve_large_dn():
    """浮球阀 DN100 → 法兰阀门安装（而非"浮球阀"直搜）"""
    query = build_quota_query(parser, "浮球阀", "规格:DN100")
    assert "法兰阀门" in query or "阀门安装" in query
    assert "浮球" not in query


def test_solenoid_valve_small_dn():
    """电磁阀 DN25 → 螺纹阀门安装"""
    query = build_quota_query(parser, "电磁阀", "规格:DN25")
    assert "螺纹" in query


def test_signal_butterfly_valve():
    """信号蝶阀 DN150 → 法兰阀门安装"""
    query = build_quota_query(parser, "信号蝶阀", "规格:DN150")
    assert "法兰阀门" in query


def test_pressure_reducing_valve():
    """减压阀 DN50 → 法兰阀门安装"""
    query = build_quota_query(parser, "减压阀", "规格:DN50")
    assert "法兰" in query


def test_safety_valve():
    """安全阀 DN25 → 螺纹阀门安装"""
    query = build_quota_query(parser, "安全阀", "规格:DN25")
    assert "螺纹" in query


# === 2. 连接方式矛盾修复 ===

def test_threaded_name_but_flange_desc():
    """螺纹阀门（描述中连接方式:法兰）→ 法兰阀门"""
    query = build_quota_query(
        parser, "螺纹阀门", "类型:蝶阀 规格:DN100 连接方式:法兰连接")
    assert "法兰" in query
    assert "螺纹" not in query


def test_flange_name_but_threaded_desc():
    """法兰阀门（描述中连接方式:螺纹）→ 螺纹阀门"""
    query = build_quota_query(
        parser, "法兰阀门", "类型:截止阀 规格:DN25 连接方式:螺纹连接")
    assert "螺纹" in query


# === 3. 现有逻辑不退化 ===

def test_gate_valve_large_dn():
    """闸阀 DN100 → 法兰阀门安装（不变）"""
    query = build_quota_query(parser, "闸阀", "规格:DN100")
    assert "法兰阀门" in query


def test_ball_valve_small_dn():
    """球阀 DN25 → 螺纹阀门安装（不变）"""
    query = build_quota_query(parser, "球阀", "规格:DN25")
    assert "螺纹" in query


def test_carbon_steel_valve():
    """碳钢阀门 → 法兰阀门安装（不变）"""
    query = build_quota_query(parser, "碳钢阀门")
    assert "法兰阀门" in query


def test_welded_flange_valve():
    """焊接法兰阀门 → 焊接法兰阀安装（不变）"""
    query = build_quota_query(parser, "焊接法兰阀门", "规格:DN100")
    assert "焊接法兰阀" in query
