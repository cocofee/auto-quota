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
from contextlib import nullcontext

from loguru import logger

import config

from src.ambiguity_gate import analyze_ambiguity
from src.candidate_arbiter import arbitrate_candidates
from src.candidate_scoring import (
    compute_candidate_rank_score,
    compute_candidate_sort_key,
    explain_candidate_rank_score,
    has_exact_experience_anchor,
    has_exact_universal_kb_anchor,
    sort_candidates_with_stage_priority,
)
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
from src.explicit_equipment_family_pickers import (
    _pick_explicit_equipment_family_candidate,
    _promote_explicit_distribution_box_candidate,
)
from src.explicit_terminal_family_pickers import (
    _pick_explicit_button_broadcast_candidate,
    _pick_explicit_lamp_family_candidate,
    _pick_explicit_sanitary_family_candidate,
)
from src.context_builder import build_context_prior, summarize_batch_context_for_trace
from src.adaptive_strategy import AdaptiveStrategy
from src.ltr_ranker import rerank_candidates_with_ltr
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
from src.province_plugins import resolve_plugin_hints
from src.query_builder import build_primary_query_profile
from src.text_parser import parser as text_parser, normalize_bill_text
from src.query_router import build_query_route_profile, select_search_books
from src.reason_taxonomy import apply_reason_metadata, merge_reason_tags
from src.specialty_classifier import (
    BORROW_PRIORITY,
    book_matches_province_scope,
    classify as classify_specialty,
    province_uses_standard_route_books,
)
from src.unified_planner import build_unified_search_plan
from src.param_validator import ParamValidator
from src.rule_validator import RuleValidator
from src.match_core import (
    calculate_confidence,
    infer_confidence_family_alignment,
    _append_trace_step,
    _normalize_classification,
    _is_measure_item,
    try_experience_exact_match,
    try_experience_match,
    _safe_json_materials,
    _summarize_candidates_for_trace,
    summarize_candidate_reasoning,
)
from src.policy_engine import PolicyEngine
from src.performance_monitor import PerformanceMonitor
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

_ADAPTIVE_STRATEGY = AdaptiveStrategy()


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

    review_item = dict(item or {})
    canonical_query = dict(review_item.get("canonical_query") or {})
    primary_query_profile = dict(canonical_query.get("primary_query_profile") or {})
    context_prior = dict(review_item.get("context_prior") or {})
    primary_subject = str(
        primary_query_profile.get("primary_subject")
        or review_item.get("primary_subject")
        or context_prior.get("primary_subject")
        or ""
    ).strip()
    if primary_subject:
        review_item["name"] = primary_subject

    desc = review_item.get("description", "") or ""
    desc_lines = extract_description_lines(desc)

    # 运行所有审核检查器，收集全部错误（不再短路）
    checkers = [
        check_category_mismatch(review_item, quota_name, desc_lines),
        check_sleeve_mismatch(review_item, quota_name, desc_lines),
        check_material_mismatch(review_item, quota_name, desc_lines),
        check_connection_mismatch(review_item, quota_name, desc_lines),
        check_pipe_usage(review_item, quota_name, desc_lines),
        check_parameter_deviation(review_item, quota_name, desc_lines),
        check_electric_pair(review_item, quota_name, desc_lines),
        check_elevator_type(review_item, quota_name, desc_lines),
        check_elevator_floor(review_item, quota_name, desc_lines, quota_id=quota_id),
    ]
    errors = [e for e in checkers if e is not None]

    if not errors:
        return None

    # 返回第一个错误作为主错误（保持向后兼容），附带全部错误列表
    error = errors[0].copy()  # 用copy避免循环引用（error本身在errors列表里）
    if len(errors) > 1:
        error["all_errors"] = errors  # 纠正步骤可以读取全部错误

    return error


def _append_item_review_rejection_trace(result: dict, item: dict | None) -> None:
    """Carry pre-search experience review rejections onto the final result trace."""
    if not isinstance(result, dict) or not isinstance(item, dict):
        return

    rejection = item.get("_experience_review_rejection")
    if not isinstance(rejection, dict):
        return

    trace = result.get("trace") or {}
    for step in trace.get("steps", []) or []:
        if not isinstance(step, dict):
            continue
        if step.get("stage") != "experience_review_rejected":
            continue
        if str(step.get("quota_id", "") or "") == str(rejection.get("quota_id", "") or ""):
            return

    _append_trace_step(
        result,
        "experience_review_rejected",
        error_type=rejection.get("type"),
        error_reason=rejection.get("reason"),
        experience_source=rejection.get("match_source"),
        quota_id=rejection.get("quota_id"),
    )


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


def _guard_explicit_candidate(item: dict,
                              top_candidate: dict,
                              explicit_candidate: dict | None,
                              hybrid_margin: float = 0.005) -> dict | None:
    if explicit_candidate is None:
        return None
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
        return _guard_explicit_candidate(item, top_candidate, sanitary_candidate)

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

def _ensure_item_feature_context(item: dict,
                                 performance_monitor: PerformanceMonitor | None = None):
    """Lazily restore feature context for retry/replay callers that bypass preprocessing."""
    if not isinstance(item, dict):
        return

    stage = (
        performance_monitor.measure("文本解析")
        if performance_monitor is not None else nullcontext()
    )
    with stage:
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


def _build_item_context(item: dict,
                        performance_monitor: PerformanceMonitor | None = None) -> dict:
    """构建匹配所需的清单上下文（名称/查询文本/单位/工程量等）。"""
    _ensure_item_feature_context(item, performance_monitor=performance_monitor)
    name = item.get("name", "")
    desc = item.get("description", "") or ""
    section = item.get("section", "") or ""
    sheet_name = item.get("sheet_name", "") or ""
    original_name = item.get("original_name", name)
    canonical_features = item.get("canonical_features") or {}
    context_prior = item.get("context_prior") or {}
    input_gate = _evaluate_input_gate(name, desc)
    query_name = input_gate.get("query_name", name)
    with (
        performance_monitor.measure("查询构建")
        if performance_monitor is not None else nullcontext()
    ):
        primary_query_profile = build_primary_query_profile(query_name, desc)
    context_prior = dict(context_prior)
    context_prior["primary_query_profile"] = primary_query_profile
    primary_subject = str(primary_query_profile.get("primary_subject") or "").strip()
    if primary_subject and not context_prior.get("primary_subject"):
        context_prior["primary_subject"] = primary_subject
    decisive_terms = [
        str(value).strip()
        for value in list(primary_query_profile.get("decisive_terms") or [])
        if str(value).strip()
    ]
    if decisive_terms:
        context_prior["decisive_terms"] = decisive_terms[:4]
    noise_marker = str(primary_query_profile.get("noise_marker") or "").strip()
    if noise_marker:
        context_prior["noise_marker"] = noise_marker
    plugin_hints = resolve_plugin_hints(
        province=str(item.get("_resolved_province") or item.get("province") or ""),
        item=item,
        canonical_features=canonical_features,
    )
    unified_plan = build_unified_search_plan(
        province=str(item.get("_resolved_province") or item.get("province") or ""),
        item=item,
        context_prior=context_prior,
        canonical_features=canonical_features,
        plugin_hints=plugin_hints,
    )
    if unified_plan and unified_plan.get("plugin_hints"):
        plugin_hints = dict(unified_plan.get("plugin_hints") or {})
    if plugin_hints:
        context_prior = dict(context_prior)
        merged_context_hints = list(context_prior.get("context_hints") or [])
        merged_context_hints.extend(plugin_hints.get("preferred_specialties", []) or [])
        merged_context_hints = list(dict.fromkeys(
            str(value).strip() for value in merged_context_hints if str(value).strip()
        ))[:5]
        if merged_context_hints:
            context_prior["context_hints"] = merged_context_hints
        context_prior["plugin_hints"] = plugin_hints
    if unified_plan:
        context_prior = dict(context_prior)
        context_prior["unified_plan"] = unified_plan
    with (
        performance_monitor.measure("查询构建")
        if performance_monitor is not None else nullcontext()
    ):
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
        "primary_query_profile": primary_query_profile,
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
        "sheet_name": sheet_name,
        "unit": item.get("unit"),
        "quantity": item.get("quantity"),
        "canonical_query": canonical_query,
        "full_query": canonical_query["validation_query"],
        "normalized_query": canonical_query["normalized_query"],
        "search_query": canonical_query["search_query"],
        "canonical_features": canonical_features,
        "context_prior": context_prior,
        "plugin_hints": plugin_hints,
        "unified_plan": unified_plan,
        "query_route": query_route,
        "item": item,  # L5：供跨省预热读取 _cross_province_hints
        "input_gate": input_gate,
    }


def _has_strong_routing_evidence(classification: dict) -> bool:
    primary = str((classification or {}).get("primary") or "").strip()
    if not primary:
        return False
    reasons = ((classification or {}).get("routing_evidence") or {}).get(primary) or []
    strong_prefixes = (
        "item_override:",
        "section:",
        "sheet:",
        "bill_title:",
        "project_title:",
        "section_system_hint:",
        "sheet_system_hint:",
        "bill_system_hint:",
        "project_title_system_hint:",
    )
    return any(str(reason).startswith(strong_prefixes) for reason in reasons)


def _is_standard_seeded_specialty(seed_primary: str) -> bool:
    seed_primary = str(seed_primary or "").strip().upper()
    return bool(re.fullmatch(r"C\d+", seed_primary))


def _is_seeded_specialty_trustworthy(
    item: dict,
    seed_primary: str,
    section: str,
    sheet_name: str,
    *,
    province: str | None = None,
) -> bool:
    seed_primary = str(seed_primary or "").strip()
    if not seed_primary:
        return False
    if seed_primary not in BORROW_PRIORITY or not _is_standard_seeded_specialty(seed_primary):
        return False
    if province and not province_uses_standard_route_books(province):
        return False
    if province and not book_matches_province_scope(seed_primary, province):
        return False

    item = dict(item or {})
    context_prior = dict(item.get("context_prior") or {})
    batch_context = dict(context_prior.get("batch_context") or {})
    supportive_fields = (
        section,
        sheet_name,
        item.get("section"),
        item.get("sheet_name"),
        item.get("specialty_name"),
        context_prior.get("system_hint"),
        batch_context.get("section_system_hint"),
        batch_context.get("sheet_system_hint"),
        batch_context.get("project_system_hint"),
        batch_context.get("neighbor_system_hint"),
    )
    if any(str(value or "").strip() for value in supportive_fields):
        return True

    fallbacks = [
        str(book).strip()
        for book in (item.get("specialty_fallbacks") or [])
        if str(book).strip()
    ]
    return bool(fallbacks)


def _build_seeded_specialty_classification(primary: str, fallbacks: list[str], *, strict: bool) -> dict:
    candidate_books = [primary] + [book for book in fallbacks if book != primary]
    classification = {
        "primary": primary,
        "fallbacks": list(fallbacks),
        "candidate_books": candidate_books,
        "search_books": list(candidate_books),
        "routing_evidence": {
            primary: ["item_specialty"] if strict else ["soft_item_specialty"]
        },
        "book_scores": {primary: 10.0 if strict else 1.2},
        "confidence": "high" if strict else "medium",
        "reason": "item_specialty" if strict else "soft_item_specialty",
        "route_mode": "strict" if strict else "moderate",
        "allow_cross_book_escape": not strict,
        "hard_book_constraints": [primary] if strict else [],
    }
    return classification



def _should_expand_seeded_c8_accessory_scope(
    primary: str,
    fallbacks: list[str],
    name: str,
    desc: str,
    section: str,
    sheet_name: str = "",
) -> bool:
    if str(primary or "").strip() != "C8":
        return False
    if "C10" not in [str(book or "").strip() for book in fallbacks or []]:
        return False

    text = " ".join(
        str(value or "").strip()
        for value in (name, desc, section, sheet_name)
        if str(value or "").strip()
    ).replace("\u789f\u9600", "\u8776\u9600")
    hvac_hints = (
        "\u98ce\u9600",
        "\u9632\u706b\u9600",
        "\u6392\u70df",
        "\u98ce\u7ba1",
        "\u591a\u53f6\u8c03\u8282\u9600",
    )
    if any(token in text for token in hvac_hints):
        return False

    accessory_hints = (
        "\u9600",
        "\u9600\u95e8",
        "\u8776\u9600",
        "\u6b62\u56de\u9600",
        "\u7403\u9600",
        "\u622a\u6b62\u9600",
        "\u8fc7\u6ee4\u5668",
        "\u9664\u6c61\u5668",
        "\u8f6f\u63a5\u5934",
    )
    return any(token in text for token in accessory_hints)
def _merge_seeded_classification_scope(classification: dict, inferred: dict) -> dict:
    base = dict(classification or {})
    inferred = dict(inferred or {})
    primary = str(base.get("primary") or "").strip()
    inferred_primary = str(inferred.get("primary") or "").strip()
    inferred_hard = _dedupe_books(inferred.get("hard_book_constraints") or inferred.get("hard_search_books") or [])
    inferred_search = _dedupe_books(inferred.get("search_books") or inferred.get("candidate_books") or [])
    if not inferred_hard and primary == "C8" and "C10" in inferred_search:
        inferred_hard = ["C8", "C10"]
    if not primary or inferred_primary != primary:
        return base
    if len(inferred_hard) <= 1 or primary not in inferred_hard:
        return base

    inferred_search = _dedupe_books(inferred.get("search_books") or inferred.get("candidate_books") or [])
    if not inferred_search:
        inferred_search = [primary] + [book for book in inferred_hard if book != primary]
    if len(inferred_search) <= 1:
        return base

    base["fallbacks"] = [book for book in inferred_search if book != primary]
    base["candidate_books"] = list(inferred.get("candidate_books") or inferred_search)
    base["search_books"] = list(inferred_search)
    base["hard_book_constraints"] = list(inferred_hard)
    base["hard_search_books"] = _dedupe_books(inferred.get("hard_search_books") or inferred_hard)
    base["advisory_search_books"] = _dedupe_books(
        inferred.get("advisory_search_books")
        or [book for book in inferred_search if book not in inferred_hard]
    )
    base["route_mode"] = str(inferred.get("route_mode") or base.get("route_mode") or "")
    base["allow_cross_book_escape"] = bool(
        inferred.get("allow_cross_book_escape", base.get("allow_cross_book_escape", True))
    )
    if inferred.get("routing_evidence"):
        base["routing_evidence"] = dict(inferred.get("routing_evidence") or {})
    if inferred.get("book_scores"):
        base["book_scores"] = dict(inferred.get("book_scores") or {})
    if inferred.get("reason"):
        base["reason"] = str(inferred.get("reason") or base.get("reason") or "")
    if inferred.get("confidence"):
        base["confidence"] = inferred.get("confidence")
    return base
def _should_override_seeded_specialty(seed_primary: str, inferred: dict) -> bool:
    seed_primary = str(seed_primary or "").strip()
    inferred = dict(inferred or {})
    inferred_primary = str(inferred.get("primary") or "").strip()
    if not seed_primary or not inferred_primary or inferred_primary == seed_primary:
        return False
    hard_constraints = [str(book).strip() for book in (inferred.get("hard_book_constraints") or []) if str(book).strip()]
    if seed_primary in hard_constraints:
        return False
    if _has_strong_routing_evidence(inferred):
        return True
    return False


def _drop_incompatible_standard_classification(classification: dict, province: str | None) -> dict:
    classification = dict(classification or {})
    province = str(province or "").strip()
    primary = str(classification.get("primary") or "").strip()
    if not province or not primary or primary not in BORROW_PRIORITY or not _is_standard_seeded_specialty(primary):
        return classification
    if not province_uses_standard_route_books(province):
        return {"primary": None, "fallbacks": []}
    if not book_matches_province_scope(primary, province):
        return {"primary": None, "fallbacks": []}
    return classification


def _dedupe_books(values) -> list[str]:
    books: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        book = str(value or "").strip()
        if not book or book in seen:
            continue
        seen.add(book)
        books.append(book)
    return books


def _filter_books_to_province_scope(values, province: str | None) -> list[str]:
    books = _dedupe_books(values)
    province = str(province or "").strip()
    if not province:
        return books
    if not province_uses_standard_route_books(province):
        return [book for book in books if book not in BORROW_PRIORITY]
    filtered: list[str] = []
    for book in books:
        if book in BORROW_PRIORITY and not book_matches_province_scope(book, province):
            continue
        filtered.append(book)
    return filtered


def _filter_classification_to_province_scope(classification: dict, province: str | None) -> dict:
    base = dict(classification or {})
    province = str(province or "").strip()
    if not province:
        return base

    primary = str(base.get("primary") or "").strip()
    fallbacks = _dedupe_books(base.get("fallbacks", []))
    candidate_books = _dedupe_books(base.get("candidate_books", []))
    search_books = _dedupe_books(base.get("search_books", []))
    hard_book_constraints = _dedupe_books(base.get("hard_book_constraints", []))
    hard_search_books = _dedupe_books(base.get("hard_search_books", []))
    advisory_search_books = _dedupe_books(base.get("advisory_search_books", []))

    ordered_scoped_books = _filter_books_to_province_scope(
        [primary]
        + fallbacks
        + candidate_books
        + search_books
        + hard_book_constraints
        + hard_search_books
        + advisory_search_books,
        province,
    )
    scoped_set = set(ordered_scoped_books)
    if primary and primary not in scoped_set:
        primary = ""
    if not primary and ordered_scoped_books:
        primary = ordered_scoped_books[0]

    fallbacks = [
        book for book in _filter_books_to_province_scope(fallbacks, province)
        if book != primary
    ]
    candidate_books = [
        book for book in _filter_books_to_province_scope(candidate_books, province)
        if book != primary
    ]
    search_books = [
        book for book in _filter_books_to_province_scope(search_books, province)
        if book != primary
    ]
    hard_book_constraints = _filter_books_to_province_scope(hard_book_constraints, province)
    hard_search_books = _filter_books_to_province_scope(hard_search_books, province)
    advisory_search_books = [
        book for book in _filter_books_to_province_scope(advisory_search_books, province)
        if book != primary
    ]

    if primary:
        if primary not in candidate_books:
            candidate_books.insert(0, primary)
        if primary not in search_books:
            search_books.insert(0, primary)
        if (
            primary in _dedupe_books(base.get("hard_book_constraints", []))
            and primary not in hard_book_constraints
        ):
            hard_book_constraints.insert(0, primary)
        if (
            primary in _dedupe_books(base.get("hard_search_books", []))
            and primary not in hard_search_books
        ):
            hard_search_books.insert(0, primary)

    kept_books = {
        primary,
        *fallbacks,
        *candidate_books,
        *search_books,
        *hard_book_constraints,
        *hard_search_books,
        *advisory_search_books,
    } - {""}

    routing_evidence = {}
    for book, reasons in dict(base.get("routing_evidence") or {}).items():
        book_key = str(book or "").strip()
        if book_key and book_key in kept_books:
            routing_evidence[book_key] = list(reasons or [])

    book_scores = {}
    for book, score in dict(base.get("book_scores") or {}).items():
        book_key = str(book or "").strip()
        if book_key and book_key in kept_books:
            book_scores[book_key] = score

    base["primary"] = primary or None
    base["fallbacks"] = fallbacks
    base["candidate_books"] = candidate_books
    base["search_books"] = search_books
    base["hard_book_constraints"] = hard_book_constraints
    base["hard_search_books"] = hard_search_books
    base["advisory_search_books"] = advisory_search_books
    base["routing_evidence"] = routing_evidence
    base["book_scores"] = book_scores
    return base


_STRONG_C10_TO_C8_TERMS = (
    "工业管道",
    "工艺管道",
    "蒸汽",
    "高压",
    "中压",
    "化工",
    "石油",
    "炼油",
    "炼化",
    "锅炉",
    "压力容器",
    "介质",
    "无缝钢管",
    "合金钢",
    "不锈钢",
    "酸洗",
    "脱脂",
)

_CONDITIONAL_C10_TO_C8_TERMS = (
    "焊接",
    "法兰",
    "对焊",
)


def _has_c10_industrial_pipe_signal(
    item: dict,
    name: str,
    desc: str,
    section: str,
    sheet_name: str = "",
) -> bool:
    context_prior = dict((item or {}).get("context_prior") or {})
    canonical_features = dict((item or {}).get("canonical_features") or {})
    text = " ".join(
        str(value or "")
        for value in (
            name,
            desc,
            section,
            sheet_name,
            context_prior.get("primary_subject"),
            context_prior.get("system_hint"),
            canonical_features.get("system"),
            canonical_features.get("entity"),
        )
        if str(value or "").strip()
    )
    if not text:
        return False
    strong_hits = sum(1 for term in _STRONG_C10_TO_C8_TERMS if term in text)
    if "工业管道" in text or "工艺管道" in text:
        return True
    if strong_hits >= 2:
        return True
    return (
        any(term in text for term in _CONDITIONAL_C10_TO_C8_TERMS)
        and strong_hits >= 1
    )


def _suppress_c10_to_c8_borrow(
    classification: dict,
    item: dict,
    name: str,
    desc: str,
    section: str,
    sheet_name: str = "",
) -> dict:
    base = dict(classification or {})
    primary = str(base.get("primary") or "").strip()
    if primary != "C10":
        return base
    if _has_c10_industrial_pipe_signal(item, name, desc, section, sheet_name):
        return base

    for key in (
        "fallbacks",
        "candidate_books",
        "search_books",
        "hard_book_constraints",
        "hard_search_books",
        "advisory_search_books",
    ):
        if key in base:
            base[key] = [
                book for book in list(base.get(key) or [])
                if str(book).strip() != "C8"
            ]

    routing_evidence = {}
    for book, reasons in dict(base.get("routing_evidence") or {}).items():
        book_key = str(book or "").strip()
        if book_key and book_key != "C8":
            routing_evidence[book_key] = list(reasons or [])
    if routing_evidence:
        base["routing_evidence"] = routing_evidence

    book_scores = {}
    for book, score in dict(base.get("book_scores") or {}).items():
        book_key = str(book or "").strip()
        if book_key and book_key != "C8":
            book_scores[book_key] = score
    if book_scores or "book_scores" in base:
        base["book_scores"] = book_scores

    return base


def _build_unified_plan_fallback_classification(item: dict, province: str | None) -> dict | None:
    unified_plan = dict((item or {}).get("unified_plan") or {})
    if not unified_plan:
        return None
    plugin_hints = dict(unified_plan.get("plugin_hints") or (item or {}).get("plugin_hints") or {})
    plugin_source = str(plugin_hints.get("source") or "").strip()
    preferred_books = []
    for value in list(unified_plan.get("preferred_books") or []):
        book = str(value or "").strip()
        if book and book not in preferred_books:
            preferred_books.append(book)
    preferred_books = _filter_books_to_province_scope(preferred_books, province)
    if not preferred_books:
        return None
    reason_tags = [
        str(value).strip()
        for value in list(unified_plan.get("reason_tags") or [])
        if str(value).strip()
    ]
    non_seed_reason_tags = [tag for tag in reason_tags if tag != "seed_specialty"]
    if not non_seed_reason_tags:
        return None
    primary = str(unified_plan.get("primary_book") or "").strip()
    if not primary:
        primary = preferred_books[0]
    province = str(province or "").strip()
    if (
        province
        and primary
        and primary in BORROW_PRIORITY
        and _is_standard_seeded_specialty(primary)
        and (
            (not province_uses_standard_route_books(province))
            or (not book_matches_province_scope(primary, province))
        )
    ):
        return None
    fallbacks = [book for book in preferred_books if book != primary]
    route_mode = str(unified_plan.get("route_mode") or "moderate").strip().lower()
    if route_mode not in {"strict", "moderate", "open"}:
        route_mode = "moderate"
    hard_book_constraints = []
    for value in list(unified_plan.get("hard_books") or []):
        book = str(value or "").strip()
        if book and book not in hard_book_constraints:
            hard_book_constraints.append(book)
    hard_book_constraints = _filter_books_to_province_scope(hard_book_constraints, province)
    strong_reason_tags = {
        "explicit_book_anchor",
        "strong_system_anchor",
        "family_cluster",
    }
    if (
        not hard_book_constraints
        and plugin_source == "generated_benchmark_knowledge"
        and not any(tag in strong_reason_tags for tag in non_seed_reason_tags)
    ):
        return None
    routing_reason = "unified_plan"
    if non_seed_reason_tags:
        routing_reason = f"unified_plan:{'+'.join(non_seed_reason_tags[:2])}"
    return _filter_classification_to_province_scope({
        "primary": primary,
        "fallbacks": fallbacks,
        "candidate_books": list(preferred_books),
        "search_books": list(preferred_books),
        "routing_evidence": {
            book: [routing_reason]
            for book in preferred_books
        },
        "book_scores": {
            book: (2.0 if book == primary else 1.0)
            for book in preferred_books
        },
        "confidence": "medium",
        "reason": routing_reason,
        "route_mode": route_mode,
        "allow_cross_book_escape": bool(
            unified_plan.get("allow_cross_book_escape", route_mode != "strict")
        ),
        "hard_book_constraints": hard_book_constraints,
    }, province)


def _build_broad_group_unified_plan_override(item: dict | None, province: str | None) -> dict | None:
    unified_plan = dict((item or {}).get("unified_plan") or {})
    if not unified_plan:
        return None
    preferred_books = []
    for value in list(unified_plan.get("preferred_books") or []):
        book = str(value or "").strip()
        if book and book not in preferred_books:
            preferred_books.append(book)
    preferred_books = _filter_books_to_province_scope(preferred_books, province)
    if not preferred_books:
        return None

    broad_route_books = {"A", "D", "E"}
    if not all(book in broad_route_books for book in preferred_books):
        return None

    plugin_hints = dict(unified_plan.get("plugin_hints") or (item or {}).get("plugin_hints") or {})
    if str(plugin_hints.get("source") or "").strip() != "generated_benchmark_knowledge":
        return None

    reason_tags = [
        str(value).strip()
        for value in list(unified_plan.get("reason_tags") or [])
        if str(value).strip()
    ]
    non_seed_reason_tags = [tag for tag in reason_tags if tag != "seed_specialty"]
    if "province_plugin" not in non_seed_reason_tags:
        return None

    primary = str(unified_plan.get("primary_book") or "").strip() or preferred_books[0]
    fallbacks = [book for book in preferred_books if book != primary]
    route_mode = str(unified_plan.get("route_mode") or "moderate").strip().lower()
    if route_mode not in {"strict", "moderate", "open"}:
        route_mode = "moderate"
    routing_reason = "unified_plan:province_plugin"
    return _filter_classification_to_province_scope({
        "primary": primary,
        "fallbacks": fallbacks,
        "candidate_books": list(preferred_books),
        "search_books": list(preferred_books),
        "routing_evidence": {
            book: [routing_reason]
            for book in preferred_books
        },
        "book_scores": {
            book: (2.0 if book == primary else 1.0)
            for book in preferred_books
        },
        "confidence": "medium",
        "reason": routing_reason,
        "route_mode": route_mode,
        "allow_cross_book_escape": bool(
            unified_plan.get("allow_cross_book_escape", route_mode != "strict")
        ),
        "hard_book_constraints": [],
    }, province)


def _should_prefer_unified_plan_fallback(
    current: dict | None,
    fallback: dict | None,
    item: dict | None,
) -> bool:
    current = dict(current or {})
    fallback = dict(fallback or {})
    current_primary = str(current.get("primary") or "").strip()
    if not current_primary:
        return True

    current_route_mode = str(current.get("route_mode") or "").strip().lower()
    if current_route_mode == "strict" or list(current.get("hard_book_constraints") or []):
        return False

    fallback_books = [
        str(book).strip()
        for book in (
            list(fallback.get("search_books") or [])
            or list(fallback.get("candidate_books") or [])
        )
        if str(book).strip()
    ]
    if not fallback_books or current_primary in fallback_books:
        return False

    broad_route_books = {"A", "D", "E"}
    broad_fallback_only = all(book in broad_route_books for book in fallback_books)
    current_is_standard_book = (
        current_primary in BORROW_PRIORITY and _is_standard_seeded_specialty(current_primary)
    )
    if not (broad_fallback_only and current_is_standard_book):
        return False

    unified_plan = dict((item or {}).get("unified_plan") or {})
    plugin_hints = dict(unified_plan.get("plugin_hints") or (item or {}).get("plugin_hints") or {})
    plugin_source = str(plugin_hints.get("source") or "").strip()
    reason_tags = [
        str(value).strip()
        for value in list(unified_plan.get("reason_tags") or [])
        if str(value).strip()
    ]
    non_seed_reason_tags = [tag for tag in reason_tags if tag != "seed_specialty"]

    if plugin_source == "generated_benchmark_knowledge":
        return "province_plugin" in non_seed_reason_tags
    return True


def _build_classification(item: dict, name: str, desc: str, section: str,
                          sheet_name: str = "",
                          province: str = None) -> dict:
    """获取并标准化专业分类结果。"""
    primary = str(item.get("specialty") or "").strip()
    fallbacks = [
        str(book).strip()
        for book in (item.get("specialty_fallbacks") or [])
        if str(book).strip()
    ]
    if primary and not fallbacks:
        fallbacks = select_search_books(primary, province, borrow=True)[1:]
    classification = {
        "primary": primary,
        "fallbacks": fallbacks,
    }
    inferred = classify_specialty(
        name, desc, section_title=section, province=province,
        bill_code=item.get("code"),
        context_prior=item.get("context_prior"),
        canonical_features=item.get("canonical_features"),
        sheet_name=sheet_name or item.get("sheet_name"),
    )
    inferred = _drop_incompatible_standard_classification(inferred, province)
    if primary:
        if _is_seeded_specialty_trustworthy(item, primary, section, sheet_name, province=province):
            classification = _build_seeded_specialty_classification(primary, fallbacks, strict=True)
            if _should_expand_seeded_c8_accessory_scope(primary, fallbacks, name, desc, section, sheet_name):
                expanded_hard = ["C8", "C10"]
                classification["search_books"] = _dedupe_books(expanded_hard + list(classification.get("search_books") or []))
                classification["candidate_books"] = _dedupe_books(
                    list(classification.get("candidate_books") or [])
                    + [book for book in fallbacks if str(book).strip()]
                )
                classification["hard_book_constraints"] = list(expanded_hard)
                classification["hard_search_books"] = list(expanded_hard)
                classification["advisory_search_books"] = [
                    book for book in classification["search_books"]
                    if book not in expanded_hard
                ]
        elif (
            primary in BORROW_PRIORITY
            and _is_standard_seeded_specialty(primary)
            and (not province or province_uses_standard_route_books(province))
            and (not province or book_matches_province_scope(primary, province))
        ):
            classification = _build_seeded_specialty_classification(primary, fallbacks, strict=False)
        else:
            classification = {"primary": None, "fallbacks": []}
    classification = _merge_seeded_classification_scope(classification, inferred)
    if not classification["primary"] or _should_override_seeded_specialty(primary, inferred):
        classification = inferred
    unified_plan_fallback = _build_unified_plan_fallback_classification(item, province)
    if not unified_plan_fallback and classification.get("primary"):
        unified_plan_fallback = _build_broad_group_unified_plan_override(item, province)
    if unified_plan_fallback and (
        not classification.get("primary")
        or _should_prefer_unified_plan_fallback(classification, unified_plan_fallback, item)
    ):
        classification = unified_plan_fallback
    classification = _filter_classification_to_province_scope(classification, province)
    classification = _suppress_c10_to_c8_borrow(
        classification,
        item,
        name,
        desc,
        section,
        sheet_name or item.get("sheet_name") or "",
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

DEFAULT_ALTERNATIVE_COUNT = 9


def _build_ranked_candidate_snapshots(candidates: list[dict], top_n: int = 20) -> list[dict]:
    snapshots = []
    for candidate in list(candidates or [])[:top_n]:
        snapshots.append({
            "quota_id": str(candidate.get("quota_id", "") or ""),
            "name": str(candidate.get("name", "") or ""),
            "unit": str(candidate.get("unit", "") or ""),
            "param_match": bool(candidate.get("param_match", True)),
            "param_tier": int(candidate.get("param_tier", 1) or 1),
            "bm25_score": candidate.get("bm25_score"),
            "vector_score": candidate.get("vector_score"),
            "hybrid_score": candidate.get("hybrid_score"),
            "rerank_score": candidate.get("rerank_score"),
            "semantic_rerank_score": candidate.get("semantic_rerank_score"),
            "spec_rerank_score": candidate.get("spec_rerank_score"),
            "param_score": candidate.get("param_score"),
            "logic_score": candidate.get("logic_score"),
            "feature_alignment_score": candidate.get("feature_alignment_score"),
            "manual_structured_score": candidate.get("manual_structured_score"),
            "ltr_score": candidate.get("ltr_score"),
            "rank_stage": str(candidate.get("rank_stage", "") or ""),
            "rank_score_source": str(candidate.get("_rank_score_source", "") or ""),
            "rank_score": candidate.get("rank_score", compute_candidate_rank_score(candidate)),
            "rank_score_breakdown": explain_candidate_rank_score(candidate),
            "cgr_score": candidate.get("cgr_score"),
            "cgr_probability": candidate.get("cgr_probability"),
            "cgr_feasible": candidate.get("cgr_feasible"),
            "cgr_fatal_hard_conflict": candidate.get("cgr_fatal_hard_conflict"),
            "cgr_high_conf_wrong_book": candidate.get("cgr_high_conf_wrong_book"),
            "cgr_high_conf_family_book_conflict": candidate.get("cgr_high_conf_family_book_conflict"),
            "cgr_sem_score": candidate.get("cgr_sem_score"),
            "cgr_str_score": candidate.get("cgr_str_score"),
            "cgr_prior_score": candidate.get("cgr_prior_score"),
            "cgr_tier_penalty": candidate.get("cgr_tier_penalty"),
            "cgr_generic_penalty": candidate.get("cgr_generic_penalty"),
            "cgr_soft_conflict_penalty": candidate.get("cgr_soft_conflict_penalty"),
            "candidate_major_prefix": str(candidate.get("candidate_major_prefix", "") or ""),
            "target_db_type": str(candidate.get("target_db_type", "") or ""),
            "candidate_scope_match": candidate.get("candidate_scope_match"),
            "candidate_scope_conflict": candidate.get("candidate_scope_conflict"),
            "candidate_canonical_features": dict(
                candidate.get("candidate_canonical_features") or candidate.get("canonical_features") or {}
            ),
            "ltr_feature_snapshot": dict(candidate.get("ltr_feature_snapshot") or {}),
        })
    return snapshots


def _build_alternatives(candidates: list[dict], selected_ids: set = None,
                        skip_obj=None, top_n: int = DEFAULT_ALTERNATIVE_COUNT) -> list[dict]:
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


def _result_top1_id(result: dict | None) -> str:
    quotas = (result or {}).get("quotas") or []
    if not quotas:
        return ""
    return str(quotas[0].get("quota_id", "") or "").strip()


def _carry_ranking_snapshot(target: dict, source: dict, *, changed_by: str = ""):
    if not isinstance(target, dict) or not isinstance(source, dict):
        return
    for key in (
        "pre_ltr_top1_id",
        "post_ltr_top1_id",
        "post_arbiter_top1_id",
        "candidate_count",
        "candidates_count",
        "ltr_rerank",
    ):
        if not target.get(key):
            target[key] = source.get(key)
    if changed_by and _result_top1_id(target) != _result_top1_id(source):
        target["final_changed_by"] = target.get("final_changed_by") or changed_by
    target["post_final_top1_id"] = _result_top1_id(target)


# ============================================================
# 兜底策略
# ============================================================

_RULE_INJECTION_VALIDATOR: ParamValidator | None = None
_PRICE_VALIDATOR = None
_PRICE_VALIDATOR_LOAD_ATTEMPTED = False


def _get_rule_injection_validator() -> ParamValidator:
    global _RULE_INJECTION_VALIDATOR
    if _RULE_INJECTION_VALIDATOR is None:
        _RULE_INJECTION_VALIDATOR = ParamValidator()
    return _RULE_INJECTION_VALIDATOR


def _get_price_validator():
    global _PRICE_VALIDATOR, _PRICE_VALIDATOR_LOAD_ATTEMPTED
    if _PRICE_VALIDATOR is not None:
        return _PRICE_VALIDATOR
    if _PRICE_VALIDATOR_LOAD_ATTEMPTED:
        return None
    if not bool(getattr(config, "QUOTA_MATCH_PRICE_VALIDATION_ENABLED", False)):
        _PRICE_VALIDATOR_LOAD_ATTEMPTED = True
        return None
    _PRICE_VALIDATOR_LOAD_ATTEMPTED = True
    try:
        from src.price_reference_db import PriceReferenceDB
        from src.price_validator import PriceValidator

        _PRICE_VALIDATOR = PriceValidator(PriceReferenceDB())
    except Exception as exc:
        logger.warning(f"price validator unavailable, skip price validation: {exc}")
        _PRICE_VALIDATOR = None
    return _PRICE_VALIDATOR


def _apply_price_validation(result: dict, item: dict, best: dict | None) -> dict:
    if not best:
        return result
    validator = _get_price_validator()
    if validator is None:
        return result

    validation = validator.validate(item, best, confidence=result.get("confidence"))
    result["price_validation"] = validation
    _append_trace_step(
        result,
        "price_validate",
        status=str(validation.get("status", "")),
        message=str(validation.get("message", "")),
        sample_count=int(validation.get("sample_count", 0) or 0),
        median_price=validation.get("median_price"),
        actual_price=validation.get("actual_price"),
        confidence_penalty=validation.get("confidence_penalty", 0),
    )

    if validation.get("status") != "price_mismatch":
        return result

    previous_confidence = float(result.get("confidence", 0) or 0.0)
    penalty = float(validation.get("confidence_penalty", -10) or 0.0)
    adjusted_confidence = max(0.0, min(100.0, previous_confidence + penalty))
    validation["previous_confidence"] = previous_confidence
    validation["adjusted_confidence"] = adjusted_confidence
    result["confidence"] = adjusted_confidence
    result["confidence_score"] = int(round(adjusted_confidence))
    result["reason_tags"] = merge_reason_tags(
        result.get("reason_tags") or [],
        ["price_mismatch", "manual_review"],
    )
    _set_result_reason(
        result,
        "price_mismatch",
        result.get("reason_tags") or [],
        str(validation.get("message") or "price validation mismatch"),
    )
    return result


def _rule_backup_primary_quota(rule_backup: dict) -> dict:
    quotas = (rule_backup or {}).get("quotas") or []
    if not quotas:
        return {}
    quota = quotas[0] or {}
    return quota if isinstance(quota, dict) else {}


def _promote_rule_candidate_prior(candidate: dict, candidates: list[dict]) -> dict:
    peers = list(candidates or [])
    def _median(values: list[float], default: float) -> float:
        if not values:
            return default
        values = sorted(values)
        return values[len(values) // 2]

    median_rerank = _median(
        [float(c.get("rerank_score", c.get("hybrid_score", 0.0)) or 0.0) for c in peers],
        float(candidate.get("rerank_score", candidate.get("hybrid_score", 0.0)) or 0.0),
    )
    median_hybrid = _median(
        [float(c.get("hybrid_score", c.get("rerank_score", 0.0)) or 0.0) for c in peers],
        float(candidate.get("hybrid_score", candidate.get("rerank_score", 0.0)) or 0.0),
    )
    median_semantic = _median(
        [float(c.get("semantic_rerank_score", c.get("rerank_score", 0.0)) or 0.0) for c in peers],
        float(candidate.get("semantic_rerank_score", candidate.get("rerank_score", 0.0)) or 0.0),
    )
    median_spec = _median(
        [float(c.get("spec_rerank_score", c.get("rerank_score", 0.0)) or 0.0) for c in peers],
        float(candidate.get("spec_rerank_score", candidate.get("rerank_score", 0.0)) or 0.0),
    )
    candidate["rerank_score"] = median_rerank
    candidate["hybrid_score"] = median_hybrid
    candidate["semantic_rerank_score"] = median_semantic
    candidate["spec_rerank_score"] = median_spec
    candidate["active_rerank_score"] = candidate["rerank_score"]
    return candidate


def _materialize_rule_backup_candidate(item: dict, rule_backup: dict, candidates: list[dict]) -> dict | None:
    quota = _rule_backup_primary_quota(rule_backup)
    quota_id = str(quota.get("quota_id", "") or "").strip()
    quota_name = str(quota.get("name", "") or "").strip()
    if not quota_id or not quota_name:
        return None

    canonical_query = (item or {}).get("canonical_query") or {}
    validation_query = str(canonical_query.get("validation_query") or item.get("name") or "").strip()
    search_query = str(canonical_query.get("search_query") or validation_query).strip()

    candidate = {
        "quota_id": quota_id,
        "name": quota_name,
        "unit": str(quota.get("unit", "") or ""),
        "id": quota.get("db_id"),
        "db_id": quota.get("db_id"),
        "match_source": "rule_injected",
        "is_rule_candidate": 1,
        "rule_confidence": float(rule_backup.get("confidence", 0) or 0.0),
        "rule_prior_score": float(rule_backup.get("confidence", 0) or 0.0) / 100.0,
        "rule_family": rule_backup.get("rule_family", ""),
        "rule_score": rule_backup.get("rule_score", 0.0),
        "rule_reason": quota.get("reason", rule_backup.get("explanation", "")),
        "candidate_canonical_features": text_parser.parse_canonical(quota_name),
    }
    candidate = _promote_rule_candidate_prior(candidate, candidates)
    validated = _get_rule_injection_validator().validate_candidates(
        validation_query,
        [candidate],
        supplement_query=search_query or None,
        bill_params=item.get("params"),
        canonical_features=item.get("canonical_features"),
        context_prior=item.get("context_prior"),
    )
    if not validated:
        return None
    injected = validated[0]
    injected["match_source"] = "rule_injected"
    injected["is_rule_candidate"] = 1
    injected["rule_confidence"] = candidate["rule_confidence"]
    injected["rule_prior_score"] = candidate["rule_prior_score"]
    injected["rule_family"] = candidate["rule_family"]
    injected["rule_score"] = candidate["rule_score"]
    injected["rule_reason"] = candidate["rule_reason"]
    return _promote_rule_candidate_prior(injected, candidates)


def _inject_rule_backup_candidate(item: dict, candidates: list[dict], rule_backup: dict) -> tuple[list[dict], str]:
    if not rule_backup:
        return list(candidates or []), ""
    quota = _rule_backup_primary_quota(rule_backup)
    quota_id = str(quota.get("quota_id", "") or "").strip()
    if not quota_id:
        return list(candidates or []), ""

    working = list(candidates or [])
    for idx, existing in enumerate(working):
        if str(existing.get("quota_id", "") or "").strip() != quota_id:
            continue
        merged = dict(existing)
        merged["match_source"] = "rule_injected"
        merged["is_rule_candidate"] = 1
        merged["rule_confidence"] = float(rule_backup.get("confidence", 0) or 0.0)
        merged["rule_prior_score"] = float(rule_backup.get("confidence", 0) or 0.0) / 100.0
        merged["rule_family"] = rule_backup.get("rule_family", "")
        merged["rule_score"] = rule_backup.get("rule_score", 0.0)
        merged["rule_reason"] = quota.get("reason", rule_backup.get("explanation", ""))
        merged = _promote_rule_candidate_prior(merged, working)
        return [merged] + [c for j, c in enumerate(working) if j != idx], quota_id

    injected = _materialize_rule_backup_candidate(item, rule_backup, working)
    if not injected:
        return working, ""
    return [injected] + working, quota_id

def _apply_rule_backup(result: dict, rule_backup: dict, rule_hits: int,
                       prefer_label: str) -> tuple[dict, int]:
    """
    低置信规则结果兜底比较：置信度更高则替换当前结果。

    prefer_label 用于日志前缀，如"搜索/经验""LLM/经验""Agent/经验"。
    """
    if not rule_backup:
        return result, rule_hits
    has_prior_knowledge = bool((result or {}).get("knowledge_evidence"))
    if (not has_prior_knowledge) and rule_backup.get("confidence", 0) > result.get("confidence", 0):
        _carry_ranking_snapshot(rule_backup, result, changed_by="rule_backup")
        _append_trace_step(
            rule_backup,
            "rule_backup_override",
            replaced_source=result.get("match_source", ""),
            replaced_confidence=result.get("confidence", 0),
        )
        return rule_backup, rule_hits + 1
    _append_backup_advisory(
        result,
        advisory_type="rule_backup",
        backup=rule_backup,
        stage="rule_backup_advisory",
    )
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
        _carry_ranking_snapshot(exp_backup, result, changed_by="experience_backup")
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
        result["post_final_top1_id"] = _result_top1_id(result)
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
            _carry_ranking_snapshot(exp_backup, result, changed_by="experience_exact")
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

    # search 模式下的 experience_similar 只做 advisory，不覆盖已产出的搜索主结果。
    # 搜索结果已经经过当前 query 的召回和参数排序，经验相似命中只保留为辅助证据。
    if not search_qids:
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

    _append_backup_advisory(
        result,
        advisory_type="experience_similar",
        backup=exp_backup,
        stage="experience_similar_advisory",
    )
    _append_trace_step(
        result,
        "experience_similar_rejected",
        search_confidence=result.get("confidence", 0),
        backup_confidence=exp_backup.get("confidence", 0),
    )
    logger.debug(
        f"搜索结果保留主选，经验库相似命中仅作参考: "
        f"搜索{result.get('confidence', 0)}分, "
        f"经验库{exp_backup.get('confidence', 0)}分")
    return result, exp_hits


def _append_backup_advisory(result: dict, advisory_type: str, backup: dict, stage: str) -> None:
    if not result or not backup:
        return

    quotas = list(backup.get("quotas") or [])
    top_quota = dict(quotas[0] or {}) if quotas else {}
    advisories = list(result.get("backup_advisories") or [])
    advisories.append({
        "type": str(advisory_type or ""),
        "match_source": str(backup.get("match_source", "") or ""),
        "confidence": backup.get("confidence", 0),
        "quota_id": str(top_quota.get("quota_id", "") or ""),
        "quota_name": str(top_quota.get("name", "") or ""),
    })
    result["backup_advisories"] = advisories
    _append_trace_step(
        result,
        stage,
        backup_type=str(advisory_type or ""),
        backup_confidence=backup.get("confidence", 0),
        backup_quota_id=str(top_quota.get("quota_id", "") or ""),
    )


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
        preferred_count = 0
        routed: list[dict] = []
        for candidate in candidates:
            quota_book = _quota_book_from_id(candidate.get("quota_id", ""))
            updated = dict(candidate)
            updated["plugin_route_book"] = quota_book
            if quota_book in preferred_books:
                preferred_count += 1
            routed.append(updated)
        return routed, {
            "applied": False,
            "reason": "soft_preferred_books_only",
            "preferred_books": sorted(preferred_books),
            "preferred_count": preferred_count,
        }

    routed: list[dict] = []
    preferred_count = 0
    for candidate in candidates:
        quota_book = _quota_book_from_id(candidate.get("quota_id", ""))
        updated = dict(candidate)
        updated["plugin_route_book"] = quota_book
        if quota_book in preferred_books:
            preferred_count += 1
        routed.append(updated)

    return routed, {
        "applied": False,
        "reason": "strict_preferred_books_disabled",
        "preferred_books": sorted(preferred_books),
        "preferred_count": preferred_count,
        "strict_requested": True,
    }


def _top_candidate_id(candidates: list[dict]) -> str:
    if not candidates:
        return ""
    first = candidates[0]
    if not isinstance(first, dict):
        return ""
    return str(first.get("quota_id", "") or "")


_TARGET_MAJOR_PREFIXES_BY_DB_TYPE = {
    "install": {"03"},
    "civil": {"01", "02"},
    "municipal": {"04"},
    "landscape": {"05"},
}


def _detect_target_db_type(province: str) -> str:
    province = str(province or "").strip()
    if not province:
        return ""
    if "安装" in province:
        return "install"
    if "市政" in province:
        return "municipal"
    if "园林" in province or "绿化" in province:
        return "landscape"
    if any(
        keyword in province
        for keyword in ("建筑和装饰", "建筑装饰", "装饰工程", "建筑工程", "房屋建筑", "房建")
    ):
        return "civil"
    return ""


def _quota_major_prefix(quota_id: str) -> str:
    quota_id = str(quota_id or "").strip()
    if not quota_id:
        return ""
    prefix = quota_id.split("-", 1)[0].strip()
    if not prefix:
        return ""
    if prefix.isdigit():
        return prefix[:2].zfill(2)
    return ""


def _annotate_candidate_scope_signals(item: dict, candidates: list[dict]) -> list[dict]:
    province = str((item or {}).get("_resolved_province") or (item or {}).get("province") or "").strip()
    target_db_type = _detect_target_db_type(province)
    target_prefixes = _TARGET_MAJOR_PREFIXES_BY_DB_TYPE.get(target_db_type) or set()
    if not candidates or not target_prefixes:
        return [dict(candidate) for candidate in (candidates or [])]

    annotated: list[dict] = []
    for candidate in candidates:
        updated = dict(candidate)
        major_prefix = _quota_major_prefix(updated.get("quota_id", ""))
        updated["candidate_major_prefix"] = major_prefix
        updated["target_db_type"] = target_db_type
        if not major_prefix:
            updated["candidate_scope_match"] = 0.0
            updated["candidate_scope_conflict"] = False
        else:
            updated["candidate_scope_match"] = 1.0 if major_prefix in target_prefixes else 0.0
            updated["candidate_scope_conflict"] = major_prefix not in target_prefixes
        annotated.append(updated)
    return annotated


def _merge_arbiter_annotations(base_candidates: list[dict], arbiter_candidates: list[dict]) -> list[dict]:
    ordered = [dict(candidate) for candidate in (base_candidates or [])]
    if not ordered or not arbiter_candidates:
        return ordered

    arbiter_by_quota_id: dict[str, dict] = {}
    for candidate in arbiter_candidates:
        if not isinstance(candidate, dict):
            continue
        quota_id = str(candidate.get("quota_id", "") or "").strip()
        if quota_id:
            arbiter_by_quota_id[quota_id] = candidate

    if not arbiter_by_quota_id:
        return ordered

    for candidate in ordered:
        quota_id = str(candidate.get("quota_id", "") or "").strip()
        advised = arbiter_by_quota_id.get(quota_id)
        if not advised:
            continue
        if "arbiter_signals" in advised:
            candidate["arbiter_signals"] = list(advised.get("arbiter_signals") or [])
        if "arbiter_recommended" in advised:
            candidate["arbiter_recommended"] = bool(advised.get("arbiter_recommended"))
    return ordered


def _merge_explicit_annotations(base_candidates: list[dict], explicit_candidates: list[dict]) -> list[dict]:
    ordered = [dict(candidate) for candidate in (base_candidates or [])]
    if not ordered or not explicit_candidates:
        return ordered

    explicit_by_quota_id: dict[str, dict] = {}
    for candidate in explicit_candidates:
        if not isinstance(candidate, dict):
            continue
        quota_id = str(candidate.get("quota_id", "") or "").strip()
        if quota_id:
            explicit_by_quota_id[quota_id] = candidate

    if not explicit_by_quota_id:
        return ordered

    for candidate in ordered:
        quota_id = str(candidate.get("quota_id", "") or "").strip()
        hinted = explicit_by_quota_id.get(quota_id)
        if not hinted:
            continue
        if "explicit_signals" in hinted:
            candidate["explicit_signals"] = list(hinted.get("explicit_signals") or [])
        if "explicit_recommended" in hinted:
            candidate["explicit_recommended"] = bool(hinted.get("explicit_recommended"))
    return ordered


def _init_ranking_meta() -> dict:
    return {
        "pre_ltr_top1_id": "",
        "post_ltr_top1_id": "",
        "post_cgr_top1_id": "",
        "post_arbiter_top1_id": "",
        "post_explicit_top1_id": "",
        "post_anchor_top1_id": "",
        "selected_top1_id": "",
        "legacy_top1_id": "",
        "post_final_top1_id": "",
        "final_changed_by": "",
        "candidate_count": 0,
        "ltr": {},
        "explicit_override": {},
        "unified_ranking_enabled": False,
        "unified_ranking_shadow_mode": False,
        "unified_ranking_mode": "disabled",
        "unified_ranking_executed": False,
        "unified_result_used": False,
        "unified_top1_id": "",
        "unified_top1_score": 0.0,
        "unified_top1_confidence": 0.0,
        "unified_top1_matches_selected": False,
        "unified_top1_matches_legacy": False,
        "legacy_top1_unified_score": None,
        "legacy_top1_unified_confidence": None,
        "unified_legacy_score_gap": None,
        "unified_ranking_diagnostics": {},
        "unified_ranking_error": "",
    }


def _resolve_unified_ranking_flags() -> dict:
    enabled = bool(getattr(config, "UNIFIED_RANKING_ENABLED", False))
    shadow_mode = bool(getattr(config, "UNIFIED_RANKING_SHADOW_MODE", False))
    if shadow_mode:
        mode = "shadow"
    elif enabled:
        mode = "enabled"
    else:
        mode = "disabled"
    return {
        "enabled": enabled,
        "shadow_mode": shadow_mode,
        "mode": mode,
    }


_UNIFIED_RANKING_PIPELINE = None


def _get_unified_ranking_pipeline():
    global _UNIFIED_RANKING_PIPELINE
    if _UNIFIED_RANKING_PIPELINE is None:
        from src.unified_ranking_pipeline import UnifiedRankingPipeline

        _UNIFIED_RANKING_PIPELINE = UnifiedRankingPipeline()
    return _UNIFIED_RANKING_PIPELINE


def _run_unified_ranking_shadow(item: dict, candidates: list[dict], *, top_k: int = 5) -> dict:
    pipeline = _get_unified_ranking_pipeline()
    return pipeline.rank_candidates(item, candidates, top_k=top_k)


def _build_unified_shadow_comparison(shadow_result: dict, ranking_meta: dict) -> dict:
    legacy_top1_id = str(ranking_meta.get("legacy_top1_id", "") or ranking_meta.get("selected_top1_id", "") or "")
    unified_top1_id = str(ranking_meta.get("unified_top1_id", "") or "")
    top1_score = float(shadow_result.get("top1_score", 0.0) or 0.0)
    legacy_candidate = None
    for candidate in list(shadow_result.get("candidates") or []):
        if str(candidate.get("quota_id", "") or "") == legacy_top1_id:
            legacy_candidate = candidate
            break

    legacy_score = None
    legacy_confidence = None
    score_gap = None
    if legacy_candidate:
        legacy_score = float(legacy_candidate.get("filtered_score", legacy_candidate.get("unified_score", 0.0)) or 0.0)
        legacy_confidence = float(legacy_candidate.get("confidence", 0.0) or 0.0)
        score_gap = top1_score - legacy_score

    return {
        "legacy_top1_id": legacy_top1_id,
        "unified_top1_id": unified_top1_id,
        "matches_legacy": bool(legacy_top1_id and unified_top1_id and legacy_top1_id == unified_top1_id),
        "legacy_candidate_present": legacy_candidate is not None,
        "legacy_top1_unified_score": legacy_score,
        "legacy_top1_unified_confidence": legacy_confidence,
        "score_gap": score_gap,
        "failure_reason": str(ranking_meta.get("unified_ranking_error", "") or ""),
    }


def _apply_unified_ranking_shadow(item: dict, candidates: list[dict], ranking_meta: dict) -> dict:
    if not candidates:
        return {}
    if str(ranking_meta.get("unified_ranking_mode") or "disabled") == "disabled":
        return {}
    top_k = len(candidates)
    try:
        shadow_result = _run_unified_ranking_shadow(item, candidates, top_k=top_k)
    except Exception as exc:  # pragma: no cover
        ranking_meta["unified_ranking_error"] = str(exc)
        ranking_meta["unified_ranking_executed"] = False
        return {}

    top_candidate = (shadow_result.get("candidates") or [None])[0]
    unified_top1_id = str((top_candidate or {}).get("quota_id", "") or "")
    ranking_meta["unified_ranking_executed"] = True
    ranking_meta["unified_result_used"] = False
    ranking_meta["unified_top1_id"] = unified_top1_id
    ranking_meta["unified_top1_score"] = float(shadow_result.get("top1_score", 0.0) or 0.0)
    ranking_meta["unified_top1_confidence"] = float(shadow_result.get("top1_confidence", 0.0) or 0.0)
    comparison = _build_unified_shadow_comparison(shadow_result, ranking_meta)
    ranking_meta["unified_top1_matches_selected"] = bool(
        unified_top1_id and unified_top1_id == str(ranking_meta.get("selected_top1_id", "") or "")
    )
    ranking_meta["unified_top1_matches_legacy"] = bool(comparison.get("matches_legacy"))
    ranking_meta["legacy_top1_unified_score"] = comparison.get("legacy_top1_unified_score")
    ranking_meta["legacy_top1_unified_confidence"] = comparison.get("legacy_top1_unified_confidence")
    ranking_meta["unified_legacy_score_gap"] = comparison.get("score_gap")
    ranking_meta["unified_ranking_diagnostics"] = dict(shadow_result.get("diagnostics") or {})
    ranking_meta["unified_ranking_error"] = ""
    return shadow_result


def _merge_unified_candidate(base_candidate: dict | None, unified_candidate: dict | None) -> dict | None:
    if not isinstance(unified_candidate, dict):
        return dict(base_candidate) if isinstance(base_candidate, dict) else None
    merged = dict(base_candidate or {})
    merged.update(dict(unified_candidate))
    return merged


def _apply_unified_candidate_order(base_candidates: list[dict], unified_candidates: list[dict]) -> list[dict]:
    base_by_quota_id = {
        str(candidate.get("quota_id", "") or "").strip(): candidate
        for candidate in (base_candidates or [])
        if str(candidate.get("quota_id", "") or "").strip()
    }
    ordered: list[dict] = []
    seen: set[str] = set()
    for unified_candidate in unified_candidates or []:
        quota_id = str(unified_candidate.get("quota_id", "") or "").strip()
        if not quota_id or quota_id in seen:
            continue
        ordered_candidate = _merge_unified_candidate(base_by_quota_id.get(quota_id), unified_candidate)
        if ordered_candidate:
            ordered.append(ordered_candidate)
            seen.add(quota_id)
    for candidate in base_candidates or []:
        quota_id = str(candidate.get("quota_id", "") or "").strip()
        if quota_id and quota_id in seen:
            continue
        ordered.append(dict(candidate))
    return ordered


def _format_unified_selection_explanation(unified_result: dict, candidate: dict | None) -> str:
    top_driver = str(((candidate or {}).get("explanation") or {}).get("top_driver") or "")
    score = float(unified_result.get("top1_score", 0.0) or 0.0)
    if top_driver:
        return f"unified_ranking: top_driver={top_driver}; filtered_score={score:.3f}"
    return f"unified_ranking: filtered_score={score:.3f}"


def _apply_unified_enabled_selection(item: dict,
                                     valid_candidates: list[dict],
                                     matched_candidates: list[dict],
                                     ranking_meta: dict,
                                     arbitration: dict,
                                     unified_result: dict,
                                     best: dict | None,
                                     confidence: float,
                                     explanation: str,
                                     reasoning_decision: dict) -> tuple[list[dict], list[dict], dict | None, float, str, dict]:
    if str(ranking_meta.get("unified_ranking_mode") or "disabled") != "enabled":
        return valid_candidates, matched_candidates, best, confidence, explanation, reasoning_decision

    unified_candidates = list((unified_result or {}).get("candidates") or [])
    if not unified_candidates:
        return valid_candidates, matched_candidates, best, confidence, explanation, reasoning_decision

    reordered_valid_candidates = _apply_unified_candidate_order(valid_candidates, unified_candidates)
    unified_best = reordered_valid_candidates[0] if reordered_valid_candidates else None
    if not unified_best:
        return valid_candidates, matched_candidates, best, confidence, explanation, reasoning_decision

    reordered_matched_candidates = list(matched_candidates or [])
    if matched_candidates:
        reordered_matched_candidates = _apply_unified_candidate_order(matched_candidates, unified_candidates)

    ranking_meta["unified_result_used"] = True
    ranking_meta["final_changed_by"] = "unified_ranking"
    ranking_meta["selected_top1_id"] = str(unified_best.get("quota_id", "") or "")
    ranking_meta["unified_top1_matches_selected"] = bool(
        ranking_meta["selected_top1_id"]
        and ranking_meta["selected_top1_id"] == str(ranking_meta.get("unified_top1_id", "") or "")
    )

    selected_confidence = float(
        unified_best.get("confidence", (unified_result or {}).get("top1_confidence", confidence)) or confidence
    )
    selected_explanation = _format_unified_selection_explanation(unified_result, unified_best)
    selected_reasoning = analyze_ambiguity(
        reordered_valid_candidates,
        route_profile=item.get("query_route"),
        arbitration=arbitration,
    ).as_dict()
    return (
        reordered_valid_candidates,
        reordered_matched_candidates,
        unified_best,
        selected_confidence,
        selected_explanation,
        selected_reasoning,
    )

def _build_parser_trace_diagnostics(item: dict) -> dict:
    canonical_query = item.get("canonical_query") or {}
    primary_query_profile = dict(canonical_query.get("primary_query_profile") or {})
    return {
        "search_query": str(canonical_query.get("search_query") or item.get("search_query") or item.get("name") or ""),
        "validation_query": str(canonical_query.get("validation_query") or ""),
        "route_query": str(canonical_query.get("normalized_query") or ""),
        "primary_subject": str(primary_query_profile.get("primary_subject") or ""),
        "decisive_terms": list(primary_query_profile.get("decisive_terms") or []),
        "quota_aliases": list(primary_query_profile.get("quota_aliases") or []),
        "noise_marker": str(primary_query_profile.get("noise_marker") or ""),
        "query_route": dict(item.get("query_route") or {}),
    }


def _build_router_trace_diagnostics(item: dict) -> dict:
    classification = dict(item.get("classification") or {})
    search_books = [
        str(book).strip()
        for book in list(classification.get("search_books") or [])
        if str(book).strip()
    ]
    hard_search_books = [
        str(book).strip()
        for book in list(
            classification.get("hard_search_books")
            or classification.get("hard_book_constraints")
            or []
        )
        if str(book).strip()
    ]
    advisory_search_books = [
        book for book in search_books
        if book not in hard_search_books
    ]
    unified_plan = dict(item.get("unified_plan") or {})
    plugin_hints = dict(item.get("plugin_hints") or {})
    classification_reason = str(classification.get("reason") or "").strip()
    if classification_reason.startswith("unified_plan"):
        effective_owner = "unified_plan"
    elif classification_reason in {"item_specialty", "soft_item_specialty"}:
        effective_owner = "seeded_specialty"
    elif classification.get("primary"):
        effective_owner = "specialty_classifier"
    else:
        effective_owner = "open_search"

    advisory_owner = ""
    if unified_plan and (
        unified_plan.get("preferred_books")
        or unified_plan.get("hard_books")
        or unified_plan.get("search_aliases")
    ):
        advisory_owner = "unified_plan"
    elif plugin_hints and (
        plugin_hints.get("preferred_books")
        or plugin_hints.get("preferred_specialties")
        or plugin_hints.get("synonym_aliases")
    ):
        advisory_owner = "province_plugin"
    elif item.get("specialty"):
        advisory_owner = "seeded_specialty"

    return {
        "query_route": dict(item.get("query_route") or {}),
        "plugin_hints": plugin_hints,
        "unified_plan": unified_plan,
        "advisory_owner": advisory_owner,
        "effective_owner": effective_owner,
        "effective_reason": classification_reason,
        "classification": {
            "primary": str(classification.get("primary") or ""),
            "fallbacks": list(classification.get("fallbacks") or []),
            "candidate_books": list(classification.get("candidate_books") or []),
            "search_books": search_books,
            "hard_book_constraints": list(classification.get("hard_book_constraints") or []),
            "hard_search_books": hard_search_books,
            "advisory_search_books": advisory_search_books,
            "route_mode": str(classification.get("route_mode") or ""),
        },
    }


def _build_retriever_trace_diagnostics(item: dict,
                                       valid_candidates: list[dict],
                                       matched_candidates: list[dict],
                                       router_diagnostics: dict | None = None) -> dict:
    classification = dict(item.get("classification") or {})
    resolution = dict(classification.get("retrieval_resolution") or {})
    calls = list(resolution.get("calls") or [])
    main_calls = [call for call in calls if str(call.get("target") or "").strip() == "main"]
    escape_used = any(str(call.get("stage") or "").strip() == "escape" for call in main_calls)
    open_used = any(
        str(call.get("stage") or "").strip() in {"escape", "open"}
        for call in main_calls
    )
    resolved_main_books = []
    for call in main_calls:
        resolved_books = [
            str(book).strip()
            for book in list(call.get("resolved_books") or [])
            if str(book).strip()
        ]
        if resolved_books:
            resolved_main_books = resolved_books
            break
    router_effective_owner = str((router_diagnostics or {}).get("effective_owner") or "")
    scope_owner = "retriever_main_escape" if escape_used else (router_effective_owner or "router")
    return {
        "candidate_count": len(valid_candidates or []),
        "matched_candidate_count": len(matched_candidates or []),
        "candidate_ids": [
            str(candidate.get("quota_id", "") or "").strip()
            for candidate in (valid_candidates or [])
            if str(candidate.get("quota_id", "") or "").strip()
        ],
        "authority_hit": any(has_exact_experience_anchor(candidate) for candidate in (valid_candidates or [])),
        "kb_hit": any(has_exact_universal_kb_anchor(candidate) for candidate in (valid_candidates or [])),
        "scope_owner": scope_owner,
        "escape_owner": "retriever_main_escape" if escape_used else "",
        "used_open_search": open_used,
        "resolved_main_books": resolved_main_books,
        "route_scope_filter": dict(classification.get("route_scope_filter") or {}),
        "candidate_scope_guard": dict(classification.get("candidate_scope_guard") or {}),
        "search_resolution": resolution,
    }


def _build_ranker_trace_diagnostics(candidates: list[dict], best: dict | None, ranking_meta: dict, arbitration: dict) -> dict:
    ordered = list(candidates or [])
    selected = best or (ordered[0] if ordered else None)
    second = ordered[1] if len(ordered) > 1 else None
    selected_score = compute_candidate_rank_score(selected) if selected else 0.0
    second_score = compute_candidate_rank_score(second) if second else 0.0

    timeline = [
        {"stage": "pre_ltr_seed", "quota_id": str(ranking_meta.get("pre_ltr_top1_id", "") or "")},
        {"stage": "ltr", "quota_id": str(ranking_meta.get("post_ltr_top1_id", "") or "")},
        {"stage": "cgr_ranker", "quota_id": str(ranking_meta.get("post_cgr_top1_id", "") or "")},
        {"stage": "candidate_arbiter", "quota_id": str(ranking_meta.get("post_arbiter_top1_id", "") or "")},
        {"stage": "explicit_override", "quota_id": str(ranking_meta.get("post_explicit_top1_id", "") or "")},
        {"stage": "experience_anchor", "quota_id": str(ranking_meta.get("post_anchor_top1_id", "") or "")},
        {
            "stage": "unified_ranking",
            "quota_id": str(ranking_meta.get("unified_top1_id", "") or "") if ranking_meta.get("unified_result_used") else "",
        },
        {"stage": "selected", "quota_id": str(ranking_meta.get("selected_top1_id", "") or "")},
    ]

    rank_timeline_changes = []
    prev_quota_id = ""
    decision_owner = "pre_ltr_seed"
    for entry in timeline:
        quota_id = str(entry.get("quota_id", "") or "")
        if not quota_id:
            continue
        if not prev_quota_id:
            prev_quota_id = quota_id
            continue
        if quota_id != prev_quota_id:
            rank_timeline_changes.append({
                "stage": entry["stage"],
                "from_quota_id": prev_quota_id,
                "to_quota_id": quota_id,
            })
            decision_owner = entry["stage"]
            prev_quota_id = quota_id

    if decision_owner == "selected":
        decision_owner = rank_timeline_changes[-1]["stage"] if rank_timeline_changes else "pre_ltr_seed"

    return {
        "selected_quota": str((selected or {}).get("quota_id", "") or ""),
        "selected_rank_score": selected_score,
        "second_rank_score": second_score,
        "score_gap": max(selected_score - second_score, 0.0),
        "selected_rank_breakdown": explain_candidate_rank_score(selected or {}),
        "second_rank_breakdown": explain_candidate_rank_score(second or {}) if second else {"rank_score": 0.0, "stage_priority": {}},
        "decision_owner": decision_owner,
        "top1_flip_count": len(rank_timeline_changes),
        "rank_timeline": timeline,
        "rank_timeline_changes": rank_timeline_changes,
        "arbitration": dict(arbitration or {}),
        "unified_ranking": {
            "enabled": bool(ranking_meta.get("unified_ranking_enabled")),
            "shadow_mode": bool(ranking_meta.get("unified_ranking_shadow_mode")),
            "mode": str(ranking_meta.get("unified_ranking_mode") or "disabled"),
            "executed": bool(ranking_meta.get("unified_ranking_executed")),
            "legacy_selected_quota": str(ranking_meta.get("legacy_top1_id", "") or ""),
            "selected_quota": str(ranking_meta.get("unified_top1_id", "") or ""),
            "score": float(ranking_meta.get("unified_top1_score", 0.0) or 0.0),
            "confidence": float(ranking_meta.get("unified_top1_confidence", 0.0) or 0.0),
            "matches_selected": bool(ranking_meta.get("unified_top1_matches_selected")),
            "matches_legacy": bool(ranking_meta.get("unified_top1_matches_legacy")),
            "legacy_score": ranking_meta.get("legacy_top1_unified_score"),
            "legacy_confidence": ranking_meta.get("legacy_top1_unified_confidence"),
            "score_gap_vs_legacy": ranking_meta.get("unified_legacy_score_gap"),
            "result_used": bool(ranking_meta.get("unified_result_used")),
            "diagnostics": dict(ranking_meta.get("unified_ranking_diagnostics") or {}),
            "error": str(ranking_meta.get("unified_ranking_error", "") or ""),
        },
    }


def _run_rank_pipeline(item: dict,
                       decision_candidates: list[dict],
                       *,
                       reservoir: list[dict],
                       allow_arbiter: bool,
                       allow_explicit: bool) -> tuple[list[dict], dict, dict, dict, dict | None]:
    ordered = list(decision_candidates or [])
    ranking_meta = _init_ranking_meta()
    ranking_meta["candidate_count"] = len(reservoir or [])
    unified_ranking_flags = _resolve_unified_ranking_flags()
    ranking_meta["unified_ranking_enabled"] = unified_ranking_flags["enabled"]
    ranking_meta["unified_ranking_shadow_mode"] = unified_ranking_flags["shadow_mode"]
    ranking_meta["unified_ranking_mode"] = unified_ranking_flags["mode"]
    arbitration: dict = {}
    explicit_override: dict = {}

    if not ordered:
        return ordered, ranking_meta, arbitration, explicit_override, None

    ranking_meta["pre_ltr_top1_id"] = _top_candidate_id(ordered)
    if ranking_meta["unified_ranking_mode"] == "enabled":
        seed_top1_id = ranking_meta["pre_ltr_top1_id"]
        ranking_meta["ltr"] = {
            "skipped_by_unified_primary": True,
            "legacy_stage_disabled": True,
        }
        ranking_meta["post_ltr_top1_id"] = seed_top1_id
        ranking_meta["post_cgr_top1_id"] = seed_top1_id
        ranking_meta["post_arbiter_top1_id"] = seed_top1_id
        ranking_meta["post_explicit_top1_id"] = seed_top1_id
        ranking_meta["post_anchor_top1_id"] = seed_top1_id
        arbitration = {
            "applied": False,
            "advisory_applied": False,
            "reason": "skipped_by_unified_primary",
            "legacy_stage_disabled": True,
        }
        explicit_override = {
            "applied": False,
            "advisory_applied": False,
            "reason": "skipped_by_unified_primary",
            "legacy_stage_disabled": True,
        }
        best = ordered[0] if ordered else None
        if not best:
            best = _pick_category_safe_candidate(item, ordered)
        if best:
            ranking_meta["selected_top1_id"] = str(best.get("quota_id", "") or "")
        return ordered, ranking_meta, arbitration, explicit_override, best

    ordered, ltr_meta = rerank_candidates_with_ltr(item, ordered, {"item": item})
    ranking_meta["ltr"] = ltr_meta
    ranking_meta["post_ltr_top1_id"] = str((ltr_meta.get("post_ltr_top1_id") or _top_candidate_id(ordered)) or "")
    ranking_meta["post_cgr_top1_id"] = str((ltr_meta.get("post_cgr_top1_id") or ranking_meta["post_ltr_top1_id"]) or "")

    if allow_arbiter:
        arbiter_candidates, arbitration = arbitrate_candidates(item, ordered, route_profile=item.get("query_route"))
        ordered = _merge_arbiter_annotations(ordered, arbiter_candidates)
        if arbitration.get("applied"):
            arbitration = {
                **dict(arbitration or {}),
                "applied": False,
                "reason": str(arbitration.get("reason") or "structured_candidate_swap_advisory"),
                "reorder_ignored_by_pipeline": True,
            }
        ranking_meta["post_arbiter_top1_id"] = _top_candidate_id(ordered)
    else:
        arbitration = {
            "applied": False,
            "advisory_applied": False,
            "route": str((item.get("query_route") or {}).get("route") or ""),
            "reason": "no_param_matched_candidates",
        }
        ranking_meta["post_arbiter_top1_id"] = ranking_meta["post_ltr_top1_id"]

    if allow_explicit:
        explicit_result = _promote_explicit_distribution_box_candidate(item, ordered)
        if isinstance(explicit_result, tuple) and len(explicit_result) == 2:
            explicit_candidates, explicit_override = explicit_result
        else:
            explicit_candidates = list(explicit_result or [])
            explicit_override = {}
        ordered = _merge_explicit_annotations(ordered, explicit_candidates)
        if explicit_override.get("applied"):
            explicit_override = {
                **dict(explicit_override or {}),
                "applied": False,
                "reason": str(explicit_override.get("reason") or "explicit_advisory"),
                "reorder_ignored_by_pipeline": True,
            }
        ranking_meta["explicit_override"] = explicit_override
        ranking_meta["post_explicit_top1_id"] = _top_candidate_id(ordered)
    else:
        ranking_meta["post_explicit_top1_id"] = ranking_meta["post_arbiter_top1_id"]

    ranking_meta["post_anchor_top1_id"] = _top_candidate_id(ordered)

    best = ordered[0] if ordered else None
    if not best:
        best = _pick_category_safe_candidate(item, ordered)
    if best:
        ranking_meta["selected_top1_id"] = str(best.get("quota_id", "") or "")
    return ordered, ranking_meta, arbitration, explicit_override, best


def _assemble_search_result_payload(item: dict,
                                    *,
                                    candidates: list[dict],
                                    valid_candidates: list[dict],
                                    matched_candidates: list[dict],
                                    best: dict | None,
                                    confidence: float,
                                    explanation: str,
                                    arbitration: dict,
                                    explicit_override: dict,
                                    plugin_route_gate: dict,
                                    reasoning_decision: dict,
                                    ranking_meta: dict) -> dict:
    all_candidate_ids = [
        str(candidate.get("quota_id", "")).strip()
        for candidate in valid_candidates
        if str(candidate.get("quota_id", "")).strip()
    ]
    parser_diagnostics = _build_parser_trace_diagnostics(item)
    router_diagnostics = _build_router_trace_diagnostics(item)
    retriever_diagnostics = _build_retriever_trace_diagnostics(
        item,
        valid_candidates,
        matched_candidates if valid_candidates else [],
        router_diagnostics,
    )
    ranker_candidates = valid_candidates if ranking_meta.get("unified_result_used") else (
        matched_candidates if matched_candidates else valid_candidates
    )
    ranker_diagnostics = _build_ranker_trace_diagnostics(ranker_candidates, best, ranking_meta, arbitration)

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
        "candidate_count": len(valid_candidates),
        "all_candidate_ids": all_candidate_ids,
        "candidate_snapshots": _build_ranked_candidate_snapshots(valid_candidates, top_n=20),
        "match_source": "search",
        "arbitration": arbitration,
        "explicit_override": explicit_override,
        "plugin_route_gate": plugin_route_gate,
        "reasoning_decision": reasoning_decision,
        "needs_reasoning": bool(reasoning_decision.get("is_ambiguous")),
        "require_final_review": bool(reasoning_decision.get("require_final_review")),
        "pre_ltr_top1_id": ranking_meta["pre_ltr_top1_id"],
        "post_ltr_top1_id": ranking_meta["post_ltr_top1_id"],
        "post_cgr_top1_id": ranking_meta["post_cgr_top1_id"],
        "post_arbiter_top1_id": ranking_meta["post_arbiter_top1_id"],
        "post_explicit_top1_id": ranking_meta["post_explicit_top1_id"],
        "post_anchor_top1_id": ranking_meta["post_anchor_top1_id"],
        "selected_top1_id": ranking_meta["selected_top1_id"],
        "legacy_top1_id": ranking_meta["legacy_top1_id"],
        "unified_ranking_enabled": ranking_meta["unified_ranking_enabled"],
        "unified_ranking_shadow_mode": ranking_meta["unified_ranking_shadow_mode"],
        "unified_ranking_mode": ranking_meta["unified_ranking_mode"],
        "unified_ranking_executed": ranking_meta["unified_ranking_executed"],
        "unified_result_used": ranking_meta["unified_result_used"],
        "unified_top1_id": ranking_meta["unified_top1_id"],
        "unified_top1_score": ranking_meta["unified_top1_score"],
        "unified_top1_confidence": ranking_meta["unified_top1_confidence"],
        "unified_top1_matches_selected": ranking_meta["unified_top1_matches_selected"],
        "unified_top1_matches_legacy": ranking_meta["unified_top1_matches_legacy"],
        "legacy_top1_unified_score": ranking_meta["legacy_top1_unified_score"],
        "legacy_top1_unified_confidence": ranking_meta["legacy_top1_unified_confidence"],
        "unified_legacy_score_gap": ranking_meta["unified_legacy_score_gap"],
        "unified_shadow_comparison": {
            "legacy_top1_id": ranking_meta["legacy_top1_id"],
            "unified_top1_id": ranking_meta["unified_top1_id"],
            "matches": ranking_meta["unified_top1_matches_legacy"],
            "legacy_top1_unified_score": ranking_meta["legacy_top1_unified_score"],
            "legacy_top1_unified_confidence": ranking_meta["legacy_top1_unified_confidence"],
            "score_gap": ranking_meta["unified_legacy_score_gap"],
            "failure_reason": ranking_meta["unified_ranking_error"],
        },
        "unified_ranking_diagnostics": ranking_meta["unified_ranking_diagnostics"],
        "unified_ranking_error": ranking_meta["unified_ranking_error"],
        "post_final_top1_id": str((quotas[0].get("quota_id", "") if quotas else "") or ""),
        "final_changed_by": ranking_meta["final_changed_by"],
        "ltr_rerank": ranking_meta["ltr"],
        "rank_decision_owner": ranker_diagnostics.get("decision_owner", ""),
        "rank_top1_flip_count": ranker_diagnostics.get("top1_flip_count", 0),
    }

    _append_trace_step(
        result,
        "search_select",
        selected_quota=best.get("quota_id") if best else "",
        selected_reasoning=summarize_candidate_reasoning(best) if best else {},
        pre_ltr_top1_id=result.get("pre_ltr_top1_id", ""),
        post_ltr_top1_id=result.get("post_ltr_top1_id", ""),
        post_cgr_top1_id=result.get("post_cgr_top1_id", ""),
        post_arbiter_top1_id=result.get("post_arbiter_top1_id", ""),
        post_explicit_top1_id=result.get("post_explicit_top1_id", ""),
        post_anchor_top1_id=result.get("post_anchor_top1_id", ""),
        selected_top1_id=result.get("selected_top1_id", ""),
        arbitration=arbitration,
        explicit_override=explicit_override,
        plugin_route_gate=plugin_route_gate,
        reasoning_decision=reasoning_decision,
        parser=parser_diagnostics,
        router=router_diagnostics,
        retriever=retriever_diagnostics,
        ranker=ranker_diagnostics,
        query_route=item.get("query_route") or {},
        batch_context=summarize_batch_context_for_trace(item),
        ltr_rerank=result.get("ltr_rerank", {}),
        candidates_count=len(valid_candidates),
        candidates=_summarize_candidates_for_trace(candidates),
    )
    return result


def _finalize_search_result_payload(result: dict,
                                    *,
                                    item: dict,
                                    candidates: list[dict],
                                    valid_candidates: list[dict],
                                    best: dict | None,
                                    explanation: str,
                                    reasoning_decision: dict) -> dict:
    input_gate = item.get("_input_gate") or {}
    if best and valid_candidates and any(candidate.get("param_match", True) for candidate in valid_candidates):
        _set_result_reason(result, "structured_selection", ["retrieved", "validated"], explanation or "selected from structured candidates")
    elif best and valid_candidates:
        _set_result_reason(result, "param_conflict", ["retrieved", "param_conflict", "manual_review"], explanation or "fallback to best candidate")
    elif candidates and not valid_candidates:
        _set_result_reason(result, "candidate_invalid", ["retrieved", "candidate_invalid", "manual_review"], "candidates missing quota_id/name")
    else:
        _set_result_reason(result, "recall_failure", ["recall_failure", "no_candidates"], "search found no candidates")

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
        _set_result_reason(result, result.get("primary_reason", ""), ambiguity_tags, result.get("reason_detail", "") or explanation)

    result = _apply_price_validation(result, item, best)

    if best and valid_candidates:
        result["alternatives"] = _build_alternatives(valid_candidates, skip_obj=best, top_n=DEFAULT_ALTERNATIVE_COUNT)
    if not best:
        result["no_match_reason"] = explanation or "搜索无匹配结果"
    return result


def _build_ranked_selection_decision(item: dict,
                                     *,
                                     best: dict | None,
                                     decision_candidates: list[dict],
                                     candidates_count: int,
                                     param_match: bool,
                                     arbitration: dict) -> tuple[float, str, dict]:
    if not best:
        return 0.0, "no safe candidate selected", {}

    if param_match:
        best_composite = compute_candidate_rank_score(best)
        others = [candidate for candidate in decision_candidates if candidate is not best]
        second_composite = max((compute_candidate_rank_score(candidate) for candidate in others), default=0)
        confidence = calculate_confidence(
            best.get("param_score", 0.5),
            param_match=True,
            name_bonus=best.get("name_bonus", 0.0),
            score_gap=best_composite - second_composite,
            rerank_score=best.get("rerank_score", best.get("hybrid_score", 0.0)),
            candidates_count=candidates_count,
            is_ambiguous_short=item.get("_is_ambiguous_short", False),
        )
        explanation = best.get("param_detail", "")
    else:
        confidence = calculate_confidence(
            best.get("param_score", 0.0),
            param_match=False,
            name_bonus=best.get("name_bonus", 0.0),
            rerank_score=best.get("rerank_score", best.get("hybrid_score", 0.0)),
            family_aligned=infer_confidence_family_alignment(best),
            family_hard_conflict=bool(best.get("family_gate_hard_conflict", False)),
            candidates_count=candidates_count,
            is_ambiguous_short=item.get("_is_ambiguous_short", False),
        )
        explanation = f"fallback_to_candidate: {best.get('param_detail', '')}"

    reasoning_decision = analyze_ambiguity(
        decision_candidates,
        route_profile=item.get("query_route"),
        arbitration=arbitration,
    ).as_dict()
    return confidence, explanation, reasoning_decision


def _build_search_result_from_candidates_legacy(item: dict, candidates: list[dict]) -> dict:
    return _build_search_result_from_candidates(item, candidates)


def _build_search_result_from_candidates(item: dict, candidates: list[dict]) -> dict:
    performance_monitor = PerformanceMonitor()
    best = None
    confidence = 0.0
    explanation = ""
    arbitration: dict = {}
    explicit_override: dict = {}
    reasoning_decision: dict = {}
    matched_candidates: list[dict] = []
    ranking_meta = _init_ranking_meta()

    with performance_monitor.measure("search_candidates_validate"):
        valid_candidates = [
            candidate
            for candidate in (candidates or [])
            if str(candidate.get("quota_id", "")).strip() and str(candidate.get("name", "")).strip()
        ]
    with performance_monitor.measure("search_plugin_route_gate"):
        valid_candidates, plugin_route_gate = _apply_plugin_route_gate(item, valid_candidates)
    with performance_monitor.measure("search_plugin_bias"):
        valid_candidates = _apply_plugin_candidate_biases(item, valid_candidates)
    with performance_monitor.measure("search_scope_annotate"):
        valid_candidates = _annotate_candidate_scope_signals(item, valid_candidates)
    ranking_meta["candidate_count"] = len(valid_candidates)
    if candidates and not valid_candidates:
        logger.warning("candidate list exists but all items miss quota_id/name; treat as no-match")

    if valid_candidates:
        with performance_monitor.measure("search_param_match_filter"):
            matched_candidates = [candidate for candidate in valid_candidates if candidate.get("param_match", True)]
        decision_candidates = matched_candidates if matched_candidates else valid_candidates
        with performance_monitor.measure("search_rank_pipeline"):
            ranked_candidates, ranking_meta, arbitration, explicit_override, best = _run_rank_pipeline(
                item,
                decision_candidates,
                reservoir=valid_candidates,
                allow_arbiter=bool(matched_candidates),
                allow_explicit=bool(matched_candidates),
            )
        if matched_candidates:
            matched_candidates = ranked_candidates
        else:
            valid_candidates = ranked_candidates

        if best:
            ranking_meta["selected_top1_id"] = str(best.get("quota_id", "") or "")
            with performance_monitor.measure("search_selection_decision"):
                confidence, explanation, reasoning_decision = _build_ranked_selection_decision(
                    item,
                    best=best,
                    decision_candidates=ranked_candidates,
                    candidates_count=len(valid_candidates),
                    param_match=bool(matched_candidates),
                    arbitration=arbitration,
                )
        else:
            explanation = "no safe candidate selected from ranked results"

    ranking_meta["legacy_top1_id"] = str(ranking_meta.get("selected_top1_id", "") or "")
    unified_result = _apply_unified_ranking_shadow(item, valid_candidates, ranking_meta)
    valid_candidates, matched_candidates, best, confidence, explanation, reasoning_decision = _apply_unified_enabled_selection(
        item,
        valid_candidates,
        matched_candidates,
        ranking_meta,
        arbitration,
        unified_result,
        best,
        confidence,
        explanation,
        reasoning_decision,
    )

    with performance_monitor.measure("search_result_payload_assemble"):
        result = _assemble_search_result_payload(
            item,
            candidates=candidates,
            valid_candidates=valid_candidates,
            matched_candidates=matched_candidates,
            best=best,
            confidence=confidence,
            explanation=explanation,
            arbitration=arbitration,
            explicit_override=explicit_override,
            plugin_route_gate=plugin_route_gate,
            reasoning_decision=reasoning_decision,
            ranking_meta=ranking_meta,
        )
    result["search_candidate_stage_performance"] = {
        "stages": performance_monitor.snapshot(),
        "total": sum(performance_monitor.snapshot().values()),
    }
    return _finalize_search_result_payload(
        result,
        item=item,
        candidates=candidates,
        valid_candidates=valid_candidates,
        best=best,
        explanation=explanation,
        reasoning_decision=reasoning_decision,
    )
def _resolve_search_mode_result(item: dict, candidates: list[dict],
                                exp_backup: dict, rule_backup: dict,
                                exp_hits: int, rule_hits: int):
    """search模式统一结果决策：搜索结果 + 经验/规则兜底。"""
    performance_monitor = PerformanceMonitor()
    active_candidates = list(candidates or [])
    injected_rule_qid = ""
    with performance_monitor.measure("search_rule_backup_injection"):
        if rule_backup:
            active_candidates, injected_rule_qid = _inject_rule_backup_candidate(
                item, active_candidates, rule_backup
            )
    with performance_monitor.measure("search_result_build"):
        result = _build_search_result_from_candidates(item, active_candidates)
    _append_item_review_rejection_trace(result, item)
    with performance_monitor.measure("search_experience_reconcile"):
        result, exp_hits = _reconcile_search_and_experience(result, exp_backup, exp_hits)
    if injected_rule_qid:
        selected_qid = str((result.get("quotas") or [{}])[0].get("quota_id", "") or "").strip()
        if selected_qid == injected_rule_qid:
            result["match_source"] = "rule_injected"
            rule_hits += 1
        _append_trace_step(
            result,
            "rule_backup_injected",
            injected_quota_id=injected_rule_qid,
            backup_confidence=rule_backup.get("confidence", 0),
            selected_rule_candidate=bool(selected_qid and selected_qid == injected_rule_qid),
        )
    elif rule_backup:
        _append_trace_step(
            result,
            "rule_backup_rejected",
            backup_confidence=rule_backup.get("confidence", 0),
            current_confidence=result.get("confidence", 0),
        )
    result["search_stage_performance"] = {
        "stages": performance_monitor.snapshot(),
        "total": sum(performance_monitor.snapshot().values()),
    }
    _append_trace_step(
        result,
        "search_mode_final",
        final_source=result.get("match_source", ""),
        final_confidence=result.get("confidence", 0),
        search_stage_performance=result.get("search_stage_performance") or {},
    )
    return result, exp_hits, rule_hits


# ============================================================
# 统一前置处理
# ============================================================

def _prepare_item_for_matching(item: dict, experience_db, rule_validator: RuleValidator,
                               province: str = None, exact_exp_direct: bool = False,
                               lightweight_experience: bool = False,
                               lightweight_rule_prematch: bool = False,
                               performance_monitor: PerformanceMonitor | None = None) -> dict:
    """
    三种模式统一的前置处理：
    1) 措施项跳过
    2) 专业分类
    3) 经验库预匹配（可配置精确命中是否直通）
    4) 规则预匹配（高置信直通、低置信备选）
    """
    if province and not item.get("_resolved_province"):
        item["_resolved_province"] = province
    ctx = _build_item_context(item, performance_monitor=performance_monitor)
    item["query_route"] = ctx.get("query_route")
    item["plugin_hints"] = ctx.get("plugin_hints") or {}
    item["unified_plan"] = ctx.get("unified_plan") or {}
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

    with (
        performance_monitor.measure("专业分类")
        if performance_monitor is not None else nullcontext()
    ):
        classification = _build_classification(
            item, name, desc, ctx["section"], ctx.get("sheet_name", ""), province=province
        )
    item["classification"] = classification
    item["_trace_classification"] = dict(classification or {})

    adaptive_meta = dict(item.get("_adaptive_strategy_meta") or item.get("adaptive_strategy_meta") or {})
    if not adaptive_meta:
        adaptive_meta = dict(_ADAPTIVE_STRATEGY.evaluate(item))
    adaptive_strategy = str(adaptive_meta.get("strategy") or item.get("adaptive_strategy") or "standard").strip().lower()
    if adaptive_strategy not in {"fast", "standard", "deep"}:
        adaptive_strategy = "standard"
    adaptive_meta["strategy"] = adaptive_strategy
    if adaptive_strategy == "fast" and experience_db is None:
        adaptive_meta["downgraded_from"] = "fast"
        adaptive_meta["downgrade_reason"] = "missing_experience_db"
        adaptive_meta["strategy"] = "standard"
        adaptive_strategy = "standard"
    item["adaptive_strategy"] = adaptive_strategy
    item["adaptive_strategy_meta"] = adaptive_meta
    item["_adaptive_strategy_meta"] = adaptive_meta

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

    if adaptive_strategy == "fast":
        exp_result = try_experience_match(
            normalized_query, item, experience_db, rule_validator, province=province)
    elif lightweight_experience:
        exp_result = try_experience_exact_match(
            normalized_query,
            item,
            experience_db,
            rule_validator,
            province=province,
            authority_only=True,
        )
    else:
        exp_result = try_experience_match(
            normalized_query, item, experience_db, rule_validator, province=province)

    # 审核规则检查：经验库命中后，用审核规则验证一遍
    # 防止错误数据进入权威层后被无限复制
    if exp_result:
        review_error = _review_check_match_result(exp_result, item)
        if review_error:
            # 在 item 上标记审核拦截（后续统计时从 result.bill_item 中读取）
            item["_review_rejected"] = True
            top_quota = ((exp_result.get("quotas") or [{}])[0] or {})
            item["_experience_review_rejection"] = {
                "type": review_error.get("type"),
                "reason": review_error.get("reason"),
                "match_source": exp_result.get("match_source", ""),
                "quota_id": str(top_quota.get("quota_id", "") or ""),
            }
            bill_name = item.get("name", "")
            logger.warning(
                f"经验库匹配被审核规则拦截: '{bill_name[:40]}' "
                f"→ {review_error.get('type')}: {review_error.get('reason')}")
            _append_trace_step(exp_result, "experience_review_rejected",
                               error_type=review_error.get("type"),
                               error_reason=review_error.get("reason"))
            exp_result = None  # 丢弃，走搜索兜底

    exp_backup = exp_result if exp_result else None

    if adaptive_strategy == "fast" and exp_result is None:
        adaptive_meta["downgraded_from"] = "fast"
        adaptive_meta["downgrade_reason"] = "experience_miss"
        adaptive_meta["strategy"] = "standard"
        item["adaptive_strategy"] = "standard"
        item["adaptive_strategy_meta"] = adaptive_meta
        item["_adaptive_strategy_meta"] = adaptive_meta
        adaptive_strategy = "standard"

    if exact_exp_direct and exp_result and exp_result.get("match_source") == "experience_exact":
        _append_trace_step(exp_result, "experience_exact_direct_return")
        return {
            "early_result": exp_result,
            "early_type": "experience_exact",
        }

    if lightweight_rule_prematch:
        rule_direct, rule_backup = None, None
    else:
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
