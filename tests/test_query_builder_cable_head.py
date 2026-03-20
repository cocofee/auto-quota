# -*- coding: utf-8 -*-
"""电缆头对象模板测试用例

测试 _build_cable_head_query 的各个路由分支：
1. 压铜接线端子（浙江特例）
2. 矿物绝缘电缆头（BTLY/YTTW等）
3. 控制电缆头（按芯数分档）
4. 电力电缆终端头（默认1kV干包式，按截面分档）
5. 中间头
"""
from src.query_builder import build_quota_query
from src.text_parser import TextParser


parser = TextParser()


# === 1. 压铜接线端子 ===

def test_copper_terminal_from_cable_head():
    """电力电缆头 名称:压铜接线端子 规格:16mm2 → 压铜接线端子"""
    query = build_quota_query(
        parser,
        "电力电缆头",
        "名称:压铜接线端子 规格:16mm2 材质、类型:铜"
    )
    assert "压铜接线端子" in query
    assert "终端头" not in query


# === 2. 矿物绝缘电缆头 ===

def test_mineral_insulated_power_cable_head():
    """电力电缆头 型号:NG-A(BTLY) 规格:4*120+1*70 → 矿物绝缘电力电缆终端头"""
    query = build_quota_query(
        parser,
        "电力电缆头",
        "规格:4*120+1*70 名称:电力电缆头 型号:NG-A(BTLY) 材质、类型:铜芯"
    )
    assert "矿物绝缘" in query
    assert "电力" in query
    assert "终端头" in query


def test_mineral_insulated_control_cable_head():
    """电缆终端头 YTTW-5X10 → 矿物绝缘控制电缆终端头"""
    query = build_quota_query(
        parser,
        "电缆终端头",
        "矿物绝缘控制电缆终端头制作、安装 YTTW-5X10"
    )
    assert "矿物绝缘" in query
    assert "控制" in query


# === 3. 控制电缆头 ===

def test_control_cable_head_6_core():
    """控制电缆头 规格:6芯以下 → 控制电缆终端头 芯数 6"""
    query = build_quota_query(
        parser,
        "控制电缆头",
        "规格:6芯以下 其他:一切未尽事宜详见相关图纸及图集"
    )
    assert "控制电缆终端头" in query
    assert "6" in query


def test_control_cable_head_14_core():
    """控制电缆头 名称:塑料控制电缆终端头 规格:14芯内 → 控制电缆终端头"""
    query = build_quota_query(
        parser,
        "控制电缆头",
        "名称:塑料控制电缆终端头制作、安装 规格:14芯内"
    )
    assert "控制电缆终端头" in query


def test_control_cable_head_from_spec():
    """控制电缆头 规格:4*1.5 型号:WDZB-KYJY → 控制电缆终端头（从NxN提取芯数4）"""
    query = build_quota_query(
        parser,
        "控制电缆头",
        "规格:4*1.5 名称:控制电缆头 型号:WDZB-KYJY 材质、类型:铜芯"
    )
    assert "控制电缆终端头" in query
    assert "4" in query


# === 4. 电力电缆终端头 ===

def test_power_cable_head_1kv_dry():
    """电力电缆头 5×10mm2 电压等级1KV以下 → 1kV以下室内干包式铜芯电力电缆终端头"""
    query = build_quota_query(
        parser,
        "电力电缆头",
        "名称:电力电缆头 规格:5×10mm2 电压等级（kV):1KV以下"
    )
    assert "1kV" in query
    assert "干包" in query
    assert "终端头" in query


def test_power_cable_head_heat_shrink():
    """电力电缆头 型号:热缩式电缆终端头 → 热(冷)缩式"""
    query = build_quota_query(
        parser,
        "电力电缆头",
        "型号:热缩式电缆终端头 规格:截面10mm2以下 材质、类型:铜芯 电压等级（kV):1"
    )
    assert "热" in query and "缩" in query
    assert "终端头" in query


def test_cable_terminal_1kv_default_dry():
    """电缆终端头 规格:25mm2 电压等级:1kV以下 → 干包式（1kV默认干包）"""
    query = build_quota_query(
        parser,
        "电缆终端头",
        "规格：25mm2; 电压等级：1kV以下."
    )
    assert "干包" in query
    assert "终端头" in query


def test_cable_terminal_from_name_with_spec():
    """电缆终端头 名称:电力电缆头 规格型号:3*2.5 → 1kV干包式"""
    query = build_quota_query(
        parser,
        "电缆终端头",
        "名称:电力电缆头 规格型号:3*2.5"
    )
    assert "1kV" in query
    assert "终端头" in query


def test_power_cable_head_50mm2():
    """电力电缆头 规格:50mm2以内(4芯) 电压:1kv → 截面50"""
    query = build_quota_query(
        parser,
        "电力电缆头",
        "型号:50mm2以内（4芯） 材质:铜质 电压等级:（kv):1kv"
    )
    assert "终端头" in query
    assert "1kV" in query


def test_cable_terminal_no_10kv_default():
    """电缆终端头（无电压标注）→ 默认1kV，不应出现10kV"""
    query = build_quota_query(
        parser,
        "电缆终端头",
        "名称:电力电缆头 规格型号:4*25+1*16"
    )
    assert "10kV" not in query
    assert "10KV" not in query


# === 5. 电缆敷设不被拦截 ===

def test_cable_laying_not_intercepted():
    """电力电缆（非电缆头）→ 不应被电缆头模板拦截"""
    query = build_quota_query(
        parser,
        "电力电缆",
        "型号:YJV-4*25 敷设方式:沿桥架"
    )
    # 应走电缆敷设路由，不走电缆头模板
    assert "终端头" not in query
    assert "敷设" in query or "电力电缆" in query


def test_cable_laying_strips_head_and_terminal_noise():
    """电缆本体描述里的中间头/接线端子噪声不应污染电缆敷设 query。"""
    query = build_quota_query(
        parser,
        "电力电缆",
        "型号:TC90-0.6/1KV-5*35 材质:铝合金 敷设方式、部位:穿管 电缆接线端子及电缆中间头制作安装"
    )
    assert "敷设" in query
    assert "中间头" not in query
    assert "接线端子" not in query


def test_middle_head_keeps_aluminum_anchor():
    """中间头 query 应保留中间头和铝芯锚点。"""
    query = build_quota_query(
        parser,
        "电缆中间头",
        "中间头制作与安装 1kV以下室内干包式铝芯电力电缆 电缆截面(mm2)≤240"
    )
    assert "中间头" in query
    assert "铝芯" in query


def test_control_cable_laying_not_intercepted():
    """控制电缆（非电缆头）→ 不应被电缆头模板拦截"""
    query = build_quota_query(
        parser,
        "控制电缆",
        "型号:KVV-4*1.5 敷设方式:沿桥架"
    )
    assert "终端头" not in query
