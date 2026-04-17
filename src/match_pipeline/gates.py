# -*- coding: utf-8 -*-
"""Input, context, and review gates for match pipeline."""

import re

from loguru import logger

from src.match_core import _append_trace_step
from src.reason_taxonomy import apply_reason_metadata, merge_reason_tags
from src.review_checkers import extract_description_lines

def _api():
    import src.match_pipeline as api

    return api

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
    api = _api()

    # 运行所有审核检查器，收集全部错误（不再短路）
    checkers = [
        api.check_category_mismatch(review_item, quota_name, desc_lines),
        api.check_sleeve_mismatch(review_item, quota_name, desc_lines),
        api.check_material_mismatch(review_item, quota_name, desc_lines),
        api.check_connection_mismatch(review_item, quota_name, desc_lines),
        api.check_pipe_usage(review_item, quota_name, desc_lines),
        api.check_parameter_deviation(review_item, quota_name, desc_lines),
        api.check_electric_pair(review_item, quota_name, desc_lines),
        api.check_elevator_type(review_item, quota_name, desc_lines),
        api.check_elevator_floor(review_item, quota_name, desc_lines, quota_id=quota_id),
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

