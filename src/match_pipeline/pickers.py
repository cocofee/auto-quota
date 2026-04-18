# -*- coding: utf-8 -*-
"""Explicit candidate pickers and fallback selectors."""

from loguru import logger

from src.explicit_equipment_family_pickers import _pick_explicit_equipment_family_candidate
from src.explicit_framework_family_pickers import (
    _pick_explicit_cable_family_candidate,
    _pick_explicit_distribution_box_candidate,
    _pick_explicit_fire_device_candidate,
    _pick_explicit_motor_family_candidate,
    _pick_explicit_network_device_candidate,
    _pick_explicit_plumbing_accessory_candidate,
    _pick_explicit_support_family_candidate,
    _pick_explicit_valve_family_candidate,
    _pick_explicit_ventilation_family_candidate,
    _pick_explicit_wiring_family_candidate,
)
from src.explicit_mep_family_pickers import (
    _pick_explicit_bridge_family_candidate,
    _pick_explicit_conduit_family_candidate,
    _pick_explicit_plastic_sleeve_candidate,
    _pick_explicit_sleeve_family_candidate,
)
from src.explicit_pipe_family_pickers import (
    _pick_explicit_cast_iron_pipe_candidate,
    _pick_explicit_insulation_family_candidate,
    _pick_explicit_pipe_run_candidate,
)
from src.explicit_terminal_family_pickers import (
    _pick_explicit_button_broadcast_candidate,
    _pick_explicit_lamp_family_candidate,
    _pick_explicit_sanitary_family_candidate,
)
from src.policy_engine import PolicyEngine
from src.review_checkers import check_category_mismatch, extract_description_lines
from src.text_parser import parser as text_parser

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


def _guard_explicit_candidate(item: dict,
                              top_candidate: dict,
                              explicit_candidate: dict | None,
                              hybrid_margin: float = 0.005) -> dict | None:
    if explicit_candidate is None:
        return None
    if not isinstance(top_candidate, dict) or not top_candidate:
        return explicit_candidate

    top_score = _safe_candidate_hybrid_score(top_candidate)
    explicit_score = _safe_candidate_hybrid_score(explicit_candidate)
    resolved_margin = float(
        PolicyEngine.get_picker_threshold("explicit_hybrid_margin", hybrid_margin)
    )
    if (top_score - explicit_score) > resolved_margin:
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
        return _guard_explicit_candidate(item, top_candidate, cable_candidate)

    wiring_candidate = _pick_explicit_wiring_family_candidate(bill_text, candidates)
    if wiring_candidate is not None:
        return _guard_explicit_candidate(item, top_candidate, wiring_candidate)

    cast_iron_pipe_candidate = _pick_explicit_cast_iron_pipe_candidate(bill_text, candidates)
    if cast_iron_pipe_candidate is not None:
        return _guard_explicit_candidate(item, top_candidate, cast_iron_pipe_candidate)

    pipe_run_candidate = _pick_explicit_pipe_run_candidate(bill_text, candidates)
    if pipe_run_candidate is not None:
        return _guard_explicit_candidate(item, top_candidate, pipe_run_candidate)

    sleeve_candidate = _pick_explicit_plastic_sleeve_candidate(bill_text, candidates)
    if sleeve_candidate is not None:
        return _guard_explicit_candidate(item, top_candidate, sleeve_candidate)

    general_sleeve_candidate = _pick_explicit_sleeve_family_candidate(bill_text, candidates)
    if general_sleeve_candidate is not None:
        return _guard_explicit_candidate(item, top_candidate, general_sleeve_candidate)

    conduit_candidate = _pick_explicit_conduit_family_candidate(bill_text, candidates)
    if conduit_candidate is not None:
        return _guard_explicit_candidate(item, top_candidate, conduit_candidate)

    bridge_candidate = _pick_explicit_bridge_family_candidate(bill_text, candidates)
    if bridge_candidate is not None:
        return _guard_explicit_candidate(item, top_candidate, bridge_candidate)

    distribution_box_candidate = _pick_explicit_distribution_box_candidate(bill_text, candidates)
    if distribution_box_candidate is not None:
        return _guard_explicit_candidate(item, top_candidate, distribution_box_candidate)

    ventilation_candidate = _pick_explicit_ventilation_family_candidate(bill_text, candidates)
    if ventilation_candidate is not None:
        return _guard_explicit_candidate(item, top_candidate, ventilation_candidate)

    support_candidate = _pick_explicit_support_family_candidate(bill_text, candidates)
    if support_candidate is not None:
        return _guard_explicit_candidate(item, top_candidate, support_candidate)
    if _should_force_conservative_support_fallback(item, bill_text):
        support_fallback_candidate = _pick_safe_support_fallback_candidate(item, candidates)
        if support_fallback_candidate is not None:
            return support_fallback_candidate
        return None

    insulation_candidate = _pick_explicit_insulation_family_candidate(bill_text, candidates)
    if insulation_candidate is not None:
        return _guard_explicit_candidate(item, top_candidate, insulation_candidate)

    motor_candidate = _pick_explicit_motor_family_candidate(bill_text, candidates)
    if motor_candidate is not None:
        return _guard_explicit_candidate(item, top_candidate, motor_candidate)

    sanitary_candidate = _pick_explicit_sanitary_family_candidate(bill_text, candidates)
    if sanitary_candidate is not None:
        return sanitary_candidate

    equipment_candidate = _pick_explicit_equipment_family_candidate(bill_text, candidates)
    if equipment_candidate is not None:
        return _guard_explicit_candidate(item, top_candidate, equipment_candidate)

    lamp_candidate = _pick_explicit_lamp_family_candidate(bill_text, candidates)
    if lamp_candidate is not None:
        return _guard_explicit_candidate(item, top_candidate, lamp_candidate)

    button_broadcast_candidate = _pick_explicit_button_broadcast_candidate(bill_text, candidates)
    if button_broadcast_candidate is not None:
        return _guard_explicit_candidate(item, top_candidate, button_broadcast_candidate)

    plumbing_accessory_candidate = _pick_explicit_plumbing_accessory_candidate(bill_text, candidates)
    if plumbing_accessory_candidate is not None:
        return _guard_explicit_candidate(item, top_candidate, plumbing_accessory_candidate)

    valve_candidate = _pick_explicit_valve_family_candidate(bill_text, candidates)
    if valve_candidate is not None:
        return _guard_explicit_candidate(item, top_candidate, valve_candidate)

    fire_candidate = _pick_explicit_fire_device_candidate(bill_text, candidates)
    if fire_candidate is not None:
        return _guard_explicit_candidate(item, top_candidate, fire_candidate)

    network_candidate = _pick_explicit_network_device_candidate(bill_text, candidates)
    if network_candidate is not None:
        return _guard_explicit_candidate(item, top_candidate, network_candidate)

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


