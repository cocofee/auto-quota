# -*- coding: utf-8 -*-
"""开关插座改进测试用例

测试改进的开关插座路由：
1. 插座默认暗装
2. N连体→多联组合开关插座暗装
3. 开关盒/插座盒→暗装开关(插座)盒
"""
from src.query_builder import build_quota_query
from src.text_parser import TextParser


parser = TextParser()


# === 1. 插座默认暗装 ===

def test_outlet_default_concealed():
    """普通插座（无安装方式标注）→ 默认暗装"""
    query = build_quota_query(parser, "单相二三极插座")
    assert "暗装" in query


def test_outlet_explicit_surface():
    """明装插座 → 不加暗装"""
    query = build_quota_query(parser, "明装插座")
    assert "明装" in query
    assert "暗装" not in query


def test_outlet_with_desc_concealed():
    """插座（描述含暗装）→ 暗装"""
    query = build_quota_query(parser, "单相插座", "安装方式:暗装")
    assert "暗装" in query or "嵌入" in query


def test_switch_default_concealed():
    """照明开关（无安装方式标注）→ 默认暗装"""
    query = build_quota_query(parser, "照明开关")
    assert "暗装" in query


def test_switch_explicit_surface():
    """明装开关 → 不加暗装"""
    query = build_quota_query(parser, "明装照明开关")
    assert "暗装" not in query


def test_info_outlet_no_concealed():
    """信息插座 → 弱电，不加单相，但加暗装"""
    query = build_quota_query(parser, "信息插座")
    assert "单相" not in query
    assert "暗装" in query


# === 2. N连体 ===

def test_smart_panel_combo():
    """智能插座面板4连体：单开+插座+USB+温控 → 多联组合开关插座 暗装"""
    query = build_quota_query(parser, "智能插座面板4连体：单开+插座+USB+温控")
    assert "多联组合" in query
    assert "暗装" in query


def test_combo_panel():
    """连体开关插座面板 → 多联组合开关插座"""
    query = build_quota_query(parser, "连体开关插座面板")
    assert "多联组合" in query


# === 3. 开关盒/插座盒 ===

def test_switch_box():
    """铁质开关盒 → 暗装开关(插座)盒"""
    query = build_quota_query(parser, "铁质开关盒")
    assert "暗装开关" in query
    assert "盒" in query


def test_outlet_box():
    """塑料插座盒 → 暗装开关(插座)盒"""
    query = build_quota_query(parser, "塑料插座盒")
    assert "暗装开关" in query


def test_combo_switch_box():
    """连体开关盒 → 暗装开关(插座)盒 连体"""
    query = build_quota_query(parser, "连体开关盒")
    assert "暗装开关" in query
    assert "连体" in query


# === 4. 不影响其他设备 ===

def test_distribution_box_not_affected():
    """配电箱 → 不受开关插座逻辑影响"""
    query = build_quota_query(parser, "配电箱")
    assert "暗装" not in query


def test_lamp_not_affected():
    """吸顶灯 → 不受开关插座逻辑影响"""
    query = build_quota_query(parser, "吸顶灯")
    assert "暗装" not in query
