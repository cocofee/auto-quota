# -*- coding: utf-8 -*-
"""
审核检测器 — 8类自动检测规则 + 辅助提取函数

从 tools/jarvis_auto_review.py 拆分而来。
每个 check_* 函数接收清单项信息，返回错误字典或 None。
新增规则只需在本文件中添加 check_xxx 函数。
"""

import re
import json
from pathlib import Path


# ============================================================
# 规则表加载（从外部 JSON 文件读取）
# ============================================================

def _load_review_rules():
    """从 data/review_rules.json 加载审核规则表"""
    rules_path = Path(__file__).parent.parent / "data" / "review_rules.json"
    if not rules_path.exists():
        return {}
    try:
        with open(rules_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for key in list(data.keys()):
            if key.startswith("_"):
                del data[key]
            elif isinstance(data[key], dict):
                data[key] = {k: v for k, v in data[key].items()
                             if not k.startswith("_")}
        return data
    except Exception:
        return {}


_RULES = _load_review_rules()

CATEGORY_KEYWORDS = _RULES.get("category_keywords", {})
CATEGORY_REJECT_KEYWORDS = _RULES.get("category_reject_keywords", {})
MATERIAL_MAP = _RULES.get("material_map", {})
PIPE_USAGE_RULES = _RULES.get("pipe_usage_rules", {})
CONNECTION_MAP = _RULES.get("connection_map", {})
MEASURE_KEYWORDS = _RULES.get("measure_keywords", {}).get("keywords", [])
SLEEVE_MAP = _RULES.get("sleeve_map", {})
ELEVATOR_TYPE_MAP = _RULES.get("elevator_type_map", {})
ELECTRIC_PAIR_RULES = _RULES.get("electric_pair_rules", {})

_floor_rules_raw = _RULES.get("elevator_floor_rules", {})
ELEVATOR_FLOOR_RULES = [
    (r[0], r[1], r[2], r[3])
    for r in _floor_rules_raw.get("rules", [])
]
ELEVATOR_HIGH_FLOOR_MAP = {
    int(k): v
    for k, v in _floor_rules_raw.get("high_floor_map", {}).items()
    if not k.startswith("_")
}

CORRECTION_STRATEGIES = _RULES.get("correction_strategies", {})


# ============================================================
# 辅助提取函数
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
        line = re.sub(r'^\(\d+\)\s*', '', line)
        if line:
            result.append(line)
    return result


def extract_core_noun(bill_name, desc_lines):
    """从清单名+描述中提取核心名词，用于类别匹配

    按关键词长度降序匹配，确保"侧排地漏"优先于"地漏"
    """
    sorted_keywords = sorted(CATEGORY_KEYWORDS.keys(), key=len, reverse=True)

    if desc_lines:
        first_line = desc_lines[0]
        for keyword in sorted_keywords:
            if keyword in first_line:
                return keyword

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


# ============================================================
# 8类检测规则
# ============================================================

def check_category_mismatch(item, quota_name, desc_lines):
    """规则1：类别关键词不一致"""
    bill_name = item.get("name", "")
    core_noun = extract_core_noun(bill_name, desc_lines)

    if not core_noun:
        return None

    expected_keywords = CATEGORY_KEYWORDS.get(core_noun, [])
    if not expected_keywords:
        return None

    has_expected = any(kw in quota_name for kw in expected_keywords)

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
    """规则6：管道用途不匹配（通气管应套排水管定额）"""
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
    """规则2：管材类型不匹配"""
    material = extract_material(desc_lines)
    if not material:
        return None

    rules = MATERIAL_MAP.get(material, {})
    should_contain = rules.get("should_contain", [])
    should_not_contain = rules.get("should_not_contain", [])

    has_required = any(kw in quota_name for kw in should_contain)

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
    """规则3：连接方式不匹配"""
    connection = extract_connection(desc_lines)
    if not connection:
        return None

    rules = CONNECTION_MAP.get(connection, {})
    should_contain = rules.get("should_contain", [])
    should_not_contain = rules.get("should_not_contain", [])

    has_required = any(kw in quota_name for kw in should_contain)

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
    """规则4：参数档位严重偏差（水箱容量、变频泵组台数）"""
    bill_name = item.get("name", "")
    full_desc = ' '.join(desc_lines)

    errors = []

    # 水箱容量检查
    if "水箱" in bill_name:
        m = re.search(r'(\d+)\s*[Tt吨]', full_desc)
        if not m:
            m = re.search(r'(\d+)\s*m3', full_desc)
        if m:
            bill_capacity = int(m.group(1))
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
        pump_count = None
        if "一用一备" in full_desc:
            pump_count = 2
        elif "二用一备" in full_desc:
            pump_count = 3
        elif "三用一备" in full_desc:
            pump_count = 4

        if pump_count:
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
    """规则5：措施项目检测（脚手架、措施费等不需要套定额）"""
    bill_name = item.get("name", "")
    full_desc = ' '.join(desc_lines) if desc_lines else ""

    for kw in MEASURE_KEYWORDS:
        if kw in bill_name or kw in full_desc:
            return {
                "type": "measure_item",
                "reason": f"措施项目（含「{kw}」），不需要套实体定额",
            }
    return None


def check_elevator_type(item, quota_name, desc_lines):
    """规则7：电梯类型不匹配"""
    bill_name = item.get("name", "")
    full_text = bill_name + " " + " ".join(desc_lines)

    for elev_type, rules in ELEVATOR_TYPE_MAP.items():
        if elev_type not in full_text:
            continue

        has_required = any(kw in quota_name for kw in rules["should_contain"])
        has_forbidden = any(kw in quota_name for kw in rules["should_not_contain"])

        if not has_required or has_forbidden:
            return {
                "type": "elevator_type_mismatch",
                "reason": f"电梯类型不匹配: 清单是「{elev_type}」，定额是「{quota_name}」",
                "elevator_type": elev_type,
            }

    return None


def _get_expected_elevator_id(elev_type, floors):
    """根据电梯类型和层数，计算正确的定额编号"""
    if elev_type == "曳引式电梯" and floors > 30:
        seq = ELEVATOR_HIGH_FLOOR_MAP.get(floors)
        if seq:
            return f"C1-4-{seq}"
        return None

    for rule_type, min_f, max_f, offset in ELEVATOR_FLOOR_RULES:
        if rule_type == elev_type and min_f <= floors <= max_f:
            return f"C1-4-{floors + offset}"

    return None


def check_elevator_floor(item, quota_name, desc_lines, quota_id=""):
    """规则8：电梯层站数不匹配"""
    bill_name = item.get("name", "")
    full_text = bill_name + " " + " ".join(desc_lines)

    m = re.search(r'层数\s*(\d+)', full_text)
    if not m:
        m = re.search(r'(\d+)\s*层', full_text)
    if not m:
        return None

    floors = int(m.group(1))

    elev_type = None
    for t in ELEVATOR_TYPE_MAP:
        if t in full_text:
            elev_type = t
            break

    if not elev_type or elev_type in ("自动扶梯", "自动人行道"):
        return None

    expected_id = _get_expected_elevator_id(elev_type, floors)
    if not expected_id or not quota_id:
        return None

    if quota_id != expected_id:
        return {
            "type": "elevator_floor_mismatch",
            "reason": f"层站数不匹配: {elev_type}{floors}层{floors}站应套{expected_id}，"
                      f"当前是{quota_id}",
            "expected_id": expected_id,
            "elevator_type": elev_type,
            "floors": floors,
        }

    return None


def check_elevator_completeness(all_results):
    """规则9：电梯跨项完整性检查"""
    reminders = []
    all_quota_ids = set()

    for r in all_results:
        quotas = r.get("quotas", [])
        if quotas and isinstance(quotas[0], dict):
            qid = quotas[0].get("quota_id", "")
            if qid:
                all_quota_ids.add(qid)

    # 有扶梯但没有外饰面
    escalator_ids = {"C1-4-68", "C1-4-69", "C1-4-70", "C1-4-71"}
    if (all_quota_ids & escalator_ids) and "C1-4-72" not in all_quota_ids:
        reminders.append({
            "type": "elevator_completeness",
            "reason": "有自动扶梯安装但未发现扶梯外饰面安装(C1-4-72)，请确认是否需要补充",
        })

    # 有电梯但没有增减层门（当层数≠站数时）
    elevator_range = {f"C1-4-{i}" for i in range(1, 68)}
    has_elevator = bool(all_quota_ids & elevator_range)
    has_door_adjust = "C1-4-77" in all_quota_ids or "C1-4-78" in all_quota_ids

    if has_elevator and not has_door_adjust:
        needs_door_adjust = False
        for r in all_results:
            quotas = r.get("quotas", [])
            if not quotas or not isinstance(quotas[0], dict):
                continue
            qid = quotas[0].get("quota_id", "")
            if qid not in elevator_range:
                continue
            bill_item = r.get("bill_item", {})
            full_text = f"{bill_item.get('name', '')} {bill_item.get('description', '')}"
            m_floor = re.search(r'(\d+)\s*层', full_text)
            m_stop = re.search(r'(\d+)\s*站', full_text)
            if m_floor and m_stop:
                if int(m_floor.group(1)) != int(m_stop.group(1)):
                    needs_door_adjust = True
                    break
            elif m_floor and not m_stop:
                needs_door_adjust = True
                break

        if needs_door_adjust:
            reminders.append({
                "type": "elevator_completeness",
                "reason": "有电梯安装且层数≠站数，但未发现增减层门(C1-4-77/78)，请补充",
            })

    return reminders


def check_sleeve_mismatch(item, quota_name, desc_lines):
    """附加规则：套管类型检查"""
    sleeve_type = extract_sleeve_type(desc_lines)
    if not sleeve_type:
        return None

    rules = SLEEVE_MAP.get(sleeve_type, {})
    should_contain = rules.get("should_contain", [])
    should_not_contain = rules.get("should_not_contain", [])

    has_required = any(kw in quota_name for kw in should_contain)
    has_forbidden = any(kw in quota_name for kw in should_not_contain)

    if not has_required or has_forbidden:
        return {
            "type": "sleeve_mismatch",
            "reason": f"套管类型不匹配: 描述是「{sleeve_type}」，定额是「{quota_name}」",
            "sleeve_type": sleeve_type,
        }
    return None


def check_electric_pair(item, quota_name, desc_lines):
    """规则10：电气配对检测（开关单/双控、插座单/三相、喷头有/无吊顶等）

    检测逻辑：清单描述含某关键词时，定额名称必须含对应词、不能含冲突词。
    例如：清单写"双控"，定额不应是"单控"。
    规则数据在 review_rules.json 的 electric_pair_rules 中。
    """
    if not ELECTRIC_PAIR_RULES:
        return None

    bill_name = item.get("name", "")
    full_text = bill_name + " " + " ".join(desc_lines)

    # 按关键词长度降序匹配（"三相插座"优先于"插座"）
    sorted_keys = sorted(ELECTRIC_PAIR_RULES.keys(), key=len, reverse=True)

    for keyword in sorted_keys:
        if keyword.startswith("_"):
            continue
        if keyword not in full_text:
            continue

        rules = ELECTRIC_PAIR_RULES[keyword]
        scope = rules.get("scope", "")

        # scope检查：规则限定的设备范围（如"开关"），清单文本需包含范围词
        # 支持"开关|插座"格式表示多个范围（满足任一即可）
        if scope:
            scope_words = scope.split("|")
            if not any(sw in full_text for sw in scope_words):
                continue

        should_contain = rules.get("should_contain", [])
        should_not_contain = rules.get("should_not_contain", [])

        has_required = any(kw in quota_name for kw in should_contain)
        has_forbidden = any(kw in quota_name for kw in should_not_contain)

        if not has_required or has_forbidden:
            forbidden_word = ""
            for kw in should_not_contain:
                if kw in quota_name:
                    forbidden_word = kw
                    break
            reason = f"配对不匹配: 清单含「{keyword}」"
            if has_forbidden:
                reason += f"，定额含冲突词「{forbidden_word}」"
            elif not has_required:
                reason += f"，定额应含「{'|'.join(should_contain)}」"
            return {
                "type": "electric_pair_mismatch",
                "reason": reason,
                "keyword": keyword,
                "scope": scope,
            }

    return None
