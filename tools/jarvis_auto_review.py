# -*- coding: utf-8 -*-
"""
Jarvis 自动审核脚本 - 自动检测5类常见定额匹配错误并给出纠正建议

用法：
    python tools/jarvis_auto_review.py "output/review/review_xxx.json" --province "北京2024"

功能：
    1. 读取匹配结果JSON
    2. 自动检测5类常见错误：
       - 类别关键词不一致（地漏→铸铁管、透气帽→铸铁管）
       - 管材类型不匹配（钢塑复合管→金属骨架复合管）
       - 连接方式不匹配（丝扣→电熔）
       - 参数档位严重偏差（6T水箱→220m3）
       - 措施项目检测（脚手架→不需要套定额）
    3. 从定额库搜索正确定额
    4. 输出精简审核摘要（stdout <3K字符）
    5. 输出 auto_corrections.json 和 manual_items.json
"""

import sys
import os
import json
import re
import sqlite3
import argparse
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import get_quota_db_path, OUTPUT_DIR, CURRENT_PROVINCE


# ============================================================
# 同义词/关键词映射表（可持续扩充）
# ============================================================

# 清单描述中的核心名词 → 定额名称中应该包含的关键词
CATEGORY_KEYWORDS = {
    # 给排水附件类
    "地漏": ["地漏"],
    "洗衣机地漏": ["地漏"],
    "侧排地漏": ["地漏"],
    "透气帽": ["通气帽", "透气帽", "量油帽"],  # 不同定额库版本名称不同
    "阻火圈": ["阻火圈"],
    "雨水斗": ["雨水斗"],
    "87型雨水斗": ["雨水斗"],
    "侧入雨水斗": ["雨水斗"],
    "防虫网": ["防虫网", "网罩"],  # 无专用定额，通常套阀门
    "压力表": ["压力表"],
    # 管材类
    "凿槽": ["凿槽"],
    "凿(压)槽": ["凿槽"],
    # 阀门类 —— 减压阀不应匹配到安全阀/调节阀
    "减压阀": ["阀门"],
    "可调式减压阀": ["阀门"],
    "过滤器": ["过滤器", "除污器"],
    "Y型过滤器": ["过滤器", "除污器"],
    "管道过滤器": ["过滤器", "除污器"],
    # 设备类
    "水箱": ["水箱"],
    "变频给水设备": ["变频泵组", "变频"],
}

# 某些阀门类需要额外检查：定额不应包含的错误关键词
# 比如"可调式减压阀"不应匹配到"安全阀""调节阀""燃气"等
CATEGORY_REJECT_KEYWORDS = {
    "可调式减压阀": ["安全阀", "调节阀", "中压", "高压"],
    "减压阀": ["安全阀", "调节阀", "中压", "高压"],
    "过滤器": ["燃气", "调压"],
    "Y型过滤器": ["燃气", "调压"],
    "管道过滤器": ["燃气", "调压"],
    "压力表": ["调压", "组合式"],
}

# 管材类型映射：描述中的关键词 → 定额中应包含的关键词
MATERIAL_MAP = {
    "钢塑复合管": {
        "should_contain": ["钢塑复合管"],
        "should_not_contain": ["金属骨架", "电熔"],
    },
    "柔性铸铁": {
        "should_contain": ["铸铁"],
        "should_not_contain": ["塑料管", "粘接"],
        "search_keywords": ["W型", "铸铁管", "管箍"],  # 搜索用更精确的关键词
    },
    "柔性铸铁雨水管": {
        "should_contain": ["铸铁"],
        "should_not_contain": ["塑料管", "粘接"],
        "search_keywords": ["W型", "铸铁管", "管箍"],
    },
    "UPVC": {
        "should_contain": ["塑料管"],
        "should_not_contain": ["铸铁", "钢管"],
    },
    "PP-R": {
        "should_contain": ["塑料管"],
        "should_not_contain": ["铸铁", "钢管"],
    },
}

# 通气管用途检测：清单描述含"通气管" → 应套排水管定额而非给水管定额
PIPE_USAGE_RULES = {
    "通气": {
        "should_contain": ["排水"],
        "should_not_contain": ["给水"],
        "search_keywords": ["排水塑料管"],
    },
}

# 连接方式映射：描述中的关键词 → 定额中应包含的关键词
CONNECTION_MAP = {
    "丝扣": {
        "should_contain": ["丝扣"],
        "should_not_contain": ["电熔", "法兰", "焊接"],
    },
    "丝扣连接": {
        "should_contain": ["丝扣"],
        "should_not_contain": ["电熔", "法兰", "焊接"],
    },
    "电熔": {
        "should_contain": ["电熔"],
        "should_not_contain": ["丝扣", "粘接"],
    },
    "热熔": {
        "should_contain": ["热熔"],
        "should_not_contain": ["电熔", "丝扣"],
    },
    "粘接": {
        "should_contain": ["粘接"],
        "should_not_contain": ["电熔", "丝扣", "热熔"],
    },
    "承插粘接": {
        "should_contain": ["粘接"],
        "should_not_contain": ["电熔", "丝扣"],
    },
    "管箍连接": {
        "should_contain": ["管箍"],
        "should_not_contain": ["粘接", "电熔"],
    },
    "法兰": {
        "should_contain": ["法兰"],
        "should_not_contain": ["丝扣", "螺纹"],
    },
    "法兰连接": {
        "should_contain": ["法兰"],
        "should_not_contain": ["丝扣", "螺纹"],
    },
    "螺纹": {
        "should_contain": ["螺纹"],
        "should_not_contain": ["法兰", "焊接"],
    },
}

# 措施项关键词（清单名称包含这些词的不需要套实体定额）
MEASURE_KEYWORDS = ["脚手架", "措施费", "搭拆"]

# 套管类型：描述含"钢套管" → 应套"一般填料套管"（DB实际名称）而非"刚性防水套管"
SLEEVE_MAP = {
    "钢套管": {
        "should_contain": ["填料套管", "钢制套管"],
        "should_not_contain": ["防水套管"],
    },
    "刚性防水套管": {
        "should_contain": ["防水套管"],
        "should_not_contain": [],
    },
}


# ============================================================
# 工具函数
# ============================================================

def extract_dn(text):
    """从文本中提取 DN 值（公称直径）"""
    m = re.search(r'DN\s*(\d+)', text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None


def extract_description_lines(desc):
    """将描述拆成行列表，去掉编号前缀"""
    if not desc:
        return []
    lines = desc.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    result = []
    for line in lines:
        line = line.strip()
        # 去掉 (1) (2) 等编号前缀
        line = re.sub(r'^\(\d+\)\s*', '', line)
        if line:
            result.append(line)
    return result


def extract_core_noun(bill_name, desc_lines):
    """从清单名+描述中提取核心名词，用于类别匹配

    优先从描述第1行提取具体名词（如"地漏""透气帽"），
    回退到清单名（如"给、排水附(配)件"太宽泛就不用）

    注意：按关键词长度降序匹配，确保"侧排地漏"优先于"地漏"
    """
    # 按长度降序排列，优先匹配更长（更精确）的关键词
    sorted_keywords = sorted(CATEGORY_KEYWORDS.keys(), key=len, reverse=True)

    # 先从描述第1行找
    if desc_lines:
        first_line = desc_lines[0]
        for keyword in sorted_keywords:
            if keyword in first_line:
                return keyword

    # 再从清单名找
    for keyword in sorted_keywords:
        if keyword in bill_name:
            return keyword

    return None


def extract_material(desc_lines):
    """从描述中提取管材类型"""
    full_desc = ' '.join(desc_lines)
    for material in MATERIAL_MAP:
        if material in full_desc:
            return material
    return None


def extract_connection(desc_lines):
    """从描述中提取连接方式"""
    full_desc = ' '.join(desc_lines)
    # 按长度降序匹配，避免"丝扣"比"丝扣连接"先匹配
    for conn in sorted(CONNECTION_MAP.keys(), key=len, reverse=True):
        if conn in full_desc:
            return conn
    return None


def extract_sleeve_type(desc_lines):
    """从描述中提取套管类型"""
    full_desc = ' '.join(desc_lines)
    for sleeve in sorted(SLEEVE_MAP.keys(), key=len, reverse=True):
        if sleeve in full_desc:
            return sleeve
    return None


def search_quota_db(keywords, dn=None, section=None, province=None, limit=10):
    """从定额库搜索匹配的定额

    参数:
        keywords: 关键词列表（取交集）
        dn: 公称直径，如果指定则在结果中按参数匹配排序
        section: 章节前缀（如 "C10-6"）
        province: 省份
        limit: 最大返回条数
    返回: [(quota_id, name, unit), ...]
    """
    db_path = get_quota_db_path(province)
    if not os.path.exists(db_path):
        return []

    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    conditions = []
    params = []
    for kw in keywords:
        conditions.append("name LIKE ?")
        params.append(f"%{kw}%")

    if section:
        conditions.append("quota_id LIKE ?")
        params.append(f"{section}%")

    where = " AND ".join(conditions)
    sql = f"SELECT quota_id, name, unit FROM quotas WHERE {where} ORDER BY quota_id LIMIT ?"
    params.append(limit)

    cursor.execute(sql, params)
    results = cursor.fetchall()
    conn.close()

    # 如果指定了 DN，优先返回参数匹配的
    if dn and results:
        # 提取定额名中的数字参数，找最接近的
        def dn_distance(row):
            m = re.search(r'(\d+)\s*$', row[1])
            if m:
                quota_dn = int(m.group(1))
                # 精确匹配最优，否则按档位向上取
                if quota_dn == dn:
                    return 0
                elif quota_dn > dn:
                    return quota_dn - dn  # 向上取档
                else:
                    return 10000 + dn - quota_dn  # 向下不太好，惩罚大
            return 5000
        results.sort(key=dn_distance)

    return results


def search_by_id(quota_id, province=None):
    """按定额编号查找"""
    db_path = get_quota_db_path(province)
    if not os.path.exists(db_path):
        return None

    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    cursor.execute("SELECT quota_id, name, unit FROM quotas WHERE quota_id = ?",
                   (quota_id,))
    row = cursor.fetchone()
    conn.close()
    return row


# ============================================================
# 5类自动检测规则
# ============================================================

def check_category_mismatch(item, quota_name, desc_lines):
    """规则1：类别关键词不一致

    从清单描述中提取核心名词（地漏、透气帽、雨水斗等），
    检查定额名称中是否包含该名词或其同义词。
    同时检查定额是否包含不应有的错误关键词。
    """
    bill_name = item.get("name", "")
    core_noun = extract_core_noun(bill_name, desc_lines)

    if not core_noun:
        return None

    expected_keywords = CATEGORY_KEYWORDS.get(core_noun, [])
    if not expected_keywords:
        return None

    # 检查定额名称是否包含至少一个期望关键词
    has_expected = False
    for kw in expected_keywords:
        if kw in quota_name:
            has_expected = True
            break

    # 检查定额名称是否包含不应有的关键词（即使前面通过了）
    reject_keywords = CATEGORY_REJECT_KEYWORDS.get(core_noun, [])
    has_rejected = False
    rejected_word = ""
    for kw in reject_keywords:
        if kw in quota_name:
            has_rejected = True
            rejected_word = kw
            break

    if not has_expected or has_rejected:
        reason = f"类别不匹配: 清单是「{core_noun}」，定额是「{quota_name}」"
        if has_rejected:
            reason = f"类别不匹配: 清单是「{core_noun}」，定额含错误词「{rejected_word}」"
        return {
            "type": "category_mismatch",
            "reason": reason,
            "core_noun": core_noun,
            "expected": expected_keywords,
        }

    return None


def check_pipe_usage(item, quota_name, desc_lines):
    """规则6：管道用途不匹配

    检查通气管是否错误匹配到给水管定额（应该用排水管定额）。
    """
    full_desc = ' '.join(desc_lines) if desc_lines else ""

    for usage_kw, rules in PIPE_USAGE_RULES.items():
        if usage_kw not in full_desc:
            continue

        should_contain = rules.get("should_contain", [])
        should_not_contain = rules.get("should_not_contain", [])

        has_required = any(kw in quota_name for kw in should_contain)
        has_forbidden = any(kw in quota_name for kw in should_not_contain)

        if not has_required or has_forbidden:
            return {
                "type": "pipe_usage_mismatch",
                "reason": f"管道用途不匹配: 描述含「{usage_kw}」，应套排水管定额，"
                          f"当前定额是「{quota_name}」",
                "usage": usage_kw,
                "search_keywords": rules.get("search_keywords", []),
            }

    return None


def check_material_mismatch(item, quota_name, desc_lines):
    """规则2：管材类型不匹配

    从描述中提取管材关键词（钢塑、铸铁、UPVC、PP-R等），
    检查定额名称中的材质是否对应。
    """
    material = extract_material(desc_lines)
    if not material:
        return None

    rules = MATERIAL_MAP.get(material, {})
    should_contain = rules.get("should_contain", [])
    should_not_contain = rules.get("should_not_contain", [])

    # 检查是否包含应有的关键词
    has_required = False
    for kw in should_contain:
        if kw in quota_name:
            has_required = True
            break

    # 检查是否包含不应有的关键词
    has_forbidden = False
    forbidden_word = ""
    for kw in should_not_contain:
        if kw in quota_name:
            has_forbidden = True
            forbidden_word = kw
            break

    if not has_required or has_forbidden:
        reason = f"管材不匹配: 描述是「{material}」"
        if has_forbidden:
            reason += f"，定额含「{forbidden_word}」"
        elif not has_required:
            reason += f"，定额应含「{'|'.join(should_contain)}」"
        return {
            "type": "material_mismatch",
            "reason": reason,
            "material": material,
        }

    return None


def check_connection_mismatch(item, quota_name, desc_lines):
    """规则3：连接方式不匹配

    从描述中提取连接方式（丝扣、电熔、热熔、粘接等），
    检查定额名称中的连接方式是否一致。
    """
    connection = extract_connection(desc_lines)
    if not connection:
        return None

    rules = CONNECTION_MAP.get(connection, {})
    should_contain = rules.get("should_contain", [])
    should_not_contain = rules.get("should_not_contain", [])

    has_required = False
    for kw in should_contain:
        if kw in quota_name:
            has_required = True
            break

    has_forbidden = False
    forbidden_word = ""
    for kw in should_not_contain:
        if kw in quota_name:
            has_forbidden = True
            forbidden_word = kw
            break

    if not has_required or has_forbidden:
        reason = f"连接方式不匹配: 描述是「{connection}」"
        if has_forbidden:
            reason += f"，定额含「{forbidden_word}」"
        return {
            "type": "connection_mismatch",
            "reason": reason,
            "connection": connection,
        }

    return None


def check_parameter_deviation(item, quota_name, desc_lines):
    """规则4：参数档位严重偏差

    对比清单参数与定额参数，检测严重偏差：
    - 水箱容量偏差>5倍
    - 变频泵组台数不匹配
    """
    bill_name = item.get("name", "")
    params = item.get("params", {})
    full_desc = ' '.join(desc_lines)

    errors = []

    # 水箱容量检查
    if "水箱" in bill_name:
        # 从描述提取容量（如"6T"、"10m3"）
        m = re.search(r'(\d+)\s*[Tt吨]', full_desc)
        if not m:
            m = re.search(r'(\d+)\s*m3', full_desc)
        if m:
            bill_capacity = int(m.group(1))
            # 从定额名提取容量
            m2 = re.search(r'(\d+)\s*$', quota_name)
            if m2:
                quota_capacity = int(m2.group(1))
                if quota_capacity > bill_capacity * 5:
                    errors.append({
                        "type": "parameter_deviation",
                        "reason": f"容量偏差过大: 清单{bill_capacity}T，定额{quota_capacity}m3",
                        "field": "capacity",
                        "bill_value": bill_capacity,
                        "quota_value": quota_capacity,
                    })

    # 变频泵组台数检查
    if "变频" in bill_name:
        # 从描述提取台数（如"一用一备"=2台）
        pump_count = None
        if "一用一备" in full_desc:
            pump_count = 2
        elif "二用一备" in full_desc:
            pump_count = 3
        elif "三用一备" in full_desc:
            pump_count = 4

        if pump_count:
            # 从定额名提取台数
            m_quota = re.search(r'([一二三四五六]|[1-6])台', quota_name)
            if m_quota:
                cn_num = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6}
                qt = m_quota.group(1)
                quota_count = cn_num.get(qt, int(qt) if qt.isdigit() else None)
                if quota_count and quota_count != pump_count:
                    errors.append({
                        "type": "parameter_deviation",
                        "reason": f"泵组台数不匹配: 清单{pump_count}台，定额{quota_count}台",
                        "field": "pump_count",
                        "bill_value": pump_count,
                        "quota_value": quota_count,
                    })

    return errors[0] if errors else None


def check_measure_item(item, desc_lines):
    """规则5：措施项目检测

    清单名称含"脚手架"、"措施费"等 → 标记为措施项目，
    不需要套实体定额。
    """
    bill_name = item.get("name", "")
    full_desc = ' '.join(desc_lines) if desc_lines else ""

    for kw in MEASURE_KEYWORDS:
        if kw in bill_name or kw in full_desc:
            return {
                "type": "measure_item",
                "reason": f"措施项目（含「{kw}」），不需要套实体定额",
            }
    return None


def check_sleeve_mismatch(item, quota_name, desc_lines):
    """附加规则：套管类型检查

    "钢套管"应套"一般钢制套管"，而非"刚性防水套管"
    "刚性防水套管"应套"刚性防水套管"
    """
    sleeve_type = extract_sleeve_type(desc_lines)
    if not sleeve_type:
        return None

    rules = SLEEVE_MAP.get(sleeve_type, {})
    should_contain = rules.get("should_contain", [])
    should_not_contain = rules.get("should_not_contain", [])

    has_required = False
    for kw in should_contain:
        if kw in quota_name:
            has_required = True
            break

    has_forbidden = False
    for kw in should_not_contain:
        if kw in quota_name:
            has_forbidden = True
            break

    if not has_required or has_forbidden:
        return {
            "type": "sleeve_mismatch",
            "reason": f"套管类型不匹配: 描述是「{sleeve_type}」，定额是「{quota_name}」",
            "sleeve_type": sleeve_type,
        }
    return None


# ============================================================
# 自动纠正：根据错误类型搜索正确定额
# ============================================================

def find_correction(item, error, dn, province=None):
    """根据错误类型搜索正确的定额

    返回: (quota_id, quota_name) 或 None
    """
    bill_name = item.get("name", "")
    desc_lines = extract_description_lines(item.get("description", ""))
    error_type = error["type"]

    # 措施项不需要纠正
    if error_type == "measure_item":
        return None

    # 类别不匹配：用核心名词+DN搜索
    if error_type == "category_mismatch":
        core_noun = error.get("core_noun", "")

        # 特殊处理：防虫网没有专用定额，套阀门即可
        if core_noun == "防虫网":
            results = search_quota_db(["螺纹阀门"], dn=dn or 15, province=province)
            if results:
                return results[0][0], results[0][1]
            return None

        # 特殊处理：侧排地漏 → 搜索"多功能地漏"（DB中为悬挂式/直埋式）
        if core_noun == "侧排地漏":
            results = search_quota_db(["多功能地漏", "悬挂"], dn=dn, province=province)
            if not results:
                results = search_quota_db(["多功能地漏"], dn=dn, province=province)
            if results:
                return results[0][0], results[0][1]

        # 特殊处理：凿槽 → 搜索全库
        if "凿槽" in core_noun or "凿" in core_noun:
            results = search_quota_db(["凿槽"], dn=dn, province=province)
            if results:
                return results[0][0], results[0][1]
            return None

        # 特殊处理：压力表 → 搜索C6-1-章节的"压力表 安装"
        if core_noun == "压力表":
            results = search_quota_db(["压力表", "安装"], section="C6-1-",
                                      province=province)
            if not results:
                results = search_quota_db(["压力表"], section="C6-1-",
                                          province=province)
            if results:
                return results[0][0], results[0][1]
            return None

        # 特殊处理：减压阀 → 搜索焊接法兰阀门（给排水常用）
        if "减压阀" in core_noun:
            # 先搜索法兰连接的阀门
            conn = extract_connection(desc_lines)
            if conn and "法兰" in conn:
                results = search_quota_db(["焊接法兰阀门"], dn=dn,
                                          section="C10-5", province=province)
            else:
                results = search_quota_db(["螺纹阀门"], dn=dn,
                                          section="C10-5", province=province)
            if results:
                return results[0][0], results[0][1]
            return None

        # 特殊处理：过滤器 → 搜索除污器
        if "过滤器" in core_noun:
            conn = extract_connection(desc_lines)
            if conn and "法兰" in conn:
                results = search_quota_db(["法兰除污器"], dn=dn, province=province)
            else:
                results = search_quota_db(["除污器"], dn=dn, province=province)
            if results:
                return results[0][0], results[0][1]
            return None

        # 通用搜索：不限制章节
        expected = error.get("expected", [core_noun])
        kw = expected[0]
        results = search_quota_db([kw], dn=dn, province=province)
        if results:
            return results[0][0], results[0][1]

    # 管材不匹配：用正确管材类型搜索
    if error_type == "material_mismatch":
        material = error.get("material", "")
        material_rules = MATERIAL_MAP.get(material, {})

        # 优先用 search_keywords（更精确）
        search_kws = material_rules.get("search_keywords", None)
        if search_kws:
            results = search_quota_db(search_kws, dn=dn, province=province)
            if results:
                return results[0][0], results[0][1]

        # 回退到 should_contain
        search_kw = material_rules.get("should_contain", [material])[0]
        full_desc = ' '.join(desc_lines)

        if "给水" in full_desc or "给水" in bill_name:
            results = search_quota_db(["室内给水", search_kw], dn=dn, province=province)
        elif "排水" in full_desc or "雨水" in full_desc:
            # 雨水管用W型铸铁管
            if "铸铁" in material or "W型" in material:
                results = search_quota_db(["W型", "铸铁管", "管箍"], dn=dn,
                                          province=province)
            else:
                results = search_quota_db(["排水", search_kw], dn=dn,
                                          province=province)
        else:
            results = search_quota_db([search_kw], dn=dn, province=province)

        if results:
            return results[0][0], results[0][1]

    # 连接方式不匹配：保持管材类型不变，改连接方式
    if error_type == "connection_mismatch":
        connection = error.get("connection", "")
        conn_rules = CONNECTION_MAP.get(connection, {})
        conn_kw = conn_rules.get("should_contain", [connection])[0]

        material = extract_material(desc_lines)
        if material:
            mat_rules = MATERIAL_MAP.get(material, {})
            mat_kw = mat_rules.get("should_contain", [material])[0]
            results = search_quota_db([mat_kw, conn_kw], dn=dn, province=province)
        else:
            results = search_quota_db([conn_kw], dn=dn, province=province)
        if results:
            return results[0][0], results[0][1]

    # 参数偏差：搜索正确参数的定额
    if error_type == "parameter_deviation":
        field = error.get("field", "")
        if field == "capacity":
            bill_value = error.get("bill_value", 0)
            results = []
            # 小容量(<=15m3)优先搜整体水箱，大容量搜组装水箱
            if bill_value <= 15:
                results = search_quota_db(["整体水箱"], dn=bill_value,
                                          section="C10-8", province=province)
            if not results:
                results = search_quota_db(["水箱安装"], dn=bill_value,
                                          section="C10-8", province=province)
            if results:
                return results[0][0], results[0][1]
        elif field == "pump_count":
            bill_count = error.get("bill_value", 0)
            cn_num = {2: "二", 3: "三", 4: "四"}
            count_str = cn_num.get(bill_count, str(bill_count))

            # 从描述提取出口DN（如果有）
            full_desc = ' '.join(desc_lines)
            pump_dn = None
            # 尝试从描述中提取DN值（可能在参数中）
            dn_match = re.search(r'DN\s*(\d+)', full_desc, re.IGNORECASE)
            if dn_match:
                pump_dn = int(dn_match.group(1))

            # 如果没有DN值，根据流量Q估算出口DN
            # Q<=5m3/h→DN25, Q<=12→DN50, Q<=25→DN65, Q<=50→DN80, Q<=100→DN100
            if not pump_dn:
                q_match = re.search(r'Q\s*=?\s*(\d+(?:\.\d+)?)\s*m3', full_desc)
                if q_match:
                    flow = float(q_match.group(1))
                    if flow <= 5:
                        pump_dn = 25
                    elif flow <= 12:
                        pump_dn = 50
                    elif flow <= 25:
                        pump_dn = 65
                    elif flow <= 50:
                        pump_dn = 80
                    else:
                        pump_dn = 100

            results = search_quota_db(["变频泵组", f"{count_str}台"],
                                      dn=pump_dn, province=province)
            if results:
                return results[0][0], results[0][1]
            # 回退不限台数
            results = search_quota_db(["变频泵组"], dn=pump_dn,
                                      province=province)
            if results:
                for r in results:
                    if f"{count_str}台" in r[1]:
                        return r[0], r[1]
                return results[0][0], results[0][1]

    # 套管类型不匹配
    if error_type == "sleeve_mismatch":
        sleeve_type = error.get("sleeve_type", "")
        if sleeve_type == "钢套管":
            # DB中实际名称是"一般填料套管制作安装"
            results = search_quota_db(["填料套管"], dn=dn, province=province)
            if not results:
                results = search_quota_db(["一般", "套管", "制作安装"], dn=dn,
                                          province=province)
        else:
            results = search_quota_db(["防水套管"], dn=dn,
                                      section="C10-4", province=province)
        if results:
            return results[0][0], results[0][1]

    # 管道用途不匹配（通气管）
    if error_type == "pipe_usage_mismatch":
        search_kws = error.get("search_keywords", ["排水塑料管"])
        conn = extract_connection(desc_lines)
        if conn:
            conn_rules = CONNECTION_MAP.get(conn, {})
            conn_kw = conn_rules.get("should_contain", [])
            if conn_kw:
                search_kws = search_kws + conn_kw[:1]
        # 限制到 C10-2 章节（室内排水管）
        results = search_quota_db(search_kws, dn=dn, section="C10-2",
                                  province=province)
        if results:
            return results[0][0], results[0][1]

    return None


# ============================================================
# 核心审核逻辑
# ============================================================

def auto_review(json_path, province=None):
    """自动审核匹配结果

    参数:
        json_path: 审核JSON文件路径
        province: 省份

    返回: (summary_text, auto_corrections, manual_items, measure_items)
    """
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    results = data.get("results", [])
    total = len(results)

    # 统计
    correct_count = 0
    error_items = []       # 自动检测到的错误（可自动纠正）
    manual_items = []      # 需要人工判断的项目
    measure_items = []     # 措施项目

    for i, r in enumerate(results):
        seq = i + 1
        bill = r.get("bill_item", {})
        bill_name = bill.get("name", "")
        desc = bill.get("description", "")
        desc_lines = extract_description_lines(desc)
        params = bill.get("params", {})
        dn = params.get("dn") or extract_dn(' '.join(desc_lines))
        match_source = r.get("match_source", "")

        # 已跳过的措施项
        if match_source == "skip_measure":
            measure_items.append({"seq": seq, "name": bill_name})
            continue

        quotas = r.get("quotas", [])
        if not quotas:
            manual_items.append({
                "seq": seq,
                "name": bill_name,
                "reason": "无匹配结果",
            })
            continue

        q = quotas[0] if isinstance(quotas[0], dict) else {}
        quota_id = q.get("quota_id", "")
        quota_name = q.get("name", "")
        confidence = r.get("confidence", 0)

        # 依次检查5类错误（加上套管检查共6类）
        error = None

        # 规则5：措施项先检测
        error = check_measure_item(bill, desc_lines)
        if error:
            measure_items.append({
                "seq": seq,
                "name": bill_name,
                "reason": error["reason"],
            })
            continue

        # 规则1：类别关键词不一致
        if not error:
            error = check_category_mismatch(bill, quota_name, desc_lines)

        # 附加：套管类型检查
        if not error:
            error = check_sleeve_mismatch(bill, quota_name, desc_lines)

        # 规则2：管材类型不匹配
        if not error:
            error = check_material_mismatch(bill, quota_name, desc_lines)

        # 规则3：连接方式不匹配
        if not error:
            error = check_connection_mismatch(bill, quota_name, desc_lines)

        # 规则6：管道用途不匹配（通气管→应套排水管）
        if not error:
            error = check_pipe_usage(bill, quota_name, desc_lines)

        # 规则4：参数档位严重偏差
        if not error:
            error = check_parameter_deviation(bill, quota_name, desc_lines)

        if error:
            # 尝试搜索正确定额
            correction = find_correction(bill, error, dn, province)
            error_entry = {
                "seq": seq,
                "name": bill_name,
                "desc_short": desc_lines[0] if desc_lines else "",
                "dn": dn,
                "current_quota_id": quota_id,
                "current_quota_name": quota_name,
                "error_type": error["type"],
                "error_reason": error["reason"],
                "confidence": confidence,
            }
            if correction:
                error_entry["corrected_quota_id"] = correction[0]
                error_entry["corrected_quota_name"] = correction[1]
                error_items.append(error_entry)
            else:
                # 找不到纠正定额，需要人工
                manual_items.append(error_entry)
        else:
            correct_count += 1

    # 生成精简摘要
    summary = generate_summary(total, correct_count, error_items,
                               manual_items, measure_items)

    # 生成纠正JSON（格式与 jarvis_correct.py 兼容）
    auto_corrections = []
    for item in error_items:
        if "corrected_quota_id" in item:
            auto_corrections.append({
                "seq": item["seq"],
                "quota_id": item["corrected_quota_id"],
                "quota_name": item["corrected_quota_name"],
                "name": item["name"],
            })

    return summary, auto_corrections, manual_items, measure_items


def generate_summary(total, correct_count, error_items, manual_items, measure_items):
    """生成精简审核摘要（控制在3K字符以内）"""
    lines = []
    lines.append("=== 自动审核报告 ===")
    lines.append(f"总条数: {total} | 正确: {correct_count} | "
                 f"错误: {len(error_items)} | 需人工: {len(manual_items)} | "
                 f"措施项: {len(measure_items)}")
    lines.append("")

    # 错误项（已自动查找纠正定额）
    if error_items:
        lines.append("--- 错误项（已自动查找纠正定额）---")
        for item in error_items:
            desc_info = f"{item['name']}"
            if item.get('desc_short'):
                desc_info += f"({item['desc_short'][:20]})"
            if item.get('dn'):
                desc_info += f" DN{item['dn']}"
            lines.append(f"[{item['seq']}] {desc_info}")
            lines.append(f"  现: {item['current_quota_id']} {item['current_quota_name'][:35]}")
            if item.get('corrected_quota_id'):
                lines.append(f"  纠: {item['corrected_quota_id']} "
                             f"{item['corrected_quota_name'][:35]}")
            lines.append(f"  因: {item['error_reason']}")
        lines.append("")

    # 需人工确认
    if manual_items:
        lines.append("--- 需人工确认 ---")
        for item in manual_items:
            reason = item.get('error_reason', item.get('reason', ''))
            lines.append(f"[{item['seq']}] {item['name']} - {reason}")
        lines.append("")

    # 措施项目
    if measure_items:
        seqs = [str(m['seq']) for m in measure_items]
        lines.append(f"--- 措施项目（不套定额）---")
        lines.append(f"[{', '.join(seqs)}] {measure_items[0]['name']} x{len(measure_items)}条")
        lines.append("")

    return '\n'.join(lines)


# ============================================================
# 入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Jarvis 自动审核工具")
    parser.add_argument("json_path", help="审核JSON文件路径")
    parser.add_argument("--province", default=None, help=f"省份（默认{CURRENT_PROVINCE}）")
    args = parser.parse_args()

    json_path = args.json_path
    if not os.path.exists(json_path):
        print(f"错误: 文件不存在: {json_path}")
        sys.exit(1)

    # 运行自动审核
    summary, auto_corrections, manual_items, measure_items = auto_review(
        json_path, args.province
    )

    # 输出摘要到 stdout
    print(summary)

    # 保存纠正JSON
    temp_dir = OUTPUT_DIR / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)

    # 从文件名推断项目名
    project_name = Path(json_path).stem
    project_name = re.sub(r'^review_', '', project_name)

    if auto_corrections:
        corr_path = temp_dir / f"auto_corrections_{project_name}.json"
        with open(corr_path, 'w', encoding='utf-8') as f:
            json.dump(auto_corrections, f, ensure_ascii=False, indent=2)
        print(f"\n纠正JSON: {corr_path}")

    if manual_items:
        manual_path = temp_dir / f"manual_items_{project_name}.json"
        with open(manual_path, 'w', encoding='utf-8') as f:
            json.dump(manual_items, f, ensure_ascii=False, indent=2)
        print(f"人工审核: {manual_path}")

    # 退出码：有错误返回1，纯措施项返回0
    if auto_corrections or manual_items:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
