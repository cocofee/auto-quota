# -*- coding: utf-8 -*-
"""风口/散流器/阀门改进测试用例

测试改进：
1. 风口归一化（防雨百叶/格栅风口→百叶风口）
2. 消声百叶不走阀门路由
3. 风量调节阀→多叶调节阀
4. 通风止回阀路由
"""
from src.query_builder import build_quota_query, _normalize_bill_name
from src.text_parser import TextParser


parser = TextParser()


# === 1. 风口归一化 ===

def test_rain_louver_to_louver_outlet():
    """防雨百叶风口 → 百叶风口（不搜到建筑百叶窗）"""
    result = _normalize_bill_name("防雨百叶风口800*150")
    assert "百叶风口" in result
    assert "800" not in result  # 尺寸噪声已去除


def test_single_layer_louver():
    """单层百叶风口 → 百叶风口"""
    result = _normalize_bill_name("单层百叶风口")
    assert "百叶风口" in result


def test_grid_outlet():
    """格栅风口 → 百叶风口"""
    result = _normalize_bill_name("单层格栅风口")
    assert "百叶风口" in result


def test_diffuser_unchanged():
    """散流器 → 保持不变"""
    result = _normalize_bill_name("方形散流器")
    assert "散流器" in result


# === 2. 消声百叶不走阀门 ===

def test_silencer_louver_not_valve():
    """消声百叶 → 不被阀门模板拦截"""
    query = build_quota_query(parser, "消声百叶")
    assert "防火" not in query
    assert "阀门" not in query


# === 3. 风量调节阀 → 多叶调节阀 ===

def test_volume_damper_to_multi_leaf():
    """风量调节阀 → 多叶调节阀安装"""
    query = build_quota_query(parser, "风量调节阀")
    assert "多叶" in query


def test_fire_damper_unchanged():
    """防火阀 → 防火调节阀安装（不变）"""
    query = build_quota_query(parser, "防火阀")
    assert "防火" in query


def test_multi_leaf_damper_unchanged():
    """对开多叶调节阀 → 多叶调节阀安装（不变）"""
    query = build_quota_query(parser, "对开多叶调节阀")
    assert "多叶" in query


# === 4. 通风止回阀 ===

def test_vent_check_valve_c7():
    """止回阀（C7通风专业）→ 风管止回阀安装"""
    query = build_quota_query(parser, "止回阀", specialty="C7")
    assert "风管止回阀" in query


def test_pipe_check_valve_not_intercepted():
    """止回阀（管道专业，无周长）→ 不走风管路由"""
    query = build_quota_query(parser, "螺纹阀门", "类型:止回阀 规格:DN25")
    assert "风管" not in query
