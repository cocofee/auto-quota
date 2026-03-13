# -*- coding: utf-8 -*-
"""灯具类型映射改进测试用例

测试改进的灯具路由：
1. 格栅灯 → 嵌入式灯具安装
2. 防水+管数 → 管数优先走荧光灯安装
3. 防爆灯 → 仅防爆密闭走密闭灯
4. 双面标志灯 → 吊杆式
5. 集中电源疏散照明灯 → 吸顶式
"""
from src.query_builder import build_quota_query
from src.text_parser import TextParser


parser = TextParser()


# === 1. 格栅灯 ===

def test_grid_lamp_5_head():
    """五头格栅灯 → 嵌入式灯具安装（不是荧光灯）"""
    query = build_quota_query(parser, "五头格栅灯")
    assert "嵌入" in query
    assert "荧光" not in query


def test_grid_lamp_10_head():
    """十头格栅灯 → 嵌入式灯具安装"""
    query = build_quota_query(parser, "十头格栅灯")
    assert "嵌入" in query


# === 2. 防水+管数 → 管数优先 ===

def test_waterproof_single_tube_hanging():
    """防水型单管灯(管吊) → 荧光灯具安装 吊管式 单管（不是防水灯头）"""
    query = build_quota_query(parser, "防水型单管灯(管吊)")
    assert "荧光灯" in query
    assert "单管" in query
    assert "吊管" in query
    assert "防水防尘灯" not in query


def test_waterproof_double_tube():
    """防水防尘双管灯 → 荧光灯具安装 双管"""
    query = build_quota_query(parser, "防水防尘双管灯")
    assert "荧光灯" in query
    assert "双管" in query


def test_waterproof_no_tube():
    """防水灯（无管数）→ 防水防尘灯安装"""
    query = build_quota_query(parser, "防水灯")
    assert "防水防尘灯" in query


# === 3. 防爆灯 ===

def test_explosion_proof_sealed():
    """防爆密闭灯 → 密闭灯安装"""
    query = build_quota_query(parser, "防爆密闭灯")
    assert "密闭" in query


def test_explosion_proof_fluorescent():
    """防爆荧光灯 → 荧光灯具安装 防爆（不是密闭灯）"""
    query = build_quota_query(parser, "防爆荧光灯")
    assert "荧光灯" in query
    assert "密闭" not in query


def test_explosion_proof_generic():
    """防爆灯 → 荧光灯具安装 防爆"""
    query = build_quota_query(parser, "防爆灯")
    assert "荧光灯" in query
    assert "密闭" not in query


# === 4. 双面标志灯 → 吊杆式 ===

def test_double_sided_exit_sign():
    """双面疏散指示灯 → 标志诱导灯安装 吊杆式"""
    query = build_quota_query(parser, "双面疏散指示灯")
    assert "标志" in query or "诱导" in query
    assert "吊杆" in query
    assert "壁" not in query


def test_single_sided_exit_sign():
    """单面疏散指示灯 → 标志诱导灯安装 壁式"""
    query = build_quota_query(parser, "单面疏散指示灯")
    assert "壁" in query


def test_double_sided_emergency():
    """消防应急照明灯(双面) → 吊杆式"""
    query = build_quota_query(parser, "消防应急照明灯(双面)")
    assert "吊杆" in query


# === 5. 集中电源疏散照明灯 ===

def test_centralized_evacuation_light():
    """集中电源疏散照明灯 → 智能应急灯具 吸顶"""
    query = build_quota_query(parser, "集中电源疏散照明灯")
    assert "智能应急" in query
    assert "吸顶" in query


# === 6. 现有逻辑不退化 ===

def test_ceiling_light_unchanged():
    """吸顶灯 → 普通灯具安装 吸顶灯（不变）"""
    query = build_quota_query(parser, "吸顶灯")
    assert "普通灯具安装" in query
    assert "吸顶灯" in query


def test_fluorescent_light_unchanged():
    """荧光灯 → 荧光灯具安装（不变）"""
    query = build_quota_query(parser, "荧光灯")
    assert "荧光灯具安装" in query


def test_wall_light_unchanged():
    """壁灯 → 壁灯安装（不变）"""
    query = build_quota_query(parser, "壁灯")
    assert "壁灯安装" in query
