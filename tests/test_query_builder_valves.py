# -*- coding: utf-8 -*-
"""阀门族对象模板测试用例

测试 _build_valve_query 的各个路由分支：
1. 通风类阀门拦截（防火阀/调节阀）
2. 特殊设备（倒流防止器/自动排气阀/减压孔板）
3. 过滤器 → 螺纹阀门
4. 软接头 → 连接方式分流
5. 法兰套件 → 塑料法兰
6. 管道阀门规范化（闸阀/蝶阀等泛称）
"""
from src.query_builder import build_quota_query
from src.text_parser import TextParser


parser = TextParser()


# === 1. 通风类阀门拦截 ===

def test_carbon_steel_fire_damper_routes_to_ventilation():
    """碳钢阀门 名称：280℃防火阀 → 应走防火调节阀，不走法兰阀门"""
    query = build_quota_query(parser, "碳钢阀门 名称：280℃防火阀")
    assert "防火" in query
    assert "法兰阀门" not in query


def test_carbon_steel_smoke_damper_routes_to_ventilation():
    """碳钢阀门 名称：280℃排烟防火阀 → 应走防火调节阀"""
    query = build_quota_query(parser, "碳钢阀门 名称：280℃排烟防火阀")
    assert "防火" in query


def test_multi_leaf_damper_routes_correctly():
    """碳钢阀门 名称：电动对开多叶调节阀 → 应走多叶调节阀"""
    query = build_quota_query(parser, "碳钢阀门 名称：电动对开多叶调节阀")
    assert "多叶调节阀" in query


def test_electric_fire_damper_with_model_prefix():
    """碳钢阀门 名称：MEE-70℃电动防火阀-超高 → 去型号去温度去超高"""
    query = build_quota_query(parser, "碳钢阀门 名称：MEE-70℃电动防火阀-超高")
    assert "防火" in query
    assert "MEE" not in query
    assert "超高" not in query


def test_c7_carbon_steel_valve_no_real_type():
    """C7通风专业的碳钢阀门（无具体名称）→ 应走防火调节阀"""
    query = build_quota_query(parser, "碳钢阀门", specialty="C7")
    assert "防火" in query


# === 2. 特殊设备 ===

def test_backflow_preventer():
    """金属阀门 名称：倒流防止器 → 倒流防止器组成与安装"""
    query = build_quota_query(parser, "金属阀门 名称：倒流防止器")
    assert "倒流防止器" in query


def test_auto_exhaust_valve():
    """自动排气阀 → 自动排气阀"""
    query = build_quota_query(parser, "自动排气阀")
    assert "自动排气阀" in query


def test_quick_exhaust_valve():
    """快速排气阀 含配套截止阀 → 自动排气阀"""
    query = build_quota_query(parser, "快速排气阀 含配套截止阀")
    assert "自动排气阀" in query


def test_pressure_orifice():
    """螺纹阀门 类型：减压孔板 → 减压孔板"""
    query = build_quota_query(parser, "螺纹阀门 类型：减压孔板")
    assert "减压孔板" in query


# === 3. 过滤器 ===

def test_y_filter():
    """Y型过滤器（小口径）→ 螺纹阀门"""
    query = build_quota_query(parser, "Y型过滤器")
    assert "螺纹阀门" in query


def test_y_filter_with_type():
    """Y型过滤器 类型：Y型过滤器 → 螺纹阀门"""
    query = build_quota_query(parser, "Y型过滤器 类型：Y型过滤器")
    assert "螺纹阀门" in query


def test_y_filter_large_dn():
    """Y形过滤器 DN125 → 大口径走法兰阀门"""
    query = build_quota_query(parser, "Y形过滤器", "规格:DN125")
    assert "法兰" in query


def test_air_filter_not_intercepted():
    """空气过滤器 → 不应被模板拦截（有专用定额）"""
    query = build_quota_query(parser, "空气过滤器")
    assert "螺纹阀门" not in query


def test_water_regulating_valve_not_ventilation():
    """动态平衡电动调节阀 → 不应走通风路由"""
    query = build_quota_query(parser, "动态平衡电动调节阀")
    assert "防火" not in query


# === 4. 软接头 ===

def test_flexible_joint_small_dn():
    """软接头(软管) DN20 → 软接头(螺纹连接)"""
    query = build_quota_query(parser, "软接头(软管)",
                              "公称直径:DN20")
    assert "软接头" in query


def test_flexible_joint_large_dn():
    """软接头(软管) DN100 → 软接头(法兰连接)"""
    query = build_quota_query(parser, "软接头(软管)",
                              "公称直径:DN100")
    assert "软接头" in query
    assert "法兰" in query


# === 5. 法兰套件 ===

def test_electrofusion_flange_kit():
    """螺纹法兰阀门 类型:电热熔法兰套件 → 塑料法兰"""
    query = build_quota_query(parser, "螺纹法兰阀门 类型:电热熔法兰套件")
    assert "塑料法兰" in query


# === 6. 管道阀门 → 交给老代码处理（模板不拦截） ===
# 管道阀门（闸阀/蝶阀/碳钢阀门等）由管道路由处理，保留location/usage上下文

def test_gate_valve_large_dn():
    """闸阀 DN100 → 老代码管道路由处理，应含法兰"""
    query = build_quota_query(parser, "闸阀 规格、压力等级：DN100")
    assert "法兰" in query and "阀门" in query


def test_ball_valve_small_dn():
    """球阀 DN25 → 老代码管道路由处理，应含螺纹"""
    query = build_quota_query(parser, "球阀",
                              "规格、压力等级:DN25")
    assert "螺纹" in query and "阀门" in query


def test_carbon_steel_valve_generic():
    """碳钢阀门（非C7）→ 老代码处理，含法兰阀门"""
    query = build_quota_query(parser, "碳钢阀门")
    assert "法兰阀门" in query


def test_threaded_valve_explicit():
    """螺纹阀门 → 老代码处理"""
    query = build_quota_query(parser, "螺纹阀门")
    assert "螺纹" in query
    assert "螺纹阀门" in query


def test_flange_valve_explicit():
    """法兰阀门 → 法兰阀门安装"""
    query = build_quota_query(parser, "法兰阀门")
    assert "法兰阀门" in query


def test_threaded_flange_valve_soft_gate():
    """螺纹法兰阀门 类型:软密封闸阀 → 老代码处理（会路由到螺纹法兰阀安装）"""
    query = build_quota_query(parser, "螺纹法兰阀门 类型:软密封闸阀")
    # 注意：理想应路由到"法兰阀门安装"（软密封闸阀是法兰连接），
    # 但老代码按清单名路由到"螺纹法兰阀安装"，暂不处理此case
    assert "螺纹法兰阀" in query


def test_copper_check_valve_dn20():
    """铜截止阀 DN20 → 螺纹阀门"""
    query = build_quota_query(parser, "铜截止阀 类型:铜截止阀 规格、压力等级:DN20 1.0MPa")
    assert "螺纹" in query


def test_non_pipe_valve_passthrough():
    """电磁阀 → 不应被阀门模板拦截（返回None，交给后续逻辑）"""
    query = build_quota_query(parser, "电磁阀")
    # 电磁阀不是管道阀门，不应被路由到"法兰阀门安装"或"螺纹阀门安装"
    assert "法兰阀门" not in query
    assert "螺纹阀门" not in query
