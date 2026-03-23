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
from src.candidate_scoring import compute_candidate_rank_score, compute_candidate_sort_key
from src.context_builder import build_context_prior, summarize_batch_context_for_trace
from src.province_plugins import resolve_plugin_hints
from src.text_parser import parser as text_parser, normalize_bill_text
from src.query_router import build_query_route_profile
from src.reason_taxonomy import apply_reason_metadata, merge_reason_tags
from src.specialty_classifier import BORROW_PRIORITY, classify as classify_specialty
from src.rule_validator import RuleValidator
from src.match_core import (
    calculate_confidence,
    infer_confidence_family_alignment,
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



def _safe_candidate_hybrid_score(candidate: dict | None) -> float:
    if not isinstance(candidate, dict):
        return 0.0
    value = candidate.get("hybrid_score", candidate.get("rerank_score", 0.0))
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _guard_explicit_candidate(top_candidate: dict, explicit_candidate: dict | None, hybrid_margin: float = 0.005) -> dict | None:
    if explicit_candidate is None:
        return None
    if not top_candidate:
        return explicit_candidate
    top_hybrid = _safe_candidate_hybrid_score(top_candidate)
    pick_hybrid = _safe_candidate_hybrid_score(explicit_candidate)
    if top_hybrid - pick_hybrid > hybrid_margin:
        return top_candidate
    return explicit_candidate


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

    top_candidate = candidates[0]
    desc = item.get("description", "") or ""
    bill_name = item.get("name", "") or ""
    bill_text = f"{bill_name} {desc}"
    desc_lines = extract_description_lines(desc)

    cable_candidate = _pick_explicit_cable_family_candidate(bill_text, candidates)
    if cable_candidate is not None:
        return _guard_explicit_candidate(top_candidate, cable_candidate)

    wiring_candidate = _pick_explicit_wiring_family_candidate(bill_text, candidates)
    if wiring_candidate is not None:
        return _guard_explicit_candidate(top_candidate, wiring_candidate)

    cast_iron_pipe_candidate = _pick_explicit_cast_iron_pipe_candidate(bill_text, candidates)
    if cast_iron_pipe_candidate is not None:
        return _guard_explicit_candidate(top_candidate, cast_iron_pipe_candidate)

    sleeve_candidate = _pick_explicit_plastic_sleeve_candidate(bill_text, candidates)
    if sleeve_candidate is not None:
        return _guard_explicit_candidate(top_candidate, sleeve_candidate)

    general_sleeve_candidate = _pick_explicit_sleeve_family_candidate(bill_text, candidates)
    if general_sleeve_candidate is not None:
        return _guard_explicit_candidate(top_candidate, general_sleeve_candidate)

    conduit_candidate = _pick_explicit_conduit_family_candidate(bill_text, candidates)
    if conduit_candidate is not None:
        return _guard_explicit_candidate(top_candidate, conduit_candidate)

    bridge_candidate = _pick_explicit_bridge_family_candidate(bill_text, candidates)
    if bridge_candidate is not None:
        return _guard_explicit_candidate(top_candidate, bridge_candidate)

    distribution_box_candidate = _pick_explicit_distribution_box_candidate(bill_text, candidates)
    if distribution_box_candidate is not None:
        return _guard_explicit_candidate(top_candidate, distribution_box_candidate)

    ventilation_candidate = _pick_explicit_ventilation_family_candidate(bill_text, candidates)
    if ventilation_candidate is not None:
        return _guard_explicit_candidate(top_candidate, ventilation_candidate)

    support_candidate = _pick_explicit_support_family_candidate(bill_text, candidates)
    if support_candidate is not None:
        return _guard_explicit_candidate(top_candidate, support_candidate)
    if _should_force_conservative_support_fallback(item, bill_text):
        support_fallback_candidate = _pick_safe_support_fallback_candidate(item, candidates)
        if support_fallback_candidate is not None:
            return support_fallback_candidate
        return None

    insulation_candidate = _pick_explicit_insulation_family_candidate(bill_text, candidates)
    if insulation_candidate is not None:
        return _guard_explicit_candidate(top_candidate, insulation_candidate)

    motor_candidate = _pick_explicit_motor_family_candidate(bill_text, candidates)
    if motor_candidate is not None:
        return _guard_explicit_candidate(top_candidate, motor_candidate)

    sanitary_candidate = _pick_explicit_sanitary_family_candidate(bill_text, candidates)
    if sanitary_candidate is not None:
        return _guard_explicit_candidate(top_candidate, sanitary_candidate)

    lamp_candidate = _pick_explicit_lamp_family_candidate(bill_text, candidates)
    if lamp_candidate is not None:
        return _guard_explicit_candidate(top_candidate, lamp_candidate)

    button_broadcast_candidate = _pick_explicit_button_broadcast_candidate(bill_text, candidates)
    if button_broadcast_candidate is not None:
        return _guard_explicit_candidate(top_candidate, button_broadcast_candidate)

    plumbing_accessory_candidate = _pick_explicit_plumbing_accessory_candidate(bill_text, candidates)
    if plumbing_accessory_candidate is not None:
        return _guard_explicit_candidate(top_candidate, plumbing_accessory_candidate)

    valve_candidate = _pick_explicit_valve_family_candidate(bill_text, candidates)
    if valve_candidate is not None:
        return _guard_explicit_candidate(top_candidate, valve_candidate)

    fire_candidate = _pick_explicit_fire_device_candidate(bill_text, candidates)
    if fire_candidate is not None:
        return _guard_explicit_candidate(top_candidate, fire_candidate)

    network_candidate = _pick_explicit_network_device_candidate(bill_text, candidates)
    if network_candidate is not None:
        return _guard_explicit_candidate(top_candidate, network_candidate)

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


def _should_force_conservative_support_fallback(item: dict, bill_text: str) -> bool:
    params = item.get("params") if isinstance(item, dict) else None
    if not isinstance(params, dict) or not params:
        params = text_parser.parse(bill_text or "")
    support_scope = str(params.get("support_scope") or "")
    if support_scope in {"抗震支架", "桥架支架", "管道支架", "设备支架"}:
        return True
    return any(
        keyword in (bill_text or "")
        for keyword in (
            "抗震支架",
            "抗震支吊架",
            "桥架支架",
            "桥架支撑架",
            "电缆桥架",
            "管道支架",
            "给排水",
            "消防水",
            "通风空调",
            "风管",
            "设备支架",
        )
    )


def _pick_safe_support_fallback_candidate(item: dict, candidates: list[dict]) -> dict | None:
    bill_name = str((item or {}).get("name") or "")
    desc = str((item or {}).get("description") or "")
    bill_text = f"{bill_name} {desc}".strip()
    params = (item or {}).get("params")
    if not isinstance(params, dict) or not params:
        params = text_parser.parse(bill_text)
    support_scope = str(params.get("support_scope") or "")
    specialty = str((item or {}).get("specialty") or "")

    prefer_bridge = support_scope == "桥架支架" or any(
        keyword in bill_text for keyword in ("桥架", "电缆桥架", "桥架支撑架")
    )
    prefer_duct = specialty.startswith("C7") or any(
        keyword in bill_text for keyword in ("通风", "空调", "风管")
    )
    prefer_pipe = not prefer_bridge and support_scope != "设备支架"

    support_anchor_words = (
        "支架", "吊架", "支吊架", "支撑架", "管架",
        "吊托支架", "仪表支架", "桥架立柱", "托臂",
    )
    hard_reject_words = (
        "风帽", "风罩", "敷设", "穿线", "控制台",
        "控制箱", "终端头", "调试", "配电箱",
        "撑杆", "计量泵",
    )
    scored: list[tuple[tuple[int, float, float], dict]] = []
    for cand in candidates:
        quota_name = str(cand.get("name") or "")
        if not quota_name:
            continue
        if any(word in quota_name for word in hard_reject_words):
            continue
        cand_params = text_parser.parse(quota_name)
        cand_scope = str(cand_params.get("support_scope") or "")
        has_support_anchor = bool(cand_scope) or any(word in quota_name for word in support_anchor_words)
        if not has_support_anchor:
            continue

        score = 0
        if support_scope and cand_scope:
            if support_scope == cand_scope:
                score += 12
            elif support_scope == "抗震支架" and cand_scope in {"桥架支架", "管道支架"}:
                score += 4
            else:
                score -= 12

        if prefer_bridge:
            if any(word in quota_name for word in ("桥架支撑架", "桥架立柱", "电缆桥架")):
                score += 14
            if "仪表支架" in quota_name and "桥架立柱" in quota_name:
                score += 8
            if any(word in quota_name for word in ("光缆", "电缆敷设")):
                score -= 14
            if any(word in quota_name for word in ("管道支吊架", "吊托支架")):
                score -= 8
        elif prefer_pipe:
            if any(word in quota_name for word in ("管道支吊架", "吊托支架", "一般管架")):
                score += 12
            if "仪表支架" in quota_name:
                score -= 10
            if any(word in quota_name for word in ("桥架", "电缆")):
                score -= 12
        if prefer_duct:
            if any(word in quota_name for word in ("管道支吊架", "吊托支架", "支吊架")):
                score += 4
            if "仪表支架" in quota_name:
                score -= 12
            if any(word in quota_name for word in ("桥架", "电缆")):
                score -= 10

        if "制作" in quota_name or "安装" in quota_name:
            score += 4
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


def _infer_cable_conductor_anchor(text: str, parsed: dict | None = None) -> str:
    parsed = parsed or {}
    wire_type = str(parsed.get("wire_type") or "").upper()
    material = str(parsed.get("material") or "")
    combined = f"{text or ''} {material}".upper()
    if "铝合金" in combined:
        return "铝合金"
    if any(keyword in combined for keyword in ("铝芯", "压铝", "铝电缆")):
        return "铝芯"
    if any(keyword in combined for keyword in ("铜芯", "压铜", "铜电缆")):
        return "铜芯"
    if wire_type.startswith(("YJLV", "VLV", "VLL", "YJHLV")):
        return "铝芯"
    if wire_type.startswith((
        "BPYJV", "YJV", "YJY", "VV", "KYJY", "KVV", "KVVP",
        "BTLY", "BTTRZ", "BTTZ", "YTTW", "BBTRZ",
    )):
        return "铜芯"
    return ""


def _extract_cable_head_craft_anchor(text: str) -> str:
    raw = str(text or "")
    if any(keyword in raw for keyword in ("热缩", "冷缩", "热(冷)缩", "热（冷）缩")):
        return "热缩"
    if "浇注" in raw:
        return "浇注"
    if "干包" in raw:
        return "干包"
    return ""


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
    bill_laying_method = str(bill_params.get("laying_method") or "")
    bill_wire_type = str(bill_params.get("wire_type") or "")
    bill_cable_type = str(bill_params.get("cable_type") or "")
    bill_head_type = str(bill_params.get("cable_head_type") or "")
    bill_conductor = _infer_cable_conductor_anchor(text, bill_params)
    bill_craft = _extract_cable_head_craft_anchor(text)
    bill_voltage = "35kV" if "35KV" in upper_text or "35KV" in upper_text else (
        "10kV" if "10KV" in upper_text or "10KV" in upper_text else (
            "1kV" if any(token in upper_text for token in ("0.6/1KV", "1KV")) else ""
        )
    )

    is_head = any(keyword in text for keyword in ("终端头", "电缆头", "中间头"))
    is_middle_head = "中间头" in text
    is_control = (
        "控制" in text
        or bill_cable_type == "控制电缆"
        or any(keyword in upper_text for keyword in ("KVV", "KVVP", "KVVR", "RVVSP", "RVSP"))
    )
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
        cand_laying_method = str(cand_params.get("laying_method") or "")
        cand_wire_type = str(cand_params.get("wire_type") or "")
        cand_cable_type = str(cand_params.get("cable_type") or "")
        cand_head_type = str(cand_params.get("cable_head_type") or "")
        cand_conductor = _infer_cable_conductor_anchor(quota_name, cand_params)
        cand_craft = _extract_cable_head_craft_anchor(quota_name)
        score = 0
        score += sum(8 for word in expected_words if word and word in quota_name)
        score -= sum(8 for word in forbidden_words if word and word in quota_name)
        score += sum(4 for word in core_words if word and word in quota_name)
        if not is_head and any(keyword in quota_name for keyword in ("终端头", "电缆头", "中间头")):
            score -= 20
        if is_head and "敷设" in quota_name and not any(keyword in quota_name for keyword in ("终端头", "电缆头", "中间头")):
            score -= 16
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
        if bill_laying_method:
            if (
                cand_laying_method and (
                    bill_laying_method == cand_laying_method
                    or any(token and token in cand_laying_method for token in bill_laying_method.split("/"))
                )
            ) or (
                ("穿管" in bill_laying_method and any(token in quota_name for token in ("穿导管", "穿管", "管内")))
                or ("桥架" in bill_laying_method and "桥架" in quota_name)
                or ("线槽" in bill_laying_method and "线槽" in quota_name)
                or ("排管" in bill_laying_method and "排管" in quota_name)
                or ("直埋" in bill_laying_method and "埋地" in quota_name)
            ):
                score += 6
            elif cand_laying_method:
                score -= 6
        if bill_wire_type and cand_wire_type:
            score += 4 if bill_wire_type == cand_wire_type else -4
        if bill_cable_type and cand_cable_type:
            score += 8 if bill_cable_type == cand_cable_type else -8
        if bill_head_type and cand_head_type:
            score += 10 if bill_head_type == cand_head_type else -10
        if bill_conductor and cand_conductor:
            score += 8 if bill_conductor == cand_conductor else -10
        if is_head and bill_craft and cand_craft:
            score += 6 if bill_craft == cand_craft else -8
        if is_head and bill_voltage:
            if bill_voltage in quota_name.upper():
                score += 4
            elif any(token in quota_name.upper() for token in ("10KV", "35KV", "1KV")):
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
    bill_laying_method = str(bill_params.get("laying_method") or "")
    bill_wire_type = str(bill_params.get("wire_type") or "")
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
        cand_laying_method = str(cand_params.get("laying_method") or "")
        cand_wire_type = str(cand_params.get("wire_type") or "")
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
        if bill_laying_method:
            if (
                cand_laying_method and (
                    bill_laying_method == cand_laying_method
                    or any(token and token in cand_laying_method for token in bill_laying_method.split("/"))
                )
            ) or (
                ("穿管" in bill_laying_method and any(token in quota_name for token in ("管内穿", "穿线", "穿管")))
                or ("线槽" in bill_laying_method and "线槽" in quota_name)
                or ("桥架" in bill_laying_method and "桥架" in quota_name)
            ):
                score += 6
            elif cand_laying_method:
                score -= 6
        if bill_wire_type and cand_wire_type:
            score += 4 if bill_wire_type == cand_wire_type else -4
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


def _pick_explicit_cast_iron_pipe_candidate(bill_text: str,
                                            candidates: list[dict]) -> dict | None:
    text = bill_text or ""
    if "铸铁" not in text or "管" not in text:
        return None

    drainage_context = any(keyword in text for keyword in ("排水", "污水", "废水", "污废水", "雨水"))
    if not drainage_context:
        return None

    bill_params = text_parser.parse(text)
    bill_dn = bill_params.get("dn")
    expected_words = ["铸铁"]
    forbidden_words = [
        "钢塑复合管", "复合管", "塑料给水管", "塑料排水管",
        "给水管", "PPR", "PP-R", "钢管",
    ]
    prefer_words: list[str] = []

    if "雨水" in text:
        expected_words.append("雨水")
        forbidden_words.append("排水管")
    else:
        expected_words.append("排水")
        forbidden_words.append("雨水")

    if "室内" in text:
        prefer_words.append("室内")
        forbidden_words.append("室外")
    elif "室外" in text:
        prefer_words.append("室外")
        forbidden_words.append("室内")

    if any(keyword in text for keyword in ("机械接口", "机械连接")):
        prefer_words.extend(["机械接口", "机械连接"])
        forbidden_words.extend(["卡箍", "胶圈"])
    elif any(keyword in text for keyword in ("卡箍", "无承口")):
        prefer_words.extend(["卡箍", "无承口"])
        forbidden_words.append("机械接口")

    scored: list[tuple[tuple[int, float, float], dict]] = []
    for cand in candidates:
        quota_name = cand.get("name", "") or ""
        if "铸铁" not in quota_name:
            continue
        cand_params = text_parser.parse(quota_name)
        score = sum(10 for word in expected_words if word and word in quota_name)
        score -= sum(10 for word in forbidden_words if word and word in quota_name)
        score += sum(4 for word in prefer_words if word and word in quota_name)
        if bill_dn is not None:
            cand_dn = cand_params.get("dn")
            if cand_dn is not None:
                if cand_dn == bill_dn:
                    score += 8
                elif cand_dn > bill_dn:
                    score += 3
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


def _pick_explicit_sleeve_family_candidate(bill_text: str,
                                           candidates: list[dict]) -> dict | None:
    text = bill_text or ""
    if not any(keyword in text for keyword in ("套管", "堵洞", "封堵")):
        return None
    if any(keyword in text for keyword in ("电气配管", "导管", "穿线管", "可挠金属套管")):
        return None

    bill_params = text_parser.parse(text)
    bill_dn = bill_params.get("dn")
    expected_words: list[str] = []
    forbidden_words: list[str] = []
    prefer_words: list[str] = []

    if any(keyword in text for keyword in ("堵洞", "封堵")):
        expected_words.extend(["堵洞", "封堵"])
        forbidden_words.extend(["套管", "钢套管", "防水套管", "管道"])
    elif any(keyword in text for keyword in ("刚性防水", "刚性防水套管")):
        expected_words.extend(["刚性防水套管"])
        forbidden_words.extend(["柔性防水", "一般钢套管", "塑料套管", "堵洞"])
    elif any(keyword in text for keyword in ("柔性防水", "柔性防水套管")):
        expected_words.extend(["柔性防水套管"])
        forbidden_words.extend(["刚性防水", "一般钢套管", "塑料套管", "堵洞"])
    elif any(keyword in text for keyword in ("密闭", "人防", "防护密闭")):
        expected_words.extend(["密闭套管", "人防", "防护密闭"])
        forbidden_words.extend(["一般钢套管", "塑料套管", "堵洞"])
    else:
        expected_words.extend(["钢套管", "一般钢套管", "填料套管"])
        forbidden_words.extend(["刚性防水", "柔性防水", "塑料套管", "成品防火", "堵洞"])

    if "穿墙" in text:
        prefer_words.append("穿墙")
    if "穿楼板" in text:
        prefer_words.append("穿楼板")

    scored: list[tuple[tuple[int, float, float], dict]] = []
    for cand in candidates:
        quota_name = cand.get("name", "") or ""
        if "套管" not in quota_name and not any(keyword in quota_name for keyword in ("堵洞", "封堵")):
            continue
        cand_params = text_parser.parse(quota_name)
        score = sum(10 for word in expected_words if word and word in quota_name)
        score -= sum(10 for word in forbidden_words if word and word in quota_name)
        score += sum(3 for word in prefer_words if word and word in quota_name)
        if bill_dn is not None:
            cand_dn = cand_params.get("dn")
            if cand_dn is not None:
                if cand_dn == bill_dn:
                    score += 6
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


def _pick_explicit_conduit_family_candidate(bill_text: str,
                                            candidates: list[dict]) -> dict | None:
    """对“明确电气配管语义”的清单，优先在前几名里选对家族。

    只处理非常明确的场景：`电气配管 SC20`、`JDG穿线管`、`电气配管 PC25`、
    `金属软管`、`可挠金属套管` 等，避免把给排水的 `SC32` 全局误判成电气配管。
    """
    if not candidates:
        return None

    text = bill_text or ""
    bill_params = text_parser.parse(text)
    upper_text = text.upper()
    code_match = re.search(r'(?<![A-Z0-9])(JDG|KBG|FPC|PVC|PC|SC|RC|MT|DG|G)\s*\d+\b', upper_text)
    bill_conduit_type = str(bill_params.get("conduit_type") or (code_match.group(1) if code_match else ""))
    bill_conduit_dn = bill_params.get("conduit_dn")
    bill_laying_method = str(bill_params.get("laying_method") or "")
    bill_wire_type = str(bill_params.get("wire_type") or "")
    bill_cable_type = str(bill_params.get("cable_type") or "")
    bill_head_type = str(bill_params.get("cable_head_type") or "")
    explicit_electrical = any(keyword in text for keyword in (
        "电气配管", "穿线管", "导管", "金属软管", "可挠金属套管",
    ))
    if not explicit_electrical and not (bill_conduit_type and "配管" in text):
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
        conduit_code = bill_conduit_type
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
        cand_params = text_parser.parse(quota_name)
        cand_conduit_type = str(cand_params.get("conduit_type") or "")
        cand_conduit_dn = cand_params.get("conduit_dn")
        cand_laying_method = str(cand_params.get("laying_method") or "")
        cand_wire_type = str(cand_params.get("wire_type") or "")
        cand_cable_type = str(cand_params.get("cable_type") or "")
        cand_head_type = str(cand_params.get("cable_head_type") or "")
        family_hits = sum(1 for word in expected_words if word and word in quota_name)
        family_penalty = sum(1 for word in forbidden_words if word and word in quota_name)
        layout_hits = sum(1 for word in layout_words if word and word in quota_name)
        size_hits = sum(1 for token in size_tokens if token and token in quota_name)
        score = family_hits * 10 + layout_hits * 4 + size_hits * 2 - family_penalty * 8
        if bill_conduit_type and cand_conduit_type:
            score += 10 if bill_conduit_type == cand_conduit_type else -10
        if bill_conduit_dn is not None and cand_conduit_dn is not None:
            if bill_conduit_dn == cand_conduit_dn:
                score += 8
            elif bill_conduit_dn < cand_conduit_dn:
                score += 2
            else:
                score -= 8
        if bill_laying_method and cand_laying_method:
            if bill_laying_method == cand_laying_method or any(
                token and token in cand_laying_method for token in bill_laying_method.split("/")
            ):
                score += 6
            else:
                score -= 6
        if bill_wire_type and cand_wire_type:
            score += 4 if bill_wire_type == cand_wire_type else -4
        if bill_cable_type and cand_cable_type:
            score += 8 if bill_cable_type == cand_cable_type else -8
        if bill_head_type and cand_head_type:
            score += 10 if bill_head_type == cand_head_type else -10
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
    bill_bridge_wh_sum = bill_params.get("bridge_wh_sum")
    bill_bridge_type = str(bill_params.get("bridge_type") or "")
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
        cand_bridge_type = str(cand_params.get("bridge_type") or "")
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
        if bill_bridge_type and cand_bridge_type:
            if bill_bridge_type == cand_bridge_type:
                score += 10
            else:
                score -= 10
        if bill_bridge_wh_sum is not None:
            cand_bridge_wh_sum = cand_params.get("bridge_wh_sum")
            if cand_bridge_wh_sum is not None:
                if cand_bridge_wh_sum == bill_bridge_wh_sum:
                    score += 6
                elif cand_bridge_wh_sum > bill_bridge_wh_sum:
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
    if not any(keyword in text for keyword in ("配电箱", "配电柜", "控制箱", "控制柜", "动力箱", "照明箱", "程序控制箱")):
        return None

    bill_params = text_parser.parse(text)
    install_method = str(bill_params.get("install_method") or "")
    box_mount_mode = str(bill_params.get("box_mount_mode") or "")
    prefer_floor = any(keyword in text for keyword in ("落地", "柜基础", "基础槽钢"))
    prefer_wall = any(keyword in text for keyword in ("悬挂", "嵌入", "明装", "暗装", "挂墙", "壁挂", "墙上", "柱上", "距地"))
    if box_mount_mode == "落地式":
        prefer_floor = True
        prefer_wall = False
    elif box_mount_mode == "悬挂/嵌入式":
        prefer_wall = True
    if install_method == "落地":
        prefer_floor = True
        prefer_wall = False
    elif install_method in {"挂墙", "嵌入"} or "明装" in install_method or "暗装" in install_method or "悬挂" in install_method:
        prefer_wall = True
    if not prefer_floor and not prefer_wall:
        if any(keyword in text for keyword in ("配电柜", "控制柜")):
            prefer_floor = True
        elif any(keyword in text for keyword in ("配电箱", "控制箱", "动力箱", "照明箱", "程序控制箱")):
            prefer_wall = True
    if not prefer_floor and not prefer_wall:
        return None

    scored: list[tuple[tuple[int, float, float], dict]] = []
    for cand in candidates:
        quota_name = cand.get("name", "") or ""
        cand_params = text_parser.parse(quota_name)
        cand_box_mount_mode = str(cand_params.get("box_mount_mode") or "")
        cand_half_perimeter = cand_params.get("half_perimeter")
        cand_circuits = cand_params.get("circuits")
        candidate_is_junction_box = any(word in quota_name for word in ("接线箱", "接线盒", "分线盒"))
        candidate_is_box_wiring = "盘、柜、箱、板配线" in quota_name
        candidate_is_box_install = any(
            word in quota_name
            for word in ("配电箱", "配电柜", "控制箱", "控制柜", "动力箱", "照明箱", "程序控制箱", "箱体安装")
        )
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
        if box_mount_mode and cand_box_mount_mode:
            if box_mount_mode == cand_box_mount_mode:
                score += 10
            else:
                score -= 10
        if candidate_is_box_install:
            score += 10
        if candidate_is_junction_box:
            score -= 28
        if candidate_is_box_wiring:
            score -= 26
        if "配线" in quota_name and "安装" not in quota_name:
            score -= 12
        bill_half_perimeter = bill_params.get("half_perimeter")
        if bill_half_perimeter is not None:
            if cand_half_perimeter is None:
                score -= 6
            elif cand_half_perimeter < bill_half_perimeter:
                score -= 18
            elif cand_half_perimeter == bill_half_perimeter:
                score += 16
            elif cand_half_perimeter <= bill_half_perimeter * 1.2:
                score += 12
            else:
                score += 8
        bill_circuits = bill_params.get("circuits")
        if bill_circuits is not None:
            if cand_circuits is None:
                score -= 4
            elif cand_circuits < bill_circuits:
                score -= 16
            elif cand_circuits == bill_circuits:
                score += 12
            elif cand_circuits <= bill_circuits * 1.5:
                score += 9
            else:
                score += 6
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
    ventilation_entity_keywords = (
        "风口", "散流器", "百叶", "风机", "排气扇", "换气扇", "通风器", "软风管", "消声器",
    )
    ventilation_valve_keywords = (
        "止回阀", "调节阀", "防火阀", "排烟阀", "定风量阀", "插板阀",
    )
    ventilation_context_keywords = (
        "风管", "通风", "空调", "送风", "回风", "排烟", "风量", "多叶", "对开", "防火",
    )
    if not any(keyword in text for keyword in (
        *ventilation_entity_keywords,
        *ventilation_valve_keywords,
    )):
        return None
    # 仅含“止回阀/调节阀”等词但没有通风上下文时，不抢占管道阀门家族。
    if (
        any(keyword in text for keyword in ventilation_valve_keywords)
        and not any(keyword in text for keyword in ventilation_context_keywords)
        and not any(keyword in text for keyword in ventilation_entity_keywords)
    ):
        return None

    bill_params = text_parser.parse(text)
    bill_features = text_parser.parse_canonical(text, params=bill_params)
    bill_perimeter = bill_params.get("perimeter")
    bill_weight = bill_params.get("weight_t")
    bill_entity = str(bill_features.get("entity") or "")
    bill_canonical_name = str(bill_features.get("canonical_name") or "")
    prefer_words: list[str] = []
    forbidden_words: list[str] = []

    if bill_canonical_name == "卫生间通风器" or any(keyword in text for keyword in ("卫生间通风器", "吊顶式通风器", "吸顶式通风器")):
        prefer_words.extend(["卫生间通风器", "天花式排气扇", "排气扇"])
        forbidden_words.extend(["风机安装", "离心式通风机"])
    elif bill_canonical_name == "暖风机" or bill_entity == "暖风机" or "暖风机" in text:
        prefer_words.extend(["暖风机"])
        forbidden_words.extend(["风机安装"])
    elif "柔性软风管" in text or "软风管" in text:
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
        if "通风器" in text:
            prefer_words.extend(["卫生间通风器", "天花式排气扇", "排气扇"])
            forbidden_words.extend(["风机安装"])
        if "风机" in text:
            prefer_words.extend(["风机", "通风机"])
        if "风口" in text or "散流器" in text:
            prefer_words.extend(["风口", "散流器"])
        if "阀" in text:
            prefer_words.extend(["阀"])
        if not prefer_words:
            return None

    scored: list[tuple[tuple[int, float, float], dict]] = []
    for cand in candidates:
        quota_name = cand.get("name", "") or ""
        cand_params = text_parser.parse(quota_name)
        cand_features = text_parser.parse_canonical(quota_name, params=cand_params)
        cand_entity = str(cand_features.get("entity") or "")
        cand_canonical_name = str(cand_features.get("canonical_name") or "")
        score = sum(8 for word in prefer_words if word and word in quota_name)
        score -= sum(8 for word in forbidden_words if word and word in quota_name)
        if bill_canonical_name and cand_canonical_name:
            if bill_canonical_name == cand_canonical_name:
                score += 12
            elif bill_canonical_name == "卫生间通风器" and cand_canonical_name == "排气扇":
                score += 4
            elif frozenset((bill_entity, cand_entity)) in {
                frozenset(("卫生间通风器", "风机")),
                frozenset(("暖风机", "风机")),
                frozenset(("排气扇", "风机")),
            }:
                score -= 12
        elif bill_entity and cand_entity:
            if bill_entity == cand_entity:
                score += 8
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
        if bill_weight is not None:
            cand_weight = cand_params.get("weight_t")
            if cand_weight is not None:
                if cand_weight == bill_weight:
                    score += 10
                elif cand_weight > bill_weight:
                    score += 4
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


def _pick_explicit_support_family_candidate_legacy(bill_text: str,
                                                   candidates: list[dict]) -> dict | None:
    text = bill_text or ""
    bill_params = text_parser.parse(text)
    bill_support_scope = str(bill_params.get("support_scope") or "")
    bill_support_action = str(bill_params.get("support_action") or "")
    bill_weight = bill_params.get("weight_t")
    bill_support_material = str(bill_params.get("support_material") or "")
    prefer_aseismic = "抗震" in text or bill_support_scope == "抗震支架"
    prefer_side = "侧向" in text
    prefer_longitudinal = "纵向" in text
    prefer_single = "单管" in text
    prefer_multi = "多管" in text
    prefer_door_frame = "门型" in text
    generic_pipe_support = any(keyword in text for keyword in ("按需制作", "一般管架"))
    if not any(keyword in text for keyword in ("支架", "支/吊架", "支吊架")):
        return None

    prefer_bridge = (
        bill_support_scope in {"桥架支架", "抗震支架"}
        or any(keyword in text for keyword in ("桥架", "电缆桥架", "桥架支撑架", "桥架侧纵向", "抗震支吊架"))
    )
    prefer_pipe = (
        bill_support_scope == "管道支架"
        or any(keyword in text for keyword in ("管道", "管架", "管道支架"))
    )
    prefer_fabrication = (
        bill_support_action in {"制作", "制作安装"}
        or any(keyword in text for keyword in ("图集", "详见图集", "制作", "单件重量", "型钢"))
    )
    if not prefer_bridge and not prefer_pipe:
        return None
    support_anchor_words = ("支架", "吊架", "支吊架", "支撑架", "管架", "抗震")
    surface_process_words = ("除锈", "刷油", "油漆", "防锈漆", "红丹", "银粉漆", "调和漆")
    support_special_shape_words = ("木垫式", "弹簧式", "侧向", "纵向", "门型", "单管", "多管")
    scored: list[tuple[tuple[int, float, float], dict]] = []
    for cand in candidates:
        quota_name = cand.get("name", "") or ""
        cand_params = text_parser.parse(quota_name)
        cand_support_scope = str(cand_params.get("support_scope") or "")
        cand_support_action = str(cand_params.get("support_action") or "")
        cand_support_material = str(cand_params.get("support_material") or "")
        has_support_anchor = (
            bool(cand_support_scope)
            or any(word in quota_name for word in support_anchor_words)
        )
        if not has_support_anchor:
            continue
        if (
            any(word in quota_name for word in surface_process_words)
            and not any(word in text for word in surface_process_words)
        ):
            # 清单是支架族但未写除锈/刷油时，压制防腐刷油定额误召回。
            continue
        score = 0
        if bill_support_scope and cand_support_scope:
            if bill_support_scope == cand_support_scope:
                score += 12
            elif (
                bill_support_scope == "抗震支架"
                and cand_support_scope in {"桥架支架", "管道支架"}
            ):
                score += 2
            else:
                score -= 12
        if bill_support_action and cand_support_action:
            if bill_support_action == cand_support_action:
                score += 10
            elif bill_support_action == "制作" and cand_support_action == "制作安装":
                score -= 4
            elif bill_support_action == "安装" and cand_support_action == "制作安装":
                score -= 2
            else:
                score -= 8
        if bill_support_material and cand_support_material:
            if bill_support_material == cand_support_material:
                score += 8
            else:
                score -= 8
        if prefer_aseismic:
            if "抗震" in quota_name:
                score += 12
            elif prefer_bridge and any(word in quota_name for word in ("桥架支撑架", "电缆桥架")):
                score += 2
            elif any(word in quota_name for word in ("一般管架", "支撑架制作", "桥架支撑架制作")):
                score -= 4
        elif "抗震" in quota_name:
            score -= 8
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
            if any(word in quota_name for word in ("桥架支撑架", "电缆桥架", "桥架支架")):
                score -= 6
            if generic_pipe_support and "一般管架" in quota_name:
                score += 10
            for word in support_special_shape_words:
                if word in quota_name and word not in text:
                    score -= 12
        if prefer_side:
            if "侧向" in quota_name:
                score += 8
            elif any(word in quota_name for word in ("纵向", "门型")):
                score -= 8
        if prefer_longitudinal:
            if "纵向" in quota_name:
                score += 8
            elif any(word in quota_name for word in ("侧向", "门型")):
                score -= 8
        if prefer_door_frame:
            if "门型" in quota_name:
                score += 8
            elif any(word in quota_name for word in ("侧向", "纵向")):
                score -= 6
        if prefer_single:
            if "单管" in quota_name:
                score += 6
            elif "多管" in quota_name:
                score -= 6
        if prefer_multi:
            if "多管" in quota_name:
                score += 6
            elif "单管" in quota_name:
                score -= 6
        if prefer_fabrication:
            if "制作" in quota_name:
                score += 10
            if any(word in quota_name for word in ("单件重量", "kg", "重量")):
                score += 6
            if any(word in quota_name for word in ("安装", "一般管架")):
                score -= 8
        if bill_weight is not None:
            cand_weight = cand_params.get("weight_t")
            if cand_weight is not None:
                if cand_weight == bill_weight:
                    score += 10
                elif cand_weight > bill_weight:
                    score += 4
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
    bill_params = text_parser.parse(text)
    bill_support_scope = str(bill_params.get("support_scope") or "")
    bill_support_action = str(bill_params.get("support_action") or "")
    bill_weight = bill_params.get("weight_t")
    bill_support_material = str(bill_params.get("support_material") or "")
    prefer_aseismic = "抗震" in text or bill_support_scope == "抗震支架"
    prefer_side = "侧向" in text
    prefer_longitudinal = "纵向" in text
    prefer_single = "单管" in text
    prefer_multi = "多管" in text
    prefer_door_frame = "门型" in text
    generic_pipe_support = any(keyword in text for keyword in ("按需制作", "一般管架"))
    prefer_equipment = bill_support_scope == "设备支架" or any(
        keyword in text for keyword in ("设备支架", "设备吊架", "设备支吊架")
    )
    prefer_duct = any(
        keyword in text for keyword in ("通风", "空调", "风管", "风口")
    )
    if not any(keyword in text for keyword in ("支架", "吊架", "支吊架", "支撑架")):
        return None

    prefer_bridge = (
        bill_support_scope == "桥架支架"
        or any(keyword in text for keyword in ("桥架", "电缆桥架", "桥架支撑架", "桥架侧纵向"))
    )
    prefer_pipe = (
        bill_support_scope == "管道支架"
        or any(keyword in text for keyword in ("管道", "管架", "管道支架", "给排水", "消防水", "喷淋", "消火栓", "水管"))
    )
    prefer_fabrication = (
        bill_support_action in {"制作", "制作安装"}
        or any(keyword in text for keyword in ("图集", "详见图集", "制作", "单件重量", "型钢"))
    )
    if not prefer_bridge and not prefer_pipe and not prefer_equipment and not prefer_duct and not prefer_aseismic:
        return None

    support_anchor_words = ("支架", "吊架", "支吊架", "支撑架", "管架", "抗震")
    surface_process_words = ("除锈", "刷油", "油漆", "防锈漆", "红丹", "银粉漆", "调和漆")
    support_special_shape_words = ("木垫式", "弹簧式", "侧向", "纵向", "门型", "单管", "多管")
    support_action_words = ("制作", "安装", "制作安装", "制安")
    equipment_support_words = ("设备支架", "设备吊架", "设备及部件支架")
    bridge_support_words = ("桥架支撑架", "电缆桥架")
    pipe_support_words = ("管架", "管道支架", "管道支吊架", "吊托支架", "支吊架")
    instrument_support_words = ("仪表支架", "仪表支吊架")

    scored: list[tuple[tuple[int, float, float], dict]] = []
    for cand in candidates:
        quota_name = cand.get("name", "") or ""
        cand_params = text_parser.parse(quota_name)
        cand_support_scope = str(cand_params.get("support_scope") or "")
        cand_support_action = str(cand_params.get("support_action") or "")
        cand_support_material = str(cand_params.get("support_material") or "")
        has_support_anchor = (
            bool(cand_support_scope)
            or any(word in quota_name for word in support_anchor_words)
        )
        if not has_support_anchor:
            continue

        candidate_is_surface_process = (
            any(word in quota_name for word in surface_process_words)
            and not any(word in quota_name for word in support_action_words)
        )
        if candidate_is_surface_process:
            continue
        if (
            any(word in quota_name for word in surface_process_words)
            and not any(word in text for word in surface_process_words)
        ):
            continue

        score = 0
        if bill_support_scope and cand_support_scope:
            if bill_support_scope == cand_support_scope:
                score += 12
            elif (
                bill_support_scope == "抗震支架"
                and cand_support_scope in {"桥架支架", "管道支架"}
            ):
                score += 2
            else:
                score -= 12
        if bill_support_action and cand_support_action:
            if bill_support_action == cand_support_action:
                score += 10
            elif bill_support_action == "制作" and cand_support_action == "制作安装":
                score -= 4
            elif bill_support_action == "安装" and cand_support_action == "制作安装":
                score -= 2
            else:
                score -= 8
        if bill_support_material and cand_support_material:
            if bill_support_material == cand_support_material:
                score += 8
            else:
                score -= 8

        if prefer_aseismic:
            if "抗震" in quota_name:
                score += 12
            elif prefer_bridge and any(word in quota_name for word in bridge_support_words):
                score += 2
            elif (prefer_pipe or prefer_duct) and any(word in quota_name for word in pipe_support_words):
                score += 2
            elif any(word in quota_name for word in ("一般管架", "支撑架制作", "桥架支撑架制作")):
                score -= 4
        elif "抗震" in quota_name:
            score -= 8

        if prefer_bridge:
            if any(word in quota_name for word in bridge_support_words):
                score += 12
            if any(word in quota_name for word in ("支架制作", "支架安装")) and "桥架" in quota_name:
                score += 6
            if any(word in quota_name for word in pipe_support_words):
                score -= 10
            if any(word in quota_name for word in equipment_support_words):
                score -= 8
        elif prefer_pipe:
            if any(word in quota_name for word in pipe_support_words):
                score += 8
            if any(word in quota_name for word in bridge_support_words):
                score -= 10
            if generic_pipe_support and "一般管架" in quota_name:
                score += 10
            for word in support_special_shape_words:
                if word in quota_name and word not in text:
                    score -= 12
            if any(word in quota_name for word in instrument_support_words):
                score -= 10
            if any(word in quota_name for word in equipment_support_words):
                score -= 10
        elif prefer_duct:
            if any(word in quota_name for word in ("支吊架", "吊托支架", "风管支吊架")):
                score += 8
            if any(word in quota_name for word in bridge_support_words):
                score -= 10
            if any(word in quota_name for word in instrument_support_words):
                score -= 10
            if any(word in quota_name for word in equipment_support_words):
                score -= 8
        elif prefer_equipment:
            if any(word in quota_name for word in equipment_support_words):
                score += 12
            if any(word in quota_name for word in pipe_support_words + bridge_support_words):
                score -= 10
            if any(word in quota_name for word in ("单件重量", "每个支架重量", "每组重量", "kg", "重量")):
                score += 6

        if prefer_side:
            if "侧向" in quota_name:
                score += 8
            elif any(word in quota_name for word in ("纵向", "门型")):
                score -= 8
        if prefer_longitudinal:
            if "纵向" in quota_name:
                score += 8
            elif any(word in quota_name for word in ("侧向", "门型")):
                score -= 8
        if prefer_door_frame:
            if "门型" in quota_name:
                score += 8
            elif any(word in quota_name for word in ("侧向", "纵向")):
                score -= 6
        if prefer_single:
            if "单管" in quota_name:
                score += 6
            elif "多管" in quota_name:
                score -= 6
        if prefer_multi:
            if "多管" in quota_name:
                score += 6
            elif "单管" in quota_name:
                score -= 6
        if prefer_fabrication:
            if "制作" in quota_name:
                score += 10
            if any(word in quota_name for word in ("单件重量", "kg", "重量")):
                score += 6
            if any(word in quota_name for word in ("安装", "一般管架")):
                score -= 8
        if bill_weight is not None:
            cand_weight = cand_params.get("weight_t")
            if cand_weight is not None:
                if cand_weight == bill_weight:
                    score += 10
                elif cand_weight > bill_weight:
                    score += 4
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


def _promote_explicit_distribution_box_candidate(item: dict,
                                                 candidates: list[dict]) -> list[dict]:
    if not candidates:
        return []

    bill_text = " ".join(
        part for part in (
            (item or {}).get("name", ""),
            (item or {}).get("description", ""),
        ) if part
    )
    picked = _pick_explicit_distribution_box_candidate(bill_text, candidates)
    if not picked:
        return list(candidates)

    picked_id = str(picked.get("quota_id", "")).strip()
    if not picked_id:
        return list(candidates)

    reordered: list[dict] = []
    chosen = None
    for candidate in candidates:
        if str(candidate.get("quota_id", "")).strip() == picked_id:
            chosen = candidate
        else:
            reordered.append(candidate)
    if chosen is None:
        return list(candidates)
    return [chosen, *reordered]


def _pick_explicit_insulation_family_candidate(bill_text: str,
                                               candidates: list[dict]) -> dict | None:
    text = bill_text or ""
    if not any(keyword in text for keyword in ("绝热", "保温", "保冷", "防潮层", "保护层")):
        return None

    bill_params = text_parser.parse(text)
    bill_thickness = bill_params.get("thickness")
    prefer_pipe = any(keyword in text for keyword in (
        "管道", "给水", "排水", "采暖", "消防", "风管", "阀门", "法兰", "弯头",
    ))
    prefer_equipment = any(keyword in text for keyword in (
        "设备", "容器", "储罐", "塔器", "换热器", "机组",
    ))
    insulation_words = ("绝热", "保温", "保冷", "防潮层", "保护层")
    pipe_anchor_words = ("管道", "风管", "管壳", "弯头", "法兰", "阀门", "给排水")
    equipment_anchor_words = ("设备", "立式设备", "卧式设备", "容器", "储罐", "塔器", "换热器")

    scored: list[tuple[tuple[int, float, float], dict]] = []
    for cand in candidates:
        quota_name = cand.get("name", "") or ""
        if not any(word in quota_name for word in insulation_words):
            continue
        cand_params = text_parser.parse(quota_name)
        score = sum(5 for word in insulation_words if word in quota_name)

        if prefer_pipe:
            if any(word in quota_name for word in pipe_anchor_words):
                score += 12
            if any(word in quota_name for word in equipment_anchor_words):
                score -= 12
        if prefer_equipment:
            if any(word in quota_name for word in equipment_anchor_words):
                score += 10
            if any(word in quota_name for word in pipe_anchor_words):
                score -= 10

        if "防潮层" in text and "防潮层" in quota_name:
            score += 6
        if "保护层" in text and "保护层" in quota_name:
            score += 6

        if bill_thickness is not None:
            cand_thickness = cand_params.get("thickness")
            if cand_thickness is not None:
                if cand_thickness == bill_thickness:
                    score += 8
                elif cand_thickness > bill_thickness:
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
    sanitary_mount_mode = str(bill_params.get("sanitary_mount_mode") or "")
    sanitary_flush_mode = str(bill_params.get("sanitary_flush_mode") or "")
    sanitary_water_mode = str(bill_params.get("sanitary_water_mode") or "")
    sanitary_nozzle_mode = str(bill_params.get("sanitary_nozzle_mode") or "")
    sanitary_tank_mode = str(bill_params.get("sanitary_tank_mode") or "")
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
        cand_params = text_parser.parse(quota_name)
        cand_mount_mode = str(cand_params.get("sanitary_mount_mode") or "")
        cand_flush_mode = str(cand_params.get("sanitary_flush_mode") or "")
        cand_water_mode = str(cand_params.get("sanitary_water_mode") or "")
        cand_nozzle_mode = str(cand_params.get("sanitary_nozzle_mode") or "")
        cand_tank_mode = str(cand_params.get("sanitary_tank_mode") or "")
        score = sum(8 for word in expected_words if word and word in quota_name)
        score -= sum(8 for word in forbidden_words if word and word in quota_name)
        score += sum(3 for word in prefer_words if word and word in quota_name)
        if sanitary_mount_mode and cand_mount_mode:
            if sanitary_mount_mode == cand_mount_mode:
                score += 10
            else:
                score -= 10
        if sanitary_flush_mode and cand_flush_mode:
            if sanitary_flush_mode == cand_flush_mode:
                score += 10
            else:
                score -= 10
        if sanitary_water_mode and cand_water_mode:
            if sanitary_water_mode == cand_water_mode:
                score += 10
            else:
                score -= 10
        if sanitary_nozzle_mode and cand_nozzle_mode:
            if sanitary_nozzle_mode == cand_nozzle_mode:
                score += 8
            else:
                score -= 8
        if sanitary_tank_mode and cand_tank_mode:
            if sanitary_tank_mode == cand_tank_mode:
                score += 10
            else:
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


def _pick_explicit_lamp_family_candidate(bill_text: str,
                                         candidates: list[dict]) -> dict | None:
    text = bill_text or ""
    if "灯" not in text or any(keyword in text for keyword in ("扬声器", "广播")):
        return None

    bill_params = text_parser.parse(text)
    lamp_type = str(bill_params.get("lamp_type") or "")
    install_method = str(bill_params.get("install_method") or "")
    if not lamp_type and not install_method:
        return None

    incompatible_map = {
        "吸顶灯": {"筒灯", "灯带", "壁灯", "标志灯", "应急灯", "轮廓灯", "投光灯"},
        "筒灯": {"吸顶灯", "灯带", "壁灯", "标志灯", "应急灯", "轮廓灯", "投光灯"},
        "灯带": {"吸顶灯", "筒灯", "壁灯", "标志灯", "应急灯"},
        "壁灯": {"吸顶灯", "筒灯", "灯带"},
        "标志灯": {"吸顶灯", "筒灯", "灯带", "壁灯"},
        "应急灯": {"吸顶灯", "筒灯", "灯带", "壁灯"},
        "轮廓灯": {"吸顶灯", "筒灯", "壁灯"},
        "投光灯": {"吸顶灯", "筒灯", "壁灯"},
    }

    scored: list[tuple[tuple[int, float, float], dict]] = []
    for cand in candidates:
        quota_name = cand.get("name", "") or ""
        cand_params = text_parser.parse(quota_name)
        cand_lamp_type = str(cand_params.get("lamp_type") or "")
        cand_install_method = str(cand_params.get("install_method") or "")
        score = 0
        if lamp_type and cand_lamp_type:
            if lamp_type == cand_lamp_type:
                score += 12
            elif cand_lamp_type in incompatible_map.get(lamp_type, set()):
                score -= 10
        if install_method and cand_install_method:
            if install_method == cand_install_method:
                score += 8
            elif install_method in {"吊装", "吸顶"} and cand_install_method in {"吊装", "吸顶"}:
                score += 2
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


def _pick_explicit_button_broadcast_candidate(bill_text: str,
                                              candidates: list[dict]) -> dict | None:
    text = bill_text or ""
    if not any(keyword in text for keyword in ("扬声器", "按钮")):
        return None

    bill_params = text_parser.parse(text)
    bill_features = text_parser.parse_canonical(text, params=bill_params)
    install_method = str(bill_params.get("install_method") or "")
    bill_entity = str(bill_features.get("entity") or "")
    scored: list[tuple[tuple[int, float, float], dict]] = []

    for cand in candidates:
        quota_name = cand.get("name", "") or ""
        cand_params = text_parser.parse(quota_name)
        cand_features = text_parser.parse_canonical(quota_name, params=cand_params)
        cand_install_method = str(cand_params.get("install_method") or "")
        cand_entity = str(cand_features.get("entity") or "")
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
            if install_method and cand_install_method:
                if install_method == cand_install_method:
                    score += 8
                elif {install_method, cand_install_method} == {"挂墙", "吸顶"}:
                    score -= 8
            if bill_entity and cand_entity:
                if bill_entity == cand_entity:
                    score += 6
                else:
                    score -= 6

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
    prefer_flexible_joint = False
    prefer_pipe_clamp = False

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
    elif any(keyword in text for keyword in ("软接头", "伸缩节", "柔性接头", "橡胶接头")):
        prefer_flexible_joint = True
        expected_words.extend(["软接头", "伸缩节", "柔性接头", "橡胶接头", "柔性接口"])
        forbidden_words.extend([
            "法兰安装", "螺纹法兰安装", "法兰阀门",
            "塑料给水管", "塑料排水管", "给水管", "排水管",
        ])
        if "法兰" in text:
            prefer_words.append("法兰")
        if "螺纹" in text or "丝扣" in text:
            prefer_words.append("螺纹")
    elif any(keyword in text for keyword in ("塑料管卡", "管卡", "管夹", "卡箍", "管箍")):
        prefer_pipe_clamp = True
        expected_words.extend(["管卡", "管夹", "卡箍", "管箍"])
        forbidden_words.extend([
            "塑料给水管", "塑料排水管", "给水管", "排水管",
            "钢管", "管道安装",
        ])
        if "塑料" in text:
            prefer_words.append("塑料")
    elif any(keyword in text for keyword in ("喇叭口", "溢水喇叭口")):
        expected_words.extend(["喇叭口"])
        forbidden_words.extend(["广播喇叭", "音箱"])
    else:
        return None

    scored: list[tuple[tuple[int, float, float], dict]] = []
    for cand in candidates:
        quota_name = cand.get("name", "") or ""
        if prefer_flexible_joint and not any(word in quota_name for word in ("软接头", "伸缩节", "柔性", "橡胶接头")):
            continue
        if prefer_pipe_clamp and not any(word in quota_name for word in ("管卡", "管夹", "卡箍", "管箍")):
            continue
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
    upper_text = text.upper()
    if "倒流防止器" in text:
        return None
    if not any(keyword in text for keyword in (
        "螺纹阀门", "焊接法兰阀门", "法兰阀门", "螺纹法兰阀门",
        "碳钢阀门", "塑料阀门", "PPR阀门", "PP-R阀门",
    )):
        return None
    if any(keyword in text for keyword in (
        "风阀", "防火阀", "调节阀", "多叶调节阀", "定风量阀", "人防", "密闭阀",
    )):
        return None

    bill_params = text_parser.parse(text)
    bill_dn = bill_params.get("dn")
    bill_connection = bill_params.get("connection")
    bill_valve_family = str(bill_params.get("valve_connection_family") or "")
    bill_valve_type = str(bill_params.get("valve_type") or "")

    prefer_words = []
    forbidden_words = [
        "塑料法兰",
        "风管", "防火阀", "调节阀", "多叶", "排烟阀", "定风量阀",
        "塑料给水管", "塑料排水管", "给水管", "排水管",
    ]

    if "碳钢阀门" in text:
        prefer_words.extend(["阀门", "碳钢"])
    elif "塑料阀门" in text or (("PPR" in upper_text or "PP-R" in upper_text) and "阀" in text):
        prefer_words.extend(["阀门", "塑料"])
        forbidden_words.extend(["法兰安装", "螺纹法兰安装"])
    elif "螺纹法兰阀门" in text:
        prefer_words.extend(["法兰阀门", "阀门"])
        forbidden_words.extend(["法兰安装", "螺纹法兰安装"])
    elif "焊接法兰阀门" in text or "法兰阀门" in text:
        prefer_words.extend(["法兰阀门", "阀门"])
        forbidden_words.extend(["法兰安装", "螺纹法兰安装", "对焊阀门", "对焊阀安装"])
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
        if "阀" not in quota_name:
            continue
        cand_params = text_parser.parse(quota_name)
        cand_valve_family = str(cand_params.get("valve_connection_family") or "")
        cand_valve_type = str(cand_params.get("valve_type") or "")
        score = 0
        if "阀门" in quota_name:
            score += 8
        if bill_valve_family and cand_valve_family:
            if bill_valve_family == cand_valve_family:
                score += 10
            else:
                score -= 10
        score += sum(6 for word in prefer_words if word and word in quota_name)
        score -= sum(10 for word in forbidden_words if word and word in quota_name)
        if bill_valve_type and cand_valve_type:
            if bill_valve_type == cand_valve_type:
                score += 8
            else:
                score -= 8
        if bill_dn is not None:
            cand_dn = cand_params.get("dn")
            if cand_dn is not None:
                if cand_dn == bill_dn:
                    score += 10
                elif cand_dn > bill_dn:
                    gap = cand_dn - bill_dn
                    score += max(1, 4 - gap / 25)
                else:
                    score -= 10
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

def _ensure_item_feature_context(item: dict):
    """Lazily restore feature context for retry/replay callers that bypass preprocessing."""
    if not isinstance(item, dict):
        return

    context_prior = item.get("context_prior")
    if not isinstance(context_prior, dict) or not context_prior:
        context_prior = build_context_prior(item)
        item["context_prior"] = context_prior

    full_text = f"{item.get('name', '')} {item.get('description', '') or ''}".strip()
    params = item.get("params")
    if not isinstance(params, dict) or not params:
        params = text_parser.parse(full_text)
        item["params"] = params

    canonical_features = item.get("canonical_features")
    if isinstance(canonical_features, dict) and canonical_features:
        return

    item["canonical_features"] = text_parser.parse_canonical(
        full_text,
        specialty=item.get("specialty", ""),
        context_prior=context_prior,
        params=params,
    )


def _build_item_context(item: dict) -> dict:
    """构建匹配所需的清单上下文（名称/查询文本/单位/工程量等）。"""
    _ensure_item_feature_context(item)
    name = item.get("name", "")
    desc = item.get("description", "") or ""
    section = item.get("section", "") or ""
    original_name = item.get("original_name", name)
    canonical_features = item.get("canonical_features") or {}
    context_prior = item.get("context_prior") or {}
    plugin_hints = resolve_plugin_hints(
        province=str(item.get("_resolved_province") or item.get("province") or ""),
        item=item,
        canonical_features=canonical_features,
    )
    if plugin_hints:
        context_prior = dict(context_prior)
        merged_context_hints = list(context_prior.get("context_hints") or [])
        merged_context_hints.extend(plugin_hints.get("preferred_specialties", []) or [])
        context_prior["context_hints"] = list(dict.fromkeys(
            str(value).strip() for value in merged_context_hints if str(value).strip()
        ))[:5]
        context_prior["plugin_hints"] = plugin_hints
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

    raw_query = f"{query_name} {desc}".strip()
    if not raw_query:
        raw_query = f"{name} {desc}".strip()

    # 从分项标题/Sheet名推断用途关键词，注入到full_query中
    # 这样param_validator的介质冲突检查能利用section的方向信息
    # 只在清单文本本身不含这些关键词时才注入，避免重复
    route_query = raw_query
    if section:
        _usage_hint = _extract_usage_from_section(section)
        if _usage_hint and _usage_hint not in route_query:
            route_query = f"{route_query} {_usage_hint}"

    canonical_name = canonical_features.get("canonical_name", "")
    canonical_system = canonical_features.get("system", "")
    if canonical_name and canonical_name not in route_query:
        route_query = f"{route_query} {canonical_name}".strip()
    if canonical_system and canonical_system not in route_query:
        route_query = f"{route_query} {canonical_system}".strip()

    validation_query = route_query.strip()
    canonical_query = {
        "raw_query": raw_query.strip(),
        "route_query": route_query.strip(),
        "validation_query": validation_query,
        "search_query": search_query.strip(),
        "normalized_query": normalize_bill_text(original_name, desc),
    }

    query_route = build_query_route_profile(
        canonical_query["route_query"],
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
        "canonical_query": canonical_query,
        "full_query": canonical_query["validation_query"],
        "normalized_query": canonical_query["normalized_query"],
        "search_query": canonical_query["search_query"],
        "canonical_features": canonical_features,
        "context_prior": context_prior,
        "plugin_hints": plugin_hints,
        "query_route": query_route,
        "item": item,  # L5：供跨省预热读取 _cross_province_hints
        "input_gate": input_gate,
    }


def _build_classification(item: dict, name: str, desc: str, section: str,
                          province: str = None) -> dict:
    """获取并标准化专业分类结果。"""
    primary = str(item.get("specialty") or "").strip()
    fallbacks = [
        str(book).strip()
        for book in (item.get("specialty_fallbacks") or [])
        if str(book).strip()
    ]
    if primary and not fallbacks:
        fallbacks = list(BORROW_PRIORITY.get(primary, []))
    classification = {
        "primary": primary,
        "fallbacks": fallbacks,
    }
    if primary:
        classification["confidence"] = "high"
        classification["reason"] = "item_specialty"
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

    def _try_match(books: list[str] | None):
        active_books = [b for b in (books or []) if b]
        result = rule_validator.match_by_rules(
            full_query,
            item,
            clean_query=search_query,
            books=active_books if active_books else None,
        )
        if not result:
            return None, active_books
        result = _check_rule_subtype_conflict(result, full_query)
        if not result:
            return None, active_books
        return result, active_books

    primary_book = classification.get("primary")
    fallback_books = [b for b in classification.get("fallbacks", []) if b]

    rule_result = None
    rule_books: list[str] = []
    if primary_book:
        rule_result, rule_books = _try_match([primary_book])

    if not rule_result:
        expanded_books = [primary_book] + fallback_books if primary_book else fallback_books
        rule_result, rule_books = _try_match(expanded_books)

    if not rule_result:
        return None, None

    # 品类一致性检查：清单明确写了子类型（如"刚性防水套管"），
    # 但规则匹配到的定额不含该子类型（如匹配到"成品防火套管"），
    # 则丢弃规则匹配结果，让搜索来处理（搜索能更精准地按名称匹配）
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
            family_aligned=infer_confidence_family_alignment(alt),
            family_hard_conflict=bool(alt.get("family_gate_hard_conflict", False)),
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


def _quota_book_from_id(quota_id: str) -> str:
    quota_id = str(quota_id or "").strip()
    if len(quota_id) >= 2 and quota_id[0] == "C" and quota_id[1].isalpha():
        letter_map = {'A': 'C1', 'B': 'C2', 'C': 'C3', 'D': 'C4',
                      'E': 'C5', 'F': 'C6', 'G': 'C7', 'H': 'C8',
                      'I': 'C9', 'J': 'C10', 'K': 'C11', 'L': 'C12'}
        return letter_map.get(quota_id[1], "")
    match = re.match(r"(C\d+)-", quota_id)
    if match:
        return match.group(1)
    match = re.match(r"(\d+)-", quota_id)
    if match:
        return f"C{match.group(1)}"
    return ""


def _compute_plugin_candidate_score(item: dict, candidate: dict) -> tuple[float, list[str]]:
    plugin_hints = dict((item or {}).get("plugin_hints") or {})
    if not plugin_hints:
        return 0.0, []

    score = 0.0
    reasons: list[str] = []
    preferred_books = {str(value or "").strip() for value in plugin_hints.get("preferred_books", []) if str(value or "").strip()}
    preferred_quota_names = [str(value or "").strip() for value in plugin_hints.get("preferred_quota_names", []) if str(value or "").strip()]
    avoided_quota_names = [str(value or "").strip() for value in plugin_hints.get("avoided_quota_names", []) if str(value or "").strip()]

    quota_name = str(candidate.get("name", "") or "")
    quota_book = _quota_book_from_id(candidate.get("quota_id", ""))
    if preferred_books:
        if quota_book in preferred_books:
            score += 0.08
            reasons.append(f"book:{quota_book}")

    if preferred_quota_names and any(name in quota_name for name in preferred_quota_names):
        score += 0.12
        reasons.append("preferred_name")

    if avoided_quota_names and any(name in quota_name for name in avoided_quota_names):
        score -= 0.12
        reasons.append("avoided_name")

    return score, reasons


def _apply_plugin_candidate_biases(item: dict, candidates: list[dict]) -> list[dict]:
    if not candidates:
        return []

    biased: list[dict] = []
    has_plugin_signal = False
    for candidate in candidates:
        updated = dict(candidate)
        plugin_score, plugin_reasons = _compute_plugin_candidate_score(item, updated)
        updated["plugin_score"] = plugin_score
        if plugin_reasons:
            updated["plugin_reasons"] = plugin_reasons
            has_plugin_signal = True
        biased.append(updated)

    if not has_plugin_signal:
        return biased

    for candidate in biased:
        candidate["rank_score"] = compute_candidate_rank_score(candidate)
    biased.sort(
        key=compute_candidate_sort_key,
        reverse=True,
    )
    return biased


def _apply_plugin_route_gate(item: dict, candidates: list[dict]) -> tuple[list[dict], dict]:
    plugin_hints = dict((item or {}).get("plugin_hints") or {})
    preferred_books = {
        str(value or "").strip()
        for value in plugin_hints.get("preferred_books", []) or []
        if str(value or "").strip()
    }
    strict_gate = bool(plugin_hints.get("strict_preferred_books"))
    if not candidates or not preferred_books:
        return list(candidates or []), {
            "applied": False,
            "reason": "no_preferred_books",
            "preferred_books": sorted(preferred_books),
        }
    if not strict_gate:
        return [dict(candidate) for candidate in candidates], {
            "applied": False,
            "reason": "soft_preferred_books_only",
            "preferred_books": sorted(preferred_books),
        }

    preferred: list[dict] = []
    fallback: list[dict] = []
    for candidate in candidates:
        quota_book = _quota_book_from_id(candidate.get("quota_id", ""))
        updated = dict(candidate)
        updated["plugin_route_book"] = quota_book
        if quota_book in preferred_books:
            preferred.append(updated)
        else:
            fallback.append(updated)

    if not preferred:
        return [dict(candidate) for candidate in candidates], {
            "applied": False,
            "reason": "no_matching_book_candidates",
            "preferred_books": sorted(preferred_books),
        }

    if len(preferred) < 2 and len(candidates) <= 3:
        return preferred + fallback, {
            "applied": False,
            "reason": "preferred_candidates_too_few",
            "preferred_books": sorted(preferred_books),
            "preferred_count": len(preferred),
        }

    gated = preferred + fallback[:2]
    return gated, {
        "applied": True,
        "reason": "preferred_books_gate",
        "preferred_books": sorted(preferred_books),
        "preferred_count": len(preferred),
        "fallback_kept": len(gated) - len(preferred),
    }


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
    valid_candidates, plugin_route_gate = _apply_plugin_route_gate(item, valid_candidates)
    valid_candidates = _apply_plugin_candidate_biases(item, valid_candidates)
    if candidates and not valid_candidates:
        logger.warning("候选列表存在，但全部缺少quota_id/name，按无匹配处理")

    if valid_candidates:
        matched_candidates = [c for c in valid_candidates if c.get("param_match", True)]
        if matched_candidates:
            matched_candidates, arbitration = arbitrate_candidates(
                item, matched_candidates, route_profile=item.get("query_route")
            )
            matched_candidates = _promote_explicit_distribution_box_candidate(
                item, matched_candidates
            )
            # 规则审核前置：跳过类别明显不匹配的候选
            best = matched_candidates[0] if matched_candidates else None
            if not best:
                best = _pick_category_safe_candidate(item, matched_candidates)
            if best:
                decision_candidates = [best] + [c for c in matched_candidates if c is not best]
                param_score = best.get("param_score", 0.5)
                best_composite = compute_candidate_rank_score(best)
                others = [c for c in matched_candidates if c is not best]
                second_composite = max((compute_candidate_rank_score(c) for c in others), default=0)
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
                explanation = "显式支架项未找到安全近邻候选，转未匹配"
        else:
            arbitration = {
                "applied": False,
                "route": str((item.get("query_route") or {}).get("route") or ""),
                "reason": "no_param_matched_candidates",
            }
            best = valid_candidates[0] if valid_candidates else None
            if not best:
                best = _pick_category_safe_candidate(item, valid_candidates)
            if best:
                decision_candidates = [best] + [c for c in valid_candidates if c is not best]
                param_score = best.get("param_score", 0.0)
                confidence = calculate_confidence(
                    param_score, param_match=False,
                    name_bonus=best.get("name_bonus", 0.0),
                    rerank_score=best.get("rerank_score", best.get("hybrid_score", 0.0)),
                    family_aligned=infer_confidence_family_alignment(best),
                    family_hard_conflict=bool(best.get("family_gate_hard_conflict", False)),
                    candidates_count=len(valid_candidates),
                    is_ambiguous_short=item.get("_is_ambiguous_short", False),
                )
                explanation = f"参数不完全匹配(回退候选): {best.get('param_detail', '')}"
                reasoning_decision = analyze_ambiguity(
                    decision_candidates,
                    route_profile=item.get("query_route"),
                    arbitration=arbitration,
                ).as_dict()
            else:
                explanation = "显式支架项未找到安全近邻候选，转未匹配"

    # 收集所有候选定额ID（供benchmark统计"正确答案是否在候选中"）
    all_candidate_ids = [
        str(c.get("quota_id", "")).strip()
        for c in valid_candidates
        if str(c.get("quota_id", "")).strip()
    ]

    quotas = [{
        "quota_id": best["quota_id"],
        "name": best["name"],
        "unit": best.get("unit", ""),
        "reason": explanation,
        "reasoning": summarize_candidate_reasoning(best),
        "db_id": best.get("id"),
    }] if best else []
    supplemental_quotas = item.get("_supplemental_quotas") if isinstance(item, dict) else []
    if quotas and isinstance(supplemental_quotas, list):
        seen_ids = {str(quota.get("quota_id", "")).strip() for quota in quotas if str(quota.get("quota_id", "")).strip()}
        for quota in supplemental_quotas:
            quota_id = str((quota or {}).get("quota_id", "")).strip()
            quota_name = str((quota or {}).get("name", "")).strip()
            if not quota_id or not quota_name or quota_id in seen_ids:
                continue
            quotas.append(dict(quota))
            seen_ids.add(quota_id)

    result = {
        "bill_item": item,
        "quotas": quotas,
        "confidence": confidence,
        "explanation": explanation,
        "candidates_count": len(valid_candidates),
        "all_candidate_ids": all_candidate_ids,
        "match_source": "search",
        "arbitration": arbitration,
        "plugin_route_gate": plugin_route_gate,
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
        plugin_route_gate=plugin_route_gate,
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
        result["no_match_reason"] = explanation or "搜索无匹配结果"
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
    if province and not item.get("_resolved_province"):
        item["_resolved_province"] = province
    ctx = _build_item_context(item)
    item["query_route"] = ctx.get("query_route")
    item["plugin_hints"] = ctx.get("plugin_hints") or {}
    item["context_prior"] = ctx.get("context_prior") or item.get("context_prior") or {}
    item["canonical_query"] = ctx.get("canonical_query") or {}
    name = ctx["name"]
    desc = ctx["desc"]
    canonical_query = ctx.get("canonical_query") or {}
    full_query = canonical_query.get("validation_query") or ctx["full_query"]
    search_query = canonical_query.get("search_query") or ctx["search_query"]
    normalized_query = canonical_query.get("normalized_query") or ctx["normalized_query"]
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
