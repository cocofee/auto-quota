"""
批量修正经验库 - 把审核确认的正确定额写入经验库权威层

根据2026-02-17审核结果，修正322条黄色/红色项中可以确定正确答案的条目。
修正规则基于用户（造价人员）逐条确认。

用法：
    python tools/batch_fix_experience.py          # 预览模式（不写入）
    python tools/batch_fix_experience.py --apply   # 实际写入经验库
"""

import json
import os
import re
import sys

# 添加项目根目录到路径
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src.text_parser import normalize_bill_text
from src.experience_db import ExperienceDB


# ============================================================
# 定额参数取档表（DN → 定额编号）
# ============================================================

# 焊接法兰阀门（C10-5-33~47）- 闸阀/止回阀/Y型过滤器/平衡阀/大管径电动阀
FLANGED_VALVE = {
    25: "C10-5-33", 32: "C10-5-33", 40: "C10-5-34",
    50: "C10-5-35", 65: "C10-5-36", 70: "C10-5-36",
    80: "C10-5-37", 100: "C10-5-38", 125: "C10-5-39",
    150: "C10-5-40", 200: "C10-5-41", 250: "C10-5-42",
    300: "C10-5-43", 350: "C10-5-44", 400: "C10-5-45",
}
FLANGED_VALVE_NAMES = {
    "C10-5-33": "焊接法兰阀门 公称直径(mm以内) 32",
    "C10-5-34": "焊接法兰阀门 公称直径(mm以内) 40",
    "C10-5-35": "焊接法兰阀门 公称直径(mm以内) 50",
    "C10-5-36": "焊接法兰阀门 公称直径(mm以内) 70",
    "C10-5-37": "焊接法兰阀门 公称直径(mm以内) 80",
    "C10-5-38": "焊接法兰阀门 公称直径(mm以内) 100",
    "C10-5-39": "焊接法兰阀门 公称直径(mm以内) 125",
    "C10-5-40": "焊接法兰阀门 公称直径(mm以内) 150",
    "C10-5-41": "焊接法兰阀门 公称直径(mm以内) 200",
    "C10-5-42": "焊接法兰阀门 公称直径(mm以内) 250",
    "C10-5-43": "焊接法兰阀门 公称直径(mm以内) 300",
    "C10-5-44": "焊接法兰阀门 公称直径(mm以内) 350",
    "C10-5-45": "焊接法兰阀门 公称直径(mm以内) 400",
}

# 对夹式法兰阀门（C10-5-48~58）- 消防蝶阀
WAFER_VALVE = {
    50: "C10-5-48", 65: "C10-5-49", 70: "C10-5-49",
    80: "C10-5-50", 100: "C10-5-51", 125: "C10-5-52",
    150: "C10-5-53", 200: "C10-5-54", 250: "C10-5-55",
    300: "C10-5-56", 350: "C10-5-57", 400: "C10-5-58",
}
WAFER_VALVE_NAMES = {
    "C10-5-48": "焊接对夹式法兰阀门 公称直径(mm以内) 50",
    "C10-5-49": "焊接对夹式法兰阀门 公称直径(mm以内) 70",
    "C10-5-50": "焊接对夹式法兰阀门 公称直径(mm以内) 80",
    "C10-5-51": "焊接对夹式法兰阀门 公称直径(mm以内) 100",
    "C10-5-52": "焊接对夹式法兰阀门 公称直径(mm以内) 125",
    "C10-5-53": "焊接对夹式法兰阀门 公称直径(mm以内) 150",
    "C10-5-54": "焊接对夹式法兰阀门 公称直径(mm以内) 200",
    "C10-5-55": "焊接对夹式法兰阀门 公称直径(mm以内) 250",
    "C10-5-56": "焊接对夹式法兰阀门 公称直径(mm以内) 300",
    "C10-5-57": "焊接对夹式法兰阀门 公称直径(mm以内) 350",
    "C10-5-58": "焊接对夹式法兰阀门 公称直径(mm以内) 400",
}

# 电动二通调节阀（C5-3-59~61）- 小管径电动阀
ELECTRIC_VALVE_SMALL = {
    25: "C5-3-59", 32: "C5-3-59", 40: "C5-3-59", 50: "C5-3-59",
    65: "C5-3-60", 80: "C5-3-60", 100: "C5-3-60",
    125: "C5-3-61", 150: "C5-3-61", 200: "C5-3-61",
}
ELECTRIC_VALVE_SMALL_NAMES = {
    "C5-3-59": "电动二通调节阀 ≤DN50",
    "C5-3-60": "电动二通调节阀 ≤DN100",
    "C5-3-61": "电动二通调节阀 ≤DN200",
}

# 消防钢管沟槽连接（C9-1-13~17）
FIRE_PIPE_GROOVE = {
    65: "C9-1-13", 80: "C9-1-14", 100: "C9-1-15",
    125: "C9-1-16", 150: "C9-1-17",
}
FIRE_PIPE_GROOVE_NAMES = {
    "C9-1-13": "钢管(沟槽连接) 公称直径 65",
    "C9-1-14": "钢管(沟槽连接) 公称直径 80",
    "C9-1-15": "钢管(沟槽连接) 公称直径 100",
    "C9-1-16": "钢管(沟槽连接) 公称直径 125",
    "C9-1-17": "钢管(沟槽连接) 公称直径 150",
}

# 涂塑碳钢管卡压连接（C10-3-96~100）
COATED_STEEL_CRIMP = {
    40: "C10-3-96", 50: "C10-3-97", 65: "C10-3-98",
    80: "C10-3-98", 100: "C10-3-99", 150: "C10-3-100",
}
COATED_STEEL_CRIMP_NAMES = {
    "C10-3-96": "涂塑碳钢管(卡压、环压连接) 40",
    "C10-3-97": "涂塑碳钢管(卡压、环压连接) 50",
    "C10-3-98": "涂塑碳钢管(卡压、环压连接) 80",
    "C10-3-99": "涂塑碳钢管(卡压、环压连接) 100",
    "C10-3-100": "涂塑碳钢管(卡压、环压连接) 150",
}

# 一般填料套管（C10-4-61~73）
FILLING_SLEEVE = {
    25: "C10-4-61", 32: "C10-4-62", 40: "C10-4-63",
    50: "C10-4-64", 65: "C10-4-65", 80: "C10-4-65",
    100: "C10-4-66", 125: "C10-4-67", 150: "C10-4-68",
    200: "C10-4-69", 250: "C10-4-70", 300: "C10-4-71",
}
FILLING_SLEEVE_NAMES = {
    "C10-4-61": "一般填料套管制作安装 公称直径(mm以内) 25",
    "C10-4-62": "一般填料套管制作安装 公称直径(mm以内) 32",
    "C10-4-63": "一般填料套管制作安装 公称直径(mm以内) 40",
    "C10-4-64": "一般填料套管制作安装 公称直径(mm以内) 50",
    "C10-4-65": "一般填料套管制作安装 公称直径(mm以内) 80",
    "C10-4-66": "一般填料套管制作安装 公称直径(mm以内) 100",
    "C10-4-67": "一般填料套管制作安装 公称直径(mm以内) 125",
    "C10-4-68": "一般填料套管制作安装 公称直径(mm以内) 150",
    "C10-4-69": "一般填料套管制作安装 公称直径(mm以内) 200",
    "C10-4-70": "一般填料套管制作安装 公称直径(mm以内) 250",
    "C10-4-71": "一般填料套管制作安装 公称直径(mm以内) 300",
}

# 地漏安装（C10-6-66~69）
FLOOR_DRAIN = {
    50: "C10-6-66", 75: "C10-6-67", 100: "C10-6-68", 150: "C10-6-69",
}
FLOOR_DRAIN_NAMES = {
    "C10-6-66": "地漏安装 公称直径(mm以内) 50",
    "C10-6-67": "地漏安装 公称直径(mm以内) 75",
    "C10-6-68": "地漏安装 公称直径(mm以内) 100",
    "C10-6-69": "地漏安装 公称直径(mm以内) 150",
}

# 软接头法兰连接（C10-5-133~143）
SOFT_JOINT = {
    25: "C10-5-133", 32: "C10-5-134", 40: "C10-5-135",
    50: "C10-5-136", 65: "C10-5-137", 80: "C10-5-137",
    100: "C10-5-138", 125: "C10-5-139", 150: "C10-5-140",
    200: "C10-5-141", 250: "C10-5-142", 300: "C10-5-143",
}
SOFT_JOINT_NAMES = {
    "C10-5-133": "软接头(法兰连接) 公称直径(mm以内) 25",
    "C10-5-134": "软接头(法兰连接) 公称直径(mm以内) 32",
    "C10-5-135": "软接头(法兰连接) 公称直径(mm以内) 40",
    "C10-5-136": "软接头(法兰连接) 公称直径(mm以内) 50",
    "C10-5-137": "软接头(法兰连接) 公称直径(mm以内) 80",
    "C10-5-138": "软接头(法兰连接) 公称直径(mm以内) 100",
    "C10-5-139": "软接头(法兰连接) 公称直径(mm以内) 125",
    "C10-5-140": "软接头(法兰连接) 公称直径(mm以内) 150",
    "C10-5-141": "软接头(法兰连接) 公称直径(mm以内) 200",
    "C10-5-142": "软接头(法兰连接) 公称直径(mm以内) 250",
    "C10-5-143": "软接头(法兰连接) 公称直径(mm以内) 300",
}

# 弯头导流叶片（C7-2-115~120）按大边长
ELBOW_VANE = {
    630: "C7-2-115", 800: "C7-2-116", 1000: "C7-2-117",
    1250: "C7-2-118", 1600: "C7-2-119", 2000: "C7-2-120",
}
ELBOW_VANE_NAMES = {
    "C7-2-115": "弯头导流叶片制作组装 大边长 630mm以内",
    "C7-2-116": "弯头导流叶片制作组装 大边长 800mm以内",
    "C7-2-117": "弯头导流叶片制作组装 大边长 1000mm以内",
    "C7-2-118": "弯头导流叶片制作组装 大边长 1250mm以内",
    "C7-2-119": "弯头导流叶片制作组装 大边长 1600mm以内",
    "C7-2-120": "弯头导流叶片制作组装 大边长 2000mm以内",
}

# 百叶风口（C7-7-1~6）按周长
LOUVER = {
    800: "C7-7-1", 1200: "C7-7-2", 1800: "C7-7-3",
    2400: "C7-7-4", 3200: "C7-7-5", 6000: "C7-7-6",
}
LOUVER_NAMES = {
    "C7-7-1": "百叶风口安装 周长(mm以内) 800",
    "C7-7-2": "百叶风口安装 周长(mm以内) 1200",
    "C7-7-3": "百叶风口安装 周长(mm以内) 1800",
    "C7-7-4": "百叶风口安装 周长(mm以内) 2400",
    "C7-7-5": "百叶风口安装 周长(mm以内) 3200",
    "C7-7-6": "百叶风口安装 周长(mm以内) 6000",
}

# 方矩形散流器（C7-7-22~25）按周长
SQUARE_DIFFUSER = {
    800: "C7-7-22", 1200: "C7-7-23", 1800: "C7-7-24", 2400: "C7-7-25",
}
SQUARE_DIFFUSER_NAMES = {
    "C7-7-22": "散流器安装 方、矩形周长(mm以内) 800",
    "C7-7-23": "散流器安装 方、矩形周长(mm以内) 1200",
    "C7-7-24": "散流器安装 方、矩形周长(mm以内) 1800",
    "C7-7-25": "散流器安装 方、矩形周长(mm以内) 2400",
}

# 防火调节阀（C7-6-1~12）按方矩形周长
FIRE_DAMPER = {
    1200: "C7-6-1", 1800: "C7-6-2", 2400: "C7-6-3",
    3200: "C7-6-4", 4800: "C7-6-5", 7200: "C7-6-6",
}
FIRE_DAMPER_NAMES = {
    "C7-6-1": "防火调节阀安装 方、矩形周长(mm以内) 1200",
    "C7-6-2": "防火调节阀安装 方、矩形周长(mm以内) 1800",
    "C7-6-3": "防火调节阀安装 方、矩形周长(mm以内) 2400",
    "C7-6-4": "防火调节阀安装 方、矩形周长(mm以内) 3200",
    "C7-6-5": "防火调节阀安装 方、矩形周长(mm以内) 4800",
    "C7-6-6": "防火调节阀安装 方、矩形周长(mm以内) 7200",
}

# 钢制槽式桥架（C4-11-248~254）按宽+高
STEEL_TRAY = {
    150: "C4-11-248", 400: "C4-11-249", 600: "C4-11-250",
    800: "C4-11-251", 1000: "C4-11-252", 1200: "C4-11-253",
    1500: "C4-11-254",
}
STEEL_TRAY_NAMES = {
    "C4-11-248": "钢制槽式桥架(宽+高)(mm以下) 150",
    "C4-11-249": "钢制槽式桥架(宽+高)(mm以下) 400",
    "C4-11-250": "钢制槽式桥架(宽+高)(mm以下) 600",
    "C4-11-251": "钢制槽式桥架(宽+高)(mm以下) 800",
    "C4-11-252": "钢制槽式桥架(宽+高)(mm以下) 1000",
    "C4-11-253": "钢制槽式桥架(宽+高)(mm以下) 1200",
    "C4-11-254": "钢制槽式桥架(宽+高)(mm以下) 1500",
}

# 管内穿铜芯线照明线路（C4-11-283~286）按截面
WIRE_LIGHTING = {
    2.5: "C4-11-283", 4: "C4-11-284", 6: "C4-11-285", 10: "C4-11-286",
}
WIRE_LIGHTING_NAMES = {
    "C4-11-283": "管内穿铜芯线照明线路 导线截面(mm2以内) 2.5",
    "C4-11-284": "管内穿铜芯线照明线路 导线截面(mm2以内) 4",
    "C4-11-285": "管内穿铜芯线照明线路 导线截面(mm2以内) 6",
    "C4-11-286": "管内穿铜芯线照明线路 导线截面(mm2以内) 10",
}

# 线槽内配线（C4-11-331~335）按截面
TRUNKING_WIRE = {
    2.5: "C4-11-331", 4: "C4-11-332", 6: "C4-11-333",
    10: "C4-11-334", 25: "C4-11-335",
}
TRUNKING_WIRE_NAMES = {
    "C4-11-331": "线槽内配线 导线截面(mm2以内) 2.5",
    "C4-11-332": "线槽内配线 导线截面(mm2以内) 4",
    "C4-11-333": "线槽内配线 导线截面(mm2以内) 6",
    "C4-11-334": "线槽内配线 导线截面(mm2以内) 10",
    "C4-11-335": "线槽内配线 导线截面(mm2以内) 25",
}


# ============================================================
# 辅助函数
# ============================================================

def extract_dn(text):
    """从清单文本中提取DN值"""
    # 匹配 DN100, DN 100, De90, Φ100 等格式
    m = re.search(r'(?:DN|De|Φ|φ)\s*(\d+)', text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    # 匹配 规格：65 或 规格：100 等纯数字
    m = re.search(r'规格[：:]\s*(\d+)\s*$', text, re.MULTILINE)
    if m:
        return int(m.group(1))
    return None


def extract_size_wh(text):
    """从规格中提取宽*高，返回(宽,高)"""
    m = re.search(r'(\d+)\s*[*×xX]\s*(\d+)', text)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


def extract_section(text):
    """从电线规格中提取截面积（mm2）"""
    # 匹配 BYJ-2.5, BV2.5, 截面2.5 等
    m = re.search(r'(?:截面|BYJ|BV|BVR|RVS)\s*[-]?\s*(\d+(?:\.\d+)?)', text, re.IGNORECASE)
    if m:
        return float(m.group(1))
    return None


def lookup_dn(table, names_table, dn):
    """根据DN从取档表中查找对应的定额编号和名称"""
    if dn is None:
        return None, None
    # 找最近的大于等于dn的档位
    for threshold in sorted(table.keys()):
        if dn <= threshold:
            code = table[threshold]
            return code, names_table.get(code, "")
    # 超出范围取最大档
    max_key = max(table.keys())
    code = table[max_key]
    return code, names_table.get(code, "")


def lookup_perimeter(table, names_table, w, h):
    """根据周长(2*(w+h))从取档表中查找"""
    if w is None or h is None:
        return None, None
    perimeter = 2 * (w + h)
    return lookup_dn(table, names_table, perimeter)


def lookup_max_side(table, names_table, w, h):
    """根据大边长从取档表中查找"""
    if w is None or h is None:
        return None, None
    max_side = max(w, h)
    return lookup_dn(table, names_table, max_side)


def lookup_sum_wh(table, names_table, w, h):
    """根据宽+高之和从取档表中查找"""
    if w is None or h is None:
        return None, None
    total = w + h
    return lookup_dn(table, names_table, total)


# ============================================================
# 修正规则
# ============================================================

def determine_correction(item):
    """
    根据清单项的名称和描述，判断应该修正为哪个定额。
    返回 (quota_id, quota_name, specialty, notes) 或 None（无法确定）
    """
    name = item["bill_name"]
    desc = item.get("description", "") or ""
    text = f"{name} {desc}"

    # ---- 阀门类 ----

    # 金属阀门：末端试水阀 → 专用定额
    if name == "金属阀门" and "末端试水" in desc:
        dn = extract_dn(text)
        if dn and dn <= 25:
            return "C9-1-73", "末端试水装置 公称直径(mm以内) 25", "C9", "末端试水阀专用定额"
        elif dn and dn <= 32:
            return "C9-1-74", "末端试水装置 公称直径(mm以内) 32", "C9", "末端试水阀专用定额"
        return "C9-1-73", "末端试水装置 公称直径(mm以内) 25", "C9", "末端试水阀专用定额（默认DN25）"

    # 金属阀门：其他类型 → 焊接法兰阀门
    if name == "金属阀门":
        dn = extract_dn(text)
        code, qname = lookup_dn(FLANGED_VALVE, FLANGED_VALVE_NAMES, dn)
        if code:
            return code, qname, "C10", "金属阀门套焊接法兰阀门"
        return None

    # Y型过滤器 → 焊接法兰阀门（用户确认）
    if "Y型过滤器" in name:
        dn = extract_dn(text)
        code, qname = lookup_dn(FLANGED_VALVE, FLANGED_VALVE_NAMES, dn)
        if code:
            return code, qname, "C10", "Y型过滤器套焊接法兰阀门"
        return None

    # 蝶阀 → 对夹式法兰阀门（消防系统）
    if name == "蝶阀":
        dn = extract_dn(text)
        code, qname = lookup_dn(WAFER_VALVE, WAFER_VALVE_NAMES, dn)
        if code:
            return code, qname, "C10", "蝶阀套对夹式法兰阀门"
        return None

    # 电动阀 → 小管径用电动二通阀，大管径用焊接法兰阀门
    if name == "电动阀":
        dn = extract_dn(text)
        if dn and dn <= 200:
            code, qname = lookup_dn(ELECTRIC_VALVE_SMALL, ELECTRIC_VALVE_SMALL_NAMES, dn)
            if code:
                return code, qname, "C5", "小管径电动阀套电动二通调节阀"
        elif dn:
            code, qname = lookup_dn(FLANGED_VALVE, FLANGED_VALVE_NAMES, dn)
            if code:
                return code, qname, "C10", "大管径电动阀套焊接法兰阀门"
        return None

    # 止回阀 → 焊接法兰阀门
    if name == "止回阀":
        dn = extract_dn(text)
        code, qname = lookup_dn(FLANGED_VALVE, FLANGED_VALVE_NAMES, dn)
        if code:
            return code, qname, "C10", "止回阀套焊接法兰阀门"
        return None

    # 平衡阀 → 焊接法兰阀门
    if name == "平衡阀":
        dn = extract_dn(text)
        code, qname = lookup_dn(FLANGED_VALVE, FLANGED_VALVE_NAMES, dn)
        if code:
            return code, qname, "C10", "平衡阀套焊接法兰阀门"
        return None

    # 闸阀 → 焊接法兰阀门
    if name == "闸阀":
        dn = extract_dn(text)
        code, qname = lookup_dn(FLANGED_VALVE, FLANGED_VALVE_NAMES, dn)
        if code:
            return code, qname, "C10", "闸阀套焊接法兰阀门"
        return None

    # ---- 管道类 ----

    # 镀锌钢管(沟槽连接) → 消防钢管沟槽连接
    if name == "镀锌钢管" and "沟槽" in desc:
        dn = extract_dn(text)
        code, qname = lookup_dn(FIRE_PIPE_GROOVE, FIRE_PIPE_GROOVE_NAMES, dn)
        if code:
            return code, qname, "C9", "消防镀锌钢管沟槽连接"
        return None

    # 无缝钢管 → 室内钢管(焊接)
    if name == "无缝钢管" or (name == "无缝钢管" and "焊接" in desc):
        dn = extract_dn(text)
        if dn and dn == 100:
            return "C10-2-27", "室内钢管(焊接) 公称直径/管外径 100/108", "C10", "无缝钢管套室内钢管焊接"
        # 其他DN暂不处理
        return None

    # 消火栓钢管(涂覆EP碳钢管卡压) → 涂塑碳钢管卡压连接
    if "消火栓" in name and ("涂覆" in desc or "卡压" in desc):
        dn = extract_dn(text)
        code, qname = lookup_dn(COATED_STEEL_CRIMP, COATED_STEEL_CRIMP_NAMES, dn)
        if code:
            return code, qname, "C10", "涂覆碳钢管消防管道借用C10涂塑碳钢管"
        return None

    # 塑料管 PVC-U → 给水塑料管粘接
    if name == "塑料管" and ("PVC" in desc or "塑料" in desc):
        dn = extract_dn(text)
        if dn and dn == 32:
            return "C10-2-68", "给水塑料管(粘接) DN32", "C10", "PVC-U管套给水塑料管粘接"
        return None

    # 普通钢制套管 / 套管(普通钢制) → 一般填料套管
    if ("套管" in name and "防水" not in name and "防水" not in desc
            and "刚性" not in desc and "柔性" not in desc):
        if "普通" in name or "普通" in desc or "一般" in name:
            dn = extract_dn(text)
            code, qname = lookup_dn(FILLING_SLEEVE, FILLING_SLEEVE_NAMES, dn)
            if code:
                return code, qname, "C10", "普通钢制套管套一般填料套管"
            return None

    # 软接头(橡胶) → 软接头法兰连接
    if "软接头" in name or "橡胶软接头" in desc:
        dn = extract_dn(text)
        code, qname = lookup_dn(SOFT_JOINT, SOFT_JOINT_NAMES, dn)
        if code:
            return code, qname, "C10", "软接头套法兰连接定额"
        return None

    # ---- 通风类 ----

    # 弯头导流叶片 → 专用定额
    if "弯头导流叶片" in name:
        w, h = extract_size_wh(desc)
        code, qname = lookup_max_side(ELBOW_VANE, ELBOW_VANE_NAMES, w, h)
        if code:
            return code, qname, "C7", "弯头导流叶片专用定额"
        return None

    # 方形散流器 → 方矩形散流器
    if "方形散流器" in name or "方散流器" in name:
        w, h = extract_size_wh(desc)
        code, qname = lookup_perimeter(SQUARE_DIFFUSER, SQUARE_DIFFUSER_NAMES, w, h)
        if code:
            return code, qname, "C7", "方形散流器按周长取档"
        return None

    # 防火阀（70℃/280℃） → 防火调节阀
    if "防火阀" in name or ("防火" in name and "阀" in name):
        w, h = extract_size_wh(desc)
        code, qname = lookup_perimeter(FIRE_DAMPER, FIRE_DAMPER_NAMES, w, h)
        if code:
            return code, qname, "C7", "防火阀套防火调节阀按周长取档"
        return None

    # 百叶风口（单层/防雨/防雨防虫等） → 百叶风口安装
    if "百叶风口" in name:
        w, h = extract_size_wh(desc)
        code, qname = lookup_perimeter(LOUVER, LOUVER_NAMES, w, h)
        if code:
            return code, qname, "C7", "百叶风口按周长取档"
        return None

    # ---- 电气类 ----

    # 桥架(钢制) → 钢制槽式桥架
    if "桥架" in name and "钢制" in text:
        w, h = extract_size_wh(desc)
        code, qname = lookup_sum_wh(STEEL_TRAY, STEEL_TRAY_NAMES, w, h)
        if code:
            return code, qname, "C4", "钢制桥架按宽+高取档"
        return None

    # 管内穿线 → 管内穿铜芯线照明线路
    if name == "管内穿线":
        sec = extract_section(text)
        if sec:
            code, qname = lookup_dn(WIRE_LIGHTING, WIRE_LIGHTING_NAMES, sec)
            if code:
                return code, qname, "C4", "管内穿线套管内穿铜芯线照明线路"
        return None

    # 桥架配线 → 线槽内配线
    if name == "桥架配线":
        sec = extract_section(text)
        if sec:
            code, qname = lookup_dn(TRUNKING_WIRE, TRUNKING_WIRE_NAMES, sec)
            if code:
                return code, qname, "C4", "桥架配线套线槽内配线"
        return None

    # 射频同轴电缆 → 同轴电缆穿管
    if "同轴" in name or "SYV" in text.upper():
        return "C5-2-36", "同轴电缆 管/暗槽内穿放 phi 9以下", "C5", "射频同轴电缆套C5同轴电缆"

    # 双绞线缆 RS485 → 双绞线缆穿管
    if "双绞线" in name or "RS485" in text.upper():
        return "C5-2-21", "双绞线缆 管内穿放 ≤4对", "C5", "双绞线/RS485套C5双绞线缆"

    # ---- 仪表类 ----

    # 压力表 → 压力表就地安装
    if name in ("压力表", "压力表-超高"):
        return "C6-1-49", "压力表 就地", "C6", "管道压力表套就地安装"

    # 温度计 → 双金属温度计
    if name in ("温度计", "温度计-超高"):
        return "C6-1-2", "膨胀式温度计 双金属温度计", "C6", "管道温度计套双金属温度计"

    # ---- 卫生器具 ----

    # 地漏 → 地漏安装
    if name in ("地漏", "洗衣机地漏"):
        dn = extract_dn(text)
        code, qname = lookup_dn(FLOOR_DRAIN, FLOOR_DRAIN_NAMES, dn or 50)
        if code:
            return code, qname, "C10", "地漏安装"
        return None

    return None


# ============================================================
# 主函数
# ============================================================

def main():
    apply_mode = "--apply" in sys.argv

    # 读取黄色/红色项
    items_file = os.path.join(PROJECT_ROOT, "output", "yellow_red_items.json")
    with open(items_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    items = data["items"]

    print(f"读取 {len(items)} 条黄色/红色项")
    print(f"模式: {'实际写入' if apply_mode else '预览（加 --apply 参数写入）'}")
    print("=" * 80)

    # 初始化经验库
    db = ExperienceDB() if apply_mode else None

    # 统计
    corrected = 0       # 能确定正确答案的
    skipped = 0         # 当前匹配已经正确的
    unmatched = 0       # 无法确定的
    written = 0         # 实际写入的

    corrections = []    # 收集所有修正，预览用

    for item in items:
        result = determine_correction(item)
        if result is None:
            unmatched += 1
            continue

        quota_id, quota_name, specialty, notes = result

        # 如果当前匹配已经正确，跳过
        if item["current_quota_id"] == quota_id:
            skipped += 1
            continue

        corrected += 1

        # 用 normalize_bill_text 规范化
        bill_text_norm = normalize_bill_text(item["bill_name"], item.get("description", ""))

        correction = {
            "bill_name": item["bill_name"],
            "bill_text": bill_text_norm,
            "unit": item.get("unit"),
            "old_quota": item["current_quota_id"],
            "new_quota_id": quota_id,
            "new_quota_name": quota_name,
            "specialty": specialty,
            "notes": notes,
        }
        corrections.append(correction)

        # 预览输出
        print(f"[{corrected}] {item['bill_name']}")
        print(f"  规范化文本: {bill_text_norm[:80]}")
        print(f"  旧定额: {item['current_quota_id']}")
        print(f"  新定额: {quota_id} {quota_name}")
        print(f"  说明: {notes}")
        print()

        # 实际写入
        if apply_mode and db:
            try:
                db.add_experience(
                    bill_text=bill_text_norm,
                    quota_ids=[quota_id],
                    quota_names=[quota_name],
                    bill_name=item["bill_name"],
                    bill_unit=item.get("unit"),
                    source="user_correction",  # 权威层
                    confidence=95,
                    specialty=specialty,
                    notes=f"批量审核修正: {notes}",
                )
                written += 1
            except Exception as e:
                print(f"  !! 写入失败: {e}")

    # 汇总
    print("=" * 80)
    print(f"总计: {len(items)} 条")
    print(f"  能修正: {corrected} 条（当前定额错误，已确定正确答案）")
    print(f"  已正确: {skipped} 条（当前定额就是正确答案）")
    print(f"  无法确定: {unmatched} 条（规则未覆盖，需人工审核）")
    if apply_mode:
        print(f"  实际写入: {written} 条")
    else:
        print(f"\n加 --apply 参数执行实际写入")

    # 保存修正列表
    output_file = os.path.join(PROJECT_ROOT, "output", "corrections_preview.json")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(corrections, f, ensure_ascii=False, indent=2)
    print(f"\n修正列表已保存到: {output_file}")


if __name__ == "__main__":
    main()
