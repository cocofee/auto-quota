# -*- coding: utf-8 -*-
"""
匹配处理流水线 — 从 match_engine.py 拆分出的中间层函数

包含：
1. 前置构建（清单上下文、专业分类、规则预匹配）
2. 结果构建（备选定额、跳过结果、空结果）
3. Search模式结果处理（搜索+经验交叉验证）
4. 兜底策略（规则/经验备选替换）
5. 统一前置处理（_prepare_item_for_matching）

依赖 match_core 的工具函数和核心搜索，不依赖 match_engine。
"""

import re

from loguru import logger

from src.compat_primitives import connections_compatible
from src.ambiguity_gate import analyze_ambiguity
from src.candidate_arbiter import arbitrate_candidates
from src.context_builder import summarize_batch_context_for_trace
from src.text_parser import parser as text_parser, normalize_bill_text
from src.query_router import build_query_route_profile
from src.reason_taxonomy import apply_reason_metadata, merge_reason_tags
from src.specialty_classifier import classify as classify_specialty
from src.rule_validator import RuleValidator
from src.match_core import (
    calculate_confidence,
    _append_trace_step,
    _normalize_classification,
    _is_measure_item,
    try_experience_match,
    _safe_json_materials,
    _summarize_candidates_for_trace,
    summarize_candidate_reasoning,
)
from src.policy_engine import PolicyEngine
from src.review_checkers import (
    check_category_mismatch,
    check_material_mismatch,
    check_connection_mismatch,
    check_pipe_usage,
    check_parameter_deviation,
    check_sleeve_mismatch,
    check_electric_pair,
    check_elevator_type,
    check_elevator_floor,
    extract_description_lines,
)


# ============================================================
# 审核规则检查（防止经验库错误数据被无限复制）
# ============================================================

def _review_check_match_result(result: dict, item: dict) -> dict | None:
    """
    用审核规则检查匹配结果，拦截明显错误。

    经验库直通的结果以前跳过所有审核规则，一旦有错误数据进入权威层，
    就会被无限复制。这个函数在直通前加一道"安检"，发现问题就拒绝直通。

    参数:
        result: 匹配结果字典（含 quotas 列表）
        item: 清单项目字典

    返回:
        审核错误字典（如果有错误），None 表示通过
    """
    quotas = result.get("quotas", [])
    if not quotas:
        return None

    main_quota = quotas[0]
    quota_name = main_quota.get("name", "")
    quota_id = main_quota.get("quota_id", "")

    if not quota_name:
        return None

    desc = item.get("description", "") or ""
    desc_lines = extract_description_lines(desc)

    # 运行所有审核检查器，收集全部错误（不再短路）
    checkers = [
        check_category_mismatch(item, quota_name, desc_lines),
        check_sleeve_mismatch(item, quota_name, desc_lines),
        check_material_mismatch(item, quota_name, desc_lines),
        check_connection_mismatch(item, quota_name, desc_lines),
        check_pipe_usage(item, quota_name, desc_lines),
        check_parameter_deviation(item, quota_name, desc_lines),
        check_electric_pair(item, quota_name, desc_lines),
        check_elevator_type(item, quota_name, desc_lines),
        check_elevator_floor(item, quota_name, desc_lines, quota_id=quota_id),
    ]
    errors = [e for e in checkers if e is not None]

    if not errors:
        return None

    # 返回第一个错误作为主错误（保持向后兼容），附带全部错误列表
    error = errors[0].copy()  # 用copy避免循环引用（error本身在errors列表里）
    if len(errors) > 1:
        error["all_errors"] = errors  # 纠正步骤可以读取全部错误

    return error


# 品类子类型互斥词表：清单含左侧关键词时，定额名也必须含该词
# 否则说明规则匹配到了错误的子类型（如"刚性防水套管"匹配到"成品防火套管"）
_SUBTYPE_KEYWORDS = [
    # 套管类：刚性防水/柔性防水/成品防火/人防/密闭 是不同定额家族
    "刚性防水", "柔性防水", "成品防火", "人防",
    # 阀门类：不同安装方式是不同定额
    "密闭阀门",
]

# 反向排斥词表：定额名含这些词但清单不含时，丢弃规则匹配
# 避免规则匹配到不相关的特殊定额（如"杆上配电设备"用于室内配电箱）
_QUOTA_ONLY_KEYWORDS = [
    "杆上",     # "杆上配电设备安装"是室外电杆设备，不用于室内配电箱
]


def _check_rule_subtype_conflict(rule_result: dict, bill_text: str) -> dict:
    """检查规则匹配结果的品类子类型是否与清单一致。

    如果清单明确写了子类型（如"刚性防水"），但匹配到的定额名
    不含该子类型，说明规则匹配搞混了不同子类型，丢弃结果。
    """
    if not rule_result:
        return rule_result
    quotas = rule_result.get("quotas", [])
    if not quotas:
        return rule_result

    quota_name = quotas[0].get("name", "")
    for kw in _SUBTYPE_KEYWORDS:
        if kw in bill_text and kw not in quota_name:
            logger.debug(
                f"规则匹配被品类子类型拦截: 清单含'{kw}'但定额'{quota_name[:30]}'不含")
            return None
    # 反向检查：定额名含特定词但清单不含时拒绝
    for kw in _QUOTA_ONLY_KEYWORDS:
        if kw in quota_name and kw not in bill_text:
            logger.debug(
                f"规则匹配被反向排斥拦截: 定额'{quota_name[:30]}'含'{kw}'但清单不含")
            return None
    return rule_result



def _pick_category_safe_candidate(item: dict, candidates: list[dict]) -> dict:
    """在候选列表中优先选类别匹配的（规则审核前置）

    遍历候选，跳过类别明显不匹配的（如清单是阀门但定额是管道）。
    如果所有候选都不通过类别检查，回退到第一个（保持原有行为）。
    只检查前5个候选，避免性能问题。
    """
    if not candidates:
        return {}
    if len(candidates) <= 1:
        return candidates[0]

    desc = item.get("description", "") or ""
    bill_name = item.get("name", "") or ""
    bill_text = f"{bill_name} {desc}"
    desc_lines = extract_description_lines(desc)

    cable_candidate = _pick_explicit_cable_family_candidate(bill_text, candidates)
    if cable_candidate is not None:
        return cable_candidate

    wiring_candidate = _pick_explicit_wiring_family_candidate(bill_text, candidates)
    if wiring_candidate is not None:
        return wiring_candidate

    sleeve_candidate = _pick_explicit_plastic_sleeve_candidate(bill_text, candidates)
    if sleeve_candidate is not None:
        return sleeve_candidate

    conduit_candidate = _pick_explicit_conduit_family_candidate(bill_text, candidates)
    if conduit_candidate is not None:
        return conduit_candidate

    bridge_candidate = _pick_explicit_bridge_family_candidate(bill_text, candidates)
    if bridge_candidate is not None:
        return bridge_candidate

    distribution_box_candidate = _pick_explicit_distribution_box_candidate(bill_text, candidates)
    if distribution_box_candidate is not None:
        return distribution_box_candidate

    ventilation_candidate = _pick_explicit_ventilation_family_candidate(bill_text, candidates)
    if ventilation_candidate is not None:
        return ventilation_candidate

    support_candidate = _pick_explicit_support_family_candidate(bill_text, candidates)
    if support_candidate is not None:
        return support_candidate

    motor_candidate = _pick_explicit_motor_family_candidate(bill_text, candidates)
    if motor_candidate is not None:
        return motor_candidate

    sanitary_candidate = _pick_explicit_sanitary_family_candidate(bill_text, candidates)
    if sanitary_candidate is not None:
        return sanitary_candidate

    button_broadcast_candidate = _pick_explicit_button_broadcast_candidate(bill_text, candidates)
    if button_broadcast_candidate is not None:
        return button_broadcast_candidate

    plumbing_accessory_candidate = _pick_explicit_plumbing_accessory_candidate(bill_text, candidates)
    if plumbing_accessory_candidate is not None:
        return plumbing_accessory_candidate

    valve_candidate = _pick_explicit_valve_family_candidate(bill_text, candidates)
    if valve_candidate is not None:
        return valve_candidate

    fire_candidate = _pick_explicit_fire_device_candidate(bill_text, candidates)
    if fire_candidate is not None:
        return fire_candidate

    network_candidate = _pick_explicit_network_device_candidate(bill_text, candidates)
    if network_candidate is not None:
        return network_candidate

    for cand in candidates[:5]:
        quota_name = cand.get("name", "")
        # 反向排斥：定额含特定场景词但清单不含时跳过
        skip = False
        for kw in _QUOTA_ONLY_KEYWORDS:
            if kw in quota_name and kw not in bill_text:
                skip = True
                break
        if skip:
            continue
        error = check_category_mismatch(item, quota_name, desc_lines)
        if not error:
            return cand

    # 全部不通过，回退到第一个
    return candidates[0]


def _pick_explicit_cable_family_candidate(bill_text: str,
                                          candidates: list[dict]) -> dict | None:
    """对明确电缆样本，优先按家族与芯数/截面/终端头类型重选候选。"""
    text = bill_text or ""
    upper_text = text.upper()
    if "电缆" not in text:
        return None

    bill_params = text_parser.parse(text)
    bill_cores = bill_params.get("cable_cores")
    bill_section = bill_params.get("cable_section")

    is_head = any(keyword in text for keyword in ("终端头", "电缆头", "中间头"))
    is_middle_head = "中间头" in text
    is_control = "控制" in text or any(keyword in upper_text for keyword in ("KVV", "KVVP", "KVVR", "RVVSP", "RVSP"))
    is_power = not is_control

    expected_words: list[str] = []
    forbidden_words: list[str] = []
    core_words: list[str] = []

    if is_control:
        expected_words.append("控制电缆")
        forbidden_words.append("电力电缆")
    elif is_power:
        expected_words.append("电力电缆")
        forbidden_words.append("控制电缆")

    if is_head:
        if is_middle_head:
            expected_words.append("中间头")
            forbidden_words.extend(["终端头", "电缆头"])
        else:
            expected_words.extend(["终端头", "电缆头"])
            forbidden_words.append("中间头")
    else:
        forbidden_words.extend(["终端头", "电缆头", "中间头"])

    if "单芯" in text:
        core_words.append("单芯")
    if "四芯" in text or re.search(r"4\s*[×xX*]", text):
        core_words.append("四芯")
    if "五芯" in text or re.search(r"5\s*[×xX*]", text):
        core_words.append("五芯")

    core_count_match = re.search(r"(\d+)\s*[×xX*]\s*\d+(?:\.\d+)?", text)
    if core_count_match:
        core_count = int(core_count_match.group(1))
        if is_control or is_head:
            core_words.extend([f"<={core_count}", f"{core_count}芯", f"{core_count}"])

    scored: list[tuple[tuple[int, float, float], dict]] = []
    for cand in candidates:
        quota_name = cand.get("name", "") or ""
        cand_params = text_parser.parse(quota_name)
        score = 0
        score += sum(8 for word in expected_words if word and word in quota_name)
        score -= sum(8 for word in forbidden_words if word and word in quota_name)
        score += sum(4 for word in core_words if word and word in quota_name)
        cand_cores = cand_params.get("cable_cores")
        if bill_cores is not None and cand_cores is not None:
            if bill_cores == cand_cores:
                score += 8
            elif bill_cores < cand_cores:
                score += 4
            else:
                score -= 10
        cand_section = cand_params.get("cable_section")
        if bill_section is not None and cand_section is not None:
            if bill_section == cand_section:
                score += 6
            elif bill_section < cand_section:
                score += 3
            else:
                score -= 8
        if score <= 0:
            continue
        scored.append((
            (
                score,
                float(cand.get("param_score", 0.0)),
                float(cand.get("rerank_score", cand.get("hybrid_score", 0.0))),
            ),
            cand,
        ))

    if not scored:
        return None

    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def _pick_explicit_wiring_family_candidate(bill_text: str,
                                           candidates: list[dict]) -> dict | None:
    text = bill_text or ""
    if not any(keyword in text for keyword in ("配线", "电气配线", "线槽配线", "管内穿线", "穿线")):
        return None
    if "电缆" in text:
        return None

    bill_params = text_parser.parse(text)
    bill_cores = bill_params.get("cable_cores")
    bill_section = bill_params.get("cable_section")
    upper_text = text.upper()

    prefer_words: list[str] = []
    forbidden_words: list[str] = []

    if any(keyword in text for keyword in ("线槽配线", "线槽", "槽内")):
        prefer_words.extend(["线槽配线", "线槽"])
        forbidden_words.extend(["管内穿", "桥架内布放"])
    elif any(keyword in text for keyword in ("管内", "穿管", "穿线管")):
        prefer_words.extend(["管内穿", "穿线"])
        forbidden_words.extend(["线槽配线", "桥架内布放"])
    else:
        prefer_words.append("配线")

    if any(keyword in upper_text for keyword in ("RY", "RYS")):
        prefer_words.extend(["软导线", "多芯软导线"])
    elif any(keyword in upper_text for keyword in ("BYJ", "BV")):
        prefer_words.extend(["导线", "铜芯"])

    if bill_cores == 1:
        prefer_words.append("单芯")
        forbidden_words.extend(["二芯", "三芯", "多芯"])
    elif bill_cores is not None and bill_cores > 1:
        prefer_words.extend(["多芯", f"{int(bill_cores)}芯"])
        forbidden_words.append("单芯")
        if bill_cores <= 2:
            prefer_words.append("二芯")

    scored: list[tuple[tuple[int, float, float], dict]] = []
    for cand in candidates:
        quota_name = cand.get("name", "") or ""
        cand_params = text_parser.parse(quota_name)
        score = sum(8 for word in prefer_words if word and word in quota_name)
        score -= sum(8 for word in forbidden_words if word and word in quota_name)
        if bill_section is not None:
            cand_section = cand_params.get("cable_section")
            if cand_section is not None:
                if cand_section == bill_section:
                    score += 6
                elif cand_section > bill_section:
                    score += 3
                else:
                    score -= 8
        if score <= 0:
            continue
        scored.append((
            (
                score,
                float(cand.get("param_score", 0.0)),
                float(cand.get("rerank_score", cand.get("hybrid_score", 0.0))),
            ),
            cand,
        ))

    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def _pick_explicit_plastic_sleeve_candidate(bill_text: str,
                                            candidates: list[dict]) -> dict | None:
    """对明确的 PVC/塑料套管样本，优先选择塑料套管家族。"""
    text = bill_text or ""
    if "套管" not in text or not any(keyword in text for keyword in ("PVC", "塑料", "管套")):
        return None

    scored: list[tuple[tuple[int, float, float], dict]] = []
    for cand in candidates:
        quota_name = cand.get("name", "") or ""
        score = 0
        if "塑料套管" in quota_name:
            score += 10
        if "钢套管" in quota_name:
            score -= 8
        if "制作安装" in quota_name:
            score += 2
        if score <= 0:
            continue
        scored.append((
            (
                score,
                float(cand.get("param_score", 0.0)),
                float(cand.get("rerank_score", cand.get("hybrid_score", 0.0))),
            ),
            cand,
        ))

    if not scored:
        return None

    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def _pick_explicit_conduit_family_candidate(bill_text: str,
                                            candidates: list[dict]) -> dict | None:
    """对“明确电气配管语义”的清单，优先在前几名里选对家族。

    只处理非常明确的场景：`电气配管 SC20`、`JDG穿线管`、`电气配管 PC25`、
    `金属软管`、`可挠金属套管` 等，避免把给排水的 `SC32` 全局误判成电气配管。
    """
    if not candidates:
        return None

    text = bill_text or ""
    upper_text = text.upper()
    code_match = re.search(r'(?<![A-Z0-9])(JDG|KBG|FPC|PVC|PC|SC|RC|MT|DG|G)\s*\d+\b', upper_text)
    explicit_electrical = any(keyword in text for keyword in (
        "电气配管", "穿线管", "导管", "金属软管", "可挠金属套管",
    ))
    if not explicit_electrical and not (code_match and "配管" in text):
        return None

    expected_words: list[str] = []
    forbidden_words: list[str] = []
    layout_words: list[str] = []
    size_tokens: list[str] = []

    if "暗配" in text:
        layout_words.append("暗配")
    if "明配" in text:
        layout_words.append("明配")

    if "金属软管" in text:
        expected_words = ["金属软管"]
    elif "可挠" in text:
        expected_words = ["可挠金属套管"]
    else:
        conduit_code = code_match.group(1) if code_match else ""
        if conduit_code in {"JDG", "KBG"}:
            expected_words = ["JDG", "紧定式", "钢导管"]
            forbidden_words = ["防爆钢管", "电缆保护"]
        elif conduit_code in {"PC", "PVC"}:
            expected_words = ["刚性阻燃管", "PVC阻燃塑料管"]
            forbidden_words = ["电缆保护", "防爆钢管"]
        elif conduit_code == "FPC":
            expected_words = ["半硬质阻燃管", "半硬质塑料管"]
            forbidden_words = ["电缆保护", "防爆钢管"]
        elif conduit_code in {"SC", "G", "DG", "RC", "MT"}:
            expected_words = ["镀锌钢管", "镀锌电线管", "钢管敷设"]
            forbidden_words = ["防爆钢管", "电缆保护"]

    size_match = re.search(r'(?<![A-Z0-9])(?:JDG|KBG|FPC|PVC|PC|SC|RC|MT|DG|G|DN|Φ|φ)\s*(\d+)\b', upper_text)
    if size_match:
        size = size_match.group(1)
        size_tokens = [f"{size}", f"≤{size}"]

    scored: list[tuple[tuple[int, float, float], dict]] = []
    for cand in candidates:
        quota_name = cand.get("name", "") or ""
        family_hits = sum(1 for word in expected_words if word and word in quota_name)
        family_penalty = sum(1 for word in forbidden_words if word and word in quota_name)
        layout_hits = sum(1 for word in layout_words if word and word in quota_name)
        size_hits = sum(1 for token in size_tokens if token and token in quota_name)
        score = family_hits * 10 + layout_hits * 4 + size_hits * 2 - family_penalty * 8
        if score <= 0:
            continue
        scored.append((
            (
                score,
                float(cand.get("param_score", 0.0)),
                float(cand.get("rerank_score", cand.get("hybrid_score", 0.0))),
            ),
            cand,
        ))

    if not scored:
        return None

    scored.sort(key=lambda item: item[0], reverse=True)
    best = scored[0][1]
    logger.debug(
        "显式电气配管候选重选: bill={} -> quota={}",
        bill_text[:80],
        best.get("name", "")[:80],
    )
    return best


def _pick_explicit_bridge_family_candidate(bill_text: str,
                                           candidates: list[dict]) -> dict | None:
    text = bill_text or ""
    if not any(keyword in text for keyword in ("桥架", "线槽", "母线槽")):
        return None
    if any(keyword in text for keyword in ("电缆", "双绞线", "网线", "光缆", "配线", "布放", "穿线", "导线")):
        return None

    bill_params = text_parser.parse(text)
    bill_half_perimeter = bill_params.get("half_perimeter")
    prefer_bridge = "桥架" in text
    prefer_trunking = "线槽" in text and "桥架" not in text
    prefer_busway = "母线槽" in text
    prefer_slot = "槽式" in text
    prefer_tray = "托盘式" in text
    prefer_ladder = "梯式" in text

    scored: list[tuple[tuple[int, float, float], dict]] = []
    for cand in candidates:
        quota_name = cand.get("name", "") or ""
        cand_params = text_parser.parse(quota_name)
        score = 0
        if prefer_busway:
            if "母线槽" in quota_name:
                score += 14
            if any(word in quota_name for word in ("桥架", "线槽")):
                score -= 10
        elif prefer_bridge:
            if "桥架" in quota_name:
                score += 12
            if "支撑架" in quota_name:
                score -= 10
            if "线槽配线" in quota_name or "桥架内布放" in quota_name:
                score -= 12
            if "线槽" in quota_name and "桥架" not in quota_name:
                score -= 6
        elif prefer_trunking:
            if "线槽" in quota_name:
                score += 12
            if "桥架" in quota_name:
                score -= 8
            if "线槽配线" in quota_name:
                score -= 10
        if prefer_slot:
            if "槽式" in quota_name:
                score += 8
            if any(word in quota_name for word in ("托盘式", "梯式")):
                score -= 8
        if prefer_tray:
            if "托盘式" in quota_name:
                score += 8
            if any(word in quota_name for word in ("槽式", "梯式")):
                score -= 8
        if prefer_ladder:
            if "梯式" in quota_name:
                score += 8
            if any(word in quota_name for word in ("槽式", "托盘式")):
                score -= 8
        if bill_half_perimeter is not None:
            cand_half_perimeter = cand_params.get("half_perimeter")
            if cand_half_perimeter is not None:
                if cand_half_perimeter == bill_half_perimeter:
                    score += 6
                elif cand_half_perimeter > bill_half_perimeter:
                    score += 3
                else:
                    score -= 8
        if score <= 0:
            continue
        scored.append((
            (
                score,
                float(cand.get("param_score", 0.0)),
                float(cand.get("rerank_score", cand.get("hybrid_score", 0.0))),
            ),
            cand,
        ))

    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def _pick_explicit_distribution_box_candidate(bill_text: str,
                                              candidates: list[dict]) -> dict | None:
    text = bill_text or ""
    if not any(keyword in text for keyword in ("配电箱", "配电柜", "控制箱", "控制柜")):
        return None

    bill_params = text_parser.parse(text)
    install_method = str(bill_params.get("install_method") or "")
    upper_text = text.upper()
    prefer_floor = any(keyword in text for keyword in ("落地", "柜基础", "基础槽钢"))
    prefer_wall = any(keyword in text for keyword in ("悬挂", "嵌入", "明装", "暗装", "挂墙", "壁挂", "墙上", "柱上", "距地"))
    if install_method == "落地":
        prefer_floor = True
        prefer_wall = False
    elif install_method in {"挂墙", "嵌入"} or "明装" in install_method or "暗装" in install_method or "悬挂" in install_method:
        prefer_wall = True
    if re.search(r"\b\d*AP\d+\b", upper_text) and not prefer_wall:
        prefer_floor = True
    if not prefer_floor and not prefer_wall:
        if any(keyword in text for keyword in ("配电柜", "控制柜")):
            prefer_floor = True
        elif any(keyword in text for keyword in ("配电箱", "控制箱", "动力箱", "照明箱")):
            prefer_wall = True
    if not prefer_floor and not prefer_wall:
        return None

    scored: list[tuple[tuple[int, float, float], dict]] = []
    for cand in candidates:
        quota_name = cand.get("name", "") or ""
        score = 0
        if prefer_floor:
            if "落地" in quota_name:
                score += 12
            if any(word in quota_name for word in ("悬挂", "嵌入", "墙上", "柱上", "挂墙")):
                score -= 10
        if prefer_wall:
            if any(word in quota_name for word in ("悬挂", "嵌入", "墙上", "柱上", "挂墙")):
                score += 12
            if "落地" in quota_name:
                score -= 10
        if score <= 0:
            continue
        scored.append((
            (
                score,
                float(cand.get("param_score", 0.0)),
                float(cand.get("rerank_score", cand.get("hybrid_score", 0.0))),
            ),
            cand,
        ))

    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def _pick_explicit_ventilation_family_candidate(bill_text: str,
                                                candidates: list[dict]) -> dict | None:
    text = bill_text or ""
    if not any(keyword in text for keyword in (
        "风口", "散流器", "百叶", "风机", "排气扇", "换气扇", "软风管", "消声器",
        "止回阀", "调节阀", "防火阀", "排烟阀", "定风量阀", "插板阀",
    )):
        return None

    bill_params = text_parser.parse(text)
    bill_perimeter = bill_params.get("perimeter")
    prefer_words: list[str] = []
    forbidden_words: list[str] = []

    if "柔性软风管" in text or "软风管" in text:
        prefer_words.extend(["柔性接口", "伸缩节", "软风管"])
        forbidden_words.extend(["阀门安装"])
    elif any(keyword in text for keyword in ("风管止回阀", "止回阀")):
        prefer_words.extend(["风管止回阀", "止回阀"])
        forbidden_words.extend(["阀门安装", "柔性软风管", "水流指示器"])
    elif any(keyword in text for keyword in ("排烟防火阀", "防火阀")):
        prefer_words.extend(["防火阀"])
        forbidden_words.extend(["阀门安装", "柔性软风管"])
        if "排烟" in text or "280" in text:
            prefer_words.append("排烟")
    elif any(keyword in text for keyword in ("手动调节阀", "电动调节阀", "风量调节阀", "多叶调节阀", "调节阀")):
        prefer_words.extend(["调节阀"])
        forbidden_words.extend(["阀门安装", "柔性软风管"])
        if any(keyword in text for keyword in ("多叶", "对开多叶")):
            prefer_words.append("多叶")
        if "电动" in text:
            prefer_words.append("电动")
        if "手动" in text:
            prefer_words.append("手动")
    elif any(keyword in text for keyword in ("定风量阀", "风量阀")):
        prefer_words.extend(["定风量阀", "风量阀"])
        forbidden_words.extend(["阀门安装"])
    elif "插板阀" in text:
        prefer_words.extend(["插板阀"])
        forbidden_words.extend(["阀门安装"])
    elif "百叶" in text and any(keyword in text for keyword in ("风口", "散流器", "百叶窗")):
        prefer_words.extend(["百叶风口"])
        forbidden_words.extend(["钢百叶窗"])
    elif any(keyword in text for keyword in ("天花板", "天花式", "管道式换气扇")):
        prefer_words.extend(["天花式排气扇", "排气扇"])
        forbidden_words.extend(["壁扇"])
    elif any(keyword in text for keyword in ("壁式排风机", "壁式")):
        prefer_words.extend(["排气扇"])
        forbidden_words.extend(["壁扇"])
    elif "消声器" in text:
        prefer_words.extend(["消声器"])
    else:
        return None

    scored: list[tuple[tuple[int, float, float], dict]] = []
    for cand in candidates:
        quota_name = cand.get("name", "") or ""
        cand_params = text_parser.parse(quota_name)
        score = sum(8 for word in prefer_words if word and word in quota_name)
        score -= sum(8 for word in forbidden_words if word and word in quota_name)
        if "风口" in text and "风口" in quota_name:
            score += 4
        if "风机" in text and "风机" in quota_name:
            score += 4
        if "阀" in text and "阀" in quota_name:
            score += 4
        if bill_perimeter is not None:
            cand_perimeter = cand_params.get("perimeter")
            if cand_perimeter is not None:
                if cand_perimeter == bill_perimeter:
                    score += 6
                elif cand_perimeter > bill_perimeter:
                    score += 3
                else:
                    score -= 8
        if score <= 0:
            continue
        scored.append((
            (
                score,
                float(cand.get("param_score", 0.0)),
                float(cand.get("rerank_score", cand.get("hybrid_score", 0.0))),
            ),
            cand,
        ))

    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def _pick_explicit_support_family_candidate(bill_text: str,
                                            candidates: list[dict]) -> dict | None:
    text = bill_text or ""
    if not any(keyword in text for keyword in ("支架", "支/吊架", "支吊架")):
        return None

    prefer_bridge = any(keyword in text for keyword in ("桥架", "电缆桥架", "桥架支撑架", "桥架侧纵向", "抗震支吊架"))
    prefer_pipe = any(keyword in text for keyword in ("管道", "管架", "管道支架"))
    prefer_fabrication = any(keyword in text for keyword in ("图集", "详见图集", "制作", "单件重量", "型钢"))
    if not prefer_bridge and not prefer_pipe:
        return None
    scored: list[tuple[tuple[int, float, float], dict]] = []
    for cand in candidates:
        quota_name = cand.get("name", "") or ""
        score = 0
        if prefer_bridge:
            if any(word in quota_name for word in ("桥架支撑架", "电缆桥架")):
                score += 12
            if any(word in quota_name for word in ("支架制作", "支架安装")) and "桥架" in quota_name:
                score += 6
            if any(word in quota_name for word in ("管架", "管道支架")):
                score -= 10
        elif prefer_pipe:
            if any(word in quota_name for word in ("管架", "管道支架")):
                score += 8
            if "桥架支撑架" in quota_name:
                score -= 6
        if prefer_fabrication:
            if "制作" in quota_name:
                score += 10
            if any(word in quota_name for word in ("单件重量", "kg", "重量")):
                score += 6
            if any(word in quota_name for word in ("安装", "一般管架")):
                score -= 8
        if score <= 0:
            continue
        scored.append((
            (
                score,
                float(cand.get("param_score", 0.0)),
                float(cand.get("rerank_score", cand.get("hybrid_score", 0.0))),
            ),
            cand,
        ))

    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def _pick_explicit_motor_family_candidate(bill_text: str,
                                          candidates: list[dict]) -> dict | None:
    text = bill_text or ""
    if "电动机" not in text:
        return None

    bill_params = text_parser.parse(text)
    bill_kw = bill_params.get("kw")
    prefer_check = "检查接线" in text
    prefer_load = "负载调试" in text
    if not prefer_check and not prefer_load:
        return None

    scored: list[tuple[tuple[int, float, float], dict]] = []
    for cand in candidates:
        quota_name = cand.get("name", "") or ""
        cand_params = text_parser.parse(quota_name)
        score = 0
        if prefer_check:
            if "检查接线" in quota_name:
                score += 12
            if "负载调试" in quota_name:
                score -= 10
        if prefer_load:
            if "负载调试" in quota_name:
                score += 12
            if "检查接线" in quota_name:
                score -= 8
        cand_kw = cand_params.get("kw")
        if bill_kw is not None and cand_kw is not None:
            if bill_kw == cand_kw:
                score += 6
            elif bill_kw < cand_kw:
                score += 3
            else:
                score -= 8
        if score <= 0:
            continue
        scored.append((
            (
                score,
                float(cand.get("param_score", 0.0)),
                float(cand.get("rerank_score", cand.get("hybrid_score", 0.0))),
            ),
            cand,
        ))

    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def _pick_explicit_sanitary_family_candidate(bill_text: str,
                                             candidates: list[dict]) -> dict | None:
    text = bill_text or ""
    if not any(keyword in text for keyword in ("便器", "水龙头", "洗脸盆", "洗涤盆", "水槽", "小便器")):
        return None

    bill_params = text_parser.parse(text)
    sanitary_subtype = str(bill_params.get("sanitary_subtype") or "")
    expected_words: list[str] = []
    forbidden_words: list[str] = []
    prefer_words: list[str] = []
    if sanitary_subtype == "坐便器":
        expected_words.extend(["坐式大便器", "坐便器"])
        forbidden_words.extend(["蹲式大便器", "小便器", "洗脸盆", "洗涤盆", "水龙头"])
    elif sanitary_subtype == "蹲便器":
        expected_words.extend(["蹲式大便器", "蹲便器"])
        forbidden_words.extend(["坐式大便器", "小便器", "洗脸盆", "洗涤盆", "水龙头"])
    elif sanitary_subtype == "小便器":
        expected_words.append("小便器")
        forbidden_words.extend(["大便器", "洗脸盆", "洗涤盆", "水龙头"])
    elif sanitary_subtype == "洗脸盆":
        expected_words.append("洗脸盆")
        forbidden_words.extend(["洗涤盆", "水龙头", "大便器", "小便器"])
    elif sanitary_subtype == "洗涤盆":
        expected_words.append("洗涤盆")
        forbidden_words.extend(["洗脸盆", "水龙头", "大便器", "小便器"])
    if any(keyword in text for keyword in ("水龙头", "龙头")):
        expected_words.append("水龙头")
        forbidden_words.extend(["控制器", "探测器", "入侵"])
        if "感应" in text:
            prefer_words.append("感应")
        if "脚踏" in text:
            prefer_words.append("脚踏")
    if "感应" in text:
        expected_words.extend(["感应开关", "感应"])
        forbidden_words.append("脚踏开关")
    if "脚踏" in text:
        expected_words.append("脚踏开关")
        forbidden_words.append("感应开关")
    if "连体水箱" in text:
        expected_words.append("连体水箱")
        forbidden_words.append("隐蔽水箱")
    if "隐蔽水箱" in text:
        expected_words.append("隐蔽水箱")
        forbidden_words.append("连体水箱")
    if "挂墙" in text:
        expected_words.append("挂墙式")
    if "立式" in text:
        expected_words.append("立式")
    if "壁挂" in text:
        expected_words.append("壁挂式")
    if "埋入" in text:
        prefer_words.append("埋入式")

    if not expected_words and not forbidden_words:
        return None

    scored: list[tuple[tuple[int, float, float], dict]] = []
    for cand in candidates:
        quota_name = cand.get("name", "") or ""
        score = sum(8 for word in expected_words if word and word in quota_name)
        score -= sum(8 for word in forbidden_words if word and word in quota_name)
        score += sum(3 for word in prefer_words if word and word in quota_name)
        if score <= 0:
            continue
        scored.append((
            (
                score,
                float(cand.get("param_score", 0.0)),
                float(cand.get("rerank_score", cand.get("hybrid_score", 0.0))),
            ),
            cand,
        ))

    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def _pick_explicit_button_broadcast_candidate(bill_text: str,
                                              candidates: list[dict]) -> dict | None:
    text = bill_text or ""
    if not any(keyword in text for keyword in ("扬声器", "按钮")):
        return None

    bill_params = text_parser.parse(text)
    install_method = str(bill_params.get("install_method") or "")
    scored: list[tuple[tuple[int, float, float], dict]] = []

    for cand in candidates:
        quota_name = cand.get("name", "") or ""
        score = 0

        if "扬声器" in text:
            if "扬声器" in quota_name:
                score += 10
            if install_method == "挂墙":
                if any(word in quota_name for word in ("壁挂", "挂墙", "壁装")):
                    score += 10
                if "吸顶" in quota_name:
                    score -= 10
            elif install_method == "吸顶":
                if "吸顶" in quota_name:
                    score += 10
                if any(word in quota_name for word in ("壁挂", "挂墙", "壁装")):
                    score -= 10

        if "按钮" in text:
            if "紧急呼叫" in text and all(word not in text for word in ("消防", "报警", "消火栓")):
                if "按钮" in quota_name:
                    score += 8
                if any(word in quota_name for word in ("报警按钮", "消火栓")):
                    score -= 10
            elif any(word in text for word in ("手动报警", "报警按钮", "消火栓")):
                if any(word in quota_name for word in ("报警按钮", "消火栓")):
                    score += 10
                if "普通开关、按钮安装 按钮" in quota_name:
                    score -= 8

        if score <= 0:
            continue
        scored.append((
            (
                score,
                float(cand.get("param_score", 0.0)),
                float(cand.get("rerank_score", cand.get("hybrid_score", 0.0))),
            ),
            cand,
        ))

    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def _pick_explicit_plumbing_accessory_candidate(bill_text: str,
                                                candidates: list[dict]) -> dict | None:
    text = bill_text or ""
    bill_params = text_parser.parse(text)
    bill_dn = bill_params.get("dn")

    expected_words: list[str] = []
    forbidden_words: list[str] = []
    prefer_words: list[str] = []

    if any(keyword in text for keyword in ("地漏", "洗衣机地漏", "侧排地漏")):
        expected_words.extend(["地漏"])
        forbidden_words.extend(["排水栓", "伸缩器"])
        if "方形" in text:
            prefer_words.append("方形")
        if "侧排" in text:
            prefer_words.append("侧排")
    elif any(keyword in text for keyword in ("雨水斗", "87型雨水斗", "侧入雨水斗")):
        expected_words.extend(["雨水斗"])
        forbidden_words.extend(["排水塑料管", "排水管"])
        if "87型" in text:
            prefer_words.append("87型")
        if "侧入" in text:
            prefer_words.append("侧入")
    elif "水表" in text:
        expected_words.extend(["水表"])
        forbidden_words.extend(["阀门", "伸缩器", "支架"])
    elif any(keyword in text for keyword in ("真空破坏器", "水锤消除器")):
        if "真空破坏器" in text:
            expected_words.extend(["真空破坏器"])
            forbidden_words.extend(["过滤器", "除污器"])
        if "水锤消除器" in text:
            expected_words.extend(["水锤消除器"])
            forbidden_words.extend(["过滤器", "除污器"])
    elif any(keyword in text for keyword in ("过滤器", "除污器", "Y型过滤器", "管道过滤器")):
        expected_words.extend(["过滤器", "除污器"])
        forbidden_words.extend(["水锤消除器"])
        if "Y型" in text:
            prefer_words.append("Y型")
    elif "倒流防止器" in text:
        expected_words.extend(["倒流防止器"])
        forbidden_words.extend(["阀门"])
        if "水表" in text:
            prefer_words.append("带水表")
            forbidden_words.append("不带水表")
        else:
            prefer_words.append("不带水表")
            forbidden_words.append("带水表")
    else:
        return None

    scored: list[tuple[tuple[int, float, float], dict]] = []
    for cand in candidates:
        quota_name = cand.get("name", "") or ""
        score = sum(10 for word in expected_words if word and word in quota_name)
        score -= sum(8 for word in forbidden_words if word and word in quota_name)
        score += sum(3 for word in prefer_words if word and word in quota_name)
        if bill_dn is not None:
            cand_params = text_parser.parse(quota_name)
            cand_dn = cand_params.get("dn")
            if cand_dn is not None:
                if cand_dn == bill_dn:
                    score += 5
                elif cand_dn > bill_dn:
                    score += 2
                else:
                    score -= 4
        if score <= 0:
            continue
        scored.append((
            (
                score,
                float(cand.get("param_score", 0.0)),
                float(cand.get("rerank_score", cand.get("hybrid_score", 0.0))),
            ),
            cand,
        ))

    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def _pick_explicit_valve_family_candidate(bill_text: str,
                                          candidates: list[dict]) -> dict | None:
    text = bill_text or ""
    if "倒流防止器" in text:
        return None
    if not any(keyword in text for keyword in (
        "螺纹阀门", "焊接法兰阀门", "法兰阀门", "螺纹法兰阀门",
        "碳钢阀门",
    )):
        return None
    if any(keyword in text for keyword in (
        "风阀", "防火阀", "调节阀", "多叶调节阀", "定风量阀", "人防", "密闭阀",
    )):
        return None

    bill_params = text_parser.parse(text)
    bill_dn = bill_params.get("dn")
    bill_connection = bill_params.get("connection")

    prefer_words = []
    forbidden_words = ["塑料法兰"]

    if "碳钢阀门" in text:
        prefer_words.extend(["阀门", "碳钢"])
        forbidden_words.extend(["调节阀", "防火阀", "风管"])
    elif "螺纹法兰阀门" in text:
        prefer_words.extend(["法兰阀门", "阀门"])
        forbidden_words.extend(["法兰安装", "螺纹法兰安装"])
    elif "焊接法兰阀门" in text or "法兰阀门" in text:
        prefer_words.extend(["法兰阀门", "阀门"])
        forbidden_words.extend(["法兰安装", "螺纹法兰安装"])
    elif "螺纹阀门" in text:
        prefer_words.extend(["螺纹阀", "阀门"])
        forbidden_words.extend(["法兰安装", "塑料法兰"])
    else:
        return None

    for keyword in ("闸阀", "蝶阀", "截止阀", "止回阀", "球阀", "减压阀", "安全阀", "电磁阀"):
        if keyword in text:
            prefer_words.append(keyword)

    scored: list[tuple[tuple[int, float, float], dict]] = []
    for cand in candidates:
        quota_name = cand.get("name", "") or ""
        cand_params = text_parser.parse(quota_name)
        score = 0
        if "阀门" in quota_name:
            score += 8
        score += sum(6 for word in prefer_words if word and word in quota_name)
        score -= sum(10 for word in forbidden_words if word and word in quota_name)
        if bill_dn is not None:
            cand_dn = cand_params.get("dn")
            if cand_dn is not None:
                if cand_dn == bill_dn:
                    score += 5
                elif cand_dn > bill_dn:
                    score += 2
                else:
                    score -= 6
        if bill_connection:
            cand_connection = cand_params.get("connection")
            if cand_connection:
                if cand_connection == bill_connection:
                    score += 4
                elif not connections_compatible(bill_connection, cand_connection):
                    score -= 5
        if score <= 0:
            continue
        scored.append((
            (
                score,
                float(cand.get("param_score", 0.0)),
                float(cand.get("rerank_score", cand.get("hybrid_score", 0.0))),
            ),
            cand,
        ))

    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def _pick_explicit_fire_device_candidate(bill_text: str,
                                         candidates: list[dict]) -> dict | None:
    text = bill_text or ""
    if "消火栓" not in text or any(keyword in text for keyword in ("钢管", "管道", "立管", "支管")):
        return None
    if any(keyword in text for keyword in ("灭火器", "干粉")):
        return None

    bill_params = text_parser.parse(text)
    bill_dn = bill_params.get("dn")
    prefer_words: list[str] = []
    forbidden_words = ["钢管", "管道"]

    if "试验消火栓" in text:
        prefer_words.extend(["试验用消火栓", "消火栓"])
        forbidden_words.extend(["室内消火栓安装"])
    elif "室内消火栓" in text:
        prefer_words.extend(["室内消火栓", "消火栓"])
        for keyword in ("单栓", "双栓", "卷盘", "暗装", "明装"):
            if keyword in text:
                prefer_words.append(keyword)
    else:
        return None

    scored: list[tuple[tuple[int, float, float], dict]] = []
    for cand in candidates:
        quota_name = cand.get("name", "") or ""
        cand_params = text_parser.parse(quota_name)
        score = sum(8 for word in prefer_words if word and word in quota_name)
        score -= sum(10 for word in forbidden_words if word and word in quota_name)
        if bill_dn is not None:
            cand_dn = cand_params.get("dn")
            if cand_dn is not None:
                if cand_dn == bill_dn:
                    score += 5
                elif cand_dn > bill_dn:
                    score += 2
                else:
                    score -= 6
        if score <= 0:
            continue
        scored.append((
            (
                score,
                float(cand.get("param_score", 0.0)),
                float(cand.get("rerank_score", cand.get("hybrid_score", 0.0))),
            ),
            cand,
        ))

    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def _pick_explicit_network_device_candidate(bill_text: str,
                                            candidates: list[dict]) -> dict | None:
    text = bill_text or ""
    if "交换机" not in text:
        return None

    bill_params = text_parser.parse(text)
    port_count = bill_params.get("port_count")
    if port_count is None:
        port_match = re.search(r"(\d+)\s*口", text)
        if port_match:
            port_count = int(port_match.group(1))
    if port_count is None:
        return None
    prefer_small = port_count <= 24

    scored: list[tuple[tuple[int, float, float], dict]] = []
    for cand in candidates:
        quota_name = cand.get("name", "") or ""
        cand_params = text_parser.parse(quota_name)
        score = 0
        cand_port = cand_params.get("port_count")
        if cand_port is not None:
            if port_count == cand_port:
                score += 12
            elif port_count < cand_port:
                score += 7
            else:
                score -= 10
        if prefer_small:
            if any(word in quota_name for word in ("≤24口", "24口及以下", "24口以内")):
                score += 12
            if any(word in quota_name for word in (">24口", "24口以上")):
                score -= 10
        else:
            if any(word in quota_name for word in (">24口", "24口以上")):
                score += 12
            if any(word in quota_name for word in ("≤24口", "24口及以下", "24口以内")):
                score -= 10
        if score <= 0:
            continue
        scored.append((
            (
                score,
                float(cand.get("param_score", 0.0)),
                float(cand.get("rerank_score", cand.get("hybrid_score", 0.0))),
            ),
            cand,
        ))

    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


# ============================================================
# 前置构建
# ============================================================

def _extract_usage_from_section(section: str) -> str:
    """从分项标题/Sheet名中提取用途关键词（给水/采暖/消防/排水等）。
    返回空字符串表示无法判断。"""
    if not section:
        return ""
    # 优先级：消防 > 采暖 > 给水 > 排水（从具体到宽泛）
    if "消防" in section:
        return "消防"
    if "采暖" in section or "供暖" in section or "暖通" in section:
        return "采暖"
    if "给水" in section:
        return "给水"
    if "排水" in section or "雨水" in section or "污水" in section:
        return "排水"
    if "工业管道" in section:
        return "工业管道"
    return ""


_PURE_CODE_NAME_RE = re.compile(r"^[A-Za-z]{0,3}\d{5,}$")
_PURE_DIGIT_NAME_RE = re.compile(r"^\d{5,}$")
_STRONG_DESC_MARKERS = (
    "名称", "规格", "型号", "类型", "材质", "连接", "安装", "敷设", "部位",
    "系统", "参数", "附件", "做法", "管", "阀", "箱", "桥架", "电缆",
    "风机", "灯", "器", "DN", "De", "kV", "mm",
)


def _looks_like_internal_code_name(name: str) -> bool:
    clean = re.sub(r"\s+", "", str(name or ""))
    if not clean:
        return False
    return bool(_PURE_CODE_NAME_RE.fullmatch(clean) or _PURE_DIGIT_NAME_RE.fullmatch(clean))


def _has_meaningful_description(desc: str) -> bool:
    text = str(desc or "").strip()
    if not text:
        return False
    if any(marker in text for marker in _STRONG_DESC_MARKERS):
        return True
    chinese_chars = re.findall(r"[\u4e00-\u9fff]", text)
    return len(chinese_chars) >= 8


def _is_generic_short_name(name: str) -> bool:
    chinese_chars = re.findall(r"[\u4e00-\u9fff]", str(name or ""))
    return 0 < len(chinese_chars) <= 3


def _set_result_reason(result: dict,
                       primary_reason: str,
                       reason_tags: list[str] | tuple[str, ...],
                       detail: str = "") -> dict:
    """Attach unified reason metadata to result objects."""
    return apply_reason_metadata(
        result,
        primary_reason=primary_reason,
        reason_tags=reason_tags,
        detail=detail,
    )


def _build_input_gate_abstain_result(item: dict,
                                     *,
                                     primary_reason: str,
                                     detail: str,
                                     reason_tags: list[str] | tuple[str, ...]) -> dict:
    result = {
        "bill_item": item,
        "quotas": [],
        "alternatives": [],
        "confidence": 0,
        "match_source": "input_gate_abstain",
        "decision_type": "abstain",
        "explanation": detail,
        "no_match_reason": detail,
    }
    _set_result_reason(result, primary_reason, reason_tags, detail)
    _append_trace_step(
        result,
        "input_gate_abstain",
        primary_reason=primary_reason,
        reason_tags=list(reason_tags or []),
        reason_detail=detail,
    )
    return result


def _evaluate_input_gate(name: str, desc: str) -> dict:
    """Classify dirty inputs before retrieval."""
    if not _looks_like_internal_code_name(name):
        return {
            "is_dirty_code": False,
            "should_abstain": False,
            "query_name": name,
        }

    desc_is_strong = _has_meaningful_description(desc)
    if not desc_is_strong:
        return {
            "is_dirty_code": True,
            "should_abstain": True,
            "primary_reason": "dirty_input",
            "reason_tags": ["dirty_input", "numeric_code", "manual_review", "abstained"],
            "detail": "清单名称为纯数字/内部编码，且缺少足够描述，转人工审核",
            "query_name": "",
        }

    return {
        "is_dirty_code": True,
        "should_abstain": False,
        "primary_reason": "dirty_input",
        "reason_tags": ["dirty_input", "numeric_code", "description_driven"],
        "detail": "清单名称为内部编码，检索阶段忽略编码，仅使用描述特征",
        "query_name": "",
    }


def _evaluate_context_gate(name: str,
                           desc: str,
                           section: str,
                           classification: dict) -> dict:
    primary = str((classification or {}).get("primary") or "").strip()
    confidence = str((classification or {}).get("confidence") or "").strip().lower()
    fallbacks = [item for item in list((classification or {}).get("fallbacks") or []) if item]
    has_section = bool(str(section or "").strip())
    desc_is_strong = _has_meaningful_description(desc)
    is_generic_short = _is_generic_short_name(name)
    weak_classification = (not primary) or (confidence != "high" and len(fallbacks) >= 3)

    if not weak_classification:
        return {
            "should_abstain": False,
            "reason_tags": [],
            "detail": "",
        }

    base_tags = ["specialty_missing"]
    if not has_section:
        base_tags.append("context_missing")
    if not desc_is_strong:
        base_tags.append("weak_text")

    if not has_section and not desc_is_strong and is_generic_short:
        return {
            "should_abstain": True,
            "primary_reason": "context_missing",
            "reason_tags": merge_reason_tags(base_tags, ["manual_review", "abstained"]),
            "detail": "缺少专业/上下文信息，且清单名称过短、描述过弱，转人工审核",
        }

    return {
        "should_abstain": False,
        "primary_reason": "specialty_missing" if "specialty_missing" in base_tags else "",
        "reason_tags": base_tags,
        "detail": "专业或上下文信息不足，继续检索但降低自动判定可信度" if base_tags else "",
    }

def _build_item_context(item: dict) -> dict:
    """构建匹配所需的清单上下文（名称/查询文本/单位/工程量等）。"""
    name = item.get("name", "")
    desc = item.get("description", "") or ""
    section = item.get("section", "") or ""
    original_name = item.get("original_name", name)
    canonical_features = item.get("canonical_features") or {}
    context_prior = item.get("context_prior") or {}
    input_gate = _evaluate_input_gate(name, desc)
    query_name = input_gate.get("query_name", name)
    search_query = text_parser.build_quota_query(query_name, desc,
                                                  specialty=item.get("specialty", ""),
                                                  bill_params=item.get("params"),
                                                  section_title=section,
                                                  canonical_features=canonical_features,
                                                  context_prior=context_prior)
    # 线缆类型标签：追加到搜索词，帮助BM25区分电线/电缆/光缆定额
    cable_type = item.get("cable_type", "")
    if cable_type:
        search_query = f"{search_query} {cable_type}"

    full_query = f"{query_name} {desc}".strip()
    if not full_query:
        full_query = f"{name} {desc}".strip()

    # 从分项标题/Sheet名推断用途关键词，注入到full_query中
    # 这样param_validator的介质冲突检查能利用section的方向信息
    # 只在清单文本本身不含这些关键词时才注入，避免重复
    if section:
        _usage_hint = _extract_usage_from_section(section)
        if _usage_hint and _usage_hint not in full_query:
            full_query = f"{full_query} {_usage_hint}"

    canonical_name = canonical_features.get("canonical_name", "")
    canonical_system = canonical_features.get("system", "")
    if canonical_name and canonical_name not in full_query:
        full_query = f"{full_query} {canonical_name}".strip()
    if canonical_system and canonical_system not in full_query:
        full_query = f"{full_query} {canonical_system}".strip()

    query_route = build_query_route_profile(
        full_query,
        item=item,
        specialty=item.get("specialty", ""),
        canonical_features=canonical_features,
        context_prior=context_prior,
    )

    return {
        "name": name,
        "desc": desc,
        "section": section,
        "unit": item.get("unit"),
        "quantity": item.get("quantity"),
        "full_query": full_query,
        "normalized_query": normalize_bill_text(original_name, desc),
        "search_query": search_query,
        "canonical_features": canonical_features,
        "context_prior": context_prior,
        "query_route": query_route,
        "item": item,  # L5：供跨省预热读取 _cross_province_hints
        "input_gate": input_gate,
    }


def _build_classification(item: dict, name: str, desc: str, section: str,
                          province: str = None) -> dict:
    """获取并标准化专业分类结果。"""
    classification = {
        "primary": item.get("specialty"),
        "fallbacks": item.get("specialty_fallbacks", []),
    }
    if not classification["primary"]:
        classification = classify_specialty(
            name, desc, section_title=section, province=province,
            bill_code=item.get("code")
        )
    return _normalize_classification(classification)


def _prepare_rule_match(rule_validator: RuleValidator, full_query: str, item: dict,
                        search_query: str, classification: dict,
                        route_profile=None) -> tuple[dict, dict]:
    """
    规则预匹配统一入口。

    返回:
        (rule_direct, rule_backup)
        - rule_direct: 高置信直通结果
        - rule_backup: 低置信备选结果
    """
    if rule_validator is None:
        return None, None
    rule_books = [classification.get("primary")] + classification.get("fallbacks", [])
    rule_books = [b for b in rule_books if b]
    rule_result = rule_validator.match_by_rules(
        full_query, item, clean_query=search_query,
        books=rule_books if rule_books else None)
    if not rule_result:
        return None, None

    # 品类一致性检查：清单明确写了子类型（如"刚性防水套管"），
    # 但规则匹配到的定额不含该子类型（如匹配到"成品防火套管"），
    # 则丢弃规则匹配结果，让搜索来处理（搜索能更精准地按名称匹配）
    rule_result = _check_rule_subtype_conflict(rule_result, full_query)
    if not rule_result:
        return None, None

    _append_trace_step(
        rule_result,
        "rule_precheck",
        books=rule_books,
        confidence=rule_result.get("confidence", 0),
        quota_ids=[q.get("quota_id", "") for q in rule_result.get("quotas", [])],
    )
    allow_direct, threshold = PolicyEngine.should_use_rule_direct(
        rule_result.get("confidence", 0),
        route_profile=route_profile,
    )
    if allow_direct:
        _append_trace_step(rule_result, "rule_direct", threshold=threshold)
        return rule_result, None
    _append_trace_step(rule_result, "rule_backup", threshold=threshold)
    return None, rule_result


# ============================================================
# 结果构建
# ============================================================

def _build_alternatives(candidates: list[dict], selected_ids: set = None,
                        skip_obj=None, top_n: int = 3) -> list[dict]:
    """从候选中构建备选定额列表。"""
    if not candidates:
        return []
    selected_ids = selected_ids or set()
    filtered = []
    for c in candidates:
        if skip_obj is not None and c is skip_obj:
            continue
        if selected_ids and c.get("quota_id") in selected_ids:
            continue
        filtered.append(c)
    alternatives = []
    for alt in filtered[:top_n]:
        quota_id = str(alt.get("quota_id", "")).strip()
        quota_name = str(alt.get("name", "")).strip()
        if not quota_id or not quota_name:
            logger.warning(f"跳过异常候选（缺少quota_id/name）: {alt}")
            continue
        alt_ps = alt.get("param_score", 0.5)
        alt_conf = calculate_confidence(
            alt_ps, alt.get("param_match", True),
            name_bonus=alt.get("name_bonus", 0.0),
            rerank_score=alt.get("rerank_score", alt.get("hybrid_score", 0.0)),
        )
        alternatives.append({
            "quota_id": quota_id,
            "name": quota_name,
            "unit": alt.get("unit", ""),
            "confidence": alt_conf,
            "reason": alt.get("param_detail", ""),
            "reasoning": summarize_candidate_reasoning(alt),
        })
    return alternatives


def _build_skip_measure_result(item: dict) -> dict:
    """构建措施项跳过结果。"""
    result = {
        "bill_item": item,
        "quotas": [],
        "alternatives": [],
        "confidence": 0,
        "match_source": "skip_measure",
        "explanation": "措施项（管理费用），不套安装定额",
    }
    _set_result_reason(
        result,
        "measure_item",
        ["measure_item", "abstained"],
        "措施项（管理费用），不套安装定额",
    )
    _append_trace_step(result, "skip_measure", reason="管理费用类条目")
    return result


def _build_empty_match_result(item: dict, reason: str, source: str = "search") -> dict:
    """构建空匹配结果（用于无候选时兜底）。"""
    result = {
        "bill_item": item,
        "quotas": [],
        "confidence": 0,
        "explanation": reason,
        "no_match_reason": reason,
        "match_source": source,
    }
    _set_result_reason(result, "recall_failure", ["recall_failure", "no_candidates"], reason)
    _append_trace_step(result, "empty_result", reason=reason)
    return result


# ============================================================
# 兜底策略
# ============================================================

def _apply_rule_backup(result: dict, rule_backup: dict, rule_hits: int,
                       prefer_label: str) -> tuple[dict, int]:
    """
    低置信规则结果兜底比较：置信度更高则替换当前结果。

    prefer_label 用于日志前缀，如"搜索/经验""LLM/经验""Agent/经验"。
    """
    if not rule_backup:
        return result, rule_hits
    if rule_backup.get("confidence", 0) > result.get("confidence", 0):
        _append_trace_step(
            rule_backup,
            "rule_backup_override",
            replaced_source=result.get("match_source", ""),
            replaced_confidence=result.get("confidence", 0),
        )
        return rule_backup, rule_hits + 1
    _append_trace_step(
        result,
        "rule_backup_rejected",
        backup_confidence=rule_backup.get("confidence", 0),
        current_confidence=result.get("confidence", 0),
    )
    logger.debug(
        f"{prefer_label}结果优于低置信规则: "
        f"当前{result.get('confidence', 0)}分 >= "
        f"规则{rule_backup.get('confidence', 0)}分, "
        f"不使用规则结果")
    return result, rule_hits


def _apply_similar_exp_backup(result: dict, exp_backup: dict, exp_hits: int,
                              prefer_label: str) -> tuple[dict, int]:
    """经验库相似匹配兜底比较：置信度更高则替换当前结果。"""
    if not exp_backup:
        return result, exp_hits
    # 严格大于才替换（等分时保持当前结果，因为搜索+参数验证更针对当前query）
    if exp_backup.get("confidence", 0) > result.get("confidence", 0):
        _append_trace_step(
            exp_backup,
            "experience_backup_override",
            replaced_source=result.get("match_source", ""),
            replaced_confidence=result.get("confidence", 0),
        )
        return exp_backup, exp_hits + 1
    _append_trace_step(
        result,
        "experience_backup_rejected",
        backup_confidence=exp_backup.get("confidence", 0),
        current_confidence=result.get("confidence", 0),
    )
    logger.debug(
        f"{prefer_label}结果优于经验库相似匹配: "
        f"当前{result.get('confidence', 0)}分 > "
        f"经验库{exp_backup.get('confidence', 0)}分, "
        f"保持{prefer_label}结果")
    return result, exp_hits


def _apply_mode_backups(result: dict, exp_backup: dict, rule_backup: dict,
                        exp_hits: int, rule_hits: int,
                        exp_label: str, rule_label: str) -> tuple[dict, int, int]:
    """full/agent 模式统一后处理：经验库相似兜底 + 低置信规则兜底。"""
    result, exp_hits = _apply_similar_exp_backup(
        result, exp_backup, exp_hits, prefer_label=exp_label)
    result, rule_hits = _apply_rule_backup(
        result, rule_backup, rule_hits, prefer_label=rule_label)
    return result, exp_hits, rule_hits


# ============================================================
# Search模式结果处理
# ============================================================

def _reconcile_search_and_experience(result: dict, exp_backup: dict,
                                     exp_hits: int) -> tuple[dict, int]:
    """
    search模式下，经验库与搜索结果交叉验证。

    规则保持原逻辑：
    1) 同一主定额：抬高置信并标注 confirmed
    2) 经验库精确匹配但与搜索不一致：经验分降到88后再比较
    3) 经验库相似匹配：按置信度比较
    """
    if not exp_backup:
        return result, exp_hits

    exp_source = exp_backup.get("match_source", "")
    exp_qids = [q.get("quota_id", "") for q in exp_backup.get("quotas", [])]
    search_qids = [q.get("quota_id", "") for q in result.get("quotas", [])]

    same_quota = (exp_qids and search_qids and exp_qids[0] == search_qids[0])
    if same_quota:
        result["confidence"] = max(result.get("confidence", 0), 92)
        result["match_source"] = f"{exp_source}_confirmed"
        result["explanation"] = f"经验库+搜索一致: {result.get('explanation', '')}"
        if exp_backup.get("materials"):
            result["materials"] = exp_backup.get("materials")
        _append_trace_step(
            result,
            "experience_search_confirmed",
            experience_source=exp_source,
            quota_id=search_qids[0] if search_qids else "",
            materials_count=len(_safe_json_materials(result.get("materials"))),
        )
        return result, exp_hits + 1

    if exp_source == "experience_exact":
        exp_conf = min(exp_backup.get("confidence", 0), 88)
        search_conf = result.get("confidence", 0)
        # 严格大于才替换（与相似匹配一致，等分时信任搜索+参数验证）
        if exp_conf > search_conf:
            exp_backup["confidence"] = exp_conf
            _append_trace_step(
                exp_backup,
                "experience_exact_degraded_override",
                degraded_confidence=exp_conf,
                search_confidence=search_conf,
            )
            logger.debug(
                f"经验库精确匹配(降级) vs 搜索: "
                f"经验{exp_conf}分 > 搜索{search_conf}分")
            return exp_backup, exp_hits + 1
        _append_trace_step(
            result,
            "experience_exact_degraded_rejected",
            degraded_confidence=exp_conf,
            search_confidence=search_conf,
        )
        logger.debug(
            f"搜索优于经验库精确匹配: "
            f"搜索{search_conf}分 > 经验{exp_conf}分(降级)")
        return result, exp_hits

    # 严格大于才替换（等分时保持当前结果，因为搜索+参数验证更针对当前query）
    if exp_backup.get("confidence", 0) > result.get("confidence", 0):
        _append_trace_step(
            exp_backup,
            "experience_similar_override",
            search_confidence=result.get("confidence", 0),
            backup_confidence=exp_backup.get("confidence", 0),
        )
        return exp_backup, exp_hits + 1

    _append_trace_step(
        result,
        "experience_similar_rejected",
        search_confidence=result.get("confidence", 0),
        backup_confidence=exp_backup.get("confidence", 0),
    )
    logger.debug(
        f"搜索结果优于经验库相似匹配: "
        f"搜索{result.get('confidence', 0)}分 > "
        f"经验库{exp_backup.get('confidence', 0)}分")
    return result, exp_hits


def _build_search_result_from_candidates(item: dict, candidates: list[dict]) -> dict:
    """
    search模式下，根据候选结果构建基础匹配结果。

    优先取 param_match=True 的首项；若全部不匹配，则降权回退到首候选。
    """
    best = None
    confidence = 0
    explanation = ""
    arbitration = {}
    reasoning_decision = {}

    valid_candidates = [
        c for c in (candidates or [])
        if str(c.get("quota_id", "")).strip() and str(c.get("name", "")).strip()
    ]
    if candidates and not valid_candidates:
        logger.warning("候选列表存在，但全部缺少quota_id/name，按无匹配处理")

    if valid_candidates:
        matched_candidates = [c for c in valid_candidates if c.get("param_match", True)]
        if matched_candidates:
            matched_candidates, arbitration = arbitrate_candidates(
                item, matched_candidates, route_profile=item.get("query_route")
            )
            # 规则审核前置：跳过类别明显不匹配的候选
            best = _pick_category_safe_candidate(item, matched_candidates)
            decision_candidates = [best] + [c for c in matched_candidates if c is not best]
            param_score = best.get("param_score", 0.5)
            # 用和排序一致的综合分来算score_gap（修复#4：和best选择脱节）
            def _calc_composite(c):
                """和param_validator排序一致的综合分"""
                ps = c.get("param_score", 0)
                nb = c.get("name_bonus", 0)
                rr = c.get("rerank_score", c.get("hybrid_score", 0))
                fg = max(min(float(c.get("family_gate_score", 0.0) or 0.0), 2.0), -2.0)
                return ps * 0.55 + fg * 0.08 + nb * 0.22 + rr * 0.15
            best_composite = _calc_composite(best)
            others = [c for c in matched_candidates if c is not best]
            second_composite = max((_calc_composite(c) for c in others), default=0)
            score_gap = best_composite - second_composite
            confidence = calculate_confidence(
                param_score, param_match=True,
                name_bonus=best.get("name_bonus", 0.0),
                score_gap=score_gap,
                rerank_score=best.get("rerank_score", best.get("hybrid_score", 0.0)),
                candidates_count=len(valid_candidates),
                is_ambiguous_short=item.get("_is_ambiguous_short", False),
            )
            explanation = best.get("param_detail", "")
            reasoning_decision = analyze_ambiguity(
                decision_candidates,
                route_profile=item.get("query_route"),
                arbitration=arbitration,
            ).as_dict()
        else:
            arbitration = {
                "applied": False,
                "route": str((item.get("query_route") or {}).get("route") or ""),
                "reason": "no_param_matched_candidates",
            }
            best = _pick_category_safe_candidate(item, valid_candidates)
            decision_candidates = [best] + [c for c in valid_candidates if c is not best]
            param_score = best.get("param_score", 0.0)
            confidence = calculate_confidence(
                param_score, param_match=False,
                candidates_count=len(valid_candidates),
                is_ambiguous_short=item.get("_is_ambiguous_short", False),
            )
            explanation = f"参数不完全匹配(回退候选): {best.get('param_detail', '')}"
            reasoning_decision = analyze_ambiguity(
                decision_candidates,
                route_profile=item.get("query_route"),
                arbitration=arbitration,
            ).as_dict()

    # 收集所有候选定额ID（供benchmark统计"正确答案是否在候选中"）
    all_candidate_ids = [
        str(c.get("quota_id", "")).strip()
        for c in valid_candidates
        if str(c.get("quota_id", "")).strip()
    ]

    result = {
        "bill_item": item,
        "quotas": [{
            "quota_id": best["quota_id"],
            "name": best["name"],
            "unit": best.get("unit", ""),
            "reason": explanation,
            "reasoning": summarize_candidate_reasoning(best),
            "db_id": best.get("id"),
        }] if best else [],
        "confidence": confidence,
        "explanation": explanation,
        "candidates_count": len(valid_candidates),
        "all_candidate_ids": all_candidate_ids,
        "match_source": "search",
        "arbitration": arbitration,
        "reasoning_decision": reasoning_decision,
        "needs_reasoning": bool(reasoning_decision.get("is_ambiguous")),
        "require_final_review": bool(reasoning_decision.get("require_final_review")),
    }
    input_gate = item.get("_input_gate") or {}
    if best and valid_candidates and any(c.get("param_match", True) for c in valid_candidates):
        _set_result_reason(
            result,
            "structured_selection",
            ["retrieved", "validated"],
            explanation or "候选已召回，按结构化参数与重排结果选定",
        )
    elif best and valid_candidates:
        _set_result_reason(
            result,
            "param_conflict",
            ["retrieved", "param_conflict", "manual_review"],
            explanation or "召回到候选，但未发现完全参数一致项",
        )
    elif candidates and not valid_candidates:
        _set_result_reason(
            result,
            "candidate_invalid",
            ["retrieved", "candidate_invalid", "manual_review"],
            "搜索返回了候选，但缺少有效定额编号或名称",
        )
    else:
        _set_result_reason(
            result,
            "recall_failure",
            ["recall_failure", "no_candidates"],
            "搜索无匹配结果",
        )
    if input_gate:
        _set_result_reason(
            result,
            result.get("primary_reason", ""),
            list(input_gate.get("reason_tags") or []),
            result.get("reason_detail", "") or str(input_gate.get("detail") or ""),
        )
    if reasoning_decision.get("is_ambiguous"):
        ambiguity_tags = ["ambiguous_candidates"]
        if reasoning_decision.get("require_final_review"):
            ambiguity_tags.append("manual_review")
        _set_result_reason(
            result,
            result.get("primary_reason", ""),
            ambiguity_tags,
            result.get("reason_detail", "") or explanation,
        )
    _append_trace_step(
        result,
        "search_select",
        selected_quota=best.get("quota_id") if best else "",
        selected_reasoning=summarize_candidate_reasoning(best) if best else {},
        arbitration=arbitration,
        reasoning_decision=reasoning_decision,
        query_route=item.get("query_route") or {},
        batch_context=summarize_batch_context_for_trace(item),
        candidates_count=len(valid_candidates),
        candidates=_summarize_candidates_for_trace(candidates),
    )

    if best and valid_candidates:
        result["alternatives"] = _build_alternatives(
            valid_candidates, skip_obj=best, top_n=3)
    if not best:
        result["no_match_reason"] = "搜索无匹配结果"
    return result


def _resolve_search_mode_result(item: dict, candidates: list[dict],
                                exp_backup: dict, rule_backup: dict,
                                exp_hits: int, rule_hits: int):
    """search模式统一结果决策：搜索结果 + 经验/规则兜底。"""
    result = _build_search_result_from_candidates(item, candidates)
    result, exp_hits = _reconcile_search_and_experience(result, exp_backup, exp_hits)
    result, rule_hits = _apply_rule_backup(
        result, rule_backup, rule_hits, prefer_label="搜索/经验")
    _append_trace_step(
        result,
        "search_mode_final",
        final_source=result.get("match_source", ""),
        final_confidence=result.get("confidence", 0),
    )
    return result, exp_hits, rule_hits


# ============================================================
# 统一前置处理
# ============================================================

def _prepare_item_for_matching(item: dict, experience_db, rule_validator: RuleValidator,
                               province: str = None, exact_exp_direct: bool = False) -> dict:
    """
    三种模式统一的前置处理：
    1) 措施项跳过
    2) 专业分类
    3) 经验库预匹配（可配置精确命中是否直通）
    4) 规则预匹配（高置信直通、低置信备选）
    """
    ctx = _build_item_context(item)
    item["query_route"] = ctx.get("query_route")
    name = ctx["name"]
    desc = ctx["desc"]
    full_query = ctx["full_query"]
    search_query = ctx["search_query"]
    normalized_query = ctx["normalized_query"]
    input_gate = ctx.get("input_gate") or {}

    if _is_measure_item(name, desc, ctx["unit"], ctx["quantity"]):
        return {
            "early_result": _build_skip_measure_result(item),
            "early_type": "skip_measure",
        }

    if input_gate.get("should_abstain"):
        return {
            "early_result": _build_input_gate_abstain_result(
                item,
                primary_reason=str(input_gate.get("primary_reason") or "dirty_input"),
                detail=str(input_gate.get("detail") or "输入质量不足，转人工审核"),
                reason_tags=list(input_gate.get("reason_tags") or []),
            ),
            "early_type": "input_gate_abstain",
        }

    if input_gate.get("is_dirty_code"):
        current_gate = dict(item.get("_input_gate") or {})
        current_gate["primary_reason"] = current_gate.get("primary_reason") or input_gate.get("primary_reason", "dirty_input")
        current_gate["reason_tags"] = merge_reason_tags(
            current_gate.get("reason_tags") or [],
            input_gate.get("reason_tags") or [],
        )
        if input_gate.get("detail") and not current_gate.get("detail"):
            current_gate["detail"] = input_gate.get("detail", "")
        item["_input_gate"] = current_gate

    classification = _build_classification(
        item, name, desc, ctx["section"], province=province
    )
    context_gate = _evaluate_context_gate(name, desc, ctx["section"], classification)
    if context_gate.get("should_abstain"):
        return {
            "early_result": _build_input_gate_abstain_result(
                item,
                primary_reason=str(context_gate.get("primary_reason") or "context_missing"),
                detail=str(context_gate.get("detail") or "上下文不足，转人工审核"),
                reason_tags=list(context_gate.get("reason_tags") or []),
            ),
            "early_type": "input_gate_abstain",
        }

    if context_gate.get("reason_tags"):
        current_gate = dict(item.get("_input_gate") or {})
        current_gate["primary_reason"] = current_gate.get("primary_reason") or context_gate.get("primary_reason", "")
        current_gate["reason_tags"] = merge_reason_tags(
            current_gate.get("reason_tags") or [],
            context_gate.get("reason_tags") or [],
        )
        if context_gate.get("detail") and not current_gate.get("detail"):
            current_gate["detail"] = context_gate.get("detail", "")
        item["_input_gate"] = current_gate

    exp_result = try_experience_match(
        normalized_query, item, experience_db, rule_validator, province=province)

    # 审核规则检查：经验库命中后，用审核规则验证一遍
    # 防止错误数据进入权威层后被无限复制
    if exp_result:
        review_error = _review_check_match_result(exp_result, item)
        if review_error:
            # 在 item 上标记审核拦截（后续统计时从 result.bill_item 中读取）
            item["_review_rejected"] = True
            bill_name = item.get("name", "")
            logger.warning(
                f"经验库匹配被审核规则拦截: '{bill_name[:40]}' "
                f"→ {review_error.get('type')}: {review_error.get('reason')}")
            _append_trace_step(exp_result, "experience_review_rejected",
                               error_type=review_error.get("type"),
                               error_reason=review_error.get("reason"))
            exp_result = None  # 丢弃，走搜索兜底

    exp_backup = exp_result if exp_result else None

    if exact_exp_direct and exp_result and exp_result.get("match_source") == "experience_exact":
        _append_trace_step(exp_result, "experience_exact_direct_return")
        return {
            "early_result": exp_result,
            "early_type": "experience_exact",
        }

    rule_direct, rule_backup = _prepare_rule_match(
        rule_validator, full_query, item, search_query, classification,
        route_profile=ctx.get("query_route"))
    if rule_direct:
        # 审核规则检查：规则直通也要过安检（与经验库直通一致）
        review_error = _review_check_match_result(rule_direct, item)
        if review_error:
            bill_name = item.get("name", "")
            logger.warning(
                f"规则直通被审核规则拦截: '{bill_name[:40]}' "
                f"→ {review_error.get('type')}: {review_error.get('reason')}")
            _append_trace_step(rule_direct, "rule_direct_review_rejected",
                               error_type=review_error.get("type"),
                               error_reason=review_error.get("reason"))
            # 已被审核规则判错的规则直通结果不能再回流为备选，
            # 否则后续可能反向覆盖掉更安全的搜索结果。
            rule_backup = None
            rule_direct = None
        else:
            _append_trace_step(rule_direct, "rule_direct_return")
            return {
                "early_result": rule_direct,
                "early_type": "rule_direct",
            }

    return {
        "early_result": None,
        "early_type": None,
        "ctx": ctx,
        "classification": classification,
        "exp_backup": exp_backup,
        "rule_backup": rule_backup,
    }
