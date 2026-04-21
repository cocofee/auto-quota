# -*- coding: utf-8 -*-
"""Explicit candidate pickers and fallback selectors."""

import re

from loguru import logger

from src.explicit_equipment_family_pickers import _pick_explicit_equipment_family_candidate
from src.explicit_framework_family_pickers import (
    _PICKER_FRAMEWORK,
    _build_cable_picker_context,
    _build_valve_picker_context,
    _build_ventilation_picker_context,
    _build_wiring_picker_context,
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
    _score_cable_candidate,
    _score_valve_candidate,
    _score_ventilation_candidate,
    _score_wiring_candidate,
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
    _pick_explicit_outlet_family_candidate,
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


_WIRE_MODEL_PREFIXES = ("BYJ", "BV", "BVR", "RV", "RVS", "RVV", "RY", "RYS")
_CABLE_WIRING_ARBITRATION_GAP = 8
_VENTILATION_VALVE_ARBITRATION_GAP = 8
_WIRING_CANDIDATE_HINTS = ("配线", "穿线", "导线", "桥架内布放", "管内穿线")
_VENTILATION_VALVE_KEYWORDS = ("止回阀", "防火阀", "排烟防火阀", "调节阀", "定风量阀", "插板阀")
_VENTILATION_CONTEXT_KEYWORDS = ("风管", "通风", "空调", "送风", "回风", "排烟", "风量", "多叶", "对开", "防火")
_FIRE_DEVICE_KEYWORDS = ("室内消火栓", "试验消火栓")


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


def _candidate_identity(candidate: dict | None) -> str:
    if not isinstance(candidate, dict):
        return ""
    return str(candidate.get("quota_id") or candidate.get("name") or "")


def _has_wiring_like_candidates(candidates: list[dict]) -> bool:
    for candidate in candidates:
        quota_name = str(candidate.get("name", "") or "")
        if any(keyword in quota_name for keyword in _WIRING_CANDIDATE_HINTS):
            return True
    return False


def _has_explicit_cable_context(bill_text: str) -> bool:
    text = str(bill_text or "")
    if "电缆" not in text:
        return False

    bill_params = text_parser.parse(text)
    return (
        any(token in text for token in ("敷设", "终端头", "电缆头", "中间头"))
        or bool(bill_params.get("wire_type"))
        or bill_params.get("cable_section") is not None
        or bill_params.get("cable_cores") is not None
    )


def _has_explicit_conduit_bridge_conflict(bill_text: str) -> bool:
    text = str(bill_text or "")
    if not any(keyword in text for keyword in ("桥架", "桥架内", "沿桥架", "线槽")):
        return False

    bill_params = text_parser.parse(text)
    return (
        any(keyword in text for keyword in ("配管", "电气配管", "导管", "穿线管", "金属软管", "可挠金属套管"))
        or bool(bill_params.get("conduit_type"))
        or bill_params.get("conduit_dn") is not None
    )


def _has_explicit_plumbing_conduit_conflict(bill_text: str) -> bool:
    text = str(bill_text or "")
    has_plumbing_joint = any(
        keyword in text for keyword in ("软接头", "柔性接头", "伸缩节", "橡胶接头")
    )
    if not has_plumbing_joint:
        return False

    return any(keyword in text for keyword in ("金属软管", "可挠金属套管"))


def _build_relaxed_wiring_bill_text(bill_text: str, candidates: list[dict]) -> str | None:
    text = str(bill_text or "")
    if "电缆" not in text or not _has_wiring_like_candidates(candidates):
        return None

    bill_params = text_parser.parse(text)
    bill_wire_type = str(bill_params.get("wire_type") or "").upper()
    bill_laying_method = str(bill_params.get("laying_method") or "")
    has_layout_hint = any(token in bill_laying_method for token in ("桥架", "线槽", "穿管"))
    has_layout_hint = has_layout_hint or any(token in text for token in ("桥架", "线槽", "穿管", "管内"))
    has_wiring_anchor = any(token in text for token in ("配线", "穿线", "导线"))
    has_wiring_anchor = has_wiring_anchor or bill_wire_type.startswith(_WIRE_MODEL_PREFIXES)
    if not (has_layout_hint and has_wiring_anchor):
        return None

    relaxed = text.replace("电缆", "", 1).strip()
    if not any(token in relaxed for token in ("配线", "穿线")):
        relaxed = f"配线 {relaxed}"
    return " ".join(relaxed.split())


def _score_framework_picker_candidate(
    bill_text: str,
    candidate: dict | None,
    *,
    picker_type: str,
    build_context,
    score_adjuster,
) -> tuple[int, float, float]:
    if not isinstance(candidate, dict):
        return (-1, 0.0, 0.0)

    rules = _PICKER_FRAMEWORK._load_rules().get(picker_type) or {}
    text = str(bill_text or "")
    if not rules or not _PICKER_FRAMEWORK._match_triggers(text, rules):
        return (-1, 0.0, 0.0)

    context = build_context(text, rules)
    if context is None or context.get("abstain"):
        return (-1, 0.0, 0.0)
    context.setdefault("bill_text", text)

    quota_name = str(candidate.get("name", "") or "")
    if not quota_name:
        return (-1, 0.0, 0.0)
    candidate_context = {
        "quota_name": quota_name,
        "candidate_params": text_parser.parse(quota_name),
    }
    score = _PICKER_FRAMEWORK._score_candidate_text(quota_name, context, rules)
    score += _PICKER_FRAMEWORK._score_numeric_rules(context, candidate_context, rules)
    score += int(score_adjuster(candidate, context, candidate_context) or 0)
    return (
        score,
        float(candidate.get("param_score", 0.0) or 0.0),
        _safe_candidate_hybrid_score(candidate),
    )


def _boost_arbitrated_candidate(candidate: dict) -> dict:
    boosted = dict(candidate)
    boosted["name_bonus"] = max(float(boosted.get("name_bonus", 0.0) or 0.0), 0.5)
    return boosted


def _has_explicit_candidate_specialty_drift(item: dict,
                                            explicit_candidate: dict | None) -> bool:
    if not isinstance(explicit_candidate, dict) or not explicit_candidate:
        return False
    item_specialty = str(item.get("specialty") or "").strip()
    candidate_specialty = str(explicit_candidate.get("specialty") or "").strip()
    explicit_param_score_raw = explicit_candidate.get("param_score")
    explicit_param_score = float(explicit_param_score_raw or 0.0)
    specialty_param_floor = float(
        PolicyEngine.get_picker_threshold("explicit_specialty_param_floor", 0.75)
    )
    return bool(
        item_specialty
        and candidate_specialty
        and item_specialty != candidate_specialty
        and explicit_param_score_raw is not None
        and explicit_param_score < specialty_param_floor
    )


def _guard_explicit_candidate(item: dict,
                              top_candidate: dict,
                              explicit_candidate: dict | None,
                              hybrid_margin: float = 0.005) -> dict | None:
    if explicit_candidate is None:
        return top_candidate
    if not isinstance(top_candidate, dict) or not top_candidate:
        return explicit_candidate

    if not explicit_candidate.get("param_match", True):
        logger.debug(
            f"explicit picker guard rejected hard param fail quota_id={explicit_candidate.get('quota_id')}"
        )
        return top_candidate
    if explicit_candidate.get("family_gate_hard_conflict"):
        logger.debug(
            f"explicit picker guard rejected family hard conflict quota_id={explicit_candidate.get('quota_id')}"
        )
        return top_candidate

    param_score_floor = float(
        PolicyEngine.get_picker_threshold("explicit_param_score_floor", 0.55)
    )
    top_score = _safe_candidate_hybrid_score(top_candidate)
    explicit_score = _safe_candidate_hybrid_score(explicit_candidate)
    resolved_margin = float(
        PolicyEngine.get_picker_threshold("explicit_hybrid_margin", hybrid_margin)
    )
    explicit_param_score_raw = explicit_candidate.get("param_score")
    explicit_param_score = float(explicit_param_score_raw or 0.0)
    desc = item.get("description", "") or ""
    desc_lines = extract_description_lines(desc)
    explicit_category_error = check_category_mismatch(item, str(explicit_candidate.get("name", "") or ""), desc_lines)
    top_category_error = check_category_mismatch(item, str(top_candidate.get("name", "") or ""), desc_lines)
    category_rescue = not explicit_category_error and bool(top_category_error)
    if explicit_param_score_raw is not None and explicit_param_score < param_score_floor:
        if category_rescue:
            logger.debug(
                f"explicit picker guard kept low-param explicit candidate due category rescue "
                f"quota_id={explicit_candidate.get('quota_id')}"
            )
        else:
            logger.debug(
                f"explicit picker guard rejected low param_score={explicit_param_score:.3f} "
                f"quota_id={explicit_candidate.get('quota_id')}"
            )
            return top_candidate

    explicit_name_bonus = float(explicit_candidate.get("name_bonus", 0.0) or 0.0)
    top_name_bonus = float(top_candidate.get("name_bonus", 0.0) or 0.0)
    name_bonus_floor = float(
        PolicyEngine.get_picker_threshold("explicit_name_bonus_floor", 0.05)
    )
    if (top_score - explicit_score) > resolved_margin and (
        explicit_name_bonus - top_name_bonus
    ) < name_bonus_floor:
        if not explicit_category_error and top_category_error:
            logger.debug(
                f"explicit picker guard kept lower-score explicit candidate due category rescue "
                f"quota_id={explicit_candidate.get('quota_id')}"
            )
        else:
            return top_candidate

    if _has_explicit_candidate_specialty_drift(item, explicit_candidate):
        item_specialty = str(item.get("specialty") or "").strip()
        candidate_specialty = str(explicit_candidate.get("specialty") or "").strip()
        logger.debug(
            f"explicit picker guard rejected specialty drift "
            f"item={item_specialty} candidate={candidate_specialty} "
            f"param_score={explicit_param_score:.3f}"
        )
        return top_candidate
    return explicit_candidate


def _pick_explicit_cable_wiring_candidate(item: dict,
                                          top_candidate: dict,
                                          bill_text: str,
                                          candidates: list[dict]) -> dict | None:
    if not _has_explicit_cable_context(bill_text):
        return None

    cable_candidate = _pick_explicit_cable_family_candidate(bill_text, candidates)
    wiring_bill_text = _build_relaxed_wiring_bill_text(bill_text, candidates)
    wiring_candidate = _pick_explicit_wiring_family_candidate(wiring_bill_text or bill_text, candidates)

    if cable_candidate is None and wiring_candidate is None:
        return None
    if cable_candidate is None:
        return _guard_explicit_candidate(item, top_candidate, wiring_candidate)
    if wiring_candidate is None:
        return _guard_explicit_candidate(item, top_candidate, cable_candidate)
    if _candidate_identity(cable_candidate) == _candidate_identity(wiring_candidate):
        return _guard_explicit_candidate(item, top_candidate, cable_candidate)

    cable_score = _score_framework_picker_candidate(
        bill_text,
        cable_candidate,
        picker_type="cable",
        build_context=_build_cable_picker_context,
        score_adjuster=_score_cable_candidate,
    )
    wiring_score = _score_framework_picker_candidate(
        wiring_bill_text or bill_text,
        wiring_candidate,
        picker_type="wiring",
        build_context=_build_wiring_picker_context,
        score_adjuster=_score_wiring_candidate,
    )

    winner = cable_candidate
    winner_score = cable_score
    loser_score = wiring_score
    if wiring_score > cable_score:
        winner = wiring_candidate
        winner_score = wiring_score
        loser_score = cable_score

    guarded = _guard_explicit_candidate(item, top_candidate, winner)
    if (
        guarded is top_candidate
        and winner_score[0] > loser_score[0]
        and (winner_score[0] - loser_score[0]) >= _CABLE_WIRING_ARBITRATION_GAP
    ):
        guarded = _guard_explicit_candidate(item, top_candidate, _boost_arbitrated_candidate(winner))
    return guarded


def _pick_explicit_bridge_support_candidate(item: dict,
                                            top_candidate: dict,
                                            bill_text: str,
                                            candidates: list[dict]) -> dict | None:
    bridge_candidate = _pick_explicit_bridge_family_candidate(bill_text, candidates)
    support_candidate = _pick_explicit_support_family_candidate(bill_text, candidates)

    if bridge_candidate is None and support_candidate is None:
        return None

    text = str(bill_text or "")
    bridge_tokens = ("桥架", "电缆桥架", "线槽", "母线槽")
    support_tokens = ("支架", "支撑架", "支吊架", "抗震")
    prefers_support = any(token in text for token in bridge_tokens) and any(token in text for token in support_tokens)

    preferred_candidate = support_candidate if prefers_support and support_candidate is not None else bridge_candidate
    if preferred_candidate is None:
        preferred_candidate = support_candidate
    if preferred_candidate is None:
        return None

    if (
        bridge_candidate is not None
        and support_candidate is not None
        and _candidate_identity(bridge_candidate) == _candidate_identity(support_candidate)
    ):
        return _guard_explicit_candidate(item, top_candidate, preferred_candidate)

    guarded = _guard_explicit_candidate(item, top_candidate, preferred_candidate)
    if guarded is top_candidate:
        guarded = _guard_explicit_candidate(item, top_candidate, _boost_arbitrated_candidate(preferred_candidate))
    return guarded


def _pick_explicit_conduit_bridge_candidate(item: dict,
                                            top_candidate: dict,
                                            bill_text: str,
                                            candidates: list[dict]) -> dict | None:
    if not _has_explicit_conduit_bridge_conflict(bill_text):
        return None

    conduit_candidate = _pick_explicit_conduit_family_candidate(bill_text, candidates)
    bridge_candidate = _pick_explicit_bridge_family_candidate(bill_text, candidates)
    if conduit_candidate is None or bridge_candidate is None:
        return None
    if _candidate_identity(conduit_candidate) == _candidate_identity(bridge_candidate):
        return _guard_explicit_candidate(item, top_candidate, conduit_candidate)

    guarded = _guard_explicit_candidate(item, top_candidate, conduit_candidate)
    if guarded is top_candidate:
        guarded = _guard_explicit_candidate(item, top_candidate, _boost_arbitrated_candidate(conduit_candidate))
    return guarded


def _pick_explicit_plumbing_conduit_candidate(item: dict,
                                              top_candidate: dict,
                                              bill_text: str,
                                              candidates: list[dict]) -> dict | None:
    if not _has_explicit_plumbing_conduit_conflict(bill_text):
        return None

    conduit_candidate = _pick_explicit_conduit_family_candidate(bill_text, candidates)
    plumbing_candidate = _pick_explicit_plumbing_accessory_candidate(bill_text, candidates)
    if conduit_candidate is None or plumbing_candidate is None:
        return None
    if _candidate_identity(conduit_candidate) == _candidate_identity(plumbing_candidate):
        return _guard_explicit_candidate(item, top_candidate, plumbing_candidate)

    guarded = _guard_explicit_candidate(item, top_candidate, plumbing_candidate)
    if guarded is top_candidate:
        guarded = _guard_explicit_candidate(item, top_candidate, _boost_arbitrated_candidate(plumbing_candidate))
    return guarded


def _has_explicit_conduit_support_conflict(bill_text: str) -> bool:
    text = str(bill_text or "")
    has_support_semantics = any(keyword in text for keyword in ("支架", "吊架", "支吊架", "支撑架", "抗震"))
    if not has_support_semantics:
        return False

    bill_params = text_parser.parse(text)
    return (
        any(keyword in text for keyword in ("配管", "电气配管", "导管", "穿线管", "金属软管", "可挠金属套管"))
        or bool(bill_params.get("conduit_type"))
        or bill_params.get("conduit_dn") is not None
    )


def _should_allow_low_param_conduit_rescue(top_candidate: dict,
                                           conduit_candidate: dict | None,
                                           bill_text: str) -> bool:
    if not isinstance(conduit_candidate, dict) or not conduit_candidate:
        return False
    if not isinstance(top_candidate, dict) or not top_candidate:
        return False

    text = str(bill_text or "")
    upper_text = text.upper()
    bill_params = text_parser.parse(text)
    code_match = re.search(r"(?<![A-Z0-9])(JDG|KBG|FPC|PVC|PC|SC|RC|MT|DG|G)\s*\d+\b", upper_text)
    bill_conduit_type = str(bill_params.get("conduit_type") or (code_match.group(1) if code_match else ""))
    explicit_electrical = any(keyword in text for keyword in (
        "电气配管", "穿线管", "导管", "金属软管", "可挠金属套管",
    ))
    if not explicit_electrical and not (bill_conduit_type and "配管" in text):
        return False

    bill_dn = bill_params.get("conduit_dn")
    if bill_dn is None:
        bill_dn = bill_params.get("dn")
    if bill_dn is None:
        return False

    expected_words: list[str] = []
    forbidden_words: list[str] = []
    if bill_conduit_type in {"JDG", "KBG"}:
        expected_words = ["JDG", "紧定式", "钢导管"]
        forbidden_words = ["防爆钢管", "电缆保护"]
    elif bill_conduit_type in {"PC", "PVC"}:
        expected_words = ["刚性阻燃管", "PVC阻燃塑料管"]
        forbidden_words = ["电缆保护", "防爆钢管"]
    elif bill_conduit_type == "FPC":
        expected_words = ["半硬质阻燃管", "半硬质塑料管"]
        forbidden_words = ["电缆保护", "防爆钢管"]
    elif bill_conduit_type in {"SC", "G", "DG", "RC", "MT"}:
        expected_words = ["镀锌钢管", "镀锌电线管", "钢管敷设"]
        forbidden_words = ["防爆钢管", "电缆保护"]

    explicit_name = str(conduit_candidate.get("name", "") or "")
    top_name = str(top_candidate.get("name", "") or "")
    if expected_words and not any(word in explicit_name for word in expected_words):
        return False

    explicit_params = text_parser.parse(explicit_name)
    top_params = text_parser.parse(top_name)
    explicit_dn = explicit_params.get("conduit_dn")
    if explicit_dn is None:
        explicit_dn = explicit_params.get("dn")
    top_dn = top_params.get("conduit_dn")
    if top_dn is None:
        top_dn = top_params.get("dn")

    explicit_exact = explicit_dn == bill_dn
    top_exact = top_dn == bill_dn
    top_forbidden = any(word in top_name for word in forbidden_words)
    return bool(explicit_exact and (top_forbidden or not top_exact))


def _pick_explicit_conduit_support_candidate(item: dict,
                                             top_candidate: dict,
                                             bill_text: str,
                                             candidates: list[dict]) -> dict | None:
    if not _has_explicit_conduit_support_conflict(bill_text):
        return None

    conduit_candidate = _pick_explicit_conduit_family_candidate(bill_text, candidates)
    support_candidate = _pick_explicit_support_family_candidate(bill_text, candidates)
    if conduit_candidate is None or support_candidate is None:
        return None
    if _candidate_identity(conduit_candidate) == _candidate_identity(support_candidate):
        return _guard_explicit_candidate(item, top_candidate, support_candidate)

    guarded = _guard_explicit_candidate(item, top_candidate, support_candidate)
    if guarded is top_candidate:
        guarded = _guard_explicit_candidate(item, top_candidate, _boost_arbitrated_candidate(support_candidate))
    return guarded


def _has_explicit_distribution_box_equipment_conflict(item: dict, bill_text: str) -> bool:
    text = str(bill_text or "")
    bill_name = str((item or {}).get("name") or "")
    has_box_semantics = any(keyword in text for keyword in ("配电箱", "配电柜", "控制箱", "控制柜", "动力箱", "照明箱"))
    has_equipment_subject = any(
        keyword in bill_name
        for keyword in ("设备", "泵组", "水箱", "气压罐", "污水泵", "潜污泵", "潜水泵", "离心泵", "电暖器")
    )
    has_box_subject = any(keyword in bill_name for keyword in ("配电箱", "配电柜", "控制箱", "控制柜", "动力箱", "照明箱"))
    return has_box_semantics and has_equipment_subject and not has_box_subject


def _pick_explicit_distribution_box_equipment_candidate(item: dict,
                                                        top_candidate: dict,
                                                        bill_text: str,
                                                        candidates: list[dict]) -> dict | None:
    if not _has_explicit_distribution_box_equipment_conflict(item, bill_text):
        return None

    distribution_box_candidate = _pick_explicit_distribution_box_candidate(bill_text, candidates)
    equipment_candidate = _pick_explicit_equipment_family_candidate(bill_text, candidates)
    if distribution_box_candidate is None or equipment_candidate is None:
        return None
    if _candidate_identity(distribution_box_candidate) == _candidate_identity(equipment_candidate):
        return _guard_explicit_candidate(item, top_candidate, equipment_candidate)

    guarded = _guard_explicit_candidate(item, top_candidate, equipment_candidate)
    if guarded is top_candidate:
        guarded = _guard_explicit_candidate(item, top_candidate, _boost_arbitrated_candidate(equipment_candidate))
    return guarded


def _has_explicit_distribution_box_motor_conflict(item: dict, bill_text: str) -> bool:
    text = str(bill_text or "")
    bill_name = str((item or {}).get("name") or "")
    has_box_semantics = any(keyword in text for keyword in ("配电箱", "配电柜", "控制箱", "控制柜", "动力箱", "照明箱"))
    has_motor_subject = "电动机" in bill_name
    has_box_subject = any(keyword in bill_name for keyword in ("配电箱", "配电柜", "控制箱", "控制柜", "动力箱", "照明箱"))
    return has_box_semantics and has_motor_subject and not has_box_subject


def _pick_explicit_distribution_box_motor_candidate(item: dict,
                                                    top_candidate: dict,
                                                    bill_text: str,
                                                    candidates: list[dict]) -> dict | None:
    if not _has_explicit_distribution_box_motor_conflict(item, bill_text):
        return None

    distribution_box_candidate = _pick_explicit_distribution_box_candidate(bill_text, candidates)
    motor_candidate = _pick_explicit_motor_family_candidate(bill_text, candidates)
    if distribution_box_candidate is None or motor_candidate is None:
        return None
    if _candidate_identity(distribution_box_candidate) == _candidate_identity(motor_candidate):
        return _guard_explicit_candidate(item, top_candidate, motor_candidate)

    guarded = _guard_explicit_candidate(item, top_candidate, motor_candidate)
    if guarded is top_candidate:
        guarded = _guard_explicit_candidate(item, top_candidate, _boost_arbitrated_candidate(motor_candidate))
    return guarded


def _has_explicit_sanitary_equipment_conflict(item: dict, bill_text: str) -> bool:
    text = str(bill_text or "")
    bill_name = str((item or {}).get("name") or "")
    has_flush_tank_subject = any(keyword in bill_name for keyword in ("\u51b2\u6d17\u6c34\u7bb1", "\u81ea\u52a8\u51b2\u6d17\u6c34\u7bb1"))
    has_water_tank_subject = "\u6c34\u7bb1" in bill_name
    has_sanitary_semantics = any(keyword in text for keyword in ("\u6d17\u8138\u76c6", "\u6d17\u624b\u76c6", "\u536b\u751f\u5668\u5177"))
    has_equipment_semantics = any(keyword in text for keyword in ("\u51b2\u6d17\u6c34\u7bb1", "\u81ea\u52a8\u51b2\u6d17\u6c34\u7bb1"))
    if has_water_tank_subject:
        has_equipment_semantics = has_equipment_semantics or "\u6c34\u7bb1" in text
    return (has_flush_tank_subject or has_water_tank_subject) and has_sanitary_semantics and has_equipment_semantics


def _pick_explicit_sanitary_equipment_candidate(item: dict,
                                                top_candidate: dict,
                                                bill_text: str,
                                                candidates: list[dict]) -> dict | None:
    if not _has_explicit_sanitary_equipment_conflict(item, bill_text):
        return None

    sanitary_candidate = _pick_explicit_sanitary_family_candidate(bill_text, candidates)
    equipment_candidate = _pick_explicit_equipment_family_candidate(bill_text, candidates)
    if sanitary_candidate is None or equipment_candidate is None:
        return None
    if _candidate_identity(sanitary_candidate) == _candidate_identity(equipment_candidate):
        return _guard_explicit_candidate(item, top_candidate, equipment_candidate)

    sanitary_name = str(sanitary_candidate.get("name", "") or "")
    equipment_name = str(equipment_candidate.get("name", "") or "")
    if "\u6d17\u8138\u76c6" not in sanitary_name or "\u6c34\u7bb1" not in equipment_name:
        return None

    guarded = _guard_explicit_candidate(item, top_candidate, equipment_candidate)
    if guarded is top_candidate:
        guarded = _guard_explicit_candidate(item, top_candidate, _boost_arbitrated_candidate(equipment_candidate))
    return guarded


def _has_explicit_sanitary_lamp_conflict(item: dict, bill_text: str) -> bool:
    text = str(bill_text or "")
    bill_name = str((item or {}).get("name") or "")
    has_lamp_subject = "灯" in bill_name
    has_sanitary_semantics = any(keyword in text for keyword in ("洗脸盆", "洗手盆", "洗涤盆", "坐便器", "水龙头"))
    return has_lamp_subject and has_sanitary_semantics


def _pick_explicit_sanitary_lamp_candidate(item: dict,
                                           top_candidate: dict,
                                           bill_text: str,
                                           candidates: list[dict]) -> dict | None:
    if not _has_explicit_sanitary_lamp_conflict(item, bill_text):
        return None

    sanitary_candidate = _pick_explicit_sanitary_family_candidate(bill_text, candidates)
    lamp_candidate = _pick_explicit_lamp_family_candidate(bill_text, candidates)
    if sanitary_candidate is None or lamp_candidate is None:
        return None
    if _candidate_identity(sanitary_candidate) == _candidate_identity(lamp_candidate):
        return _guard_explicit_candidate(item, top_candidate, lamp_candidate)

    sanitary_name = str(sanitary_candidate.get("name", "") or "")
    lamp_name = str(lamp_candidate.get("name", "") or "")
    if "灯" not in lamp_name or not any(keyword in sanitary_name for keyword in ("盆", "便器", "龙头")):
        return None

    guarded = _guard_explicit_candidate(item, top_candidate, lamp_candidate)
    if guarded is top_candidate:
        guarded = _guard_explicit_candidate(item, top_candidate, _boost_arbitrated_candidate(lamp_candidate))
    return guarded


def _has_explicit_ventilation_valve_conflict(bill_text: str) -> bool:
    text = str(bill_text or "")
    return (
        "阀" in text
        and any(keyword in text for keyword in _VENTILATION_VALVE_KEYWORDS)
        and any(keyword in text for keyword in _VENTILATION_CONTEXT_KEYWORDS)
    )


def _pick_explicit_ventilation_valve_candidate(item: dict,
                                               top_candidate: dict,
                                               bill_text: str,
                                               candidates: list[dict]) -> dict | None:
    if not _has_explicit_ventilation_valve_conflict(bill_text):
        return None

    ventilation_candidate = _pick_explicit_ventilation_family_candidate(bill_text, candidates)
    valve_candidate = _pick_explicit_valve_family_candidate(bill_text, candidates)
    if ventilation_candidate is None or valve_candidate is None:
        return None
    if _candidate_identity(ventilation_candidate) == _candidate_identity(valve_candidate):
        return _guard_explicit_candidate(item, top_candidate, ventilation_candidate)

    ventilation_score = _score_framework_picker_candidate(
        bill_text,
        ventilation_candidate,
        picker_type="ventilation",
        build_context=_build_ventilation_picker_context,
        score_adjuster=_score_ventilation_candidate,
    )
    valve_score = _score_framework_picker_candidate(
        bill_text,
        valve_candidate,
        picker_type="valve",
        build_context=_build_valve_picker_context,
        score_adjuster=_score_valve_candidate,
    )

    winner = ventilation_candidate
    winner_score = ventilation_score
    loser_score = valve_score
    if valve_score > ventilation_score:
        winner = valve_candidate
        winner_score = valve_score
        loser_score = ventilation_score

    guarded = _guard_explicit_candidate(item, top_candidate, winner)
    if (
        guarded is top_candidate
        and winner_score[0] > loser_score[0]
        and (winner_score[0] - loser_score[0]) >= _VENTILATION_VALVE_ARBITRATION_GAP
    ):
        guarded = _guard_explicit_candidate(item, top_candidate, _boost_arbitrated_candidate(winner))
    return guarded


def _has_explicit_button_fire_conflict(bill_text: str) -> bool:
    text = str(bill_text or "")
    return "按钮" in text and any(keyword in text for keyword in _FIRE_DEVICE_KEYWORDS)


def _pick_explicit_button_fire_candidate(item: dict,
                                         top_candidate: dict,
                                         bill_text: str,
                                         candidates: list[dict]) -> dict | None:
    if not _has_explicit_button_fire_conflict(bill_text):
        return None

    button_candidate = _pick_explicit_button_broadcast_candidate(bill_text, candidates)
    fire_candidate = _pick_explicit_fire_device_candidate(bill_text, candidates)
    if button_candidate is None or fire_candidate is None:
        return None
    if _candidate_identity(button_candidate) == _candidate_identity(fire_candidate):
        return _guard_explicit_candidate(item, top_candidate, fire_candidate)

    guarded = _guard_explicit_candidate(item, top_candidate, fire_candidate)
    if guarded is top_candidate:
        guarded = _guard_explicit_candidate(item, top_candidate, _boost_arbitrated_candidate(fire_candidate))
    return guarded


def _has_explicit_valve_fire_conflict(bill_text: str) -> bool:
    text = str(bill_text or "")
    has_fire_semantics = any(keyword in text for keyword in _FIRE_DEVICE_KEYWORDS)
    has_valve_semantics = any(
        keyword in text
        for keyword in ("螺纹阀门", "焊接法兰阀门", "法兰阀门", "螺纹法兰阀门", "碳钢阀门", "塑料阀门", "阀门")
    )
    return has_fire_semantics and has_valve_semantics


def _pick_explicit_valve_fire_candidate(item: dict,
                                        top_candidate: dict,
                                        bill_text: str,
                                        candidates: list[dict]) -> dict | None:
    if not _has_explicit_valve_fire_conflict(bill_text):
        return None

    valve_candidate = _pick_explicit_valve_family_candidate(bill_text, candidates)
    fire_candidate = _pick_explicit_fire_device_candidate(bill_text, candidates)
    if valve_candidate is None or fire_candidate is None:
        return None
    if _candidate_identity(valve_candidate) == _candidate_identity(fire_candidate):
        return _guard_explicit_candidate(item, top_candidate, fire_candidate)

    guarded = _guard_explicit_candidate(item, top_candidate, fire_candidate)
    if guarded is top_candidate:
        guarded = _guard_explicit_candidate(item, top_candidate, _boost_arbitrated_candidate(fire_candidate))
    return guarded


def _has_explicit_fire_network_conflict(item: dict, bill_text: str) -> bool:
    text = str(bill_text or "")
    bill_name = str((item or {}).get("name") or "")
    has_network_subject = "\u4ea4\u6362\u673a" in bill_name
    has_fire_semantics = any(keyword in text for keyword in _FIRE_DEVICE_KEYWORDS)
    return has_network_subject and has_fire_semantics and "\u4ea4\u6362\u673a" in text


def _pick_explicit_fire_network_candidate(item: dict,
                                          top_candidate: dict,
                                          bill_text: str,
                                          candidates: list[dict]) -> dict | None:
    if not _has_explicit_fire_network_conflict(item, bill_text):
        return None

    fire_candidate = _pick_explicit_fire_device_candidate(bill_text, candidates)
    network_candidate = _pick_explicit_network_device_candidate(bill_text, candidates)
    if fire_candidate is None or network_candidate is None:
        return None
    if _candidate_identity(fire_candidate) == _candidate_identity(network_candidate):
        return _guard_explicit_candidate(item, top_candidate, network_candidate)

    fire_name = str(fire_candidate.get("name", "") or "")
    network_name = str(network_candidate.get("name", "") or "")
    if "\u6d88\u706b\u6813" not in fire_name or "\u4ea4\u6362\u673a" not in network_name:
        return None

    guarded = _guard_explicit_candidate(item, top_candidate, network_candidate)
    if guarded is top_candidate:
        guarded = _guard_explicit_candidate(item, top_candidate, _boost_arbitrated_candidate(network_candidate))
    return guarded


def _has_explicit_outlet_button_conflict(item: dict, bill_text: str) -> bool:
    text = str(bill_text or "")
    bill_name = str((item or {}).get("name") or "")
    return (
        "插座" in text
        and "按钮" in text
        and "按钮" in bill_name
        and "插座" not in bill_name
    )


def _pick_explicit_outlet_button_candidate(item: dict,
                                           top_candidate: dict,
                                           bill_text: str,
                                           candidates: list[dict]) -> dict | None:
    if not _has_explicit_outlet_button_conflict(item, bill_text):
        return None

    outlet_candidate = _pick_explicit_outlet_family_candidate(bill_text, candidates)
    button_candidate = _pick_explicit_button_broadcast_candidate(bill_text, candidates)
    if outlet_candidate is None or button_candidate is None:
        return None
    if _candidate_identity(outlet_candidate) == _candidate_identity(button_candidate):
        return _guard_explicit_candidate(item, top_candidate, button_candidate)

    outlet_name = str(outlet_candidate.get("name", "") or "")
    button_name = str(button_candidate.get("name", "") or "")
    if "插座" not in outlet_name or "按钮" not in button_name:
        return None

    guarded = _guard_explicit_candidate(item, top_candidate, button_candidate)
    if guarded is top_candidate:
        guarded = _guard_explicit_candidate(item, top_candidate, _boost_arbitrated_candidate(button_candidate))
    return guarded


def _has_explicit_lamp_outlet_conflict(item: dict, bill_text: str) -> bool:
    text = str(bill_text or "")
    bill_name = str((item or {}).get("name") or "")
    has_lamp_subject = any(keyword in bill_name for keyword in ("灯", "灯带", "灯具"))
    return has_lamp_subject and "插座" in text


def _pick_explicit_lamp_outlet_candidate(item: dict,
                                         top_candidate: dict,
                                         bill_text: str,
                                         candidates: list[dict]) -> dict | None:
    if not _has_explicit_lamp_outlet_conflict(item, bill_text):
        return None

    lamp_candidate = _pick_explicit_lamp_family_candidate(bill_text, candidates)
    outlet_candidate = _pick_explicit_outlet_family_candidate(bill_text, candidates)
    if lamp_candidate is None or outlet_candidate is None:
        return None
    if _candidate_identity(lamp_candidate) == _candidate_identity(outlet_candidate):
        return _guard_explicit_candidate(item, top_candidate, lamp_candidate)

    lamp_name = str(lamp_candidate.get("name", "") or "")
    outlet_name = str(outlet_candidate.get("name", "") or "")
    if "灯" not in lamp_name or "插座" not in outlet_name:
        return None

    guarded = _guard_explicit_candidate(item, top_candidate, lamp_candidate)
    if guarded is top_candidate:
        guarded = _guard_explicit_candidate(item, top_candidate, _boost_arbitrated_candidate(lamp_candidate))
    return guarded


def _has_explicit_lamp_button_conflict(item: dict, bill_text: str) -> bool:
    text = str(bill_text or "")
    bill_name = str((item or {}).get("name") or "")
    has_button_subject = "按钮" in bill_name
    return has_button_subject and "灯" in text


def _pick_explicit_lamp_button_candidate(item: dict,
                                         top_candidate: dict,
                                         bill_text: str,
                                         candidates: list[dict]) -> dict | None:
    if not _has_explicit_lamp_button_conflict(item, bill_text):
        return None

    lamp_candidate = _pick_explicit_lamp_family_candidate(bill_text, candidates)
    button_candidate = _pick_explicit_button_broadcast_candidate(bill_text, candidates)
    if lamp_candidate is None or button_candidate is None:
        return None
    if _candidate_identity(lamp_candidate) == _candidate_identity(button_candidate):
        return _guard_explicit_candidate(item, top_candidate, button_candidate)

    lamp_name = str(lamp_candidate.get("name", "") or "")
    button_name = str(button_candidate.get("name", "") or "")
    if "灯" not in lamp_name or "按钮" not in button_name:
        return None

    guarded = _guard_explicit_candidate(item, top_candidate, button_candidate)
    if guarded is top_candidate:
        guarded = _guard_explicit_candidate(item, top_candidate, _boost_arbitrated_candidate(button_candidate))
    return guarded


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

    cable_wiring_candidate = _pick_explicit_cable_wiring_candidate(item, top_candidate, bill_text, candidates)
    if cable_wiring_candidate is not None:
        return cable_wiring_candidate

    wiring_candidate = _pick_explicit_wiring_family_candidate(bill_text, candidates)
    if wiring_candidate is not None:
        guarded = _guard_explicit_candidate(item, top_candidate, wiring_candidate)
        if guarded is top_candidate:
            wiring_score = _score_framework_picker_candidate(
                bill_text,
                wiring_candidate,
                picker_type="wiring",
                build_context=_build_wiring_picker_context,
                score_adjuster=_score_wiring_candidate,
            )
            top_wiring_score = _score_framework_picker_candidate(
                bill_text,
                top_candidate,
                picker_type="wiring",
                build_context=_build_wiring_picker_context,
                score_adjuster=_score_wiring_candidate,
            )
            if wiring_score[0] > top_wiring_score[0] and (
                wiring_score[0] - top_wiring_score[0]
            ) >= _CABLE_WIRING_ARBITRATION_GAP:
                guarded = _guard_explicit_candidate(item, top_candidate, _boost_arbitrated_candidate(wiring_candidate))
        return guarded

    conduit_bridge_candidate = _pick_explicit_conduit_bridge_candidate(item, top_candidate, bill_text, candidates)
    if conduit_bridge_candidate is not None:
        return conduit_bridge_candidate

    plumbing_conduit_candidate = _pick_explicit_plumbing_conduit_candidate(item, top_candidate, bill_text, candidates)
    if plumbing_conduit_candidate is not None:
        return plumbing_conduit_candidate

    conduit_support_candidate = _pick_explicit_conduit_support_candidate(item, top_candidate, bill_text, candidates)
    if conduit_support_candidate is not None:
        return conduit_support_candidate

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
        conduit_guard_hard_reject = (
            not conduit_candidate.get("param_match", True)
            or bool(conduit_candidate.get("family_gate_hard_conflict"))
            or _has_explicit_candidate_specialty_drift(item, conduit_candidate)
        )
        guarded = _guard_explicit_candidate(item, top_candidate, conduit_candidate)
        if (
            not conduit_guard_hard_reject
            and guarded is top_candidate
            and _should_allow_low_param_conduit_rescue(
                top_candidate,
                conduit_candidate,
                bill_text,
            )
        ):
            logger.debug(
                "category_safe allowed low-param conduit rescue top={} rescue={}",
                top_candidate.get("quota_id") or top_candidate.get("name", ""),
                conduit_candidate.get("quota_id") or conduit_candidate.get("name", ""),
            )
            return conduit_candidate
        return guarded

    bridge_support_candidate = _pick_explicit_bridge_support_candidate(item, top_candidate, bill_text, candidates)
    if bridge_support_candidate is not None:
        return bridge_support_candidate

    distribution_box_equipment_candidate = _pick_explicit_distribution_box_equipment_candidate(
        item, top_candidate, bill_text, candidates
    )
    if distribution_box_equipment_candidate is not None:
        return distribution_box_equipment_candidate

    distribution_box_motor_candidate = _pick_explicit_distribution_box_motor_candidate(
        item, top_candidate, bill_text, candidates
    )
    if distribution_box_motor_candidate is not None:
        return distribution_box_motor_candidate

    distribution_box_candidate = _pick_explicit_distribution_box_candidate(bill_text, candidates)
    if distribution_box_candidate is not None:
        return _guard_explicit_candidate(item, top_candidate, distribution_box_candidate)

    ventilation_valve_candidate = _pick_explicit_ventilation_valve_candidate(item, top_candidate, bill_text, candidates)
    if ventilation_valve_candidate is not None:
        return ventilation_valve_candidate

    ventilation_candidate = _pick_explicit_ventilation_family_candidate(bill_text, candidates)
    if ventilation_candidate is not None:
        return _guard_explicit_candidate(item, top_candidate, ventilation_candidate)

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

    sanitary_lamp_candidate = _pick_explicit_sanitary_lamp_candidate(item, top_candidate, bill_text, candidates)
    if sanitary_lamp_candidate is not None:
        return sanitary_lamp_candidate

    sanitary_equipment_candidate = _pick_explicit_sanitary_equipment_candidate(
        item, top_candidate, bill_text, candidates
    )
    if sanitary_equipment_candidate is not None:
        return sanitary_equipment_candidate

    sanitary_candidate = _pick_explicit_sanitary_family_candidate(bill_text, candidates)
    if sanitary_candidate is not None:
        return sanitary_candidate

    equipment_candidate = _pick_explicit_equipment_family_candidate(bill_text, candidates)
    if equipment_candidate is not None:
        return _guard_explicit_candidate(item, top_candidate, equipment_candidate)

    lamp_button_candidate = _pick_explicit_lamp_button_candidate(item, top_candidate, bill_text, candidates)
    if lamp_button_candidate is not None:
        return lamp_button_candidate

    lamp_outlet_candidate = _pick_explicit_lamp_outlet_candidate(item, top_candidate, bill_text, candidates)
    if lamp_outlet_candidate is not None:
        return lamp_outlet_candidate

    lamp_candidate = _pick_explicit_lamp_family_candidate(bill_text, candidates)
    if lamp_candidate is not None:
        return _guard_explicit_candidate(item, top_candidate, lamp_candidate)

    outlet_button_candidate = _pick_explicit_outlet_button_candidate(item, top_candidate, bill_text, candidates)
    if outlet_button_candidate is not None:
        return outlet_button_candidate

    outlet_candidate = _pick_explicit_outlet_family_candidate(bill_text, candidates)
    if outlet_candidate is not None:
        return _guard_explicit_candidate(item, top_candidate, outlet_candidate)

    button_fire_candidate = _pick_explicit_button_fire_candidate(item, top_candidate, bill_text, candidates)
    if button_fire_candidate is not None:
        return button_fire_candidate

    button_broadcast_candidate = _pick_explicit_button_broadcast_candidate(bill_text, candidates)
    if button_broadcast_candidate is not None:
        return _guard_explicit_candidate(item, top_candidate, button_broadcast_candidate)

    plumbing_accessory_candidate = _pick_explicit_plumbing_accessory_candidate(bill_text, candidates)
    if plumbing_accessory_candidate is not None:
        return _guard_explicit_candidate(item, top_candidate, plumbing_accessory_candidate)

    valve_fire_candidate = _pick_explicit_valve_fire_candidate(item, top_candidate, bill_text, candidates)
    if valve_fire_candidate is not None:
        return valve_fire_candidate

    valve_candidate = _pick_explicit_valve_family_candidate(bill_text, candidates)
    if valve_candidate is not None:
        return _guard_explicit_candidate(item, top_candidate, valve_candidate)

    fire_network_candidate = _pick_explicit_fire_network_candidate(item, top_candidate, bill_text, candidates)
    if fire_network_candidate is not None:
        return fire_network_candidate

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


